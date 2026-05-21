"""Core tzlog parsing, archive extraction, and report formatting."""

from __future__ import annotations

from datetime import datetime
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

LOG_DIR_RE = re.compile(r'Info \| (?:Info \| )?Log directory:\s*"([^"]+)"')
APP_VERSION_RE = re.compile(r"Info \| (?:Info \| )?Starting Topaz Photo\s+(.+?)\s*$")
CRASHPAD_RE = re.compile(
    r"Info \| (?:Info \| )?Starting Crashpad handler with sessionId[:\s]*([^\s]+)"
)
USER_EMAIL_RE = re.compile(r"Info \| (?:Info \| )?User email:\s*(.+?)\s*\|")
SYSTEM_START_RE = re.compile(r"Info \| \[AIE\] === System Information ===")
SYSTEM_END_RE = re.compile(r"Info \| \[AIE\] =+")

OS_RE = re.compile(r"Info \| \[AIE\] OS (.+?)\s*$")
CPU_RE = re.compile(r"Info \| \[AIE\] CPU (.+?)\s*$")
RAM_RE = re.compile(r"Info \| \[AIE\] RAM (.+?)\s*$")
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
        "user_email": None,
        "system_information": {
            "os": None,
            "cpu": None,
            "ram": None,
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

            if result["user_email"] is None:
                match = USER_EMAIL_RE.search(line)
                if match:
                    result["user_email"] = match.group(1).strip()
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
    name = path.name.lower()
    return path.is_file() and any(name.endswith(suffix) for suffix in SUPPORTED_ARCHIVE_SUFFIXES)


def get_archive_destination_name(archive_path: Path) -> str:
    destination_name = archive_path.name
    for suffix in (".tar.gz", ".zip", ".tar", ".tgz"):
        if destination_name.lower().endswith(suffix):
            return destination_name[: -len(suffix)]
    return archive_path.stem


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> None:
    destination_root = destination.resolve()
    for member in archive.getmembers():
        member_path = (destination / member.name).resolve()
        if not str(member_path).startswith(str(destination_root)):
            raise tarfile.TarError(f"Unsafe path found in archive: {member.name}")
    archive.extractall(destination)


def extract_archive_files(input_path: Path) -> list[Path]:
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
    return extracted_dirs if extracted_dirs else [input_path]


def discover_log_files(path_input: Path) -> list[Path]:
    if path_input.is_file():
        return [path_input]
    if path_input.is_dir():
        return sorted(path_input.rglob("*.tzlog"))
    return []


def timestamp_from_log_filename(file_path: Path) -> datetime | None:
    name = file_path.name
    patterns = [
        r"(\d{4})[-_](\d{2})[-_](\d{2})[-_T ](\d{2})[-_:.](\d{2})[-_:.](\d{2})",
        r"(\d{4})(\d{2})(\d{2})[-_T ]?(\d{2})(\d{2})(\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, name)
        if not match:
            continue
        try:
            year, month, day, hour, minute, second = (int(part) for part in match.groups())
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            continue

    return None


def log_sort_datetime(file_path: Path) -> datetime:
    filename_timestamp = timestamp_from_log_filename(file_path)
    if filename_timestamp is not None:
        return filename_timestamp
    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime)
    except OSError:
        return datetime.min


def find_first_log_with_system_info(parsed_logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in parsed_logs:
        sys_info = entry["system_information"]
        if sys_info["os"] or sys_info["cpu"] or sys_info["ram"] or sys_info["indexed_gpus"]:
            return entry
    return None


def first_user_email_from_parsed(parsed_logs: list[dict[str, Any]]) -> str | None:
    for entry in parsed_logs:
        email = entry.get("user_email")
        if email:
            return str(email).strip()
    return None


def most_recent_app_version_from_parsed(parsed_logs: list[dict[str, Any]]) -> str | None:
    logs_with_versions = [entry for entry in parsed_logs if entry.get("app_version")]
    if not logs_with_versions:
        return None

    newest_entry = max(
        logs_with_versions,
        key=lambda entry: log_sort_datetime(Path(entry["file"])),
    )
    return str(newest_entry["app_version"]).strip()


def merge_system_info_entry_for_report(parsed_logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    entry = find_first_log_with_system_info(parsed_logs)
    if entry is None:
        return None

    merged_entry = dict(entry)
    merged_email = entry.get("user_email") or first_user_email_from_parsed(parsed_logs)
    newest_app_version = most_recent_app_version_from_parsed(parsed_logs)

    if merged_email:
        merged_entry["user_email"] = merged_email
    if newest_app_version:
        merged_entry["app_version"] = newest_app_version

    return merged_entry


def write_report_to_txt(report: str, output_folder: Path, filename: str = "support_tzlog_report.txt") -> Path:
    if output_folder.is_file():
        output_folder = output_folder.parent
    output_folder.mkdir(parents=True, exist_ok=True)
    report_path = output_folder / filename
    report_path.write_text(report, encoding="utf-8")
    return report_path


def report_destination_dirs(extracted_dirs: list[Path], fallback: Path) -> list[Path]:
    return extracted_dirs if extracted_dirs else [fallback]


def format_notable_errors_for_report(lines: Iterable[str]) -> str:
    trimmed = [ln.strip() for ln in lines if ln.strip()]
    return "\n".join(f"- {item}" for item in trimmed)


def format_system_info_report(entry: dict[str, Any] | None) -> str:
    if entry is None:
        return "SYSTEM INFORMATION:\n\n  Not found\n"

    sys_info = entry["system_information"]
    lines = [
        "SYSTEM INFORMATION:",
        "",
        f"  User OS: {entry['inferred_user_os'] or 'Not found'}",
        f"  Topaz Photo version: {entry['app_version'] or 'Not found'}",
        f"  User email (activation): {entry.get('user_email') or 'Not found'}",
        f"  OS: {sys_info['os'] or 'Not found'}",
        f"  CPU: {sys_info['cpu'] or 'Not found'}",
        f"  RAM: {sys_info['ram'] or 'Not found'}",
    ]

    if sys_info["indexed_gpus"]:
        lines.append("  Indexed GPUs:")
        for gpu in sys_info["indexed_gpus"]:
            lines.append(
                f"    - Index {gpu['index']}: {gpu['name']} | VRAM: {gpu['vram'] or 'Not found'}"
            )
    else:
        lines.append("  Indexed GPUs: Not found")

    lines.append("")
    return "\n".join(lines)


def format_issue_report(entry: dict[str, Any]) -> str:
    log_name = Path(entry["file"]).name
    lines = [
        "",
        "ISSUE LOG INFORMATION:",
        "",
        f"  Log file: {log_name}",
        f"  Crashpad session ID: {entry['crashpad_session_id'] or 'Not found'}",
    ]
    return "\n".join(lines)


def format_notable_error_report(notable_errors: str) -> str:
    return "\n".join(["NOTABLE ERROR MESSAGES:", "", notable_errors])


def format_known_pattern_summary(matches: list[Any]) -> str:
    if not matches:
        return "KNOWN PATTERN SUMMARY:\n\n  No known patterns matched."

    lines = ["KNOWN PATTERN SUMMARY:", ""]
    for match in matches:
        pattern = getattr(match, "pattern_text", "")
        count = getattr(match, "count", 0)
        last_seen = getattr(match, "last_seen", "")
        lines.append(f"  - Seen {count} time(s), last {last_seen}: {pattern}")
    return "\n".join(lines)


def process_system_info_from_path(
    input_path: Path,
) -> tuple[str, dict[str, Any] | None, list[Path], list[Path]]:
    extracted_dirs = extract_archive_files(input_path)

    system_info_files: list[Path] = []
    for search_path in get_system_info_search_paths(input_path, extracted_dirs):
        system_info_files.extend(discover_log_files(search_path))

    tzlog_files = sorted({p.resolve() for p in system_info_files})
    if not tzlog_files:
        raise FileNotFoundError(
            "No .tzlog files found for system information in the extracted archive folders or original path."
        )

    parsed_system_info_logs = [parse_tzlog(file_path) for file_path in tzlog_files]
    system_info_entry = merge_system_info_entry_for_report(parsed_system_info_logs)
    system_info_report = format_system_info_report(system_info_entry)

    return system_info_report, system_info_entry, extracted_dirs, tzlog_files


def process_issue_logs_from_path(
    issue_log_path: Path,
    notable_errors: str = "Not provided",
    known_matches: list[Any] | None = None,
) -> str:
    issue_log_files = discover_log_files(issue_log_path)
    if not issue_log_files:
        raise FileNotFoundError(f"No .tzlog files found for issue log information at: {issue_log_path}")

    parsed_issue_logs = [parse_tzlog(file_path) for file_path in issue_log_files]
    report_blocks: list[str] = []
    for entry in parsed_issue_logs:
        report_blocks.append(format_issue_report(entry))

    report_blocks.append(format_notable_error_report(notable_errors))
    if known_matches is not None:
        report_blocks.append(format_known_pattern_summary(known_matches))

    return "\n\n".join(report_blocks)
