"""Automatic candidate detection for notable tzlog errors."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re

import error_message_bank as error_bank
from log_parser import discover_log_files

ERROR_WORDS = (
    "error",
    "exception",
    "failed",
    "failure",
    "fatal",
    "critical",
    "crash",
    "denied",
    "timeout",
    "unable",
    "cannot",
    "invalid",
    "missing",
    "corrupt",
    "unsupported",
    "out of memory",
    "oom",
)
DOMAIN_WORDS = (
    "onnx",
    "model",
    "cuda",
    "metal",
    "gpu",
    "vram",
    "opencv",
    "filesystem",
    "permission",
    "download",
    "license",
    "activation",
    "render",
    "export",
)
NULLISH_RE = re.compile(
    r"^\s*(?:error|exception|last_?error|message|reason|result|status)?\s*[:=]\s*"
    r"(?:null|none|nil|undefined|nan|\{\}|\[\]|0|false)\s*$",
    re.IGNORECASE,
)
LOW_SIGNAL_RE = re.compile(
    r"\b(?:undefined|null|none)\b.*\b(?:undefined|null|none)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ErrorCandidate:
    pattern_text: str
    example_text: str
    count_in_log: int
    score: int
    known_match: error_bank.BankEntryView | None


def clean_log_line(line: str) -> str:
    return line.strip().replace("\x00", "")


def has_term(text: str, term: str) -> bool:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text, re.IGNORECASE) is not None


def is_low_signal_line(line: str) -> bool:
    text = clean_log_line(line)
    if len(text) < 12:
        return True
    if NULLISH_RE.search(text):
        return True
    lowered = text.casefold()
    if LOW_SIGNAL_RE.search(text) and not any(has_term(lowered, word) for word in DOMAIN_WORDS):
        return True
    if lowered.count("null") >= 3 or lowered.count("undefined") >= 3:
        return True
    return False


def candidate_score(line: str, known_match: error_bank.BankEntryView | None = None) -> int:
    lowered = line.casefold()
    score = 0
    score += sum(4 for word in ERROR_WORDS if has_term(lowered, word))
    score += sum(2 for word in DOMAIN_WORDS if has_term(lowered, word))
    if has_term(lowered, "traceback") or has_term(lowered, "stack trace"):
        score += 8
    if has_term(lowered, "out of memory") or has_term(lowered, "oom"):
        score += 8
    if known_match:
        score += 12
    if is_low_signal_line(line):
        score -= 20
    return score


def is_candidate_line(line: str) -> bool:
    if is_low_signal_line(line):
        return False
    lowered = line.casefold()
    return any(has_term(lowered, word) for word in ERROR_WORDS) or (
        any(has_term(lowered, word) for word in DOMAIN_WORDS) and has_term(lowered, "not found")
    )


def scan_log_file_for_error_candidates(file_path: Path, *, max_lines: int = 80_000) -> list[ErrorCandidate]:
    pattern_counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    known_by_pattern: dict[str, error_bank.BankEntryView] = {}

    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        for idx, raw_line in enumerate(handle):
            if idx >= max_lines:
                break
            text = clean_log_line(raw_line)
            known_match = error_bank.find_known_match(text)
            if not known_match and not is_candidate_line(text):
                continue
            pattern_text = error_bank.patternize_error_text(text)
            if not pattern_text or is_low_signal_line(pattern_text):
                continue
            pattern_counts[pattern_text] += 1
            examples.setdefault(pattern_text, text)
            if known_match:
                known_by_pattern[pattern_text] = known_match

    candidates: list[ErrorCandidate] = []
    for pattern_text, count in pattern_counts.items():
        known_match = known_by_pattern.get(pattern_text)
        score = candidate_score(pattern_text, known_match) + min(count, 10)
        if score < 4:
            continue
        candidates.append(
            ErrorCandidate(
                pattern_text=pattern_text,
                example_text=examples[pattern_text],
                count_in_log=count,
                score=score,
                known_match=known_match,
            )
        )

    candidates.sort(
        key=lambda c: (
            c.known_match is None,
            -c.score,
            -c.count_in_log,
            c.pattern_text,
        )
    )
    return candidates


def scan_path_for_error_candidates(path: Path, *, limit: int = 60) -> list[ErrorCandidate]:
    files = discover_log_files(path)
    merged: dict[str, ErrorCandidate] = {}

    for file_path in files:
        for candidate in scan_log_file_for_error_candidates(file_path):
            existing = merged.get(candidate.pattern_text)
            if existing is None:
                merged[candidate.pattern_text] = candidate
                continue
            merged[candidate.pattern_text] = ErrorCandidate(
                pattern_text=candidate.pattern_text,
                example_text=existing.example_text,
                count_in_log=existing.count_in_log + candidate.count_in_log,
                score=max(existing.score, candidate.score),
                known_match=existing.known_match or candidate.known_match,
            )

    candidates = list(merged.values())
    candidates.sort(
        key=lambda c: (
            c.known_match is None,
            -c.score,
            -c.count_in_log,
            c.pattern_text,
        )
    )
    return candidates[:limit]
