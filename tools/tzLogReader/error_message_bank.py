"""Local bank of notable support error lines for trend spotting and ticket entry."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

BANK_FILENAME = "support_error_bank.json"
BANK_VERSION = 1

# Strip common tzlog prefixes so the same failure groups together across tickets.
_LOG_PREFIX_RE = re.compile(r"^(?:\[[^\]]+\]\s*)+\s*")


def default_bank_path() -> Path:
    return Path(__file__).resolve().parent / BANK_FILENAME


def normalize_error_signature(line: str) -> str:
    """Normalize a log line for stable deduplication keys."""
    s = line.strip()
    if not s:
        return ""
    s = _LOG_PREFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold()


def signature_key(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_bank(path: Path | None = None) -> dict[str, Any]:
    p = path or default_bank_path()
    if not p.is_file():
        return {"version": BANK_VERSION, "entries": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": BANK_VERSION, "entries": {}}
    if not isinstance(data, dict) or "entries" not in data:
        return {"version": BANK_VERSION, "entries": {}}
    entries = data["entries"]
    if not isinstance(entries, dict):
        return {"version": BANK_VERSION, "entries": {}}
    return {"version": BANK_VERSION, "entries": entries}


def save_bank(data: dict[str, Any], path: Path | None = None) -> Path:
    p = path or default_bank_path()
    payload = {"version": BANK_VERSION, "entries": data.get("entries", {})}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


@dataclass(frozen=True)
class BankEntryView:
    key: str
    sample_text: str
    count: int
    first_seen: str
    last_seen: str


def record_lines(lines: list[str], path: Path | None = None) -> tuple[int, Path]:
    """Merge non-empty lines into the bank. Returns (number of new keys added, path written)."""
    data = load_bank(path)
    entries: dict[str, Any] = data["entries"]
    today = date.today().isoformat()
    new_keys = 0

    for raw in lines:
        text = raw.strip()
        if not text:
            continue
        norm = normalize_error_signature(text)
        if not norm:
            continue
        key = signature_key(norm)
        if key not in entries:
            entries[key] = {
                "sample_text": text,
                "count": 0,
                "first_seen": today,
                "last_seen": today,
            }
            new_keys += 1
        row = entries[key]
        row["count"] = int(row.get("count", 0)) + 1
        row["last_seen"] = today
        if "first_seen" not in row:
            row["first_seen"] = today
        # Keep a readable example (prefer longer samples for context).
        prev = str(row.get("sample_text", ""))
        if len(text) > len(prev):
            row["sample_text"] = text

    out = save_bank(data, path)
    return new_keys, out


def list_entries_sorted(path: Path | None = None, *, limit: int | None = None) -> list[BankEntryView]:
    data = load_bank(path)
    rows: list[BankEntryView] = []
    for key, row in data["entries"].items():
        if not isinstance(row, dict):
            continue
        rows.append(
            BankEntryView(
                key=key,
                sample_text=str(row.get("sample_text", "")),
                count=int(row.get("count", 0)),
                first_seen=str(row.get("first_seen", "")),
                last_seen=str(row.get("last_seen", "")),
            )
        )
    rows.sort(key=lambda r: (-r.count, r.sample_text))
    if limit is not None:
        rows = rows[:limit]
    return rows


def search_entries(query: str, path: Path | None = None, *, limit: int = 30) -> list[BankEntryView]:
    q = normalize_error_signature(query)
    if not q:
        return list_entries_sorted(path, limit=limit)
    words = [w for w in q.split() if len(w) > 1]
    if not words:
        return list_entries_sorted(path, limit=limit)
    scored: list[tuple[int, BankEntryView]] = []
    for entry in list_entries_sorted(path):
        hay = normalize_error_signature(entry.sample_text)
        if not hay:
            continue
        if all(w in hay for w in words):
            score = sum(hay.count(w) for w in words)
            scored.append((score, entry))
    scored.sort(key=lambda t: (-t[0], -t[1].count, t[1].sample_text))
    return [e for _, e in scored[:limit]]


def format_trends_text(path: Path | None = None, *, limit: int = 100) -> str:
    lines: list[str] = []
    for entry in list_entries_sorted(path, limit=limit):
        lines.append(f"{entry.count:>5}  [{entry.last_seen}]  {entry.sample_text}")
    if not lines:
        return "No entries in the error bank yet. Record lines from Step 3 or paste into the bank."
    return "\n".join(lines)
