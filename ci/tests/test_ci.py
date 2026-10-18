import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ci  # noqa: E402


def proj(path, depends_on=()):
    return ci.Project(path=path, name=path.split("/")[-1], type="go", depends_on=tuple(depends_on))


class AffectedTest(unittest.TestCase):
    def setUp(self):
        self.lib = proj("libs/common")
        self.a = proj("projects/a", depends_on=["libs/common"])
        self.b = proj("projects/b")
        self.c = proj("projects/c", depends_on=["projects/a"])  # transitive via a
        self.all = [self.lib, self.a, self.b, self.c]

    def names(self, files):
        return sorted(p.name for p in ci.affected(self.all, files))

    def test_unknown_diff_builds_everything(self):
        self.assertEqual(self.names(None), ["a", "b", "c", "common"])

    def test_single_project_change(self):
        self.assertEqual(self.names(["projects/b/main.go"]), ["b"])

    def test_library_change_fans_out_transitively(self):
        self.assertEqual(self.names(["libs/common/x.go"]), ["a", "c", "common"])

    def test_docs_only_change_builds_nothing(self):
        self.assertEqual(self.names(["README.md", "projects/a/README.md", "ci/README.md"]), [])

    def test_pipeline_change_builds_everything(self):
        self.assertEqual(self.names([".github/workflows/ci.yml"]), ["a", "b", "c", "common"])
        self.assertEqual(self.names(["ci/ci.py"]), ["a", "b", "c", "common"])

    def test_prefix_is_not_confused_with_sibling(self):
        extra = proj("projects/a-v2")
        self.assertEqual(
            sorted(p.name for p in ci.affected(self.all + [extra], ["projects/a-v2/x"])),
            ["a-v2"],
        )

    def test_unowned_root_file_builds_nothing(self):
        self.assertEqual(self.names(["Makefile"]), [])


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, body):
        path = self.root / rel / ci.MANIFEST
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body))
        return path

    def test_defaults_and_overrides(self):
        m = self.write("projects/svc", """
            type: node
            commands:
              build: null
              test: npm run test:ci
        """)
        p = ci.load_manifest(m, self.root)
        self.assertEqual(p.name, "svc")
        self.assertEqual(p.version, "22")
        self.assertEqual(p.commands["install"], "npm ci")
        self.assertEqual(p.commands["build"], "")
        self.assertEqual(p.commands["test"], "npm run test:ci")

    def test_rejects_unknown_type(self):
        m = self.write("projects/x", "type: cobol\n")
        with self.assertRaisesRegex(ci.ManifestError, "'type' must be one of"):
            ci.load_manifest(m, self.root)

    def test_custom_requires_test_command(self):
        m = self.write("projects/x", "type: custom\n")
        with self.assertRaisesRegex(ci.ManifestError, "no 'test' command"):
            ci.load_manifest(m, self.root)

    def test_docker_requires_dockerfile(self):
        m = self.write("projects/x", "type: go\ndocker: true\n")
        with self.assertRaisesRegex(ci.ManifestError, "no Dockerfile"):
            ci.load_manifest(m, self.root)

    def test_discover_reports_bad_dependency_and_duplicate_name(self):
        self.write("projects/a", "type: go\ndepends_on: [libs/missing]\n")
        self.write("projects/b", "type: go\nname: a\n")
        with self.assertRaises(ci.ManifestError) as ctx:
            ci.discover(self.root)
        msg = str(ctx.exception)
        self.assertIn("libs/missing", msg)
        self.assertIn("already used", msg)

    def test_discover_skips_vendored_dirs(self):
        self.write("projects/a", "type: go\n")
        self.write("projects/a/node_modules/dep", "type: node\n")
        self.assertEqual([p.path for p in ci.discover(self.root)], ["projects/a"])


if __name__ == "__main__":
    unittest.main()
