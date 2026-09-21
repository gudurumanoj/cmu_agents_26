# Agent Messages, Reasoning, Tools, and Compaction

This note records the design questions encountered while implementing the
assignment harness. The examples distinguish Chat Completions messages from
Responses API items because mixing those two formats is a common source of
errors.

## 1. Reasoning output is provider- and endpoint-specific

Do not manually wrap reasoning as `<think>...</think>` when the inference server
has a reasoning parser configured. The parser separates model output into
dedicated fields or items.

Common formats:

- Current vLLM Chat Completions: assistant `reasoning` plus `content`.
- Older vLLM: assistant `reasoning_content` plus `content`.
- DeepSeek Chat Completions: assistant `reasoning_content` plus `content`.
- OpenAI Responses: a typed `reasoning` output item, followed by message or
  function-call items. The reasoning state can be opaque/encrypted rather than
  readable chain-of-thought.
- Anthropic Messages: signed `thinking` content blocks.

Raw `<think>` tags generally indicate that a local model is being served without
the matching reasoning parser or chat-template configuration. Application code
should not add those tags around a parsed reasoning field.

## 2. Hidden reasoning, returned reasoning, and token counts differ

These are separate concepts:

1. A model can reason internally without returning its chain-of-thought.
2. Usage can report `reasoning_tokens`; that is only a count.
3. A provider can return replayable reasoning state, such as
   `reasoning_content`, `reasoning`, or an encrypted Responses item.
4. A provider can return a safe reasoning summary rather than raw reasoning.

Never invent hidden reasoning. Only retain or replay fields/items the provider
actually returns and documents.

## 3. Replay rules with tools

The safest default is to preserve the complete provider response associated
with a tool call.

### Chat Completions shape

```json
[
  {
    "role": "assistant",
    "content": "",
    "reasoning_content": "provider-returned reasoning, when applicable",
    "tool_calls": [
      {
        "id": "call_1",
        "type": "function",
        "function": {
          "name": "execute",
          "arguments": "{\"command\":\"pytest\"}"
        }
      }
    ]
  },
  {
    "role": "tool",
    "tool_call_id": "call_1",
    "content": "tool result"
  }
]
```

DeepSeek thinking mode requires its returned `reasoning_content` to be replayed
for tool-enabled continuations. Removing it can cause HTTP 400.

Current vLLM names the parsed field `reasoning`; older versions used
`reasoning_content`. Whether the server consumes old reasoning depends on the
model, reasoning parser, and chat template.

### Responses API shape

```json
[
  {"type": "reasoning", "...": "opaque or provider-defined state"},
  {
    "type": "function_call",
    "call_id": "call_1",
    "name": "multiply",
    "arguments": "{\"a\":6,\"b\":7}"
  },
  {
    "type": "function_call_output",
    "call_id": "call_1",
    "output": "{\"product\":42}"
  }
]
```

OpenAI requires reasoning items associated with function calls to be available
to the continuation. This can be done by:

- replaying all returned output items,
- using `previous_response_id`, or
- attaching requests to a persisted conversation.

With stateless/ZDR operation, request encrypted reasoning content and replay the
returned reasoning item unchanged.

## 4. What a provider adapter does

A provider adapter translates between a harness's canonical state and one API's
wire format. It can:

- map tool schemas,
- map `reasoning_effort` to the provider's request shape,
- parse assistant messages or typed output items,
- preserve reasoning continuation state,
- translate tool calls and outputs,
- normalize token usage,
- support provider-managed continuation IDs.

For example, Chat Completions uses `messages`, nested function definitions, and
`role: tool`. Responses uses an `input` item array, flat function definitions,
and `function_call_output`. They should not be treated as identical structures.

## 5. Tool calls are requests, not execution

The model only emits a requested function name and JSON arguments. The harness:

1. validates the call,
2. executes recognized tools,
3. creates one linked observation per call,
4. appends those observations to active history,
5. asks the model for its next action.

For Chat Completions, each tool observation must carry the original
`tool_call_id`. Multiple calls in one assistant response require multiple tool
observations in the same order.

In this assignment, `execute` runs only inside the Docker environment.
`send_message` sets `Agent.finished = True`. `invoke_skill` returns a named
skill's complete `SKILL.md`.

### Tool design includes the returned observation

A tool's interface is not only its function name and argument schema. The
information it returns is equally important because that observation determines
what work the model must do next.

For example, a chess tool that returns only the updated FEN forces the model to
derive legal moves itself. Returning the updated FEN, board state, game status,
and `legal_moves` moves deterministic legality work into the environment and
lets the model focus on strategy. This improves reliability because the chess
library, rather than the language model, becomes the source of truth for legal
actions.

Tool observations should therefore expose the authoritative facts needed for
the next decision, while avoiding irrelevant output that consumes context. Good
tool design considers the complete action-observation contract:

- the function schema determines what the model may request;
- validation determines which requests the environment accepts;
- the returned observation determines what the model can know afterward;
- explicit errors determine whether the model can recover from a bad request.

### Direct tools versus programmatic tools

A direct tool call asks the harness to perform one named operation. For example,
the model emits `play_move({"move": "e2e4"})`, the harness executes it, returns
one observation, and then makes another model request. Every new decision
normally requires another model round trip.

Programmatic tool calling adds a higher-order tool such as `run_python`. The
model supplies one Python program, and that program can use loops, branches,
variables, and many calls to lower-level tools before it finishes. This is
useful for deterministic work such as searching hundreds of chess positions:
the intermediate results remain Python values instead of becoming hundreds of
chat messages and model round trips.

`programmatic` describes how calls are orchestrated, not whether they mutate
state. In this assignment:

- `simulate_move` is stateless;
- `play_move` mutates the live game;
- `run_python` changes the live game only if its code calls `play_move`;
- `invoke_skill` only returns instructions.

`simulate_move` is itself an ordinary function tool. It is gated by
`--programmatic-tools` because the assignment bundles simulation and Python as
optional advanced capabilities, preserving a `play_move`-only baseline. This is
an experiment/configuration choice, not a technical requirement.

### How multiple simulations help without invoking the opponent

`simulate_move(fen, move)` reconstructs a fresh board from the supplied FEN and
applies exactly one legal ply. It never updates the live board and never invokes
the deterministic Black bot. The returned FEN records whose turn comes next, so
Python can explicitly explore both sides:

```python
after_white = simulate_move(current_fen, candidate)
scores = []
for black_reply in after_white["legal_moves"]:
    after_black = simulate_move(after_white["fen"], black_reply)
    scores.append(evaluate(after_black))
candidate_score = min(scores)
```

The script treats every legal Black reply as a possible opponent response and,
for minimax search, scores the White candidate by its worst reply. This moves
board reconstruction, turn handling, and move legality into `python-chess`,
while Python performs reliable iteration and scoring. The real Black bot acts
only after the selected White move is committed through `/api/move`.

Each `simulate_move` call returns a dictionary to the running Python code with
fields such as `fen`, `squares`, `turn`, `legal_moves`, and terminal status.
Those dictionaries are internal variables; they do not automatically become
the tool observation. A top-level expression also has no visible result under
`exec`. The code must `print` information it wants returned, or call
`play_move(best)` to create the intended live-game side effect.

### How custom functions exist inside model-written Python

The model-written snippet does not import ordinary library functions named
`simulate_move` and `play_move`. Before executing the snippet,
`sandbox_python.py` creates wrapper functions with those names. Each wrapper
serializes its Python arguments, calls the existing chess helper through an
HTTP client connected to the server inside Docker, and converts the returned
JSON back into a Python dictionary.

The runner injects the wrappers into the globals passed to `exec`:

```python
namespace = {
    "__name__": "__agent__",
    "simulate_move": simulate_move,
    "play_move": play_move,
}
exec(compile(code, "<agent-python>", "exec"), namespace, namespace)
```

The complete execution path is:

1. the model requests `run_python` with a source-code string;
2. `_run_python` base64-encodes that code to avoid shell-quoting problems;
3. `Environment.execute` starts `/opt/assignment/sandbox_python.py` inside
   Docker;
4. the runner creates the HTTP-backed wrappers and injects them into `exec`;
5. simulation calls use `/api/simulate`, while a committed move uses
   `/api/move`;
6. the runner captures printed output, tracebacks, and exceptions into one JSON
   object with `stdout`, `stderr`, and `error`;
7. after a successful runner invocation, `ChessAgent` reads `/api/state` again,
   updates `last_state` and `finished`, and appends the formatted live board.

The intended strategy is therefore: simulate many hypothetical lines, select
the best candidate in Python, call `play_move` exactly once, and then continue
from the refreshed real board after Black's automatic reply.

## 6. Live history versus API audit logs

`message_history` is the mutable source of truth for the active conversation.

`api_prompts` is an immutable list of snapshots of requests that were actually
sent. It exists for auditing, grading, and token analysis. A deep copy prevents
aliasing, but it does not turn a stale request snapshot into current state.

After the first response, `api_prompts[-1]` still lacks that response and its
tool observations. A separate ordered history avoids reconstructing state from
logs and makes compaction straightforward.

## 7. Consecutive user messages are valid

After compaction, this assignment emits:

```text
system: original standing instructions
user: original task
user: generated working memory
assistant: retained recent action
tool: retained recent observation
```

Chat Completions does not require strict user/assistant alternation. The strict
relationship is between an assistant tool call and its linked tool result.

Keeping working memory separate is preferable to mutating `task_prompt`:

- the assignment requires the original task message verbatim,
- working memory is lossy and changes after each compaction,
- repeated compactions need to replace the old summary,
- permanent objectives and temporary state remain distinguishable.

Some open-model chat templates enforce alternation more strictly than the API.
If targeting one of those templates, adapt at the provider boundary rather than
mutating the canonical task.

## 8. Compaction policy used by this assignment

The implementation:

1. finds assistant-turn boundaries,
2. selects an old prefix,
3. retains the latest complete assistant/tool groups verbatim,
4. serializes the old prefix for a summarization request,
5. asks for concise factual working memory,
6. replaces the old prefix with one user-role working-memory message.

Older tool results and returned reasoning fields are source material for the
summary. Recent tool results and provider-specific reasoning fields remain
verbatim.

Compaction should retain:

- objective and constraints,
- files and symbols,
- commands and concrete results,
- edits,
- failed approaches,
- tests and outcomes,
- blockers and next action.

It should remove verbose narration and bulky raw output. Original system/task
messages and API audit logs must not be rewritten.

## 9. `model_dump()` in the OpenAI Python SDK

SDK response objects are Pydantic models. `model_dump()` recursively converts
them into Python dictionaries.

```python
message.model_dump(exclude_none=True)
```

removes fields whose value is `None`.

```python
response.model_dump(mode="json")
```

converts nested values into JSON-compatible primitives. It still returns a
Python dictionary; `json.dumps()` or `json.dump()` produces JSON text.

The installed SDK's `ChatCompletionMessage` allows extra provider fields, so a
returned `reasoning` or `reasoning_content` field survives `model_dump()` even
though it is not a standard declared OpenAI Chat Completions field.

## 10. Exceptions and source locations

An uncaught exception prints a traceback with filenames and line numbers to
stderr. A caught exception does not print automatically.

For debugging a caught exception:

```python
import traceback

try:
    ...
except ValueError:
    traceback.print_exc()
```

To return only the message to an agent, use `str(exc)`. Avoid exposing full
internal tracebacks to models unless they are genuinely needed.

## 11. Running the Responses demonstration

The accompanying script makes six small billable requests and writes no
credentials:

```bash
uv run python lessons/responses_api_reasoning_demo.py
```

It uses:

- `/v1/responses`,
- the model from `OPENAI_MODEL`,
- `reasoning.effort = xhigh` and `reasoning.summary = auto` by default,
- `store = false`,
- manual replay of every `response.output` item,
- one three-turn conversation without tools,
- one three-request conversation with a local `multiply` tool.

Inspect `lessons/responses_api_examples.json`. For each request it records:

- the full sanitized input item list,
- output item types,
- the complete response object,
- the final replayable history.

OpenAI reasoning items in that file are continuation state, not readable raw
chain-of-thought. The important lesson is their placement and replay, especially
around `function_call` and `function_call_output`.

`xhigh` is an upper effort allowance, not a promise that every response will use
reasoning tokens. Simple prompts can report zero reasoning tokens. Requesting
`summary: auto` asks for a safe model-generated reasoning summary when the model
does reason; it still does not expose raw chain-of-thought.

### Observed live run

The checked-in JSON was generated successfully against `gpt-5.6-luna`:

- No-tools turns 1 and 2 returned only `message` items and used zero reasoning
  tokens.
- No-tools turn 3 returned `reasoning` then `message`, used 35 reasoning tokens,
  and included a safe reasoning summary.
- The tool conversation's first response returned `reasoning` then
  `function_call` and used 19 reasoning tokens.
- The next request replayed that encrypted `reasoning` item and
  `function_call`, then appended the matching `function_call_output`.
- The remaining tool-conversation responses returned normal message items.

This demonstrates why code must branch on item `type` rather than assuming every
response contains one assistant message, and why `xhigh` does not imply visible
reasoning on every turn.

## 12. Current assignment architecture

The assignment intentionally uses a small template-method design:

```text
Agent
  owns shared state, model calls, the ReAct loop, compaction, and logging

CodeAgent
  supplies coding prompts, coding tools, and coding-tool execution

ChessAgent
  supplies chess prompts, chess tools, and chess-tool execution

Environment
  executes untrusted commands inside Docker
```

The base class owns behavior that must be identical across domains. A subclass
does not copy the ReAct loop; it provides domain-specific instructions and
implements `execute_tool_calls()`.

### Important state

`message_history`
: The active action/observation transcript used to construct the next prompt.
  Compaction is allowed to replace an old prefix of this list.

`api_prompts`
: Deep-copied snapshots of each action request exactly as sent. These are audit
  records, not live state.

`api_responses`
: JSON-compatible dumps of raw action responses, including usage and
  provider-returned metadata.

`compaction_events`
: Before/after context, the summarization prompt, the raw summarization
  response, and estimated token reduction.

`steps_taken`
: Number of successful action-model calls. Compaction-model calls do not count
  as agent actions.

`finished`
: Domain completion signal. `CodeAgent.send_message` sets it; chess will set it
  from `game_over`.

`tools`
: Schemas advertised to the model. A schema makes a tool callable by the model;
  the subclass dispatcher performs the actual operation.

### One action step

```mermaid
sequenceDiagram
    participant Loop as Agent.run
    participant State as message_history
    participant Model as ChatCompletions
    participant Domain as CodeAgent
    participant Env as DockerEnvironment

    Loop->>Loop: maybe_compact_context
    Loop->>State: build_prompt
    Loop->>Model: query_language_model
    Model-->>Loop: assistant message and tool_calls
    Loop->>State: append assistant message
    Loop->>Domain: execute_tool_calls
    Domain->>Env: execute validated command
    Env-->>Domain: stdout stderr returncode
    Domain-->>Loop: linked tool observations
    Loop->>State: append observations
```

The next request is therefore built from:

```text
original system message
original task message
all active assistant actions and tool observations
```

## 13. Function responsibilities in `Agent`

### `build_prompt()`

Purely assembles the current request context:

```python
[
    {"role": "system", "content": self.system_prompt},
    {"role": "user", "content": self.task_prompt},
    *deepcopy(self.message_history),
]
```

It does not call the model, execute tools, increment counters, or append audit
logs. Keeping it close to a pure function makes token estimation and tests
reliable.

### `query_language_model()`

Owns one action-model request:

1. calls `build_prompt()`,
2. records a deep-copied request snapshot,
3. calls Chat Completions,
4. records the raw response,
5. increments `steps_taken`,
6. normalizes the assistant message with `process_response()`.

This creates one place for request options, progress output, retries supplied by
the SDK client, and response bookkeeping.

### `process_response()`

Converts an SDK/Pydantic message object to a normal dictionary while preserving
extra provider fields. It does not execute calls or mutate conversation state.

This small boundary is where a future provider adapter would begin.

### `run()`

Owns lifecycle and ordering:

1. enforce the step limit,
2. compact if needed,
3. request one action,
4. append the assistant action,
5. execute all tool calls,
6. append linked observations,
7. repeat until `finished`.

Its `finally` block always writes a trajectory and stops the environment, even
when an API request, tool, or step-limit check fails.

### `execute_tool_calls()`

The base method is abstract because execution is domain-specific.

`CodeAgent.execute_tool_calls()`:

- validates tool-call structure and JSON,
- validates arguments,
- invokes `Environment.execute`,
- serves skills loaded during initialization,
- handles `send_message`,
- returns recoverable error observations.

`ChessAgent.execute_tool_calls()` will call the chess server and update game
state instead. Both return the same linked tool-observation shape, allowing the
base loop to remain domain-independent.

### `load_skills()`

Validates and parses local skill metadata once during initialization. The prompt
receives only metadata; `invoke_skill` returns full content on demand. This keeps
the standing prompt smaller and avoids revealing submission instructions when
no skill is configured.

### `format_tool_output()`

Turns an environment result dictionary into tagged text and truncates unusually
large string values. Central formatting prevents each dispatcher branch from
inventing a different observation format.

### `estimate_active_prompt_tokens()`

Uses the provider's latest prompt-token count as a calibration anchor, then
estimates only content added since that request. It falls back to a
provider-neutral character estimate when real usage is unavailable.

### `maybe_compact_context()`

Owns trigger policy and event logging:

- return immediately when compaction is disabled,
- compare active context against the threshold,
- ensure enough complete turns exist,
- record before/after evidence.

It delegates the lossy transformation itself to `compact_context()`.

### `compact_context()`

Owns compaction mechanics:

- split history at assistant-turn boundaries,
- keep recent assistant/tool groups intact,
- serialize only the old prefix for summarization,
- validate the generated summary,
- install one working-memory message plus the exact recent suffix.

Separating trigger policy from compaction mechanics lets tests force a
compaction without reproducing production threshold conditions.

## 14. Why the logging design is useful

The trajectory deliberately stores three related but different records.

### Prompt snapshots answer “what did the model see?”

Each `api_prompts[index]` is captured before its corresponding action request.
If the agent makes a bad decision, this reveals whether the relevant tool output
was present, truncated, summarized, or missing.

### Raw responses answer “what did the provider return?”

`api_responses[index]` retains:

- assistant content,
- tool-call IDs and arguments,
- model and finish metadata,
- usage fields,
- extra compatible-provider fields such as reasoning.

This allows evaluation without making new model calls.

Tool observations are visible in the next request's prompt snapshot rather than
stored as a separate top-level trajectory stream. Consequently, an observation
created by the final `send_message` step may exist only in live
`message_history`; there is no later request in which to capture it. A production
event log would record tool completion separately.

### Compaction events answer “what information was replaced?”

Each event preserves:

- active prompt before compaction,
- summarization request,
- raw summary response,
- before/after estimates,
- action step where compaction happened.

That makes a lossy operation auditable. Without this record, a later failure
could be caused by either model behavior or an information-losing summary, and
there would be no evidence to distinguish them.

### Why use deep copies?

The active history continues changing after every request. Deep copies ensure
past prompt snapshots remain exactly as they were at send time. A shallow alias
would let later appends or nested edits rewrite historical evidence.

Deep copying does not make an old prompt current. That is why
`message_history` remains the source of truth and `api_prompts` remains an audit
log.

## 15. Why the current `Agent` base class is effective

The current base class is not merely code sharing. It encodes the invariant
algorithm that every domain agent must follow while exposing one narrow
domain-specific seam.

### It uses the template-method pattern appropriately

`Agent.run()` defines the shared lifecycle:

```text
check limits
compact shared context
request an action
record the assistant turn
delegate tool execution
record observations
repeat or finish
```

`execute_tool_calls()` is the overridable step. This is a good use of
inheritance because the overall algorithm is genuinely the same for coding and
chess; only the effect of an action differs.

If subclasses overrode `run()`, they could silently drift in step-limit,
compaction, logging, cleanup, or message-order behavior. Keeping the loop in the
base class makes those guarantees uniform.

### It places state with the code that owns its invariants

The base class owns:

- ordered conversation history,
- step count and limit,
- completion status,
- model request/response records,
- compaction policy and evidence,
- environment cleanup.

Those fields participate in the shared lifecycle, so keeping them together
reduces cross-object synchronization. A subclass owns only domain state such as
the coding task, submitted message, chess board state, or HTTP client.

### Its abstract boundary has a small stable contract

Every subclass receives:

```python
list[tool_call]
```

and returns:

```python
list[tool_observation]
```

The base class does not need to understand shell commands, chess moves, HTTP
errors, or skill-specific behavior. It only needs the standard observation
shape and call IDs.

This boundary is effective because it matches the model protocol directly:
assistant tool calls go in; linked tool-role observations come out.

### Cross-cutting context behavior is shared once

Prompt construction and compaction apply equally to every agent domain. Putting
them in `Agent` means:

- coding and chess retain history identically,
- both preserve complete recent tool groups,
- both use the same token trigger,
- both produce comparable compaction logs,
- fixes to context handling apply everywhere.

Context policy would be easy to duplicate incorrectly if it lived in each
subclass.

### Lifecycle safety is centralized

The base `finally` block guarantees trajectory writing and environment cleanup
whether the run:

- completes normally,
- reaches the step limit,
- receives malformed provider output,
- encounters a fatal tool/environment error,
- fails during compaction.

Centralizing cleanup is especially important because subclasses interact with
different external resources. They should not each have to remember every exit
path.

### Controlled mutation remains easy to follow

There is one primary active transcript, `message_history`. `run()` appends
assistant actions and observations in protocol order. `compact_context()` is
the only operation allowed to replace an old prefix.

Audit collections have separate purposes and append points:

- `api_prompts` changes at the request boundary,
- `api_responses` changes after a successful response,
- `compaction_events` changes after a completed compaction.

This restricted mutation model makes it possible to reason about the agent at
any point in the loop.

### The design is proportional to the assignment

A production framework might prefer composition with provider adapters, an
event store, and a tool registry. Adding all of those here would obscure the
ReAct concepts the assignment is teaching.

The current base class gives useful separation without excessive indirection:

- one shared class for lifecycle,
- one subclass hook for effects,
- plain dictionaries matching the API,
- explicit lists for auditable state.

That balance is why it is effective for this project. It is small enough to
trace in a debugger but structured enough to extend from `CodeAgent` to
`ChessAgent` without copying the loop.

### Its effectiveness is observable

The architecture supports focused tests for prompt history, multiple tool
calls, malformed arguments, truncation, step limits, compaction, and cleanup.
The Part 1 generated patch also replayed successfully with `RESOLVED: yes`.

These outcomes do not prove the architecture is universally optimal, but they
show that its boundaries support the assignment's required behavior and make
failures diagnosable.

## 16. Why not implement everything in one or two methods?

A monolithic `run()` could build prompts, call the API, parse responses, execute
tools, compact context, and write logs. It would be shorter initially but harder
to reason about.

### Independent invariants become hidden

The current boundaries each protect one invariant:

- `build_prompt`: stable system/task plus active history.
- `query_language_model`: one request corresponds to one prompt/response log.
- `run`: assistant action precedes all linked tool observations.
- `execute_tool_calls`: one result per requested call.
- `compact_context`: only an old complete prefix is replaced.
- `finally`: artifacts and cleanup occur on success and failure.

In one large method, a change to error handling or compaction can accidentally
violate an unrelated message-order or logging invariant.

### Focused tests become possible

Tests can separately verify:

- prompt construction,
- long-output truncation,
- step-limit behavior,
- malformed arguments,
- skill loading,
- compaction boundaries,
- cleanup.

A monolithic method would require mocking the model, filesystem, tools,
compaction, and environment for almost every test.

### Domain reuse becomes practical

`CodeAgent` and `ChessAgent` share one lifecycle but have unrelated tool
execution. The abstract dispatcher is the seam that permits reuse without
duplicating the loop.

### Failure handling stays coherent

Recoverable model mistakes become tool observations. Infrastructure failures
can still propagate into the shared `finally` block. Mixing these in one method
often leads either to swallowed terminal failures or crashes on recoverable
model output.

### Observability remains trustworthy

Request logging occurs at the exact model boundary, tool observations are
created at the tool boundary, and compaction evidence is recorded at the
compaction boundary. Records generated far away from the operation they
describe are easier to omit or misorder.

## 17. Limitations of the assignment design

The current design is intentionally educational rather than fully
multi-provider:

- `Agent` constructs the OpenAI client directly.
- `query_language_model()` is coupled to Chat Completions.
- tool schemas use the Chat Completions nested function format.
- compaction uses the same model and endpoint as actions.
- tool validation is hand-written inside `CodeAgent`.
- trajectory JSON is written as one document at shutdown.
- execution is synchronous.

These are reasonable constraints for the assignment. If supporting incompatible
providers becomes a real requirement, evolve the boundaries rather than adding
provider checks throughout `Agent`.

The companion design document,
`lessons/multi-provider-agent-api-and-compaction-design.md`, shows that
evolution using adapters, canonical messages, a model gateway, a tool registry,
typed compaction plans, and append-only events.

