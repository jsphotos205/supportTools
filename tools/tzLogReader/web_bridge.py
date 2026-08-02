#!/usr/bin/env python3
"""JSON bridge used by the local Node web UI."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback
from typing import Any
from contextlib import redirect_stdout

import error_message_bank as error_bank
from error_scanner import scan_path_for_error_candidates
from log_parser import (
    build_log_archive_from_path,
    format_session_scan_report,
    process_issue_logs_from_path,
    process_system_info_from_path,
    report_destination_dirs,
    write_report_to_txt,
)


def path_from_user(value: str) -> Path:
    return Path(value.strip().strip("'\"").replace("\\ ", " ")).expanduser().resolve()


def display_root_for_tzlogs(input_path: Path, extracted_dirs: list[Path]) -> Path:
    if extracted_dirs:
        return extracted_dirs[0].resolve()
    if input_path.is_dir():
        return input_path.resolve()
    return input_path.resolve().parent


def bank_entry_to_json(entry: error_bank.BankEntryView) -> dict[str, Any]:
    return {
        "key": entry.key,
        "patternText": entry.pattern_text,
        "count": entry.count,
        "firstSeen": entry.first_seen,
        "lastSeen": entry.last_seen,
        "seenByDate": entry.seen_by_date,
    }


def handle_system_info(payload: dict[str, Any]) -> dict[str, Any]:
    input_path = path_from_user(str(payload.get("path", "")))
    if not input_path.exists():
        raise FileNotFoundError(f"Path not found: {input_path}")
    report, _entry, extracted_dirs, tzlog_files = process_system_info_from_path(input_path)
    root = display_root_for_tzlogs(input_path, extracted_dirs)
    for dest in report_destination_dirs(extracted_dirs, input_path):
        write_report_to_txt(report, dest, "support_system_information.txt")
    return {
        "report": report,
        "supportPath": str(input_path),
        "extractedDirs": [str(path) for path in extracted_dirs],
        "displayRoot": str(root),
        "tzlogFiles": [
            {
                "path": str(path),
                "label": str(path.resolve().relative_to(root)) if str(path.resolve()).startswith(str(root)) else str(path),
            }
            for path in tzlog_files
        ],
    }


def handle_scan(payload: dict[str, Any]) -> dict[str, Any]:
    path = path_from_user(str(payload.get("path", "")))
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    candidates = scan_path_for_error_candidates(path)
    return {
        "candidates": [
            {
                "patternText": candidate.pattern_text,
                "exampleText": candidate.example_text,
                "countInLog": candidate.count_in_log,
                "score": candidate.score,
                "knownMatch": bank_entry_to_json(candidate.known_match) if candidate.known_match else None,
            }
            for candidate in candidates
        ]
    }


def handle_session_scan(payload: dict[str, Any]) -> dict[str, Any]:
    path = path_from_user(str(payload.get("path", "")))
    if not path.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    archive = build_log_archive_from_path(path)
    return {
        "archive": archive.to_dict(),
        "report": format_session_scan_report(archive),
    }


def handle_bank_list(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    entries = error_bank.search_entries(query, limit=80) if query else error_bank.list_entries_sorted(limit=80)
    return {"entries": [bank_entry_to_json(entry) for entry in entries]}


def handle_bank_record(payload: dict[str, Any]) -> dict[str, Any]:
    patterns = [str(item) for item in payload.get("patterns", []) if str(item).strip()]
    new_keys, path = error_bank.record_lines(patterns)
    return {"newKeys": new_keys, "path": str(path), "entries": [bank_entry_to_json(entry) for entry in error_bank.list_entries_sorted(limit=80)]}


def handle_build_report(payload: dict[str, Any]) -> dict[str, Any]:
    issue_path = path_from_user(str(payload.get("issuePath", "")))
    if not issue_path.exists():
        raise FileNotFoundError(f"Path not found: {issue_path}")
    notable_patterns = [str(item).strip() for item in payload.get("notablePatterns", []) if str(item).strip()]
    notable_text = "Not provided"
    if notable_patterns:
        notable_text = "\n".join(f"- {pattern}" for pattern in notable_patterns)
    known_matches = error_bank.known_matches_for_lines(notable_patterns) if notable_patterns else []
    issue_report = process_issue_logs_from_path(issue_path, notable_text, known_matches)
    system_report = str(payload.get("systemReport", "")).strip()
    full_report = f"{system_report}\n{issue_report}" if system_report else issue_report

    support_path_raw = str(payload.get("supportPath", "")).strip()
    extracted_dirs = [Path(item) for item in payload.get("extractedDirs", []) if str(item).strip()]
    fallback = path_from_user(support_path_raw) if support_path_raw else issue_path
    for dest in report_destination_dirs(extracted_dirs, fallback):
        write_report_to_txt(full_report, dest, "support_full_tzlog_report.txt")

    if payload.get("recordPatterns"):
        error_bank.record_lines(notable_patterns)

    return {
        "report": full_report,
        "knownMatches": [bank_entry_to_json(entry) for entry in known_matches],
    }


def handle_read_log(payload: dict[str, Any]) -> dict[str, Any]:
    path = path_from_user(str(payload.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError(f"Log file not found: {path}")
    max_chars = int(payload.get("maxChars", 250_000))
    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > max_chars
    return {"text": text[:max_chars], "truncated": truncated, "path": str(path)}


HANDLERS = {
    "systemInfo": handle_system_info,
    "scan": handle_scan,
    "sessionScan": handle_session_scan,
    "bankList": handle_bank_list,
    "bankRecord": handle_bank_record,
    "buildReport": handle_build_report,
    "readLog": handle_read_log,
}


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        action = str(payload.get("action", ""))
        if action not in HANDLERS:
            raise ValueError(f"Unknown action: {action}")
        with redirect_stdout(sys.stderr):
            handler_result = HANDLERS[action](payload)
        result = {"ok": True, **handler_result}
    except Exception as error:
        result = {
            "ok": False,
            "error": str(error),
            "traceback": traceback.format_exc(),
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
