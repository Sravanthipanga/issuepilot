"""Sandboxed test execution.

The patch and the generated tests are applied to a throwaway copy of
the repository — never to the real one — and pytest runs there:

- ``LocalSandbox``: a subprocess in the temp copy with a timeout.
  Used by default and in CI (this build environment has no Docker).
- ``DockerSandbox``: the same copy mounted into a
  ``python:3.12-slim`` container with ``--network none``, a memory cap
  and a timeout. Selected with ``ISSUEPILOT_SANDBOX=docker`` wherever
  Docker is available (see docker-compose.yml).

Both return the same result shape, so the graph cannot tell — or care —
which one ran.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _parse_counts(output: str) -> tuple[int, int]:
    passed = failed = 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) error", output)
    if m:
        failed += int(m.group(1))
    return passed, failed


def materialize(repo_path: str, patch: dict | None,
                tests: list[dict]) -> Path:
    """Copy the repo to a temp dir, apply the patch, add the tests."""
    work = Path(tempfile.mkdtemp(prefix="issuepilot-"))
    shutil.copytree(repo_path, work, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(
                        ".git", ".venv", "__pycache__", ".pytest_cache"))
    if patch:
        target = work / patch["file"]
        target.write_text(patch["after"], encoding="utf-8")
    for test in tests:
        dest = work / test["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(test["content"], encoding="utf-8")
    return work


class LocalSandbox:
    name = "local"

    def run(self, repo_path: str, patch: dict | None,
            tests: list[dict], timeout: int = 180) -> dict:
        work = materialize(repo_path, patch, tests)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header"],
                cwd=work, capture_output=True, text=True, timeout=timeout)
            output = (proc.stdout + proc.stderr)[-6000:]
            passed, failed = _parse_counts(output)
            return {"passed": passed, "failed": failed,
                    "ok": proc.returncode == 0 and failed == 0,
                    "output": output, "runner": self.name}
        except subprocess.TimeoutExpired:
            return {"passed": 0, "failed": 0, "ok": False,
                    "output": f"pytest timed out after {timeout}s",
                    "runner": self.name}
        finally:
            shutil.rmtree(work, ignore_errors=True)


class DockerSandbox:
    name = "docker"

    def run(self, repo_path: str, patch: dict | None,
            tests: list[dict], timeout: int = 180) -> dict:
        if shutil.which("docker") is None:
            raise RuntimeError("ISSUEPILOT_SANDBOX=docker but docker "
                               "is not on PATH")
        work = materialize(repo_path, patch, tests)
        try:
            proc = subprocess.run(
                ["docker", "run", "--rm", "--network", "none",
                 "--memory", "512m", "-v", f"{work}:/work", "-w", "/work",
                 "python:3.12-slim", "sh", "-c",
                 "pip install -q pytest && python -m pytest -q --no-header"],
                capture_output=True, text=True, timeout=timeout + 120)
            output = (proc.stdout + proc.stderr)[-6000:]
            passed, failed = _parse_counts(output)
            return {"passed": passed, "failed": failed,
                    "ok": proc.returncode == 0 and failed == 0,
                    "output": output, "runner": self.name}
        finally:
            shutil.rmtree(work, ignore_errors=True)


def get_sandbox():
    if os.environ.get("ISSUEPILOT_SANDBOX") == "docker":
        return DockerSandbox()
    return LocalSandbox()
