"""Local pattern bank for recognizable support errors."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

BANK_FILENAME = "support_error_bank.json"
BANK_VERSION = 2

_LOG_PREFIX_RE = re.compile(
    r"^(?:\[[^\]]+\]\s*)*(?:\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\s*)?"
    r"(?:(?:Info|Debug|Warning|Warn|Error|Critical|Trace)\s*\|\s*)+",
    re.IGNORECASE,
)
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?\b")
_VERSION_RE = re.compile(r"\b\d+(?:\.\d+){1,4}\b")
_NUMBER_RE = re.compile(r"\b\d+\b")
_POSIX_FILE_PATH_RE = re.compile(
    r"(?<!\w)/(?:Users|Applications|Library|System|Volumes|tmp|var|private|home)/.+?"
    r"(?:\.(?:onnx|json|tzlog|log|dylib|dll|exe|app|png|jpg|jpeg|tif|tiff|heic|mov|mp4))\b",
    re.IGNORECASE,
)
_WINDOWS_FILE_PATH_RE = re.compile(
    r"\b[A-Za-z]:\\.+?(?:\.(?:onnx|json|tzlog|log|dll|exe|png|jpg|jpeg|tif|tiff|heic|mov|mp4))\b",
    re.IGNORECASE,
)
_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?:Users|Applications|Library|System|Volumes|tmp|var|private|home)/[^\s,;:)]+")
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s,;:)]+")
_QUOTED_PATH_RE = re.compile(r"(['\"])(?:[A-Za-z]:\\|/)[^'\"]+\1")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_WHITESPACE_RE = re.compile(r"\s+")


def default_bank_path() -> Path:
    return Path(__file__).resolve().parent / BANK_FILENAME


def patternize_error_text(line: str) -> str:
    """Turn a user-instance log line into a reusable support pattern."""
    s = line.strip()
    if not s:
        return ""
    s = _LOG_PREFIX_RE.sub("", s)
    s = _EMAIL_RE.sub("<EMAIL>", s)
    s = _QUOTED_PATH_RE.sub("<PATH>", s)
    s = _POSIX_FILE_PATH_RE.sub("<PATH>", s)
    s = _WINDOWS_FILE_PATH_RE.sub("<PATH>", s)
    s = _POSIX_PATH_RE.sub("<PATH>", s)
    s = _WINDOWS_PATH_RE.sub("<PATH>", s)
    s = _UUID_RE.sub("<ID>", s)
    s = _HEX_RE.sub("<HEX>", s)
    s = _ISO_DATE_RE.sub("<DATE>", s)
    s = _VERSION_RE.sub("<VERSION>", s)
    s = _NUMBER_RE.sub("<NUM>", s)
    s = _WHITESPACE_RE.sub(" ", s).strip(" -\t")
    return s


def normalize_error_signature(line: str) -> str:
    """Normalize a line or pattern for stable deduplication and matching."""
    return patternize_error_text(line).casefold()


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
    pattern_text: str
    count: int
    first_seen: str
    last_seen: str
    seen_by_date: dict[str, int]

    @property
    def sample_text(self) -> str:
        return self.pattern_text


def _coerce_entry(key: str, row: dict[str, Any]) -> BankEntryView:
    pattern_text = str(row.get("pattern_text") or row.get("sample_text") or "")
    seen_by_date_raw = row.get("seen_by_date", {})
    seen_by_date = {}
    if isinstance(seen_by_date_raw, dict):
        seen_by_date = {str(day): int(count) for day, count in seen_by_date_raw.items()}
    return BankEntryView(
        key=key,
        pattern_text=pattern_text,
        count=int(row.get("count", 0)),
        first_seen=str(row.get("first_seen", "")),
        last_seen=str(row.get("last_seen", "")),
        seen_by_date=seen_by_date,
    )


def record_lines(lines: list[str], path: Path | None = None, *, seen_on: str | None = None) -> tuple[int, Path]:
    """Merge lines into pattern records. Exact user-instance lines are not stored."""
    data = load_bank(path)
    entries: dict[str, Any] = data["entries"]
    day = seen_on or date.today().isoformat()
    new_keys = 0

    for raw in lines:
        pattern_text = patternize_error_text(raw)
        norm = pattern_text.casefold()
        if not norm:
            continue
        key = signature_key(norm)
        if key not in entries:
            entries[key] = {
                "pattern_text": pattern_text,
                "count": 0,
                "first_seen": day,
                "last_seen": day,
                "seen_by_date": {},
            }
            new_keys += 1
        row = entries[key]
        row["pattern_text"] = str(row.get("pattern_text") or pattern_text)
        row["count"] = int(row.get("count", 0)) + 1
        row["last_seen"] = day
        row.setdefault("first_seen", day)
        seen_by_date = row.setdefault("seen_by_date", {})
        if isinstance(seen_by_date, dict):
            seen_by_date[day] = int(seen_by_date.get(day, 0)) + 1

    return new_keys, save_bank(data, path)


def list_entries_sorted(path: Path | None = None, *, limit: int | None = None) -> list[BankEntryView]:
    data = load_bank(path)
    rows: list[BankEntryView] = []
    for key, row in data["entries"].items():
        if isinstance(row, dict):
            rows.append(_coerce_entry(key, row))
    rows.sort(key=lambda r: (-r.count, r.pattern_text))
    return rows[:limit] if limit is not None else rows


def search_entries(query: str, path: Path | None = None, *, limit: int = 30) -> list[BankEntryView]:
    q = normalize_error_signature(query)
    if not q:
        return list_entries_sorted(path, limit=limit)
    words = [w for w in q.split() if len(w) > 1 and not w.startswith("<")]
    if not words:
        return list_entries_sorted(path, limit=limit)
    scored: list[tuple[int, BankEntryView]] = []
    for entry in list_entries_sorted(path):
        hay = normalize_error_signature(entry.pattern_text)
        if all(w in hay for w in words):
            score = sum(hay.count(w) for w in words)
            scored.append((score, entry))
    scored.sort(key=lambda t: (-t[0], -t[1].count, t[1].pattern_text))
    return [e for _, e in scored[:limit]]


def find_known_match(line: str, path: Path | None = None) -> BankEntryView | None:
    norm = normalize_error_signature(line)
    if not norm:
        return None
    key = signature_key(norm)
    direct = load_bank(path)["entries"].get(key)
    if isinstance(direct, dict):
        return _coerce_entry(key, direct)

    best: BankEntryView | None = None
    for entry in list_entries_sorted(path):
        pattern = normalize_error_signature(entry.pattern_text)
        if pattern and (pattern in norm or norm in pattern):
            if best is None or entry.count > best.count:
                best = entry
    return best


def known_matches_for_lines(lines: list[str], path: Path | None = None) -> list[BankEntryView]:
    matches: dict[str, BankEntryView] = {}
    for line in lines:
        match = find_known_match(line, path)
        if match:
            matches[match.key] = match
    return sorted(matches.values(), key=lambda m: (-m.count, m.pattern_text))


def format_trends_text(path: Path | None = None, *, limit: int = 100) -> str:
    lines: list[str] = []
    for entry in list_entries_sorted(path, limit=limit):
        lines.append(f"{entry.count:>5}  [{entry.last_seen}]  {entry.pattern_text}")
    if not lines:
        return "No entries in the error bank yet. Record reviewed patterns from Step 3."
    return "\n".join(lines)
