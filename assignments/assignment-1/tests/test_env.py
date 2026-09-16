"""Tests for the Docker sandbox environment.

These start real containers, so they are slow and need a reachable Docker
daemon. Run with `pytest -m docker`.
"""

import subprocess

import pytest

from assignment.env import DOCKER, SANDBOX_LABEL, Environment

pytestmark = pytest.mark.docker

IMAGE = "python:3.12-slim"


def sandbox_containers() -> list[str]:
    """Container ids this assignment started and has not removed."""
    listed = subprocess.run(
        [*DOCKER, "ps", "--quiet", "--filter", f"label={SANDBOX_LABEL}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listed.stdout.split()


@pytest.fixture(scope="module")
def env():
    """One container shared by every test in this module.

    The assertions here are part of the test surface: they check that the
    sandbox is launched and torn down cleanly, and are reported as errors at
    setup/teardown of whichever test is running.
    """
    before = set(sandbox_containers())

    env = Environment(image=IMAGE)
    assert set(sandbox_containers()) - before == {env.container_id[:12]}, (
        "Launching one sandbox should leave exactly one new labelled container."
    )

    yield env

    env.stop()
    assert set(sandbox_containers()) == before, (
        "The sandbox was stopped but its container is still running. "
        "Run `make clean-sandboxes`."
    )


def test_shell_command(env):
    """A bare command string runs through the shell, via the shell=True default."""
    output = env.execute("echo 'hello, world'")
    assert output["returncode"] == 0, "`echo 'hello, world'` did not return expected exit code"
    assert output["output"] == "hello, world\n", (
        f"`echo 'hello, world'` did not produce expected output when run on sandbox, "
        f"instead produced {output['output']}"
    )


def test_argv_command(env):
    """An argv list runs unshelled when shell=False is passed explicitly."""
    output = env.execute(["echo", "hello, world"], shell=False)
    assert output["returncode"] == 0, "`echo hello, world` did not return expected exit code"
    assert output["output"] == "hello, world\n", (
        f"`echo hello, world` did not produce expected output when run on sandbox, "
        f"instead produced {output['output']}"
    )


def test_failing_command(env):
    """A failing command reports its exit code, with the traceback in the output."""
    output = env.execute("python -c 'print(\"hello, world\"); raise Exception(\"test exception\")'")
    assert output["returncode"] != 0, "A raising command did not return a nonzero exit code"
    assert "hello, world\n" in output["output"], (
        f"A raising command did not produce expected output when run on sandbox, "
        f"instead produced {output['output']}"
    )
    # Streams are merged, so the traceback lands in output alongside stdout.
    assert "test exception" in output["output"], "the traceback should be in the output"


def test_platform_is_read_from_inside_the_container(env):
    """The system information the agent is told about describes the sandbox."""
    assert env.system == "Linux"
    assert env.machine


def test_working_directory_and_environment_apply(env):
    """A per-call cwd and env reach the command."""
    output = env.execute("pwd; echo $ASSIGNMENT_MARKER", cwd="/tmp", env={"ASSIGNMENT_MARKER": "set"})
    assert output["returncode"] == 0
    assert output["output"].split() == ["/tmp", "set"]
