#!/usr/bin/env python3
"""Extract first-step troubleshooting details from Topaz .tzlog files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any


LOG_DIR_RE = re.compile(r'Info \| (?:Info \| )?Log directory:\s*"([^"]+)"')
APP_VERSION_RE = re.compile(r"Info \| (?:Info \| )?Starting Topaz Photo\s+(.+?)\s*$")
CRASHPAD_RE = re.compile(
    r"Info \| (?:Info \| )?Starting Crashpad handler with sessionId[:\s]*([^\s]+)"
)
SYSTEM_START_RE = re.compile(r"Info \| \[AIE\] === System Information ===")
SYSTEM_END_RE = re.compile(r"Info \| \[AIE\] =+")

OS_RE = re.compile(r"Info \| \[AIE\] OS (.+?)\s*$")
CPU_RE = re.compile(r"Info \| \[AIE\] CPU (.+?)\s*$")
RAM_RE = re.compile(r"Info \| \[AIE\] RAM (.+?)\s*$")
MACHINE_ID_RE = re.compile(r"Info \| \[AIE\] Machine Id:\s*(.+?)\s*$")
DEVICE_COUNT_RE = re.compile(r"Info \| \[AIE\] Device count:\s*(\d+)\s*$")
GPU_INDEX_RE = re.compile(r"Info \| \[AIE\] - Index (\d+) Name (.+?) Cores \d+\s*$")
GPU_VRAM_RE = re.compile(r"Info \| \[AIE\]\s+VRAM (.+?)\s*$")

ARCHIVE_EXTRACT_DIR_NAME = "extracted_tzlog_archives"
SUPPORTED_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def infer_platform_from_log_dir(log_dir: str | None) -> str | None:
    if not log_dir:
        return None
    normalized = log_dir.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return "Windows"
    if normalized.startswith("/Users/"):
        return "macOS"
    return "Unknown"


def parse_tzlog(file_path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file": str(file_path),
        "log_directory": None,
        "inferred_user_os": None,
        "app_version": None,
        "crashpad_session_id": None,
        "system_information": {
            "os": None,
            "cpu": None,
            "ram": None,
            "machine_id": None,
            "device_count": None,
            "indexed_gpus": [],
        },
    }

    in_system_block = False
    current_gpu: dict[str, Any] | None = None

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")

            if result["log_directory"] is None:
                match = LOG_DIR_RE.search(line)
                if match:
                    result["log_directory"] = match.group(1)
                    result["inferred_user_os"] = infer_platform_from_log_dir(
                        result["log_directory"]
                    )
                    continue

            if result["app_version"] is None:
                match = APP_VERSION_RE.search(line)
                if match:
                    result["app_version"] = match.group(1).strip()
                    continue

            if result["crashpad_session_id"] is None:
                match = CRASHPAD_RE.search(line)
                if match:
                    result["crashpad_session_id"] = match.group(1).strip()
                    continue

            if SYSTEM_START_RE.search(line):
                in_system_block = True
                current_gpu = None
                continue

            if in_system_block and SYSTEM_END_RE.search(line):
                in_system_block = False
                current_gpu = None
                continue

            if not in_system_block:
                continue

            if result["system_information"]["os"] is None:
                match = OS_RE.search(line)
                if match:
                    result["system_information"]["os"] = match.group(1).strip()
                    continue

            if result["system_information"]["cpu"] is None:
                match = CPU_RE.search(line)
                if match:
                    result["system_information"]["cpu"] = match.group(1).strip()
                    continue

            if result["system_information"]["ram"] is None:
                match = RAM_RE.search(line)
                if match:
                    result["system_information"]["ram"] = match.group(1).strip()
                    continue

            if result["system_information"]["machine_id"] is None:
                match = MACHINE_ID_RE.search(line)
                if match:
                    result["system_information"]["machine_id"] = match.group(1).strip()
                    continue

            if result["system_information"]["device_count"] is None:
                match = DEVICE_COUNT_RE.search(line)
                if match:
                    result["system_information"]["device_count"] = int(match.group(1))
                    continue

            match = GPU_INDEX_RE.search(line)
            if match:
                current_gpu = {
                    "index": int(match.group(1)),
                    "name": match.group(2).strip(),
                    "vram": None,
                }
                result["system_information"]["indexed_gpus"].append(current_gpu)
                continue

            if current_gpu is not None:
                match = GPU_VRAM_RE.search(line)
                if match and current_gpu["vram"] is None:
                    current_gpu["vram"] = match.group(1).strip()
                    continue

    return result


def is_supported_archive(path: Path) -> bool:
    """Return True when the path is a supported archive file."""
    name = path.name.lower()
    return path.is_file() and any(name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES)


def get_archive_destination_name(archive_path: Path) -> str:
    """Return a clean folder name for an extracted archive."""
    destination_name = archive_path.name
    for suffix in (".tar.gz", ".zip", ".tar", ".tgz"):
        if destination_name.lower().endswith(suffix):
            return destination_name[: -len(suffix)]

    return archive_path.stem


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    """Extract a tar archive while preventing files from being written outside destination."""
    destination_root = destination.resolve()

    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if not str(member_path).startswith(str(destination_root)):
            raise tarfile.TarError(f"Unsafe path found in archive: {member.name}")

    archive.extractall(destination)


def extract_archive_files(input_path: Path) -> list[Path]:
    """Extract supported archive files from a folder or a direct archive path."""
    if input_path.is_file() and is_supported_archive(input_path):
        archive_files = [input_path]
        extraction_root = input_path.parent / ARCHIVE_EXTRACT_DIR_NAME
    elif input_path.is_dir():
        archive_files = sorted(path for path in input_path.iterdir() if is_supported_archive(path))
        extraction_root = input_path / ARCHIVE_EXTRACT_DIR_NAME
    else:
        return []

    if not archive_files:
        return []

    extraction_root.mkdir(exist_ok=True)

    extracted_dirs: list[Path] = []
    for archive_path in archive_files:
        destination = extraction_root / get_archive_destination_name(archive_path)
        destination.mkdir(exist_ok=True)

        try:
            if archive_path.name.lower().endswith(".zip"):
                with zipfile.ZipFile(archive_path, "r") as archive:
                    archive.extractall(destination)
            else:
                with tarfile.open(archive_path, "r:*") as archive:
                    safe_extract_tar(archive, destination)
        except (zipfile.BadZipFile, tarfile.TarError, OSError) as error:
            print(f"Skipping invalid archive file: {archive_path}", file=sys.stderr)
            print(f"Reason: {error}", file=sys.stderr)
            continue

        extracted_tzlogs = sorted(destination.rglob("*.tzlog"))
        print(f"Extracted archive: {archive_path}")
        print(f"Extraction folder: {destination}")
        print(f"Found {len(extracted_tzlogs)} .tzlog file(s) in extracted archive.")
        extracted_dirs.append(destination)

    return extracted_dirs


def get_system_info_search_paths(input_path: Path, extracted_dirs: list[Path]) -> list[Path]:
    """Prefer extracted archive folders, then fall back to the original input path."""
    if extracted_dirs:
        return extracted_dirs

    return [input_path]


def ask_for_issue_log_path(default_path: Path) -> Path:
    """Ask Support for the log file/folder that contains the reported issue."""
    print("\nIssue log step.")
    print("Enter the path to the .tzlog file or folder that shows the reported issue so the Crashpad session ID can be included in Linear.")
    print(f"Press Enter to use the original path: {default_path}")

    user_input = input("Issue log path: ").strip()
    if not user_input:
        return default_path

    return Path(user_input).expanduser().resolve()


def discover_log_files(path_input: Path) -> list[Path]:
    if path_input.is_file():
        return [path_input]
    if path_input.is_dir():
        return sorted(path_input.rglob("*.tzlog"))
    return []


def find_first_log_with_system_info(parsed_logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first parsed log that contains any system information."""
    for entry in parsed_logs:
        sys_info = entry["system_information"]
        if (
            sys_info["os"]
            or sys_info["cpu"]
            or sys_info["ram"]
            or sys_info["machine_id"]
            or sys_info["device_count"] is not None
            or sys_info["indexed_gpus"]
        ):
            return entry

    return None


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


def format_system_info_report(entry: dict[str, Any] | None) -> str:
    if entry is None:
        return "System information:\n  Not found"

    sys_info = entry["system_information"]
    lines = [
        "System information:",
        f"  User OS: {entry['inferred_user_os'] or 'Not found'}",
        f"  Topaz Photo version: {entry['app_version'] or 'Not found'}",
        f"  OS: {sys_info['os'] or 'Not found'}",
        f"  CPU: {sys_info['cpu'] or 'Not found'}",
        f"  RAM: {sys_info['ram'] or 'Not found'}",
        f"  Machine ID: {sys_info['machine_id'] or 'Not found'}",
        f"  Device count: {sys_info['device_count'] if sys_info['device_count'] is not None else 'Not found'}",
    ]

    if sys_info["indexed_gpus"]:
        lines.append("  Indexed GPUs:")
        for gpu in sys_info["indexed_gpus"]:
            lines.append(
                f"    - Index {gpu['index']}: {gpu['name']} | VRAM: {gpu['vram'] or 'Not found'}"
            )
    else:
        lines.append("  Indexed GPUs: Not found")

    return "\n".join(lines)


def format_issue_report(entry: dict[str, Any]) -> str:
    log_name = Path(entry["file"]).name

    lines = [
        "Issue log information:",
        f"  Log file: {log_name}",
        f"  Crashpad session ID: {entry['crashpad_session_id'] or 'Not found'}",
    ]

    return "\n".join(lines)


def format_text_report(entry: dict[str, Any]) -> str:
    sys_info = entry["system_information"]
    lines = [
        f"User OS: {entry['inferred_user_os'] or 'Not found'}",
        f"Topaz Photo version: {entry['app_version'] or 'Not found'}",
        f"Crashpad session ID: {entry['crashpad_session_id'] or 'Not found'}",
        "System information:",
        f"  OS: {sys_info['os'] or 'Not found'}",
        f"  CPU: {sys_info['cpu'] or 'Not found'}",
        f"  RAM: {sys_info['ram'] or 'Not found'}",
        f"  Machine ID: {sys_info['machine_id'] or 'Not found'}",
        f"  Device count: {sys_info['device_count'] if sys_info['device_count'] is not None else 'Not found'}",
    ]

    if sys_info["indexed_gpus"]:
        lines.append("  Indexed GPUs:")
        for gpu in sys_info["indexed_gpus"]:
            lines.append(
                f"    - Index {gpu['index']}: {gpu['name']} | VRAM: {gpu['vram'] or 'Not found'}"
            )
    else:
        lines.append("  Indexed GPUs: Not found")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract support triage details from .tzlog files."
    )
    parser.add_argument(
        "path",
        help="Path to a .tzlog file, a directory containing .tzlog files, or a folder containing archive files with .tzlog files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of text report.",
    )
    args = parser.parse_args()

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
    system_info_entry = find_first_log_with_system_info(parsed_system_info_logs)

    system_info_report = format_system_info_report(system_info_entry)
    print("\n" + system_info_report)

    if copy_to_clipboard(system_info_report):
        print("\nCopied system information to clipboard.")
    else:
        print("\nCould not copy system information to clipboard automatically.")

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

    report_blocks: list[str] = [format_system_info_report(system_info_entry)]
    for entry in parsed_issue_logs:
        report_blocks.append(format_issue_report(entry))

    report = ("\n" + ("-" * 72) + "\n").join(report_blocks)
    print(report)

    if copy_to_clipboard(report):
        print("\nCopied report to clipboard.")
    else:
        print("\nCould not copy report to clipboard automatically.")


if __name__ == "__main__":
    main()
