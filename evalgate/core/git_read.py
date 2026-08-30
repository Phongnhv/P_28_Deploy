"""Read-only git access for EvalGate.

Every git call EvalGate makes goes through here, and the first thing this module
does is refuse any subcommand that is not on an explicit read-only allow-list.  A
release gate that can mutate the repository it is judging is not a gate, so the
restriction is enforced in code rather than left to reviewer discipline.

Reading a file at a ref uses ``git show <ref>:<path>``; the index is addressed as
the empty ref (``git show :<path>``).  Nothing is ever checked out, so the working
tree is untouched no matter which ref is inspected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Subcommands that cannot change the repository, the index or the working tree.
_READ_ONLY_SUBCOMMANDS = frozenset(
    {"show", "rev-parse", "ls-files", "ls-tree", "diff", "status", "log", "cat-file"}
)

#: ``git show :<path>`` reads the staged (index) copy of a file.
INDEX_REF = ""

_cache: dict[tuple[str, ...], str | None] = {}


class GitUnavailableError(RuntimeError):
    """Raised when git itself cannot be used, as opposed to a ref being absent."""


def run(*args: str) -> str | None:
    """Run one allow-listed read-only git command.

    Returns stdout on success and ``None`` when git exits non-zero, which for the
    calls made here means "this ref or path does not exist" rather than a fault.
    """
    if not args or args[0] not in _READ_ONLY_SUBCOMMANDS:
        raise ValueError(f"git subcommand not on the read-only allow-list: {args[:1]}")
    key = args
    if key in _cache:
        return _cache[key]
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError as exc:  # git is not installed at all
        raise GitUnavailableError("git executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitUnavailableError("git command timed out") from exc
    result = completed.stdout if completed.returncode == 0 else None
    _cache[key] = result
    return result


def clear_cache() -> None:
    """Drop memoised output. Tests that change repository state must call this."""
    _cache.clear()


def head_ref() -> str:
    branch = (run("rev-parse", "--abbrev-ref", "HEAD") or "unknown").strip()
    sha = (run("rev-parse", "--short", "HEAD") or "unknown").strip()
    return f"{branch}@{sha}"


def head_sha() -> str:
    return (run("rev-parse", "--short", "HEAD") or "unknown").strip()


def ref_sha(git_ref: str) -> str:
    """Extract the sha from a ``branch@sha`` label produced by :func:`head_ref`."""
    return git_ref.rsplit("@", 1)[-1].strip() if git_ref else ""


def ref_exists(ref: str) -> bool:
    """True when ``ref`` resolves to a commit in this repository.

    Callers must check this before comparing against a ref. ``list_files`` on a
    missing ref returns an empty list, which is indistinguishable from "the
    baseline had no files" -- and a comparison against nothing reports no
    regressions at all. Failing loudly is the only safe behaviour.
    """
    if ref == INDEX_REF:
        return True
    return run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is not None


def read_file(ref: str, path: str) -> str | None:
    """Return the content of ``path`` at ``ref``; ``INDEX_REF`` reads the index."""
    spec = f"{ref}:{path}" if ref else f":{path}"
    return run("show", spec)


def list_files(ref: str) -> list[str]:
    """Return every tracked path at ``ref``; ``INDEX_REF`` lists the index."""
    if ref == INDEX_REF:
        output = run("ls-files") or ""
    else:
        output = run("ls-tree", "-r", "--name-only", ref) or ""
    return [line.strip() for line in output.splitlines() if line.strip()]


def changed_paths(*, staged: bool, pathspec: list[str] | None = None) -> list[str]:
    args = ["diff", "--name-only"]
    if staged:
        args.append("--cached")
    if pathspec:
        args += ["--", *pathspec]
    return [line.strip() for line in (run(*args) or "").splitlines() if line.strip()]


def untracked_paths(pathspec: list[str] | None = None) -> list[str]:
    args = ["ls-files", "--others", "--exclude-standard"]
    if pathspec:
        args += ["--", *pathspec]
    return [line.strip() for line in (run(*args) or "").splitlines() if line.strip()]


def unmerged_paths() -> list[str]:
    """Paths still in a conflicted merge state, de-duplicated across stages."""
    output = run("ls-files", "-u") or ""
    paths: list[str] = []
    for line in output.splitlines():
        if "\t" in line:
            path = line.split("\t", 1)[1].strip()
            if path and path not in paths:
                paths.append(path)
    return paths


def staged_line_delta(pathspec: list[str] | None = None) -> tuple[int, int]:
    """Return (insertions, deletions) staged for ``pathspec``."""
    args = ["diff", "--cached", "--numstat"]
    if pathspec:
        args += ["--", *pathspec]
    insertions = deletions = 0
    for line in (run(*args) or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed = parts[0], parts[1]
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return insertions, deletions
