"""Execute bash commands in a local Docker container.

Agents run code they wrote themselves, so it has to run somewhere other than
the machine driving the run. A container started from the task's testbed image
gives that isolation without a cloud account: the agent gets a writable
`/testbed`, its own filesystem, and its own process tree, and the whole thing
disappears when `stop()` runs.

Everything here shells out to the `docker` CLI rather than talking to the
daemon socket directly, so a working `docker` command is the only requirement.
Containers are labelled, so `make clean-sandboxes` can find and remove any that
a crashed run left behind.
"""

from __future__ import annotations

import logging
import os
import posixpath
import shlex
import subprocess
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

# The CLI to drive, split like a shell would so that a wrapper with arguments
# works: ASSIGNMENT_DOCKER="podman --remote", or "sudo -n docker" on a host
# where the daemon socket is only reachable through sudo.
DOCKER = tuple(shlex.split(os.environ.get("ASSIGNMENT_DOCKER", "docker")))

# Every container this module starts carries this label, which is what makes
# orphans findable: `docker ps --filter label=assignment.sandbox=1`.
SANDBOX_LABEL = "assignment.sandbox=1"

# Keeping the container alive is the entrypoint's only job; commands arrive
# later through `docker exec`. `tail -f /dev/null` blocks forever, costs
# nothing, and exists in every image the assignment uses.
KEEPALIVE_ENTRYPOINT = ("--entrypoint", "tail")
KEEPALIVE_COMMAND = ("-f", "/dev/null")


class DockerUnavailable(RuntimeError):
    """The `docker` command is missing, or its daemon is not reachable."""


def docker_version() -> str:
    """Return the running daemon's version.

    Raises:
        DockerUnavailable: If the CLI is missing or the daemon refuses the
            connection, with the daemon's own message rather than a traceback
            from deep inside a client library.
    """
    try:
        result = subprocess.run(
            [*DOCKER, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise DockerUnavailable(
            f"`{DOCKER[0]}` is not on PATH. Install Docker, or point ASSIGNMENT_DOCKER "
            "at a compatible CLI such as podman."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DockerUnavailable("`docker version` timed out.") from exc

    if result.returncode != 0:
        message = result.stderr.strip() or "unknown error"
        if "permission denied" in message.lower():
            # Hundreds of `docker exec` calls happen per agent run, none of them
            # on a terminal that could answer a password prompt, so group
            # membership is the only workable fix.
            message += (
                "\nYour user cannot reach the daemon socket. Add yourself to the "
                "`docker` group (`sudo usermod -aG docker $USER`, then log out and "
                "back in). Prefixing commands with sudo does not work here: the "
                "harness runs docker non-interactively."
            )
        raise DockerUnavailable(f"The Docker daemon is not reachable: {message}")
    return result.stdout.strip()


def run_docker(
    arguments: Sequence[str], timeout: float | None = None
) -> subprocess.CompletedProcess:
    """Run one `docker` subcommand and capture its output."""
    return subprocess.run(
        [*DOCKER, *arguments], capture_output=True, text=True, timeout=timeout
    )


def publish_port_arguments(ports: Iterable[int]) -> list[str]:
    """Build the `docker run` flags that expose container ports on localhost.

    Each port is published to an arbitrary free host port rather than the same
    number, so two sandboxes can run side by side and a port already taken on
    the host does not fail the launch. `tunnel_url` reads back which host port
    a container actually got.

    Args:
        ports: Container ports to publish. Duplicates are ignored.

    Returns:
        The flags to pass to `docker run`, in the order the ports were given.

    Raises:
        ValueError: If a port is outside the valid range.
    """
    flags: list[str] = []
    for port in dict.fromkeys(int(port) for port in ports):
        if not 1 <= port <= 65535:
            raise ValueError(f"Invalid port: {port}")
        flags += ["-p", f"127.0.0.1::{port}"]
    return flags


def environment_arguments(variables: dict[str, str]) -> list[str]:
    """Build the `-e NAME=value` flags for one command's environment."""
    return [flag for name, value in variables.items() for flag in ("-e", f"{name}={value}")]


class Environment:
    """A command sandbox backed by one long-running Docker container."""

    def __init__(
        self,
        image: str = "python:3.12",
        cwd: str = "/",
        startup_timeout: float = 600,
        runtime_timeout: float = 600,
        deployment_timeout: float | None = None,
        ports: Sequence[int] = (),
        docker_run_arguments: Sequence[str] = (),
        pull: bool = True,
        conda_env: str | None = None,
    ):
        """Start a container and block until it is running.

        Args:
            image: An image reference. Build a task testbed with
                `assignment.utils.image.build_testbed_image`, which returns the
                tag it built; a published image such as a SWE-bench evaluation
                image is pulled on first use.
            cwd: Working directory for commands that do not specify one.
            startup_timeout: Seconds to wait for the container to report
                running.
            runtime_timeout: Seconds a single command may run when the caller
                does not pass its own timeout.
            deployment_timeout: Seconds the container may stay alive before it
                is removed, or None to let it run until `stop()`. Nothing is
                billed locally, so None is the default; the chess sandbox sets
                a limit because a served game should not outlive the run.
            ports: Container ports to publish on localhost. Read the host side
                back with `tunnel_url`.
            docker_run_arguments: Extra flags forwarded to `docker run`, for
                capabilities this class does not model.
            pull: Fetch the image when it is not already present locally.
            conda_env: Name of a conda environment to put on PATH for every
                command. SWE-bench images install the repository under test
                into an environment named ``testbed`` but never activate it, so
                without this ``python`` is conda's base environment, where the
                repository and its dependencies are not installed.
        """
        self.image = str(image)
        self.cwd = cwd
        self.runtime_timeout = runtime_timeout
        # Merged into every command's environment; a per-call `env` wins.
        self.env_defaults: dict[str, str] = {}
        self.container_id = ""
        self._reaper: threading.Timer | None = None

        docker_version()
        if pull:
            self._ensure_image()

        started = time.monotonic()
        create = run_docker(
            [
                "run",
                "--detach",
                "--label",
                SANDBOX_LABEL,
                "--workdir",
                cwd,
                *publish_port_arguments(ports),
                *docker_run_arguments,
                *KEEPALIVE_ENTRYPOINT,
                self.image,
                *KEEPALIVE_COMMAND,
            ],
            timeout=startup_timeout,
        )
        if create.returncode != 0:
            raise RuntimeError(
                f"Could not start a container from {self.image}: {create.stderr.strip()}"
            )
        self.container_id = create.stdout.strip()
        logger.info("Started container %s from %s", self.container_id[:12], self.image)

        try:
            self._wait_until_alive(startup_timeout - (time.monotonic() - started))
        except Exception:
            self.stop()
            raise

        if deployment_timeout is not None:
            self._reaper = threading.Timer(deployment_timeout, self._reap)
            self._reaper.daemon = True
            self._reaper.start()

        if conda_env:
            self.activate_conda_env(conda_env)

        # Read the platform from inside the container, not from platform.uname(),
        # which would describe the machine running this code instead.
        self.system, self.release, self.version, self.machine = self.execute(
            "uname -s; uname -r; uname -v; uname -m"
        )["output"].splitlines()

    def _ensure_image(self) -> None:
        """Pull the image unless it is already in the local store.

        SWE-bench evaluation images are gigabytes, so the pull streams its
        progress to the terminal instead of being captured and swallowed.
        """
        if run_docker(["image", "inspect", self.image], timeout=60).returncode == 0:
            return

        print(f"[env] pulling {self.image} (first use only)", flush=True)
        if subprocess.run([*DOCKER, "pull", self.image]).returncode != 0:
            raise RuntimeError(f"Could not pull {self.image}.")

    def _wait_until_alive(self, timeout: float) -> None:
        """Poll until the container reports running, or give up with its logs."""
        deadline = time.monotonic() + max(timeout, 1)
        while time.monotonic() < deadline:
            if self.is_alive():
                return
            time.sleep(0.1)

        logs = run_docker(["logs", "--tail", "40", self.container_id], timeout=30)
        raise RuntimeError(
            f"Container {self.container_id[:12]} did not stay running.\n"
            f"{(logs.stdout + logs.stderr).strip()}"
        )

    def _reap(self) -> None:
        """Remove the container once `deployment_timeout` has elapsed."""
        logger.warning(
            "Container %s reached its lifetime limit and was removed.",
            self.container_id[:12],
        )
        run_docker(["rm", "--force", self.container_id], timeout=60)

    def is_alive(self) -> bool:
        """Whether the container is still running.

        A container that exited or was removed answers False rather than
        raising, so callers can distinguish a failed command from a dead
        sandbox.
        """
        if not self.container_id:
            return False
        inspect = run_docker(
            ["inspect", "--format", "{{.State.Running}}", self.container_id], timeout=30
        )
        return inspect.returncode == 0 and inspect.stdout.strip() == "true"

    def activate_conda_env(self, name: str, root: str = "/opt/miniconda3") -> str:
        """Put a conda environment's bin directory first on PATH for all commands.

        Activating properly needs a login shell, which these commands do not
        get. Prepending the environment's bin directory has the same effect for
        ``python``, ``pip``, and anything else installed there.

        Args:
            name: The environment name, for example ``testbed``.
            root: Where conda is installed in the image.

        Returns:
            The PATH now used for every command.

        Raises:
            FileNotFoundError: If the environment is not in the image, rather
                than silently leaving the wrong interpreter on PATH.
        """
        binary_dir = posixpath.join(root, "envs", name, "bin")
        if self.execute(f"test -d {binary_dir}", cwd="/")["returncode"] != 0:
            raise FileNotFoundError(f"No conda environment at {binary_dir}")

        current = self.execute("printenv PATH", cwd="/")["output"].strip()
        self.env_defaults["PATH"] = f"{binary_dir}:{current}"
        return self.env_defaults["PATH"]

    def copy_in(self, source: str | Path, destination: str) -> None:
        """Copy a local file into the container.

        Args:
            source: Path on the machine running the agent.
            destination: Absolute path inside the container. Its parent
                directory is created first.

        Raises:
            RuntimeError: If the copy fails.
        """
        parent = posixpath.dirname(destination)
        if parent:
            self.execute(f"mkdir -p {shlex.quote(parent)}", cwd="/")

        result = run_docker(
            ["cp", str(source), f"{self.container_id}:{destination}"], timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError(f"Could not copy {source} into the sandbox: {result.stderr.strip()}")

    def execute(
        self,
        command: str | list[str],
        timeout: float | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        # A model calling this as a tool usually sends a command line, so
        # default to running it through a shell; pass shell=False with an argv
        # list to skip that.
        shell: bool | None = True,
        # check=False so a failing command returns its exit code instead of
        # raising and discarding the output the agent needs to see.
        check: bool = False,
    ) -> dict:
        """Run a command in the container and return its result.

        Args:
            command: A shell string, or an argv list if `shell` is False.
            timeout: Seconds before the command is killed. Defaults to the
                environment's `runtime_timeout`.
            cwd: Working directory to run the command in. Defaults to the
                environment's `cwd`.
            env: Environment variables to set for the command.
            shell: Run the command through bash, as with `subprocess.run`.
                None is treated as True, since a model calling this as a tool
                may send null for an omitted argument.
            check: Raise on a non-zero exit code instead of reporting it.

        Returns:
            A dict with `output`, `returncode`, `stdout`, `stderr`, and
            `exception_info`. `output` holds stdout and stderr interleaved, as
            they would appear in a terminal. `exception_info` is empty when the
            command ran, whatever its exit code. If the call itself fails (the
            command timed out, the container died), the exception is caught
            rather than raised: `returncode` is -1, `exception_info` describes
            it, and an `extra` key carries the exception type.
        """
        shell = True if shell is None else shell
        if shell:
            command_line = command if isinstance(command, str) else shlex.join(command)
            argv = ["/bin/bash", "-c", command_line]
        else:
            argv = shlex.split(command) if isinstance(command, str) else list(command)

        arguments = ["exec", "--workdir", cwd if cwd is not None else self.cwd]
        arguments += environment_arguments({**self.env_defaults, **(env or {})})
        arguments += [self.container_id, *argv]

        try:
            result = run_docker(
                arguments, timeout=timeout if timeout is not None else self.runtime_timeout
            )
            if check and result.returncode != 0:
                raise RuntimeError(
                    f"Command exited with {result.returncode}: {result.stderr.strip()}"
                )
            output = {
                "stdout": result.stdout,
                "stderr": result.stderr,
                # The coding agent consumes one observation string, while the
                # task harness needs the streams separately for diagnostics.
                "output": result.stdout,
                "returncode": result.returncode,
                "exception_info": "",
            }
            if result.stderr:
                output["output"] += result.stderr
        except Exception as e:
            # A command can fail for reasons worth reporting to the model, but a
            # container that is gone is terminal: every later command fails the
            # same way, so raise rather than let the caller keep going.
            if not self.is_alive():
                raise RuntimeError(
                    "The sandbox is no longer running, so no further commands can "
                    f"be executed. Last error: {e}"
                ) from e

            # Same keys as the success path, so callers never have to branch on
            # which one they got.
            message = str(e) or type(e).__name__
            output = {
                "stdout": "",
                "stderr": message,
                "output": message,
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {message}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        return output

    def stop(self, timeout: float = 10):
        """Remove the container.

        Args:
            timeout: Seconds allowed for the removal.
        """
        if self._reaper is not None:
            self._reaper.cancel()
            self._reaper = None
        if not self.container_id:
            return

        try:
            run_docker(["rm", "--force", self.container_id], timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning(
                "Removing container %s timed out; `make clean-sandboxes` will collect it.",
                self.container_id[:12],
            )
        finally:
            self.container_id = ""

    def tunnel_url(self, port: int) -> str:
        """Return the localhost URL for a port published when the container started."""
        mapping = run_docker(["port", self.container_id, f"{port}/tcp"], timeout=30)
        published = mapping.stdout.strip().splitlines()
        if mapping.returncode != 0 or not published:
            forwarded = run_docker(["port", self.container_id], timeout=30).stdout.strip()
            raise ValueError(
                f"Port {port} was not published. Published ports: {forwarded or 'none'}."
            )

        # `docker port` prints one `host:port` line per address family; the
        # IPv4 loopback binding is the one publish_port_arguments requested.
        host_port = published[0].rsplit(":", 1)[-1]
        return f"http://127.0.0.1:{host_port}"

    def __enter__(self):
        """Enter a `with` block. The container is already running by this point."""
        return self

    def __exit__(self, *exc):
        """Remove the container on leaving a `with` block, including on error."""
        self.stop()
