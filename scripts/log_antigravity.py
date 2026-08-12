#!/usr/bin/env python3
"""
Antigravity IDE log scanner — extracts the exact user-typed prompts from
local Antigravity conversation databases and transcripts.

Sources:
  1. Primary (Antigravity IDE / SQLite):
     ~/.gemini/antigravity-ide/conversations/<conv_id>.db
     (and fallback to ~/.gemini/antigravity/conversations/<conv_id>.db)
  2. Legacy / Fallback (Plaintext Transcripts):
     ~/.gemini/antigravity-ide/brain/<conv_id>/.system_generated/logs/transcript.jsonl

Usage:
  python scripts/log_antigravity.py --auto            # default: last 24h
  python scripts/log_antigravity.py --hours 72
  python scripts/log_antigravity.py --all             # every conv, no cutoff
  python scripts/log_antigravity.py --conv-id <id>    # one conversation
  python scripts/log_antigravity.py --dry-run         # preview only

Env overrides:
  ANTIGRAVITY_BRAIN_DIR  point at a different brain/ directory
  ANTIGRAVITY_CONV_DIR   point at a different conversations/ directory
  AI_LOG_DIR             where session.jsonl is written (default: .ai-log)
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Fix Windows console encoding so VN diacritics in prompts print cleanly.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VN_TZ = timezone(timedelta(hours=7))
GEMINI_HOME = Path.home() / ".gemini"

BRAIN_CANDIDATES = (
    GEMINI_HOME / "antigravity-ide" / "brain",
    GEMINI_HOME / "antigravity" / "brain",
)

CONV_CANDIDATES = (
    GEMINI_HOME / "antigravity-ide" / "conversations",
    GEMINI_HOME / "antigravity" / "conversations",
)

USER_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.DOTALL)
AUX_BLOCK_RE = re.compile(
    r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>"
    r".*?"
    r"</(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE|SYSTEM_MESSAGE)>",
    re.DOTALL,
)


def git(cmd: str) -> str:
    try:
        return subprocess.check_output(
            cmd.split(), shell=False, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def get_brain_dirs() -> list[Path]:
    """Brain directories to scan, newest layout first."""
    env = os.environ.get("ANTIGRAVITY_BRAIN_DIR")
    if env:
        p = Path(env)
        return [p] if p.exists() else []
    return [p for p in BRAIN_CANDIDATES if p.exists()]


def get_conv_dirs() -> list[Path]:
    """Conversations directories containing SQLite .db files."""
    env = os.environ.get("ANTIGRAVITY_CONV_DIR")
    if env:
        p = Path(env)
        return [p] if p.exists() else []
    return [p for p in CONV_CANDIDATES if p.exists()]


# ---------------------------------------------------------------------------
# Path normalization + repo gating
# ---------------------------------------------------------------------------

def _normalize(p: str) -> str:
    """Lower-case + backslash form, no trailing separator."""
    if not p:
        return ""
    return p.strip().lower().replace("/", "\\").rstrip("\\")


def _unquote_arg(val):
    """Antigravity stores tool args as JSON-encoded strings. Unwrap them."""
    if not isinstance(val, str):
        return val
    val = val.strip()
    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val[1:-1]
    return val


def _conv_cwds(transcript: Path) -> set[str]:
    """All Cwd values that appear in tool calls inside this transcript."""
    cwds: set[str] = set()
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for tc in (entry.get("tool_calls") or []):
                    args = tc.get("args") or {}
                    cwd = args.get("Cwd") or args.get("cwd")
                    cwd = _unquote_arg(cwd)
                    if isinstance(cwd, str):
                        n = _normalize(cwd)
                        if n:
                            cwds.add(n)
    except OSError:
        pass
    return cwds


def _conv_matches_repo(cwds: set[str], repo_root_n: str) -> bool:
    """True if any cwd is equal to, ancestor of, or descendant of the repo."""
    if not repo_root_n or not cwds:
        return False
    for cwd in cwds:
        if cwd == repo_root_n:
            return True
        if cwd.startswith(repo_root_n + "\\"):
            return True
        if repo_root_n.startswith(cwd + "\\"):
            return True
    return False


# ---------------------------------------------------------------------------
# Protobuf / SQLite decoding helpers
# ---------------------------------------------------------------------------

def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    res = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return res, offset


def extract_timestamp_from_meta(meta: bytes | None) -> str:
    if not meta:
        return ""
    idx = meta.find(b"\x08")
    while idx != -1 and idx + 5 <= len(meta):
        try:
            val, _ = decode_varint(meta, idx + 1)
            if 1700000000 < val < 2500000000:
                dt = datetime.fromtimestamp(val, timezone.utc)
                return dt.isoformat()
        except Exception:
            pass
        idx = meta.find(b"\x08", idx + 1)
    return ""


def extract_prompt_from_payload(payload: bytes) -> str:
    if not payload:
        return ""
    idx = payload.find(b"\x9a\x01")
    if idx != -1:
        try:
            sub_len, sub_start = decode_varint(payload, idx + 2)
            sub_bytes = payload[sub_start : sub_start + sub_len]
            if sub_bytes.startswith(b"\x12"):
                text_len, text_start = decode_varint(sub_bytes, 1)
                prompt_bytes = sub_bytes[text_start : text_start + text_len]
                text = prompt_bytes.decode("utf-8")
                m = USER_REQUEST_RE.search(text)
                if m:
                    return m.group(1).strip()
                cleaned = AUX_BLOCK_RE.sub("", text).strip()
                if cleaned:
                    return cleaned
        except Exception:
            pass

    try:
        raw_text = payload.decode("utf-8", errors="ignore")
        m = USER_REQUEST_RE.search(raw_text)
        if m:
            return m.group(1).strip()
        cleaned = AUX_BLOCK_RE.sub("", raw_text).strip()
        cleaned = re.sub(r"^[\x00-\x1f]+", "", cleaned).strip()
        return cleaned
    except Exception:
        return ""


def extract_user_prompt(content: str) -> str:
    """Pull the text between <USER_REQUEST>...</USER_REQUEST>."""
    if not isinstance(content, str):
        return ""
    m = USER_REQUEST_RE.search(content)
    if m:
        return m.group(1).strip()
    cleaned = AUX_BLOCK_RE.sub("", content)
    return cleaned.strip()


def get_logged_entry_ids(log_file: Path) -> set[str]:
    logged: set[str] = set()
    if not log_file.exists():
        return logged
    with open(log_file, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = entry.get("entry_id", "")
            if eid:
                logged.add(eid)
    return logged


# ---------------------------------------------------------------------------
# Scanning SQLite Conversations
# ---------------------------------------------------------------------------

def iter_sqlite_inputs(conv_dirs: list[Path], cutoff: datetime | None,
                       only_conv: str | None, repo_root_n: str):
    """Yield user-input dicts from SQLite conversation databases."""
    seen_convs = set()
    for cdir in conv_dirs:
        for db_file in sorted(cdir.glob("*.db")):
            conv_id = db_file.stem
            if only_conv and conv_id != only_conv:
                continue
            if conv_id in seen_convs:
                continue
            seen_convs.add(conv_id)

            try:
                uri = f"file:{db_file.as_posix()}?mode=ro"
                conn = sqlite3.connect(uri, uri=True, timeout=3.0)
            except Exception:
                try:
                    conn = sqlite3.connect(str(db_file), timeout=3.0)
                except Exception:
                    continue

            try:
                cursor = conn.cursor()

                # Gating by repo root
                if repo_root_n:
                    matched = False
                    try:
                        cursor.execute("SELECT data FROM trajectory_metadata_blob LIMIT 5")
                        for (blob,) in cursor.fetchall():
                            if blob:
                                blob_str = _normalize(blob.decode("utf-8", errors="ignore"))
                                if repo_root_n in blob_str:
                                    matched = True
                                    break
                    except Exception:
                        pass

                    if not matched:
                        try:
                            cursor.execute("SELECT step_payload FROM steps LIMIT 15")
                            for (payload,) in cursor.fetchall():
                                if payload:
                                    p_str = _normalize(payload.decode("utf-8", errors="ignore"))
                                    if repo_root_n in p_str:
                                        matched = True
                                        break
                        except Exception:
                            pass

                    if not matched:
                        continue

                # Query step_type 14 (USER_INPUT)
                cursor.execute(
                    "SELECT idx, step_type, metadata, step_payload "
                    "FROM steps WHERE step_type = '14' OR step_type = 14 "
                    "ORDER BY CAST(idx AS INTEGER)"
                )
                for idx, step_type, meta, payload in cursor.fetchall():
                    if not payload:
                        continue
                    ts = extract_timestamp_from_meta(meta)
                    if cutoff and ts:
                        try:
                            ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if ts_dt < cutoff:
                                continue
                        except ValueError:
                            pass

                    prompt_text = extract_prompt_from_payload(payload)
                    if len(prompt_text) < 2:
                        continue

                    yield {
                        "conv_id": conv_id,
                        "step_index": int(idx),
                        "timestamp": ts,
                        "text": prompt_text,
                    }
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# Iterating user inputs (SQLite primary + JSONL fallback)
# ---------------------------------------------------------------------------

def iter_user_inputs(brain_dirs: list[Path], conv_dirs: list[Path],
                      cutoff: datetime | None, only_conv: str | None,
                      repo_root_n: str):
    """Yield user inputs from SQLite databases and/or transcript JSONLs."""
    yielded_any = False
    for item in iter_sqlite_inputs(conv_dirs, cutoff, only_conv, repo_root_n):
        yielded_any = True
        yield item

    # Fallback to transcript.jsonl files if SQLite returned nothing
    if not yielded_any:
        for brain in brain_dirs:
            for conv_dir in sorted(brain.iterdir()):
                if not conv_dir.is_dir():
                    continue
                if only_conv and conv_dir.name != only_conv:
                    continue
                transcript = (
                    conv_dir / ".system_generated" / "logs" / "transcript.jsonl"
                )
                if not transcript.exists() or transcript.stat().st_size == 0:
                    continue

                cwds = _conv_cwds(transcript)
                if repo_root_n and not _conv_matches_repo(cwds, repo_root_n):
                    continue

                with open(transcript, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if (entry.get("type") != "USER_INPUT"
                                or entry.get("source") != "USER_EXPLICIT"):
                            continue

                        ts = entry.get("created_at") or ""
                        if cutoff and ts:
                            try:
                                ts_dt = datetime.fromisoformat(
                                    ts.replace("Z", "+00:00")
                                )
                                if ts_dt < cutoff:
                                    continue
                            except ValueError:
                                pass

                        text = extract_user_prompt(entry.get("content", ""))
                        if len(text) < 2:
                            continue

                        yield {
                            "conv_id": conv_dir.name,
                            "step_index": int(entry.get("step_index", 0)),
                            "timestamp": ts,
                            "text": text,
                        }


# ---------------------------------------------------------------------------
# Emitting entries
# ---------------------------------------------------------------------------

def build_entry(msg: dict, repo: str, branch: str, commit: str,
                student: str) -> dict:
    ts = msg["timestamp"]
    if ts.endswith("Z") or "+00:00" in ts:
        try:
            ts = (
                datetime.fromisoformat(ts.replace("Z", "+00:00"))
                .astimezone(VN_TZ)
                .isoformat()
            )
        except ValueError:
            pass

    return {
        "ts": ts or datetime.now(VN_TZ).isoformat(),
        "tool": "antigravity",
        "event": "UserPrompt",
        "entry_id": f"antigravity-{msg['conv_id']}-{msg['step_index']:05d}",
        "session_id": msg["conv_id"],
        "model": "gemini",
        "repo": repo,
        "branch": branch,
        "commit": commit,
        "student": student,
        "prompt": msg["text"],
        "response_summary": "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract user prompts from Antigravity IDE transcripts"
                    " into .ai-log/session.jsonl."
    )
    parser.add_argument("--auto", action="store_true",
                        help="Default mode: scan recent conversations.")
    parser.add_argument("--hours", type=int, default=24,
                        help="Window in hours when scanning (default: 24).")
    parser.add_argument("--all", action="store_true",
                        help="Ignore the time window; scan everything.")
    parser.add_argument("--conv-id",
                        help="Limit to a single conversation id.")
    parser.add_argument("--no-repo-filter", action="store_true",
                        help="Don't filter conversations by current repo.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be logged, don't write.")
    # Legacy positional args from old log_manual.py callers.
    parser.add_argument("summary", nargs="?", help=argparse.SUPPRESS)
    parser.add_argument("model", nargs="?", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.summary and not (args.auto or args.conv_id or args.all):
        _legacy_log(args.summary, args.model or "gemini")
        return

    brain_dirs = get_brain_dirs()
    conv_dirs = get_conv_dirs()
    if not brain_dirs and not conv_dirs:
        print("[antigravity-log] No Antigravity brain/ or conversations/ directory found.",
              file=sys.stderr)
        sys.exit(0)

    log_dir = Path(os.environ.get("AI_LOG_DIR", ".ai-log"))
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "session.jsonl"
    logged_ids = get_logged_entry_ids(log_file)

    cutoff = None
    if not args.all:
        cutoff = datetime.now(tz=VN_TZ) - timedelta(hours=args.hours)

    repo_root_n = "" if args.no_repo_filter else _normalize(str(Path.cwd()))

    repo = git("git remote get-url origin").split("/")[-1].replace(".git", "")
    branch = git("git rev-parse --abbrev-ref HEAD")
    commit = git("git rev-parse --short HEAD")
    student = git("git config user.email") or os.environ.get(
        "USERNAME", os.environ.get("USER", "unknown"))

    new_entries: list[dict] = []
    for msg in iter_user_inputs(brain_dirs, conv_dirs, cutoff, args.conv_id, repo_root_n):
        entry = build_entry(msg, repo or Path.cwd().name, branch, commit,
                            student)
        if entry["entry_id"] in logged_ids:
            continue
        new_entries.append(entry)
        logged_ids.add(entry["entry_id"])

    if not new_entries:
        scope = "all" if args.all else f"{args.hours}h"
        repo_note = "any repo" if args.no_repo_filter else f"repo={repo_root_n or '(unknown)'}"
        print(f"[antigravity-log] No new prompts ({repo_note}, window={scope}).",
              file=sys.stderr)
        sys.exit(0)

    if args.dry_run:
        print(f"\n[antigravity-log] DRY RUN — would log "
              f"{len(new_entries)} entries:\n")
        for e in new_entries:
            preview = e["prompt"].replace("\n", " ")[:120]
            print(f"  [{e['ts'][:19]}] {preview}")
        sys.exit(0)

    with open(log_file, "a", encoding="utf-8") as f:
        for e in new_entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"[antigravity-log] Logged {len(new_entries)} prompt(s) from "
          f"Antigravity IDE.", file=sys.stderr)


def _legacy_log(summary: str, model: str) -> None:
    ts = datetime.now(VN_TZ).isoformat()
    entry = {
        "ts": ts,
        "tool": "antigravity",
        "event": "TaskComplete",
        "entry_id": f"antigravity-{datetime.now(VN_TZ).strftime('%Y%m%d-%H%M%S')}",
        "model": model,
        "repo": git("git remote get-url origin").split("/")[-1].replace(".git", ""),
        "branch": git("git rev-parse --abbrev-ref HEAD"),
        "commit": git("git rev-parse --short HEAD"),
        "student": git("git config user.email") or os.environ.get(
            "USERNAME", os.environ.get("USER", "unknown")),
        "prompt": summary[:1000],
        "response_summary": f"[Antigravity] {summary[:500]}",
    }
    log_dir = Path(os.environ.get("AI_LOG_DIR", ".ai-log"))
    log_dir.mkdir(exist_ok=True)
    with open(log_dir / "session.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[antigravity-log] Logged manual: {summary[:80]}...", file=sys.stderr)


if __name__ == "__main__":
    main()
