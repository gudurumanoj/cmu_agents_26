.PHONY: setup setup-pip chess-app doctor verify-sources test test-docker test-chess-docker \
	check-part1 check-swebench clean-sandboxes \
	run-code-agent run-swebench-agent run-chess-agent \
	run-obs-experiment-no-legal-moves run-obs-experiment-legal-moves \
	run-obs-deepseek-no-legal run-obs-deepseek-legal \
	run-obs-gpt-oss-no-legal run-obs-gpt-oss-legal \
	run-chess-server instructor-eval-base instructor-eval-gold instructor-eval-patch

TASK ?= tasks/chess-terminal-move
CODE_SKILLS ?= tasks/code-skills
PATCH ?= artifacts/fix.patch
PART1_TRAJECTORY ?= artifacts/part1-trajectory.json
PRIVATE_EVAL ?= .private/chess-terminal-move
PUBLIC_EVAL ?= $(TASK)/public_tests
# One of the vendored instances under tasks/swebench/.
INSTANCE ?= django__django-15368
SWEBENCH_PATCH ?= artifacts/$(INSTANCE).patch
SWEBENCH_TRAJECTORY ?= artifacts/$(INSTANCE)-trajectory.json
TRAJECTORY ?= artifacts/part3-trajectory.json
RESULT ?= artifacts/game-result.json
# Any model your OPENAI_BASE_URL serves. Override on the command line or set
# OPENAI_MODEL in .env and pass MODEL= to fall back to it.
MODEL ?= gpt-5.6-luna
# The two models the Part 3 observation experiment compares. The artifact names
# below keep the tags `deepseek` and `gpt-oss` whatever you point these at, so
# override them with two models your provider actually serves.
DEEPSEEK_MODEL ?= $(MODEL)
GPT_OSS_MODEL ?= $(MODEL)
MODEL_TAG ?= $(subst /,-,$(MODEL))
COMPACT_THRESHOLD ?= 6000
STEPS ?= 200
CHESS_TIMEOUT ?= 1800
OBS_NO_LEGAL_TRAJECTORY ?= artifacts/part3-no-legal-moves-$(MODEL_TAG).json
OBS_LEGAL_TRAJECTORY ?= artifacts/part3-legal-moves-$(MODEL_TAG).json
OBS_NO_LEGAL_RESULT ?= artifacts/part3-no-legal-moves-$(MODEL_TAG)-result.json
OBS_LEGAL_RESULT ?= artifacts/part3-legal-moves-$(MODEL_TAG)-result.json

# The chess app under test. It is a submodule in the course repository; in a
# plain copy of this directory the clone below stands in for it.
CHESS_APP_URL ?= https://github.com/cmu-agents/chess-app.git
CHESS_APP_COMMIT ?= 4aaabca1194ef5f175ccae4fecf902bc71cc303b

# How every target below invokes the project's Python. `setup-pip` builds a
# plain virtualenv instead; activate it and pass RUN= to use that one.
RUN ?= uv run

setup:
	uv sync
	$(MAKE) chess-app
	$(MAKE) verify-sources

# For machines without uv: a plain virtualenv holding the same dependencies.
# Activate it with `source .venv/bin/activate`, then run every other target as
# `make <target> RUN=` so they call the activated interpreter instead of uv.
setup-pip:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e . pytest
	$(MAKE) chess-app
	.venv/bin/python -c "from assignment.task import Task; from assignment.utils.image import verify_source; verify_source(Task.load('tasks/chess-terminal-move'))"

# Put chess_app/ on the commit the task is defined against, whether it is
# registered as a submodule or not.
chess-app:
	@if git submodule status chess_app >/dev/null 2>&1; then \
		git submodule update --init chess_app; \
	elif [ ! -d chess_app/.git ]; then \
		rm -rf chess_app && git clone $(CHESS_APP_URL) chess_app; \
	fi
	@git -C chess_app fetch --quiet origin $(CHESS_APP_COMMIT) 2>/dev/null || true
	git -C chess_app checkout --quiet $(CHESS_APP_COMMIT)

doctor:
	$(RUN) assignment-doctor $(DOCTOR_ARGS)

verify-sources:
	$(RUN) python -c "from assignment.task import Task; from assignment.utils.image import verify_source; verify_source(Task.load('tasks/chess-terminal-move'))"

# Fast and free. These tests fail in the starter until the corresponding TODOs
# are implemented.
test:
	$(RUN) pytest

# Slow: builds the testbed image and runs real containers.
test-docker:
	$(RUN) pytest -m docker

test-chess-docker:
	$(RUN) pytest -m docker tests/test_chess_sandbox.py

# Remove sandbox containers a crashed run left behind. Honours the same
# ASSIGNMENT_DOCKER override the Python side uses.
DOCKER ?= $(or $(ASSIGNMENT_DOCKER),docker)

clean-sandboxes:
	@$(DOCKER) ps -aq --filter label=assignment.sandbox=1 | xargs -r $(DOCKER) rm --force

# Apply the student's generated patch to a fresh testbed and run the public
# Part 1 regression test plus the existing chess-app suite.
check-part1:
	@test -f "$(PATCH)" || (echo "Patch not found: $(PATCH). Run make run-code-agent first."; exit 1)
	$(RUN) python scripts/evaluate.py --task $(TASK) --evaluation $(PUBLIC_EVAL) \
		--patch $(PATCH) -v

# Apply a patch to the published SWE-bench image for INSTANCE and grade it
# against that instance's FAIL_TO_PASS and PASS_TO_PASS tests.
check-swebench:
	@test -f "$(SWEBENCH_PATCH)" || (echo "Patch not found: $(SWEBENCH_PATCH). Run make run-swebench-agent INSTANCE=$(INSTANCE) first."; exit 1)
	$(RUN) python scripts/evaluate_swebench.py $(INSTANCE) --patch $(SWEBENCH_PATCH) -v

run-code-agent:
	$(RUN) assignment-code-agent --task $(TASK) --model $(MODEL) --step-limit $(STEPS) \
		--skills-path $(CODE_SKILLS) --trajectory $(PART1_TRAJECTORY) \
		--patch-output $(PATCH)

# Run the code agent on a vendored SWE-bench instance, in its published image.
run-swebench-agent:
	$(RUN) assignment-swebench-agent $(INSTANCE) --model $(MODEL) \
		--patch-output $(SWEBENCH_PATCH) \
		--trajectory $(SWEBENCH_TRAJECTORY) \
		--skills-path $(CODE_SKILLS) \
		--step-limit $(STEPS) $(if $(filter-out 0,$(COMPACT_THRESHOLD)),--compact-threshold-tokens $(COMPACT_THRESHOLD),)

run-chess-agent:
	$(RUN) assignment-play-chess --model $(MODEL) --task $(TASK) --patch $(PATCH) \
		--step-limit $(STEPS) --sandbox-timeout $(CHESS_TIMEOUT) \
		--trajectory $(TRAJECTORY) --result $(RESULT)

run-obs-experiment-no-legal-moves:
	$(RUN) assignment-play-chess --model $(MODEL) --task $(TASK) --patch $(PATCH) \
		--omit-legal-moves --step-limit $(STEPS) --sandbox-timeout $(CHESS_TIMEOUT) \
		--trajectory $(OBS_NO_LEGAL_TRAJECTORY) --result $(OBS_NO_LEGAL_RESULT)

run-obs-experiment-legal-moves:
	$(RUN) assignment-play-chess --model $(MODEL) --task $(TASK) --patch $(PATCH) \
		--step-limit $(STEPS) --sandbox-timeout $(CHESS_TIMEOUT) \
		--trajectory $(OBS_LEGAL_TRAJECTORY) --result $(OBS_LEGAL_RESULT)

run-obs-deepseek-no-legal:
	$(MAKE) run-obs-experiment-no-legal-moves MODEL="$(DEEPSEEK_MODEL)" MODEL_TAG=deepseek

run-obs-deepseek-legal:
	$(MAKE) run-obs-experiment-legal-moves MODEL="$(DEEPSEEK_MODEL)" MODEL_TAG=deepseek

run-obs-gpt-oss-no-legal:
	$(MAKE) run-obs-experiment-no-legal-moves MODEL="$(GPT_OSS_MODEL)" MODEL_TAG=gpt-oss

run-obs-gpt-oss-legal:
	$(MAKE) run-obs-experiment-legal-moves MODEL="$(GPT_OSS_MODEL)" MODEL_TAG=gpt-oss

run-chess-server:
	$(RUN) assignment-chess-sandbox --task $(TASK) --sandbox-timeout $(CHESS_TIMEOUT) $(if $(wildcard $(PATCH)),--patch $(PATCH),)
