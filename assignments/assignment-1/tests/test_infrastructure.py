"""Fast checks for setup helpers that must work before student TODOs."""

from pathlib import Path

import pytest

from assignment.env import environment_arguments, publish_port_arguments
from assignment.utils.image import is_ignored


def test_each_port_is_published_on_loopback_once():
    assert publish_port_arguments([8000, 9000, 8000]) == [
        "-p",
        "127.0.0.1::8000",
        "-p",
        "127.0.0.1::9000",
    ]


def test_no_ports_means_no_flags():
    assert publish_port_arguments([]) == []


def test_an_out_of_range_port_is_rejected():
    with pytest.raises(ValueError):
        publish_port_arguments([70000])


def test_environment_variables_become_exec_flags():
    assert environment_arguments({"PATH": "/opt/bin:/usr/bin"}) == [
        "-e",
        "PATH=/opt/bin:/usr/bin",
    ]


def test_build_context_excludes_local_state():
    assert is_ignored(Path(".git/config"))
    assert is_ignored(Path("src/__pycache__/server.cpython-312.pyc"))
    assert is_ignored(Path(".env"))
    assert not is_ignored(Path("src/chess_app/server.py"))
