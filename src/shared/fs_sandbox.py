"""A process-wide filesystem root override, for harnesses that must not touch real files.

Motivation (#566): ``coder_bench`` sets up a disposable ``git worktree`` at a pre-fix commit
and runs a real agent turn against it. The docstring claims isolation, but the local
discovery tools resolve their roots from ``%USERPROFILE%`` and default to searching
``Documents``/``Desktop``/``Downloads`` — so a bench turn found and read a file from the
**live production repo** rather than the disposable checkout. Beyond invalidating the
measurement, it means a bench run can read (and, through other filesystem tools, act on)
real user files.

The lever is deliberately an environment variable rather than a constructor argument:
``DiscoveryEngine`` is a singleton constructed at module import, so by the time a harness
knows its sandbox path the instance already exists. Roots are therefore resolved at *call*
time against this module, which a harness can set before — or after — anything is built.

Not a security boundary. It is a correctness guard for our own harnesses against our own
tools; it does not contain hostile code, and nothing here should be relied on as a jail.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

ENV_VAR = "CHAD_FS_SANDBOX_ROOT"


def sandbox_root() -> Optional[Path]:
    """The active sandbox root, or None when filesystem tools should behave normally.

    A path that doesn't exist is treated as no sandbox: silently confining every tool to a
    missing directory would turn a typo into a run where every file lookup mysteriously
    returns nothing.
    """
    raw = os.environ.get(ENV_VAR)
    if not raw or not raw.strip():
        return None
    try:
        path = Path(os.path.expandvars(raw.strip())).resolve()
    except Exception:
        return None
    return path if path.is_dir() else None


def is_active() -> bool:
    return sandbox_root() is not None


@contextmanager
def fs_sandbox(root: str | os.PathLike) -> Iterator[Path]:
    """Confine filesystem discovery to ``root`` for the duration of the block.

    Restores the previous value on exit, including on exception, so a harness that runs
    several cases can't leak one case's sandbox into the next.
    """
    previous = os.environ.get(ENV_VAR)
    os.environ[ENV_VAR] = str(root)
    try:
        yield Path(str(root)).resolve()
    finally:
        if previous is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = previous


__all__ = ["ENV_VAR", "sandbox_root", "is_active", "fs_sandbox"]
