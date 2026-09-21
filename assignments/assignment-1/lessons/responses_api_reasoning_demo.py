"""Demonstrate Responses API reasoning continuity with and without tools.

The script makes six small, billable requests:

1. A three-turn conversation without tools.
2. A three-request conversation containing one local function call.

It manages state manually with ``store=False`` so the generated JSON shows the
reasoning, message, function-call, and function-call-output items that are
replayed. Credentials are loaded from ``.env`` but are never written to the log.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


DEFAULT_OUTPUT = Path(__file__).with_name("responses_api_examples.json")

MULTIPLY_TOOL = {
    "type": "function",
    "name": "multiply",
    "description": "Multiply two integers and return their product.",
    "parameters": {
        "type": "object",
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    "strict": True,
}


def jsonable(value: Any) -> Any:
    """Recursively convert SDK models into JSON-compatible values."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def call_responses(
    client: OpenAI,
    *,
    model: str,
    effort: str,
    history: list[Any],
    turns: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
) -> Any:
    """Call Responses and record the exact sanitized request and response."""

    request: dict[str, Any] = {
        "model": model,
        "input": history,
        "reasoning": {"effort": effort, "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "max_output_tokens": 1_024,
        "store": False,
    }
    if tools is not None:
        request["tools"] = tools
    if tool_choice is not None:
        request["tool_choice"] = tool_choice

    request_log = jsonable(request)
    response = client.responses.create(**request)
    turns.append(
        {
            "request": request_log,
            "response": response.model_dump(mode="json", exclude_none=True),
            "output_item_types": [item.type for item in response.output],
        }
    )
    return response


def run_without_tools(
    client: OpenAI, *, model: str, effort: str
) -> dict[str, Any]:
    """Run three user/assistant turns while replaying all response items."""

    history: list[Any] = [
        {
            "role": "user",
            "content": (
                "Remember this fact for this conversation: the codename ORBIT "
                "corresponds to the number seven. Acknowledge in one short sentence."
            ),
        }
    ]
    turns: list[dict[str, Any]] = []

    response = call_responses(
        client, model=model, effort=effort, history=history, turns=turns
    )
    history.extend(response.output)
    history.append(
        {
            "role": "user",
            "content": "What codename and number did I give you? Answer briefly.",
        }
    )

    response = call_responses(
        client, model=model, effort=effort, history=history, turns=turns
    )
    history.extend(response.output)
    history.append(
        {
            "role": "user",
            "content": "State that remembered fact one final time in five words or fewer.",
        }
    )

    response = call_responses(
        client, model=model, effort=effort, history=history, turns=turns
    )
    history.extend(response.output)

    return {
        "name": "three_turns_without_tools",
        "state_management": (
            "Stateless replay: every response.output item is appended to input."
        ),
        "turns": turns,
        "final_history": jsonable(history),
    }


def execute_multiply(arguments: str) -> str:
    """Validate and execute the demonstration tool locally."""

    parsed = json.loads(arguments)
    if not isinstance(parsed, dict) or set(parsed) != {"a", "b"}:
        raise ValueError("multiply requires exactly integer arguments `a` and `b`")
    a, b = parsed["a"], parsed["b"]
    if (
        isinstance(a, bool)
        or isinstance(b, bool)
        or not isinstance(a, int)
        or not isinstance(b, int)
    ):
        raise ValueError("multiply arguments must be integers")
    return json.dumps({"a": a, "b": b, "product": a * b})


def run_with_tool(client: OpenAI, *, model: str, effort: str) -> dict[str, Any]:
    """Run reasoning -> function call -> function output -> continuation."""

    history: list[Any] = [
        {
            "role": "user",
            "content": (
                "Use the multiply tool exactly once to calculate 6 times 7. "
                "Do not calculate it without the tool."
            ),
        }
    ]
    turns: list[dict[str, Any]] = []

    response = call_responses(
        client,
        model=model,
        effort=effort,
        history=history,
        turns=turns,
        tools=[MULTIPLY_TOOL],
        tool_choice="required",
    )
    history.extend(response.output)

    function_calls = [
        item for item in response.output if item.type == "function_call"
    ]
    if not function_calls:
        raise RuntimeError("The model did not produce the required function call.")
    for call in function_calls:
        if call.name != "multiply":
            raise RuntimeError(f"Unexpected function call: {call.name}")
        history.append(
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": execute_multiply(call.arguments),
            }
        )

    response = call_responses(
        client,
        model=model,
        effort=effort,
        history=history,
        turns=turns,
        tools=[MULTIPLY_TOOL],
        tool_choice="auto",
    )
    history.extend(response.output)
    history.append(
        {
            "role": "user",
            "content": (
                "In one short sentence, state the result and name the tool that "
                "provided it. Do not call the tool again."
            ),
        }
    )

    response = call_responses(
        client,
        model=model,
        effort=effort,
        history=history,
        turns=turns,
        tools=[MULTIPLY_TOOL],
        tool_choice="auto",
    )
    history.extend(response.output)

    return {
        "name": "three_requests_with_function_call",
        "state_management": (
            "Stateless replay: reasoning and function_call items are replayed, "
            "followed by a matching function_call_output."
        ),
        "turns": turns,
        "final_history": jsonable(history),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Defaults to OPENAI_MODEL.")
    parser.add_argument(
        "--reasoning-effort",
        default="xhigh",
        help="Responses API reasoning effort (default: xhigh).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("OPENAI_BASE_URL")
    model = args.model or os.environ.get("OPENAI_MODEL")
    if not api_key or not base_url or not model:
        raise SystemExit(
            "OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL must be configured."
        )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=int(os.environ.get("OPENAI_MAX_RETRIES", "5")),
    )

    log = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": "/v1/responses",
        "base_url_host": base_url.split("://", 1)[-1].split("/", 1)[0],
        "model": model,
        "reasoning_effort": args.reasoning_effort,
        "credentials_logged": False,
        "important_note": (
            "OpenAI reasoning items are opaque/encrypted continuation state, "
            "not exposed raw chain-of-thought."
        ),
        "conversations": [
            run_without_tools(
                client, model=model, effort=args.reasoning_effort
            ),
            run_with_tool(client, model=model, effort=args.reasoning_effort),
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"Wrote sanitized Responses API examples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
