"""What EvalGate is allowed to look at.

EvalGate lives inside the repository it grades, so any evaluator that walks the
whole tree will eventually read EvalGate's own files and report what it finds as
a fact about the *product*.  That is not hypothetical: on 2026-08-22 the secret
scanner raised a CRITICAL, release-blocking ``HG-S6`` twice in one afternoon --
first against a test fixture that used a realistic credential shape, then against
the report section written to document that very fix.

Both findings were true statements about the repository and completely useless as
statements about the product.  Worse, they were louder than the real findings:
the score moved 30.35 -> 21.52 on the second one, which is a bigger swing than any
genuine defect produced that day.

So the rule is simple and absolute: **an evaluator measures the product, never the
instrument.**  A defect in EvalGate is caught by EvalGate's own test suite, which
is where it belongs -- the release gate is not the place to report that a test
fixture contains a plausible-looking string.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Directories that are the instrument rather than the product. Kept as a tuple of
#: repo-relative POSIX prefixes so it works against both Path objects and the raw
#: strings ``git ls-files`` returns.
INSTRUMENT_PREFIXES: tuple[str, ...] = ("evalgate/",)


def _relative(path: Path | str) -> str:
    """Repo-relative POSIX form, or the input unchanged when it is outside the repo."""
    if isinstance(path, str):
        return path.replace("\\", "/").lstrip("./")
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except (ValueError, OSError):
        return path.as_posix().replace("\\", "/")


def is_instrument(path: Path | str) -> bool:
    """True when the path belongs to EvalGate itself rather than to the product."""
    rel = _relative(path)
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in INSTRUMENT_PREFIXES)


def product_only(paths: list[Path]) -> list[Path]:
    """Drop everything that is part of the instrument.

    Use this in any evaluator that enumerates files without an explicit product
    root. Evaluators already scoped to ``src/`` need nothing -- they cannot reach
    EvalGate in the first place.
    """
    return [p for p in paths if not is_instrument(p)]
