"""Validate local assignment configuration without launching a sandbox."""

from __future__ import annotations

import argparse
import os
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from openai import OpenAI

from assignment.env import DockerUnavailable, docker_version
from assignment.task import Task
from assignment.utils.image import SourceMismatch, verify_source


TASKS = ("tasks/chess-terminal-move",)
PLACEHOLDERS = ("replace-", "course-key", "<", ">")

# The cheapest request that still exercises the combination the agents use:
# chat completions, with a function tool, at the configured reasoning effort.
# Providers reject some combinations only when all three are present, and a
# failure here is far easier to read than the same failure mid-run.
PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Reply that the harness can call a tool.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


def _configured(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or any(marker in value.lower() for marker in PLACEHOLDERS):
        return None
    return value


def _probe_tool_calling(api_key: str, base_url: str, model: str) -> str | None:
    """Make one tiny tool-enabled request; return an error message, or None."""
    from assignment.agent.base import reasoning_effort

    client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
    effort = reasoning_effort()
    extra = {"reasoning_effort": effort} if effort else {}
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Call ping."}],
            tools=[PROBE_TOOL],
            max_completion_tokens=16,
            **extra,
        )
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="validate files and credentials without contacting the inference endpoint",
    )
    parser.add_argument(
        "--no-probe",
        dest="probe",
        action="store_false",
        help="skip the tool-calling probe, which spends a handful of tokens",
    )
    args = parser.parse_args()
    load_dotenv()

    failures: list[str] = []
    warnings: list[str] = []

    for task_path in TASKS:
        try:
            verify_source(Task.load(task_path))
            print(f"[ok] pinned source: {task_path}")
        except (FileNotFoundError, SourceMismatch, ValueError) as exc:
            failures.append(f"{task_path}: {exc}")

    try:
        print(f"[ok] Docker daemon: {docker_version()}")
    except DockerUnavailable as exc:
        failures.append(str(exc))

    api_key = _configured("OPENAI_API_KEY")
    base_url = _configured("OPENAI_BASE_URL")
    model = _configured("OPENAI_MODEL")
    if api_key:
        print("[ok] OPENAI_API_KEY is set")
    else:
        failures.append("OPENAI_API_KEY is missing or still a placeholder")
    if model:
        print(f"[ok] model: {model}")
    else:
        failures.append("OPENAI_MODEL is missing")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            failures.append("OPENAI_BASE_URL must be a complete HTTPS URL")
        else:
            print(f"[ok] inference endpoint: {parsed.netloc}")
    else:
        failures.append("OPENAI_BASE_URL is missing")

    if not args.offline and api_key and base_url:
        models_url = base_url.rstrip("/") + "/models"
        try:
            response = httpx.get(
                models_url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if response.status_code in {401, 403}:
                failures.append(
                    f"inference endpoint rejected the API key ({response.status_code})"
                )
            elif response.status_code >= 500:
                failures.append(
                    f"inference endpoint is unavailable ({response.status_code})"
                )
            elif response.status_code == 200:
                print("[ok] inference endpoint accepted the API key")
            else:
                warnings.append(
                    f"endpoint is reachable but GET /models returned {response.status_code}"
                )
        except httpx.HTTPError as exc:
            failures.append(f"could not reach inference endpoint: {exc}")

    if args.probe and not args.offline and api_key and base_url and model:
        error = _probe_tool_calling(api_key, base_url, model)
        if error:
            failures.append(f"{model} rejected a tool-enabled request: {error}")
        else:
            print(f"[ok] {model} accepted a tool-enabled request")

    for warning in warnings:
        print(f"[warning] {warning}")
    if failures:
        for failure in failures:
            print(f"[error] {failure}")
        print("Configuration is not ready; no sandbox was launched.")
        return 1
    print("Assignment configuration is ready; no sandbox was launched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
