# Assignment 1: Build an Agent Harness

An agent harness provides the interface that allows a language model (that produces probable strings) to observe and act in an environment. One of the most popular frameworks for building agent harnesses is [ReAct](https://arxiv.org/abs/2210.03629), which interleaves text corresponding to environment observations, reasoning/chain-of-thought, and agent actions in the prompt for a language model. In this assigment, you will build a harness in the ReAct framework.

In Part 1, you will implement the ReAct loop of a base [`Agent`](./src/assignment/agent/base.py), that is instantiated into a [`CodeAgent`](./src/assignment/agent/code_agent.py) that acts in a terminal to solve software issues. Your ReAct loop will be abstract and applied to multiple domains, allowing you to see the general structure of an agent harness. You will implement machinery to allow your coding agent to observe and act in the terminal environment, and also apply reusable agent skills to solve a task. You will then have this coding agent fix an issue in a chess game app.

In Part 2, you will work on how your coding agent uses context when solving a more complex software issue. You will implement a simple context compaction method to manage how much of the language model's context window your agent uses.

In Part 3, you will use the base `Agent` you have built instantiated as a `ChessAgent` that plays chess on the app you fixed with your `CodeAgent`. You will implement the tool specifications for this agent, which allows the language model to play the chess game running in the app against a rule-based bot (you can spectate!). You will also explore programmatic tool calling, which brings the power of programs to agent actions, and can allow your agent to execute more complex strategies using Python code execution.

The three parts are related as follows:

```text
                SWE-Bench issue
                      |
                      v
buggy chess app -> CodeAgent -> fix.patch -> repaired chess server
                                              ^
                                              |
                                    ChessAgent tools
```

## Setup

You need [uv](https://docs.astral.sh/uv/) and a working [Docker](https://docs.docker.com/engine/install/) installation. Check that your user can reach the Docker daemon before going any further:
```bash
docker run --rm hello-world
```
If that fails with a permission error on `/var/run/docker.sock`, add yourself to the `docker` group (`sudo usermod -aG docker $USER`, then log out and back in) or use a [rootless Docker](https://docs.docker.com/engine/security/rootless/) installation. Running the assignment under `sudo` is not a workaround: the harness shells out to `docker` hundreds of times per agent run with no terminal attached, so a password prompt would hang it.

`ASSIGNMENT_DOCKER` selects the CLI and is split like a shell command line, so a wrapper with its own arguments works too — `ASSIGNMENT_DOCKER=podman`, or `ASSIGNMENT_DOCKER="sudo -n docker"` on a host where a `NOPASSWD` sudoers rule is the only way in.

Then run:
```bash
make setup
```
This will set up a Python environment (using `uv`), download and install relevant dependencies, and check out the pinned `chess_app` source (for a task that your agent will work on). This fails if that source is missing or at the wrong commit.

If you cannot install `uv`, `make setup-pip` builds a plain virtualenv in `.venv` instead. Activate it with `source .venv/bin/activate` and add `RUN=` to every other target, for example `make test RUN=`, so they call the activated interpreter rather than `uv run`.

Your agents run the code they generate inside a Docker container built from the task's testbed image, not on your machine. The container gets its own filesystem and process tree and is removed when the run ends, so an agent that deletes `/testbed` or installs something strange affects nothing outside it. Nothing here needs a cloud account.

Next, run
```bash
cp .env.example .env
```
to create the file that contains your environment secrets – primarily LLM API details. Fill in an API key and the base URL for whichever OpenAI-compatible endpoint you are using.

```dotenv
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_REASONING_EFFORT=none
OPENAI_MAX_RETRIES=5
```

`OPENAI_REASONING_EFFORT` controls the `reasoning_effort` parameter the harness sends. Leave it empty to omit the parameter entirely, which is what a model with no reasoning mode needs. OpenAI's own GPT-5.6 models (`gpt-5.6-luna`, `-terra`, `-sol`) reject `/v1/chat/completions` requests that combine function tools with any effort other than `none`, and every agent in this assignment calls tools, so `none` is the setting that works there. A provider serving open models over an OpenAI-compatible API will generally accept `medium`.

We will refer to any activity that will use API credits from the LLM provider as _billable_. You will run billable evaluations throughout the assignment. We recommend monitoring your use on the provider's dashboard.

Before a billable run, validate the pinned source, Docker, and the model endpoint without starting a container:
```bash
make doctor
```
This also makes one tiny tool-enabled request, which is the cheapest way to find out that your model and `reasoning_effort` combination is accepted. Skip it with `make doctor DOCTOR_ARGS=--no-probe`, or skip the network entirely with `make doctor DOCTOR_ARGS=--offline`.

_(Optional)_ If you would like to test that all sandbox components are running as intended, run
```bash
make test-docker
make test-chess-docker
```
Note that the first of these builds the testbed image, which takes a few minutes.

As a general tip, you can use
```bash
docker ps --filter label=assignment.sandbox=1
```
to check whether you have a sandbox container running. If a run crashes, the container may be left behind; `make clean-sandboxes` removes every container the assignment started.

## Public tests

`make test` is fast, offline, and not billable. The starter intentionally fails tests for student TODOs. Use these milestones as a guide; exact pytest counts may change if clarifying tests are added.

Passing public tests is not proof of full correctness. Private tests also cover cleanup on failure, duplicate/malformed skills, parallel chess calls, transport errors, artifact consistency, patch replay, and real sandbox integration.

## Rules

- Do not modify `tests/`, `tasks/`, `chess_app/`.
- Do not change provided logging/cleanup or duplicate the shared ReAct loop in a subclass.
- Do not hard-code the inteded solutions for an agent task to pass the tests. Your agents should generate the moves or a patch for a provided task.
- **Never expose, log, or commit credentials like API keys.** Be careful with the contents of the `.env` file.
- Over the course of the assignment, you will work with multiple agents, and multiple versions of these agents. At any point, only use the intended set of tools for that agent.

## Part 1: Build the coding agent and repair the chess app

The shared `Agent` must implement a conventional ReAct loop: build a request, obtain an assistant action, execute its tool calls, add linked observations, and repeat until completion.

### 1. Build the prompt

The prompt for the language model is a sequence of messages used to query the language model at every step of the interaction. A message is a dictionary object. Minimally, it has the `role` and `content` keys, and may have additional information. The `content` of the message maybe a string, or a structured object that contains reasoning tokens, or (in cases not covered in this assignment) images. The message's `role` can take one of a few values. 

The `system` role is used to provide standing instructions – instructions about the domain, information about the environment, rules to follow, general strategies that may be useful across tasks, etc. The `user` role provides the task information[^1] and additional guidelines for solving the task if necessary. `assistant` role messages are used to indicate the LLM generations, and `tool` role messages represent the results of tool calls that act as observations of the environment. Your task is to structure the sequence of these messages. By convention, exactly one `system` message appears first, and a `user` message follows before any assistant messages. Since the LLM generates one response at a time, an `assistant` message should be followed by `tool` messages (which reveal the results of tool calls) or a `user` message. You can find information about the structure of these messages at the [OpenAI API reference](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create) and this [example](https://docs.vllm.ai/en/v0.7.2/getting_started/examples/openai_chat_completion_client_with_tools.html) from the vLLM docs.

You need to implement the `Agent.build_prompt` method. Given the system prompt, and the task prompt accessible as attributes of the agent class, and any additional book-keeping you may add, this method should prepare the input to the language model. As you can see, we provide an `Agent.query_language_model` method that directly uses the output of this method to query the language model API. Your implementation needs to supply this query with the correct inputs. The `Agent.query_language_model` method supplies the language model API with the tools and inference parameters, and returns a structure that contains all relevant information. It also maintains the step count – the number of times the API is queried. The API handles rendering the provided prompt and tools into a single token sequence, and is not something you have to handle. It also provides additional machinery to log your requests for grading, which you should not modify.

The agent's LLM client retries temporary provider failures up to `OPENAI_MAX_RETRIES` times. 

> **TODO(1.1.a)**
> Add machinery to maintain agent state as it takes actions and observes the results. Construct a sequence of messages that form the language model prompt. This should include standing instructions, task specification, prior interaction including observations, reasoning, and actions from previous turns. Note that this method should be domain-agnostic and construct the prompt in a way that would apply to any of the inheriting domain-specific agents.

Next, you should construct the system and task prompts for `CodeAgent`. The system message _must_ contain this block verbatim, using values exposed by the environment:
```text
<system_information>
{
  "machine": <machine>,
  "release": <release>,
  "system": <system>,
  "version": <version>
}
</system_information>
```

> **TODO(1.1.b)**
> Construct the system prompt and task_prompt for `CodeAgent`. These should be usable by the `Agent.build_prompt` method.

### 2. Run the ReAct loop

Implement the body of `Agent.run`. 

> **TODO(1.2)**
> Run the ReAct loop. Orchestrate the sequence of prompting the language model to produce reasoning and actions, extracting the tool calls produced by the model, and executing the tool calls to obtain the agent's observation for the next step. Ensure you identify when the agent has completed the task by setting `Agent.finished`. If the agent exceeds the `step_limit`, raise `StepLimitError`.

### 3. Execute coding tools

Implement `CodeAgent.execute_tool_calls`. The coding agent supports two tools: `execute` and `send_message`. You can find the definitions of these tools [here](./src/assignment/agent/tools.py). You need to implement a mechanism for these tools to be executed, and prepare their outputs to be shown to the language model. These outputs are also messages of the form `{"role": str, "tool_call_id": str, "content": str}`. Each message is the observation of executing one of the tools, and is of a special role called `tool`.[^2] You should use the `Environment.execute` method to ensure you execute code in the sandbox container rather than on your own machine. You may leave the optional parameters to their current default values, but allow the agent to override them in its tool call.

> **TODO(1.3)**
> Make the `execute` and `send_message` tools available to the agent. Parse each call, execute recognized tools, and return one message per call (there may be multiple tool calls in one agent response!). Malformed JSON and unknown tools must become recoverable observations relayed to the agent instead of exceptions.

### 4. Load skills
You might think we're all set to run the agent on a software issue, but not yet! One final piece of knowledge `CodeAgent` needs is instructions on how to submit the solution. The specific protocol we are using is that once the agent has solved the issue, it saves its solution in a file called `patch.txt`. This file is extracted from the environment for evaluation.

To teach the agent this protocol, we will use a skill. Skills are ways to extend agent capabilities with specialized, reusable workflows. The [Agent Skills protocol](https://agentskills.io/home) defines a standard for skills to follow so they can be supported by multiple agent frameworks. `tasks/code-skills/submit-task` defines a rudimentary skill for submitting the solution following this protocol. While the protocol has more advanced features you can explore, you will work with a minimal skill for this assignment.

The core feature you need to implement is _progressive disclosure_. To allow agents to use many skills, each of which may be complex, information in a skill is revealed to an agent in progressively increasing levels of detail, with the agent accessing the information it needs. For this assignment you will implement a simple form of progressive disclosure where you will give the agent the description of all available skills _in the system prompt_. You will also give the agent access to the `invoke_skill` tool, which the agent can call with a skill name to view the complete skill. So, when the agent views the complete `submit-task` skill, it will know how to submit the fix for grading. We will check that the system prompt mentions the skill name and description. When no skill is available to the agent, no part of the prompt should mention `patch.txt` or give the agent submission instructions.

The skill files are available [locally](./tasks/code-skills/), and `Agent.skills_path` points to this location when the agent is run, if it is using skills (it is set to `None` otherwise). Note that in this assignment, the agent will only be using one skill (one subfolder in `./tasks/code-skills/`) but in principle an agent could have many skills that it uses, making progressive disclosure more important and useful.

> **TODO(1.4)**
> Validate ``skills_path``, discover one ``SKILL.md`` per child directory, parse its YAML frontmatter (what's between the `---` tags at the head of the file), and return a mapping keyed by the frontmatter ``name``. Each value must contain a concise ``metadata`` string for the model's skill catalog and the complete ``content`` of the skill file for ``invoke_skill``. Reject duplicate names and malformed or missing frontmatter with a clear ``ValueError``. If any skills are available to the agent, make their descriptions/metadata available to the agent in the prompt.

For example, the `SKILL.md` has `content`
```
---
name: hello-world
description: Write "hello, world" to the terminal
---

echo "hello, world"
```
and `metadata`
```
name: hello-world
description: Write "hello, world" to the terminal
```

### 5. Run and check the repair

Now, let's have the agent fix a software issue.

```bash
make run-code-agent
make check-part1
```

The model comes from `MODEL` in the Makefile, which defaults to `gpt-5.6-luna`; override it per run with `make run-code-agent MODEL=...`. The agent receives `tasks/chess-terminal-move/problem_statement.md`, works inside `/testbed`, and must reproduce, fix, and verify the failure. A run produces:

- `artifacts/fix.patch`
- `artifacts/part1-trajectory.json`

`make check-part1` applies that generated patch to a fresh testbed and runs the public regression test plus the chess-app test suite. Do not edit the target in `chess_app/` directly.

After you have completed part 1, your solution should pass tests relating to prompt construction, truncation, step limit, malformed/unknown tools, and patch submission.

## Part 2: Implement context compaction

Long ReAct transcripts increase cost and eventually crowd out useful context. As agents undertake increasingly complex and long-horizon tasks, they also eventually hit the limits of the language model's context window. To enable agents to efficiently tackle longer running tasks, we want to retain only relevant information about prior actions and observations. This is achieved by compacting the context into a working memory. Now, you will implement a model-generated working memory in the shared `Agent`; do not use a provider-specific compaction endpoint.

> **TODO(2.1)**
> Implement `Agent.compact_context`. Prompt the model to compact the context. The compaction system prompt should ask for concise factual working memory and preserve the objective, constraints, files, commands, edits, concrete results, failed approaches, tests, blockers, and next action. Summarize only an old prefix; retain the original system/task messages verbatim and at least the latest complete assistant action with all linked tool observations. The resulting summary should change what `build_prompt` emits, and reduce the length of the prompt.

**Do not** touch the `api_prompt` and `api_responses` attributes of the agent class. These are meant for book-keeping and evaluation.

> **TODO(2.2)**
> Call `maybe_compact_context()` before each new action request in your shared loop. It already estimates active tokens and handles the threshold, and tracks compaction events for logging.

Run the vendored `django__django-15368` task with a 6,000-token threshold. The
instance runs in its published SWE-bench image, which Docker pulls on first use;
it is several gigabytes, so budget for that download before the first run.

```bash
COMPACT_THRESHOLD=6000 \
SWEBENCH_PATCH=artifacts/django__django-15368.patch \
SWEBENCH_TRAJECTORY=artifacts/django__django-15368-trajectory.json \
make run-swebench-agent INSTANCE=django__django-15368
make check-swebench INSTANCE=django__django-15368
```

Set `COMPACT_THRESHOLD=0` to omit the compaction flag and run a full-context
baseline. Use distinct output names when retaining both runs, for example:

```bash
COMPACT_THRESHOLD=0 \
SWEBENCH_PATCH=artifacts/django__django-15368-baseline.patch \
SWEBENCH_TRAJECTORY=artifacts/django__django-15368-baseline-trajectory.json \
make run-swebench-agent INSTANCE=django__django-15368
```

The submitted compacted run must trigger at least one compaction, materially reduce active context, and produce a patch that passes `check-swebench`. Generation is stochastic, so it need not use fewer ReAct steps than every baseline sample. 

Once you have produced both the compaction and no-compaction trajectories, compare token usage across the trajectories. Present your observations in `artifacts/token-usage-analysis.md`, and provide a brief explanation for the trends you observe. What are the tradeoffs in context usage between the compaction and no-compaction conditions?

## Part 3: Build a `ChessAgent`

Your agent has fixed the issue with the chess app in Part 1, so now you can build an agent that plays chess. `ChessAgent` reuses the exact loop from Parts 1 and 2. It plays White while the server's deterministic bot plays Black. The server automatically replies after every legal White move.

### 1. Implement `play_move`

You will first define a new tool that allows the agent to play the game that is running in the app. Follow the [OpenAI function calling guidelines](https://developers.openai.com/api/docs/guides/function-calling) to define tools.

> **TODO(3.1.a)**
> Define an OpenAI function-tool schema named ``play_move``. It must accept exactly one required string argument named ``move``, explain that moves use [UCI notation](https://en.wikipedia.org/wiki/Universal_Chess_Interface) (for example `e2e4` and `e7e8q`), and reject extra arguments.

Then, you will implement the mechanism that runs the tool.[^3]


> **TODO(3.1.b)**
> Implement `_play_move` in `chess_tools.py`. Parse the arguments and POST `{"move": <uci move>}` to `/api/move`. Return its serialized JSON object. Catch any errors raised by the tool and return an error message between `<chess_error></chess_error>` for the agent to address. Cover malformed JSON arguments, arguments that are not an object, a missing or non-string fen, a non-string move, a position or move the server rejects, and a transport failure.

You may look at `chess_app` for how the API works.

Format a successful returned state with the `format_state` method, update `last_state`, and set `finished` from `game_over`. Link the observation using the original `tool_call_id`. Malformed JSON, unknown tools, illegal moves, and network errors become `<chess_error>...</chess_error>` observations. Because the live position changes after a move, execute at most one move from a set of parallel calls and reject the rest recoverably.

The initial and successful post-move observations already show the board, Black's reply, and the next legal moves. No separate board-reading tool is needed.

Run:

```bash
make run-chess-agent
```

This applies your `fix.patch`, launches the repaired server, and saves:

- `artifacts/part3-trajectory.json`
- `artifacts/game-result.json`

The printed `http://127.0.0.1:<port>` URL serves the board and API. The
container publishes its port to a free port on your machine, so open the URL
exactly as printed rather than assuming a fixed number. The board polls the
live state while the agent plays; its button refreshes state without resetting
the game. `CHESS_TIMEOUT=1800` controls the sandbox lifetime, after which the
container is removed.

### 2. Run the observation A/B experiment

To see the effect of the tool interface on agent behavior, you will compare board-only observations with board plus legal moves for two models. The runners configure this without source edits and use distinct filenames:

```bash
make run-obs-deepseek-no-legal DEEPSEEK_MODEL=<first model>
make run-obs-deepseek-legal    DEEPSEEK_MODEL=<first model>
make run-obs-gpt-oss-no-legal  GPT_OSS_MODEL=<second model>
make run-obs-gpt-oss-legal     GPT_OSS_MODEL=<second model>
```

The target names and the `deepseek`/`gpt-oss` artifact tags are fixed so the
grader can find the files, but the two models are yours to choose: pick any two
your endpoint serves, and say which ones you used in the report below. Both
default to `MODEL`, so running the targets bare compares the same model against
itself, which is not the comparison being asked for.

For each of the four runs, record total `play_move` calls, calls rejected as
illegal, invalid-move rate, and whether `game_over: true` was reached. Write a
short comparison in `artifacts/observation-experiment.md`. You are graded on
the experiment and evidence, not on a particular result or winning the game.

### 3. Add `simulate_move`

To allow the agent the ability to plan more complex strategies, you will give it the ability to simulate moves. Simulation allows the agent to simulate the effect of playing a move, but not actually change the state of the actual game board. You will define and register `SIMULATE_MOVE_TOOL`, implement `_simulate_move`, and add it to the existing dispatcher. 

> **TODO(3.3.a)**
> Define the `simulate_move` tool, like the `play_move` tool.

`simulate_move(fen, move=None)` calls `POST /api/simulate`. 

Passing a complete six-field [FEN](https://en.wikipedia.org/wiki/Forsyth%E2%80%93Edwards_Notation) alone to `simulate_move` returns that position and its legal moves. Passing a FEN plus UCI move returns the position after exactly one ply, for either side. Return the JSON so Python can use `fen`, `squares`, `turn`, `legal_moves`, and terminal-result fields.

> **TODO(3.3.b)**
> Implement `_simulate_move` in `chess_tools.py`. Parse the arguments, call the provided `/api/simulate` endpoint with the FEN and optional move, and return its serialized JSON. Catch any errors raised by the tool and return an error message between `<chess_error></chess_error>` for the agent to address. Cover malformed JSON arguments, arguments that are not an object, a missing or non-string fen, a non-string move, a position or move the server rejects, and a transport failure.

You should post the request using `ChessAgent.chess_client`.

### 4. Add `run_python`

With the ability to simulate moves on the board, the agent can now engage in more complex forms of planning. Being able to execute code will allow the agent to more reliably execute the plans it comes up with, and you will enable this with programmatic tool calling. Define and register `RUN_PYTHON_TOOL`, implement `_run_python`, and add it to the dispatcher. The model supplies `run_python(code)`, and the snippet can call `simulate_move` and `play_move` as ordinary synchronous Python functions.

> **TODO(3.4)**
> Parse the arguments and run the code in the sandbox with the registered tools available by name. `/opt/assignment/sandbox_python.py` is a script on the `env` sandbox that has access to the same tool definitions in this file. Use it to run the code that the model produced as an argument to the run_python tool. The script accepts two positional arguments -- `port` and a base64-encoded string of code (to prevent issues with quoting). Implement this tool call.
> The script prints one JSON object with `stdout`, `stderr`, and `error` from running the code -- return that string as it is. A non-zero returncode means the sandbox itself failed, not the model's code. Report `exception_info` or `stderr` as a <chess_error>. 
> Return <chess_error>{message}</chess_error> if there are issues like type mismatches or parsing failures.

Model-written code must not run in the local agent process. `_run_python` sends base64-encoded code through `env.execute` to the provided sandbox runner:

```text
python /opt/assignment/sandbox_python.py <port> <base64-code>
```

Return the runner's JSON string with `stdout`, `stderr`, and `error`. A non-zero sandbox command becomes `<chess_error>`. A Python exception from the snippet is a successful runner invocation and belongs in its `error` field. After each snippet, re-read the live board, update `last_state` and `finished`, and append the formatted state to the observation; otherwise the model may replay a move that the snippet already committed.

Enable these tools with:

```bash
uv run assignment-play-chess --programmatic-tools
```

Once you have implemented this, your agent should be able to produce a piece of code that selects a move, and play that move in the game. You may still find that your chess playing agent does not use the tools at its disposal to play good moves, and ends up directly using `play_move` to act often. To give it more structure and strategy, we turn again to the idea of a skill that we explored earlier.

### 5. Load and use the chess skill

While the ability to run Python code gives the agent the option to execute complex plans, agents may not have the tendency to. To give the agent a concrete strategy to execute, we have provided another skill in `tasks/chess-skills/select-move`. To have the model use this, you will give `ChessAgent` some of the skill use abilities from `CodeAgent`. Due to the slightly different tool execution mechanism, you have to re-implement the handling of the `invoke_skill` tool for `ChessAgent` in  `_invoke_skill(skills, arguments)`, register `INVOKE_SKILL_TOOL` only
when skills were loaded, and return the named full content.

> **TODO(3.5)**
> Parse the arguments and return the named skill's content. Return <chess_error>{message}</chess_error> if there are issues like type mismatches or parsing failures.

```bash
uv run assignment-play-chess \
  --programmatic-tools \
  --skills-path tasks/chess-skills \
  --trajectory artifacts/part3-python-skill-trajectory.json
```

The trajectory must show `invoke_skill`, then `run_python` code that calls `simulate_move` to search and `play_move` once to commit. Reading the skill and then making one direct `play_move` call per turn does not demonstrate the tools working together. The game need not finish or be won.

## Grading

The assignment is worth **100 points**. Each row is graded independently; a
failed stochastic model run does not erase unrelated implementation credit.

| Part | Criterion | Points | Evidence |
|---|---|---|---|
| 1 | Prompt construction and valid action/observation history | 6 | Private unit tests |
| 1 | ReAct lifecycle, text-only recovery, step limit, cleanup, trajectory | 6 | Private unit tests |
| 1 | Coding-tool dispatch, recoverable errors, patch submission | 6 | Private unit tests |
| 1 | Chess patch applies and passes private/regression tests | 8 | Patch replay in fresh testbed |
| 1 | Skill discovery and `invoke_skill` behavior | 4 | Private tests |
| 2 | Compaction trigger and model-generated summary | 6 | Private tests and trajectory |
| 2 | Original instructions and complete recent tool step remain valid | 6 | Private tests and trajectory |
| 2 | Auditable compaction materially reduces active context | 4 | Compaction events and usage |
| 2 | Full context vs. compaction token usage analysis report | 4 | Report |
| 2 | SWE-bench patch passes FAIL_TO_PASS and PASS_TO_PASS | 8 | Patch replay |
| 3 | `play_move` schema and registration | 4 | Private tests |
| 3 | `play_move` state updates and recoverable errors | 6 | Private tests |
| 3 | Basic chess trajectory reaches a terminal state | 4 | Trajectory and result |
| 3 | Four-run observation A/B experiment is complete | 4 | Four trajectories, four results |
| 3 | Observation A/B experiment report | 4 | Report |
| 3 | `simulate_move` stateless behavior and errors | 6 | Private unit/integration tests |
| 3 | `run_python` sandbox execution, state refresh, errors | 6 | Private tests with sandbox stand-in |
| 3 | Skill trajectory combines skill, programmatic search, and live move | 6 | Trajectory replay |
| — | Complete, parseable, rule-compliant submission | 2 | Archive validation |
| | **Total** | **100** | |

The grader replays patches and submitted trajectories; it does not make new LLM calls. Missing or inconsistent evidence loses credit only for the affected row. Instructor tests and reference patches are not included in this repo.

## Submission

Submit one archive containing your changed files under `src/assignment/agent/`
and these artifacts:

```text
artifacts/fix.patch
artifacts/part1-trajectory.json
artifacts/django__django-15368.patch
artifacts/django__django-15368-trajectory.json
artifacts/token-usage-analysis.md
artifacts/part3-trajectory.json
artifacts/game-result.json
artifacts/part3-no-legal-moves-deepseek.json
artifacts/part3-no-legal-moves-deepseek-result.json
artifacts/part3-legal-moves-deepseek.json
artifacts/part3-legal-moves-deepseek-result.json
artifacts/part3-no-legal-moves-gpt-oss.json
artifacts/part3-no-legal-moves-gpt-oss-result.json
artifacts/part3-legal-moves-gpt-oss.json
artifacts/part3-legal-moves-gpt-oss-result.json
artifacts/observation-experiment.md
artifacts/part3-python-skill-trajectory.json
```

Also include `src/assignment/prompts.py` only if you changed it. Do not submit
credentials, `.env`, task files, tests, submodule contents, or instructor files.

[^1]: In offline evaluation settings like this, you typically specify the task by structuring it as a request from a user. See these [docs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/chatgpt?tabs=python-key%2Cdotnet-secure%2Cjavascript-secure&pivots=programming-language-python) for more examples.

[^2]: A repeated `call_0` ID from a provider is valid: match each tool observation to the call in the same assistant action and do not assume IDs are globally unique across the trajectory.

[^3]: `_play_move` and other tools are isolated in `chess_tools.py` to make executing these tools inside the sandbox possible. Work within the structure of this code to correctly use the sandbox container for executing chess moves.