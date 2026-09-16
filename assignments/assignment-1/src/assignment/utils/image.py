"""Build a task's testbed image with local Docker, from a local checkout.

The repository under test is copied in from `task.source` — normally the
`chess_app/` checkout — rather than cloned inside the build. That keeps the
build offline, works with a private repository without any credential, and
avoids a network round trip on every rebuild.

The cost is that the image reflects whatever is on disk, so `verify_source`
checks the `chess_app` source before every build to confirm it matches the
source the assignment intends people to fix. A testbed silently built from an
already-fixed working tree would make a broken agent look like it passed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from assignment.env import DOCKER, docker_version
from assignment.task import Task

logger = logging.getLogger(__name__)

# Local caches, credentials, and git metadata never belong in the testbed. The
# .git directory in particular would be meaningless inside the container; the
# build makes a fresh repository instead.
IGNORED_NAMES = {".git", ".venv", ".pytest_cache", "__pycache__", ".env", ".DS_Store"}

class SourceMismatch(Exception):
    """The local checkout is not the commit the task is defined against."""

def _git(source: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True)

def is_ignored(path: Path) -> bool:
    """Whether a file should be kept out of the build context."""
    return any(part in IGNORED_NAMES for part in path.parts) or path.suffix == ".pyc"

def verify_source(task: Task, strict: bool = True) -> None:
    """Check that the local checkout matches the task's base commit.

    Args:
        task: The task whose `source` to check.
        strict: Raise on a mismatch. When False, mismatches are logged as
            warnings and the build proceeds — useful when deliberately testing
            a modified checkout.

    Raises:
        SourceMismatch: The checkout is missing, on the wrong commit, or dirty.
    """

    def complain(message: str) -> None:
        if strict:
            raise SourceMismatch(message)
        logger.warning("%s (continuing: strict=False)", message)

    if not task.source.is_dir() or not any(task.source.iterdir()):
        raise SourceMismatch(
            f"{task.source} is missing or empty. Run `make chess-app` to check it out."
        )

    head = _git(task.source, "rev-parse", "HEAD")
    if head.returncode != 0:
        complain(f"{task.source} is not a git checkout: {head.stderr.strip()}")
        return

    if head.stdout.strip() != task.base_commit:
        complain(
            f"{task.source} is at {head.stdout.strip()[:12]}, but {task.id} is defined against "
            f"{task.base_commit[:12]}. The testbed would not be the task's base commit."
        )

    dirty = _git(task.source, "status", "--porcelain")
    if dirty.stdout.strip():
        # Each line is a status code then the path. The code's width varies, so
        # split on whitespace rather than slicing at a fixed offset.
        paths = [line.strip().split(None, 1)[-1] for line in dirty.stdout.strip().splitlines()[:5]]
        changed = ", ".join(paths)
        complain(
            f"{task.source} has uncommitted changes ({changed}). The testbed would contain them, "
            "which silently invalidates the evaluation if one of them is the fix."
        )

def _context_filter(root: Path):
    """A `shutil.copytree` ignore callback that applies `is_ignored`."""

    def ignore(directory: str, names: list[str]) -> set[str]:
        base = Path(directory)
        return {name for name in names if is_ignored((base / name).relative_to(root))}

    return ignore

def _build(arguments: list[str], context: str, tag: str) -> None:
    """Run one `docker build`, streaming its output so a slow build is visible."""
    result = subprocess.run([*DOCKER, "build", "--tag", tag, *arguments, context])
    if result.returncode != 0:
        raise RuntimeError(f"Could not build {tag}. See the build output above.")

def build_testbed_image(task: Task, strict: bool = True, force_build: bool = False) -> str:
    """Build the image holding the repository under test at its base commit.

    Args:
        task: The task whose Dockerfile and source checkout to build from.
        strict: Refuse to build when the checkout does not match the task's
            base commit. See `verify_source`.
        force_build: Rebuild every layer instead of reusing Docker's cache.

    Returns:
        The tag of an image with the repository installed at /testbed.
    """
    verify_source(task, strict=strict)
    docker_version()

    base_tag = f"assignment-testbed/{task.id}:base"
    tag = f"assignment-testbed/{task.id}:latest"
    cache = ["--no-cache"] if force_build else []

    logger.info("Building %s from %s at %s", task.id, task.source, task.base_commit[:12])
    # The Dockerfile lives with the task and the context is the checkout, so the
    # context is staged in a temporary directory: a .dockerignore would have to
    # be written into the checkout, which `verify_source` would then reject as
    # an uncommitted change.
    with tempfile.TemporaryDirectory(prefix=f"{task.id}-context-") as staging:
        context = Path(staging) / "context"
        shutil.copytree(task.source, context, ignore=_context_filter(task.source))
        _build(["--file", str(task.dockerfile), *cache], str(context), base_tag)

    if not task.pins:
        subprocess.run([*DOCKER, "tag", base_tag, tag], check=True)
        return tag

    # Pin last, deliberately: the task's `pins` are a full freeze of a
    # known-good resolution, and applying them as the final layer means a
    # rebuild installs exactly what was tested, whatever the Dockerfile's own
    # dependency resolution produced.
    logger.info("Pinning %d packages on top of the built image", len(task.pins))
    with tempfile.TemporaryDirectory(prefix=f"{task.id}-pins-") as staging:
        pins = Path(staging) / "Dockerfile"
        pins.write_text(
            f"FROM {base_tag}\n"
            f"RUN pip install --no-cache-dir {' '.join(task.pins)}\n"
        )
        _build(["--file", str(pins), *cache], staging, tag)

    return tag
