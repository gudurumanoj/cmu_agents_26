"""Integration test for the chess server hosted in a Docker sandbox."""

import subprocess

import pytest

from assignment.chess_sandbox import ChessSandbox, IllegalMove
from assignment.env import DOCKER, SANDBOX_LABEL

pytestmark = pytest.mark.docker


def sandbox_containers() -> list[str]:
    """Container ids this assignment started and has not removed."""
    listed = subprocess.run(
        [*DOCKER, "ps", "--quiet", "--filter", f"label={SANDBOX_LABEL}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return listed.stdout.split()


def test_chess_app_is_playable_through_the_published_port():
    before = set(sandbox_containers())

    # This public test verifies the port publishing and API infrastructure
    # without depending on the intentionally broken engine search. The private
    # grader applies a candidate patch before exercising successful moves.
    with ChessSandbox() as sandbox:
        assert len(set(sandbox_containers()) - before) == 1
        assert sandbox.server_url.startswith("http://127.0.0.1:")

        current = sandbox.state()
        assert current["turn"] == "white"
        assert current["history"] == []

        with pytest.raises(IllegalMove):
            sandbox.play("e2e5")

        assert sandbox.reset()["history"] == []

    assert set(sandbox_containers()) == before, "Chess sandbox was not removed after the test."
