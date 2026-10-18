# Local shortcuts. They call the exact same driver CI uses (ci/ci.py).
PY   ?= python3
CI   := $(PY) ci/ci.py
BASE ?= origin/main

.DEFAULT_GOAL := help
.PHONY: help list validate affected ci ci-affected ci-all test-tooling hooks

help: ## show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

list: ## list all projects
	@$(CI) list

validate: ## validate every project.yaml
	@$(CI) validate

affected: ## show projects affected vs BASE (default origin/main)
	@$(CI) detect --base $(BASE) > /dev/null

ci: ## run the pipeline for one project: make ci P=projects/api-node [STAGES=lint,test]
	@test -n "$(P)" || (echo "usage: make ci P=<project path or name>"; exit 2)
	@$(CI) run $(P) $(if $(STAGES),--stages $(STAGES))

ci-affected: ## run the pipeline for everything affected vs BASE
	@$(CI) run --affected --base $(BASE) $(if $(STAGES),--stages $(STAGES))

ci-all: ## run the pipeline for every project
	@$(CI) run --all $(if $(STAGES),--stages $(STAGES))

test-tooling: ## unit-test ci/ci.py
	@$(PY) -m unittest discover -s ci/tests

hooks: ## install git hooks (pre-push runs lint+test on affected projects)
	@git config core.hooksPath .githooks && echo "hooks installed (.githooks)"
