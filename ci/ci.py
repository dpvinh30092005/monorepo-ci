#!/usr/bin/env python3
"""Monorepo CI driver.

One entrypoint shared by GitHub Actions and local development, so a green
run on a laptop means the same commands will pass in CI:

    python ci/ci.py list                              # every project
    python ci/ci.py validate                          # check all project.yaml
    python ci/ci.py detect --base origin/main         # which projects are affected?
    python ci/ci.py run projects/api-node             # install/lint/build/test one project
    python ci/ci.py run --affected --base origin/main # only what my branch touched

A project is any directory under projects/ or libs/ that contains a project.yaml.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "project.yaml"
SEARCH_ROOTS = ("libs", "projects")  # libs first: dependencies run before dependents locally
SKIP_DIRS = {"node_modules", ".venv", "venv", "vendor", "dist", "build", ".git"}
STAGES = ("install", "lint", "build", "test")
ZERO_SHA = "0" * 40

# Never trigger a build. Checked first, so a README under ci/ is still ignored.
IGNORED_PATTERNS = (
    "*.md",
    "docs/*",
    "LICENSE",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
)
# The pipeline itself changed: every project must be re-verified.
GLOBAL_PATTERNS = (
    ".github/workflows/*",
    ".github/actions/*",
    "ci/*",
)

# Per-language defaults. A project.yaml only needs to state what differs.
TOOLCHAINS: dict[str, dict] = {
    "node": {
        "version": "22",
        "commands": {
            "install": "npm ci",
            "lint": "npm run lint --if-present",
            "build": "npm run build --if-present",
            "test": "npm test",
        },
    },
    "python": {
        "version": "3.12",
        "commands": {
            "install": 'python -m pip install -e ".[dev]"',
            "lint": "ruff check . && ruff format --check .",
            "build": "python -m compileall -q src",
            "test": "pytest",
        },
    },
    "go": {
        "version": "",  # empty => setup-go reads it from go.mod
        "commands": {
            "install": "go mod download",
            "lint": 'go vet ./... && test -z "$(gofmt -l . | tee /dev/stderr)"',
            "build": "go build ./...",
            "test": "go test -race -coverprofile=reports/coverage.out ./...",
        },
    },
    # No toolchain; the project must spell out its commands (e.g. a Makefile).
    "custom": {"version": "", "commands": {}},
}

MANIFEST_KEYS = {"name", "type", "version", "depends_on", "commands", "docker"}


class ManifestError(Exception):
    pass


@dataclass
class Project:
    path: str  # repo-relative, posix
    name: str
    type: str
    version: str = ""
    depends_on: tuple[str, ...] = ()
    commands: dict[str, str] = field(default_factory=dict)
    docker: bool = False

    def matrix_entry(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "version": self.version,
            "docker": self.docker,
            "commands": self.commands,
        }


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def load_manifest(manifest: Path, root: Path = ROOT) -> Project:
    rel = manifest.parent.relative_to(root).as_posix()
    where = f"{rel}/{MANIFEST}"
    try:
        data = yaml.safe_load(manifest.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{where}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{where}: must be a mapping")

    unknown = set(data) - MANIFEST_KEYS
    if unknown:
        raise ManifestError(f"{where}: unknown keys {sorted(unknown)}")

    ptype = data.get("type")
    if ptype not in TOOLCHAINS:
        raise ManifestError(f"{where}: 'type' must be one of {sorted(TOOLCHAINS)}, got {ptype!r}")
    defaults = TOOLCHAINS[ptype]

    overrides = data.get("commands") or {}
    if not isinstance(overrides, dict) or set(overrides) - set(STAGES):
        raise ManifestError(f"{where}: 'commands' keys must be a subset of {list(STAGES)}")
    # An explicit null/"" disables a stage.
    commands = {
        s: str(overrides[s] or "") if s in overrides else defaults["commands"].get(s, "")
        for s in STAGES
    }
    if not commands["test"]:
        raise ManifestError(f"{where}: no 'test' command (type {ptype!r} has no default)")

    docker = bool(data.get("docker", False))
    if docker and not (manifest.parent / "Dockerfile").is_file():
        raise ManifestError(f"{where}: docker: true but no Dockerfile next to it")

    depends_on = data.get("depends_on") or []
    if not isinstance(depends_on, list):
        raise ManifestError(f"{where}: 'depends_on' must be a list of project paths")

    return Project(
        path=rel,
        name=str(data.get("name") or manifest.parent.name),
        type=ptype,
        version=str(data.get("version") or defaults["version"]),
        depends_on=tuple(str(d).rstrip("/") for d in depends_on),
        commands=commands,
        docker=docker,
    )


def discover(root: Path = ROOT) -> list[Project]:
    manifests: list[Path] = []
    for search in SEARCH_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root / search):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            if MANIFEST in filenames:
                manifests.append(Path(dirpath) / MANIFEST)

    projects, errors = [], []
    for m in manifests:
        try:
            projects.append(load_manifest(m, root))
        except ManifestError as exc:
            errors.append(str(exc))

    paths = {p.path for p in projects}
    seen: dict[str, str] = {}
    for p in projects:
        if p.name in seen:
            errors.append(f"{p.path}: name {p.name!r} already used by {seen[p.name]}")
        seen[p.name] = p.path
        for dep in p.depends_on:
            if dep not in paths:
                errors.append(f"{p.path}: depends_on {dep!r} is not a known project")

    if errors:
        raise ManifestError("\n".join(errors))
    return projects


# --------------------------------------------------------------------------- #
# Change detection
# --------------------------------------------------------------------------- #
def changed_files(base: str | None, head: str = "HEAD", root: Path = ROOT) -> list[str] | None:
    """Files changed on head since it forked from base; None means "unknown, build all"."""
    if not base or base == ZERO_SHA:
        return None
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...{head}"],
        cwd=root, capture_output=True, text=True, check=False,  # failure handled below
    )
    if proc.returncode != 0:
        # e.g. force-push removed `before`, or shallow clone. Fail safe: build everything.
        print(f"warning: git diff {base}...{head} failed: {proc.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in proc.stdout.splitlines() if line]


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def affected(projects: list[Project], files: list[str] | None) -> list[Project]:
    """Projects touched by `files`, plus everything that (transitively) depends on them."""
    if files is None:
        return list(projects)

    owners = sorted((p.path for p in projects), key=len, reverse=True)  # deepest first
    hit: set[str] = set()
    for f in files:
        if _matches(f, IGNORED_PATTERNS):
            continue
        if _matches(f, GLOBAL_PATTERNS):
            return list(projects)
        owner = next((o for o in owners if f.startswith(o + "/")), None)
        if owner:
            hit.add(owner)

    dependents: dict[str, set[str]] = {p.path: set() for p in projects}
    for p in projects:
        for dep in p.depends_on:
            dependents[dep].add(p.path)
    queue = list(hit)
    while queue:
        for d in dependents[queue.pop()]:
            if d not in hit:
                hit.add(d)
                queue.append(d)

    return [p for p in projects if p.path in hit]


# --------------------------------------------------------------------------- #
# GitHub Actions plumbing
# --------------------------------------------------------------------------- #
def _gh_append(env_var: str, text: str) -> None:
    target = os.environ.get(env_var)
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)


def _summary(selected: list[Project], total: int, files: list[str] | None) -> str:
    reason = "full build (no usable diff base)" if files is None else f"{len(files)} changed file(s)"
    lines = [
        "### CI plan",
        "",
        f"**{len(selected)} / {total}** project(s) selected — {reason}",
        "",
    ]
    if selected:
        lines += ["| Project | Path | Type | Docker |", "|---|---|---|---|"]
        lines += [
            f"| {p.name} | `{p.path}` | {p.type} | {'yes' if p.docker else '-'} |"
            for p in selected
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_list(args) -> int:
    projects = discover()
    if args.json:
        print(json.dumps([p.matrix_entry() for p in projects], indent=2))
        return 0
    width = max((len(p.name) for p in projects), default=4)
    for p in projects:
        deps = f"  <- {', '.join(p.depends_on)}" if p.depends_on else ""
        print(f"{p.name:<{width}}  {p.type:<7} {p.path}{deps}")
    return 0


def cmd_validate(_args) -> int:
    projects = discover()  # raises ManifestError with every problem found
    print(f"OK: {len(projects)} project(s) valid")
    return 0


def _select(args, projects: list[Project]) -> tuple[list[Project], list[str] | None]:
    if getattr(args, "all", False):
        return list(projects), None
    files = changed_files(args.base, args.head)
    return affected(projects, files), files


def cmd_detect(args) -> int:
    projects = discover()
    selected, files = _select(args, projects)
    matrix = json.dumps([p.matrix_entry() for p in selected], separators=(",", ":"))

    _gh_append("GITHUB_OUTPUT", f"matrix={matrix}\ncount={len(selected)}\n")
    _gh_append("GITHUB_STEP_SUMMARY", _summary(selected, len(projects), files))

    print(_summary(selected, len(projects), files), file=sys.stderr)
    print(matrix)
    return 0


def run_project(p: Project, stages: list[str]) -> bool:
    cwd = ROOT / p.path
    (cwd / "reports").mkdir(exist_ok=True)
    for stage in stages:
        cmd = p.commands.get(stage, "")
        if not cmd:
            print(f"- [{p.name}] {stage}: skipped (no command)")
            continue
        print(f"\n> [{p.name}] {stage}: {cmd}", flush=True)
        started = time.monotonic()
        rc = subprocess.run(["bash", "-eo", "pipefail", "-c", cmd], cwd=cwd, check=False).returncode
        took = time.monotonic() - started
        if rc != 0:
            print(f"x [{p.name}] {stage} FAILED (exit {rc}, {took:.1f}s)", flush=True)
            return False
        print(f"v [{p.name}] {stage} ok ({took:.1f}s)", flush=True)
    return True


def cmd_run(args) -> int:
    projects = discover()
    if args.targets:
        index = {p.path: p for p in projects} | {p.name: p for p in projects}
        missing = [t for t in args.targets if t.rstrip("/") not in index]
        if missing:
            print(f"unknown project(s): {', '.join(missing)}", file=sys.stderr)
            return 2
        selected = [index[t.rstrip("/")] for t in args.targets]
    elif args.affected or args.all:
        selected, _ = _select(args, projects)
    else:
        print("give project paths/names, --affected or --all", file=sys.stderr)
        return 2

    stages = args.stages.split(",") if args.stages else list(STAGES)
    bad = [s for s in stages if s not in STAGES]
    if bad:
        print(f"unknown stage(s): {bad}; valid: {list(STAGES)}", file=sys.stderr)
        return 2

    if not selected:
        print("nothing to do: no affected projects")
        return 0

    # Keep going after a failure so one run reports every broken project.
    results = {p.name: run_project(p, stages) for p in selected}
    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(results.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list projects")
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_list)

    sub.add_parser("validate", help="validate every project.yaml").set_defaults(func=cmd_validate)

    def diff_args(p):
        p.add_argument("--base", help="git ref/sha to diff against (empty => everything)")
        p.add_argument("--head", default="HEAD")
        p.add_argument("--all", action="store_true", help="ignore the diff, select every project")

    p_detect = sub.add_parser("detect", help="print the CI matrix of affected projects")
    diff_args(p_detect)
    p_detect.set_defaults(func=cmd_detect)

    p_run = sub.add_parser("run", help="run pipeline stages locally")
    p_run.add_argument("targets", nargs="*", help="project paths or names")
    p_run.add_argument("--affected", action="store_true", help="only projects changed since --base")
    p_run.add_argument("--stages", help=f"comma separated subset of {','.join(STAGES)}")
    diff_args(p_run)
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"manifest error:\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
