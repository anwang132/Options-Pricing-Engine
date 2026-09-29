"""Capture the execution environment for manifests and reports."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import scipy

from options_engine import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[3]
THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _run(args: list[str]) -> str | None:
    try:
        out = subprocess.run(
            args, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Paths that can affect computed results; docs and reports are deliberately excluded.
STATE_PATHS = (
    "src",
    "config",
    "fixtures",
    "scripts",
    "tests",
    "examples",
    "pyproject.toml",
    "uv.lock",
    "ui/src",
    "ui/package.json",
    "ui/package-lock.json",
)


def git_state() -> dict[str, Any]:
    """Commit plus a hash identifying uncommitted changes to result-affecting paths.

    ``dirty_state_sha256`` covers the tracked diff against HEAD and the content
    of every untracked, non-ignored file under ``STATE_PATHS``.
    """
    commit = _run(["git", "rev-parse", "HEAD"])
    if commit is None:
        return {"commit": None, "dirty": None, "dirty_state_sha256": None}
    diff = _run(["git", "diff", "HEAD", "--binary", "--", *STATE_PATHS]) or ""
    listed = _run(["git", "ls-files", "--others", "--exclude-standard", "--", *STATE_PATHS])
    untracked = sorted((listed or "").splitlines())
    digest = hashlib.sha256(diff.encode())
    for rel in untracked:
        digest.update(rel.encode() + b"\0" + (file_sha256(PROJECT_ROOT / rel) or "").encode())
    dirty = bool(diff) or bool(untracked)
    return {
        "commit": commit.strip(),
        "dirty": dirty,
        "dirty_state_sha256": digest.hexdigest() if dirty else None,
        "state_paths": list(STATE_PATHS),
        "untracked_file_count": len(untracked),
    }


@lru_cache(maxsize=1)
def _cpu_brand() -> str | None:
    if sys.platform == "darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        return out.strip() if out else None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or None


def _blas() -> dict[str, Any]:
    try:
        cfg = np.show_config(mode="dicts")
        blas = cfg.get("Build Dependencies", {}).get("blas", {})
        return {"name": blas.get("name"), "version": blas.get("version")}
    except Exception:  # pragma: no cover - diagnostic only
        return {}


def environment_info(include_git: bool = True) -> dict[str, Any]:
    info: dict[str, Any] = {
        "package_version": __version__,
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "blas": _blas(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu_brand(),
        "logical_cpus": os.cpu_count(),
        "thread_env": {k: os.environ.get(k) for k in THREAD_VARS},
        "uv_lock_sha256": file_sha256(PROJECT_ROOT / "uv.lock"),
    }
    if include_git:
        info["git"] = git_state()
    return info
