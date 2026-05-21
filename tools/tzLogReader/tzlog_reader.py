#!/usr/bin/env python3
"""CLI entry point for Topaz tzlog support reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

import error_message_bank as error_bank
from gui_app import run_gui
from log_parser import (
    discover_log_files,
    extract_archive_files,
    format_issue_report,
    format_notable_error_report,
    format_notable_errors_for_report,
    format_system_info_report,
    get_system_info_search_paths,
    merge_system_info_entry_for_report,
    parse_tzlog,
    report_destination_dirs,
    write_report_to_txt,
)


def copy_to_clipboard(text: str) -> bool:
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
            return True
        if sys.platform == "win32":
            subprocess.run(["clip"], input=text, text=True, check=True)
            return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return False


def ask_for_issue_log_path(default_path: Path) -> Path:
    print("\nIssue log step.")
    print("Enter the path to the .tzlog file or folder that shows the reported issue.")
    print(f"Press Enter to use the original path: {default_path}")

    user_input = input("Issue log path: ").strip()
    if not user_input:
        return default_path
    return Path(user_input).expanduser().resolve()


def ask_for_notable_error_messages() -> str:
    print("\nNotable error pattern step.")
    print("Add reviewed notable error patterns found in the problem log.")
    print("Press Enter once on a blank line when finished.")

    lines: list[str] = []
    while True:
        user_input = input("Error pattern: ").strip()
        if not user_input:
            break
        lines.append(user_input)

    if not lines:
        return "Not provided"
    return format_notable_errors_for_report(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract support triage details from .tzlog files.")
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a .tzlog file, a directory containing .tzlog files, or a folder containing archive files.",
    )
    parser.add_argument("--json", action="store_true", help="Output results as JSON instead of text report.")
    parser.add_argument(
        "--error-bank-stats",
        action="store_true",
        help="Print cached error patterns sorted by frequency and exit.",
    )
    args = parser.parse_args()

    if args.error_bank_stats:
        bank_path = error_bank.default_bank_path()
        print(f"Error bank file: {bank_path}\n")
        print(error_bank.format_trends_text(limit=500))
        return

    if args.path is None:
        run_gui()
        return

    input_path = Path(args.path).expanduser().resolve()
    extracted_dirs = extract_archive_files(input_path)
    if extracted_dirs:
        print("\nArchive extraction complete. Search paths:")
        for extracted_dir in extracted_dirs:
            print(f"- {extracted_dir}")
    else:
        print("No supported archive files found to extract. The script will search the original path.")

    system_info_files: list[Path] = []
    for search_path in get_system_info_search_paths(input_path, extracted_dirs):
        system_info_files.extend(discover_log_files(search_path))

    print(f"Found {len(system_info_files)} .tzlog file(s) available for system information review.")
    if not system_info_files:
        raise SystemExit("No .tzlog files found for system information in the extracted archive folders or original path.")

    parsed_system_info_logs = [parse_tzlog(file_path) for file_path in system_info_files]
    system_info_entry = merge_system_info_entry_for_report(parsed_system_info_logs)
    system_info_report = format_system_info_report(system_info_entry)
    print("\n" + system_info_report)

    if copy_to_clipboard(system_info_report):
        print("\nCopied system information to clipboard.")
    else:
        print("\nCould not copy system information to clipboard automatically.")

    for dest in report_destination_dirs(extracted_dirs, input_path):
        system_info_report_path = write_report_to_txt(
            system_info_report,
            dest,
            "support_system_information.txt",
        )
        print(f"System information report saved to: {system_info_report_path}")

    issue_log_path = ask_for_issue_log_path(input_path)
    issue_log_files = discover_log_files(issue_log_path)
    if not issue_log_files:
        raise SystemExit(f"No .tzlog files found for issue log information at: {issue_log_path}")

    parsed_issue_logs = [parse_tzlog(file_path) for file_path in issue_log_files]
    if args.json:
        output = {
            "system_information": system_info_entry,
            "issue_logs": parsed_issue_logs,
        }
        print(json.dumps(output, indent=2))
        return

    report_blocks: list[str] = []
    for entry in parsed_issue_logs:
        report_blocks.append(format_issue_report(entry))

    notable_errors = ask_for_notable_error_messages()
    report_blocks.append(format_notable_error_report(notable_errors))
    report = "\n\n".join(report_blocks)
    print(report)

    full_report = system_info_report + "\n" + report
    if copy_to_clipboard(full_report):
        print("\nCopied full support report to clipboard.")
    else:
        print("\nCould not copy full support report to clipboard automatically.")

    for dest in report_destination_dirs(extracted_dirs, input_path):
        full_report_path = write_report_to_txt(
            full_report,
            dest,
            "support_full_tzlog_report.txt",
        )
        print(f"Full Support report saved to: {full_report_path}")


if __name__ == "__main__":
    main()
