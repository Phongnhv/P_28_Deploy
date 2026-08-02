#!/usr/bin/env bash
# Cross-platform Python launcher for AI log hooks.
#
# Usage:
#   bash scripts/_pyrun.sh <script.py> [arguments...]
#
# The launcher prioritizes:
#   1. Currently activated virtual environment
#   2. venv/.venv inside the repository
#   3. Windows Python Launcher: py -3.11 / py -3
#   4. Valid python3 / python commands
#   5. Common Windows Python installation directories
#
# If no valid Python interpreter is found, exit successfully so the Git hook
# does not block git push.

set -u

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Convert a Windows path to a Git Bash path when necessary.
to_bash_path() {
  local input_path="$1"

  if command -v cygpath >/dev/null 2>&1; then
    cygpath -u "$input_path" 2>/dev/null || printf '%s\n' "$input_path"
  else
    printf '%s\n' "$input_path"
  fi
}

# Run an interpreter only when it actually works.
run_python() {
  local python_path="$1"
  shift

  if [ -x "$python_path" ] && "$python_path" --version >/dev/null 2>&1; then
    exec "$python_path" "$@"
  fi
}

# ---------------------------------------------------------------------------
# 1. Prioritize the currently activated virtual environment
# ---------------------------------------------------------------------------

if [ -n "${VIRTUAL_ENV:-}" ]; then
  VIRTUAL_ENV_BASH="$(to_bash_path "$VIRTUAL_ENV")"

  run_python "$VIRTUAL_ENV_BASH/Scripts/python.exe" "$@"
  run_python "$VIRTUAL_ENV_BASH/bin/python" "$@"
fi

# ---------------------------------------------------------------------------
# 2. Look for a virtual environment inside the repository
# ---------------------------------------------------------------------------

run_python "$REPO_ROOT/venv/Scripts/python.exe" "$@"
run_python "$REPO_ROOT/.venv/Scripts/python.exe" "$@"
run_python "$REPO_ROOT/venv/bin/python" "$@"
run_python "$REPO_ROOT/.venv/bin/python" "$@"

# ---------------------------------------------------------------------------
# 3. Use the Windows Python Launcher
# ---------------------------------------------------------------------------

if command -v py >/dev/null 2>&1; then
  if py -3.11 --version >/dev/null 2>&1; then
    exec py -3.11 "$@"
  fi

  if py -3 --version >/dev/null 2>&1; then
    exec py -3 "$@"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Use python3 or python only when the command really works
# ---------------------------------------------------------------------------

if command -v python3 >/dev/null 2>&1; then
  if python3 --version >/dev/null 2>&1; then
    exec python3 "$@"
  fi
fi

if command -v python >/dev/null 2>&1; then
  if python --version >/dev/null 2>&1; then
    exec python "$@"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Probe common Windows installation locations
# ---------------------------------------------------------------------------

shopt -s nullglob 2>/dev/null || true

for candidate in \
  /c/Users/*/AppData/Local/Programs/Python/Python311/python.exe \
  /c/Users/*/AppData/Local/Programs/Python/Python312/python.exe \
  /c/Users/*/AppData/Local/Programs/Python/Python313/python.exe \
  /c/Users/*/AppData/Local/Programs/Python/Python314/python.exe \
  /c/Users/*/AppData/Local/Programs/Python/Python*/python.exe \
  "/c/Program Files/Python"*/python.exe \
  "/c/Program Files (x86)/Python"*/python.exe \
  /c/Python*/python.exe; do

  if [ -x "$candidate" ] && "$candidate" --version >/dev/null 2>&1; then
    exec "$candidate" "$@"
  fi
done

shopt -u nullglob 2>/dev/null || true

echo "[ai-log] No valid Python interpreter was found. Skipping AI log hook." >&2
exit 0