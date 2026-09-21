"""The Part 1 coding agent: fix a software issue and submit a git patch."""

from __future__ import annotations

import json
from typing import Any

from assignment.agent.base import (
    DEFAULT_COMPACTION_KEEP_RECENT_STEPS,
    DEFAULT_COMPACTION_MAX_TOKENS,
    Agent,
    format_tool_output,
)
from assignment.agent.tools import EXECUTE_TOOL, SEND_MESSAGE_TOOL
from assignment.env import Environment


class CodeAgent(Agent):
    """An agent that fixes a software issue and submits a git patch."""

    def __init__(
        self,
        task: str,
        environment: Environment,
        model: str | None = None,
        logs_save_path: str | None = None,
        step_limit: int = 100,
        skills_path: str | None = None,
        auto_stop_environment: bool = True,
        compact_threshold_tokens: int | None = None,
        compaction_keep_recent_steps: int = DEFAULT_COMPACTION_KEEP_RECENT_STEPS,
        compaction_max_tokens: int = DEFAULT_COMPACTION_MAX_TOKENS,
    ):
        super().__init__(
            environment=environment,
            model=model,
            logs_save_path=logs_save_path,
            step_limit=step_limit,
            skills_path=skills_path,
            auto_stop_environment=auto_stop_environment,
            compact_threshold_tokens=compact_threshold_tokens,
            compaction_keep_recent_steps=compaction_keep_recent_steps,
            compaction_max_tokens=compaction_max_tokens,
        )
        self.task = task
        self.submitted_patch = ""

        # TODO(Part 1.3): Make the `execute` and `send_message` tools available
        # to the agent.
        self.tools.extend([EXECUTE_TOOL, SEND_MESSAGE_TOOL])

        # TODO(1.1.b): Construct the system prompt and task_prompt. These
        # should be usable by the `Agent.build_prompt` method.
        system_information = json.dumps(
            {
                "machine": environment.machine,
                "release": environment.release,
                "system": environment.system,
                "version": environment.version,
            },
            indent=2,
        )
        self.system_prompt = (
            "You are a software engineering agent working in an isolated "
            "terminal sandbox. Investigate the task, make the smallest correct "
            "source changes, and verify them with relevant tests. Use the "
            "`execute` tool to inspect and modify the repository. Call "
            "`send_message` only after the task is complete.\n\n"
            "<system_information>\n"
            f"{system_information}\n"
            "</system_information>"
        )
        self.task_prompt = f"Complete the following software task:\n\n{task}"

        # TODO(1.4): If any skills are available to the agent, make their
        # descriptions/metadata available to the agent in the prompt.
        if self.skills:
            catalog = "\n\n".join(
                skill["metadata"] for skill in self.skills.values()
            )
            self.system_prompt += (
                "\n\nReusable skills are available. Call `invoke_skill` with a "
                "skill's name to load its complete instructions, then follow "
                "those instructions.\n\n"
                f"<skills>\n{catalog}\n</skills>"
            )

    def execute_tool_calls(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """Execute ``execute`` and ``send_message`` calls in the code sandbox."""

        # TODO(Part 1.3): Parse each call, execute recognized tools, and return
        # one message per call (there may be multiple tool calls in one agent
        # response!). Malformed JSON and unknown tools must become recoverable
        # observations relayed to the agent instead of exceptions.
        observations: list[dict[str, str]] = []
        for index, tool_call in enumerate(tool_calls):
            call_id = (
                tool_call.get("id", f"invalid_call_{index}")
                if isinstance(tool_call, dict)
                else f"invalid_call_{index}"
            )
            if not isinstance(call_id, str):
                call_id = str(call_id)

            try:
                if not isinstance(tool_call, dict):
                    raise ValueError("Tool call must be an object.")
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    raise ValueError("Tool call is missing a function object.")

                name = function.get("name")
                if not isinstance(name, str) or not name:
                    raise ValueError("Tool call function name must be a string.")
                raw_arguments = function.get("arguments")
                if not isinstance(raw_arguments, str):
                    raise ValueError("Tool call arguments must be a JSON string.")
                arguments = json.loads(raw_arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool call arguments must decode to an object.")

                if name == "execute":
                    allowed = {"command", "timeout", "cwd", "env", "shell"}
                    extras = set(arguments) - allowed
                    if extras:
                        raise ValueError(
                            "Unexpected execute argument(s): "
                            + ", ".join(sorted(extras))
                        )

                    command = arguments.get("command")
                    if not (
                        isinstance(command, str)
                        or (
                            isinstance(command, list)
                            and all(isinstance(part, str) for part in command)
                        )
                    ):
                        raise ValueError(
                            "execute requires command to be a string or list of strings."
                        )
                    shell = arguments.get("shell")
                    if shell is not None and not isinstance(shell, bool):
                        raise ValueError("execute shell must be a boolean or null.")
                    cwd = arguments.get("cwd")
                    if cwd is not None and not isinstance(cwd, str):
                        raise ValueError("execute cwd must be a string or null.")
                    timeout = arguments.get("timeout")
                    if timeout is not None and (
                        isinstance(timeout, bool)
                        or not isinstance(timeout, (int, float))
                    ):
                        raise ValueError("execute timeout must be a number or null.")
                    env = arguments.get("env")
                    if env is not None and (
                        not isinstance(env, dict)
                        or not all(
                            isinstance(key, str) and isinstance(value, str)
                            for key, value in env.items()
                        )
                    ):
                        raise ValueError(
                            "execute env must be an object of string values or null."
                        )

                    output = self.env.execute(**arguments)
                    if not isinstance(output, dict):
                        raise TypeError("The environment returned a non-object result.")
                    content = format_tool_output(output)

                elif name == "send_message":
                    if set(arguments) != {"summary"}:
                        raise ValueError(
                            "send_message requires exactly one `summary` argument."
                        )
                    summary = arguments["summary"]
                    if not isinstance(summary, str):
                        raise ValueError("send_message summary must be a string.")
                    self.finished = True
                    content = f"<message_sent>{summary}</message_sent>"

                elif name == "invoke_skill" and self.skills:
                    if set(arguments) != {"name"}:
                        raise ValueError(
                            "invoke_skill requires exactly one `name` argument."
                        )
                    skill_name = arguments["name"]
                    if not isinstance(skill_name, str):
                        raise ValueError("invoke_skill name must be a string.")
                    skill = self.skills.get(skill_name)
                    if skill is None:
                        raise ValueError(f"Unknown skill: {skill_name}")
                    content = skill["content"]

                else:
                    raise ValueError(f"Unknown tool: {name}")

            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                content = format_tool_output({"error": str(exc)})

            observations.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": content,
                }
            )

        return observations
