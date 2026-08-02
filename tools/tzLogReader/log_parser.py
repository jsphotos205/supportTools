"""Core tzlog parsing, archive extraction, and report formatting."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

try:
    from .analysis_model import ApplicationSession, GPURecord, IssueClassification, LogArchive, SystemInformation
except ImportError:  # pragma: no cover - direct script execution fallback
    from analysis_model import ApplicationSession, GPURecord, IssueClassification, LogArchive, SystemInformation

try:
    from .issue_classifier import classify_issue_text
except ImportError:  # pragma: no cover - direct script execution fallback
    from issue_classifier import classify_issue_text

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
SESSION_WARNING_RE = re.compile(r"\bwarning\b|\bwarn\b", re.IGNORECASE)
SESSION_ERROR_RE = re.compile(
    r"\berror\b|\bexception\b|\bfailure\b|\bfatal\b|\bcrash\b|\bdenied\b|\btimeout\b|\bunable\b|\bcannot\b|\bunsupported\b|\bcorrupt\b|\bmissing\b|\bfailed\b",
    re.IGNORECASE,
)
SESSION_EXPORT_FAILURE_RE = re.compile(
    r"\bexport\b.*\b(?:fail|error|exception|denied)\b|\bfailed to export\b|\bexport failed\b",
    re.IGNORECASE,
)
SESSION_GRACEFUL_SHUTDOWN_RE = re.compile(
    r"\bgraceful(?:ly)? shutdown\b|\bshutdown complete\b|\bnormal exit\b|\bapplication closed\b|\bcleanup completed\b|\bexport completed\b|\bcompleted successfully\b",
    re.IGNORECASE,
)
SESSION_FORCED_QUIT_RE = re.compile(
    r"\bforce quit\b|\bforced quit\b|\bquit unexpectedly\b|\bterminated\b|\bSIGKILL\b|\btask kill\b|\bapp killed\b",
    re.IGNORECASE,
)
SESSION_CRASH_EVIDENCE_RE = re.compile(
    r"\bcrash\b|\bcrashed\b|\bpanic\b|\bsegmentation fault\b|\bunhandled exception\b|\bstack trace\b|\btraceback\b",
    re.IGNORECASE,
)
SESSION_PROCESSING_STARTED_RE = re.compile(
    r"\bstarting topaz photo\b|\bstarting .*model\b|\bprocessing\b|\bdenoise\b|\bsharpen\b|\brender\b",
    re.IGNORECASE,
)
ENHANCEMENT_KEYWORDS = (
    "denoise",
    "sharpen",
    "super focus",
    "sdi",
    "inpaint",
    "model",
    "render",
)

ARCHIVE_EXTRACT_DIR_NAME = "extracted_tzlog_archives"
SUPPORTED_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")

SESSION_STATUS_SUCCESSFUL = "successful"
SESSION_STATUS_WARNING_ONLY = "warning-only"
SESSION_STATUS_PROCESSING_FAILURE = "processing failure"
SESSION_STATUS_EXPORT_FAILURE = "export failure"
SESSION_STATUS_PROBABLE_ABRUPT_EXIT = "probable abrupt exit"
SESSION_STATUS_CONFIRMED_GRACEFUL_SHUTDOWN = "confirmed graceful shutdown"
SESSION_STATUS_INCONCLUSIVE = "inconclusive"
TERMINATION_CONFIRMED_CRASH = "confirmed crash evidence"
TERMINATION_PROBABLE_ABRUPT_EXIT = "probable abrupt termination"
TERMINATION_FORCED_QUIT = "forced quit"
TERMINATION_INCOMPLETE_LOG_CAPTURE = "incomplete log capture"
TERMINATION_UNKNOWN = "unknown termination"
TERMINATION_CONFIRMED_GRACEFUL_SHUTDOWN = "confirmed graceful shutdown"


def infer_platform_from_log_dir(log_dir: str | None) -> str | None:
    if not log_dir:
        return None
    normalized = log_dir.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return "Windows"
    if normalized.startswith("/Users/"):
        return "macOS"
    return "Unknown"


def _append_unique(values: list[str], value: str | None) -> None:
    if not value:
        return
    cleaned = value.strip()
    if cleaned and cleaned not in values:
        values.append(cleaned)


def _infer_session_stage(line: str) -> str | None:
    lowered = line.casefold()
    if "sharpen" in lowered:
        return "Sharpen model processing"
    if "denoise" in lowered:
        return "Denoise model processing"
    if "render" in lowered:
        return "Rendering"
    if "export" in lowered:
        return "Export"
    if "model" in lowered:
        return "Model processing"
    return None


def _annotate_session_from_lines(session: ApplicationSession, raw_lines: list[str]) -> None:
    graceful_markers: list[str] = []
    forced_quit_markers: list[str] = []
    crash_markers: list[str] = []
    export_failures: list[str] = []
    incomplete_capture = False
    log_text = "\n".join(raw_lines)
    session.issue_classifications = classify_issue_text(log_text)

    for raw_line in raw_lines:
        line = raw_line.rstrip("\n")
        lowered = line.casefold()
        if SESSION_WARNING_RE.search(line):
            _append_unique(session.warnings, line)
        if SESSION_ERROR_RE.search(line):
            _append_unique(session.errors, line)
        if SESSION_EXPORT_FAILURE_RE.search(line):
            _append_unique(session.export_results, line)
            _append_unique(export_failures, line)
        if SESSION_GRACEFUL_SHUTDOWN_RE.search(line):
            _append_unique(graceful_markers, line)
            session.termination_state = SESSION_STATUS_CONFIRMED_GRACEFUL_SHUTDOWN
        if SESSION_FORCED_QUIT_RE.search(line):
            _append_unique(forced_quit_markers, line)
        if SESSION_CRASH_EVIDENCE_RE.search(line):
            _append_unique(crash_markers, line)
        if SESSION_PROCESSING_STARTED_RE.search(line):
            _append_unique(session.processing_operations, "processing_started")
        for keyword in ENHANCEMENT_KEYWORDS:
            if keyword in lowered:
                _append_unique(session.enhancements, keyword)
                break
        stage = _infer_session_stage(line)
        if stage:
            session.last_stage = stage

    if raw_lines and not raw_lines[-1].endswith("\n"):
        incomplete_capture = True

    if graceful_markers:
        session.termination_evidence.extend(graceful_markers)
    if forced_quit_markers:
        session.termination_evidence.extend(forced_quit_markers)
    if crash_markers:
        session.termination_evidence.extend(crash_markers)
    if export_failures:
        session.termination_evidence.extend(export_failures)
    if incomplete_capture:
        session.termination_evidence.append("Log capture appears incomplete")

    session.termination_analysis = determine_termination_analysis(session)
    if session.termination_analysis == TERMINATION_PROBABLE_ABRUPT_EXIT and not session.termination_evidence:
        session.termination_evidence.append("Processing began without a completion marker")

    if session.termination_state is None and session.errors:
        session.termination_state = SESSION_STATUS_PROBABLE_ABRUPT_EXIT
    elif session.termination_state is None:
        session.termination_state = "completed"

    session.session_status = classify_session_status(session)
    session.session_score = calculate_session_score(session)


def determine_termination_analysis(session: ApplicationSession) -> str:
    evidence = "\n".join(session.termination_evidence).casefold()
    if any(marker in evidence for marker in (TERMINATION_CONFIRMED_GRACEFUL_SHUTDOWN, "cleanup completed", "export completed")):
        return TERMINATION_CONFIRMED_GRACEFUL_SHUTDOWN
    if any(marker in evidence for marker in (TERMINATION_FORCED_QUIT, "force quit", "killed", "terminated")):
        return TERMINATION_FORCED_QUIT
    if any(marker in evidence for marker in (TERMINATION_CONFIRMED_CRASH, "crash", "panic", "segmentation fault", "unhandled exception", "stack trace", "traceback")):
        return TERMINATION_CONFIRMED_CRASH
    if any(marker in evidence for marker in ("log capture appears incomplete", "truncated", "incomplete")):
        return TERMINATION_INCOMPLETE_LOG_CAPTURE
    if session.processing_operations:
        return TERMINATION_PROBABLE_ABRUPT_EXIT
    return TERMINATION_UNKNOWN


def calculate_session_score(session: ApplicationSession) -> int:
    score = 0
    score += len(session.errors) * 8
    score += len(session.warnings) * 2
    score += len(session.export_results) * 6
    if session.crashpad_session_id:
        score += 2
    if session.last_stage:
        score += 1
    if session.session_status == SESSION_STATUS_PROBABLE_ABRUPT_EXIT:
        score += 4
    return score


def classify_session_status(session: ApplicationSession) -> str:
    if session.termination_analysis == TERMINATION_CONFIRMED_CRASH:
        return SESSION_STATUS_PROCESSING_FAILURE
    if session.termination_analysis == TERMINATION_FORCED_QUIT:
        return SESSION_STATUS_PROBABLE_ABRUPT_EXIT
    if session.termination_analysis == TERMINATION_PROBABLE_ABRUPT_EXIT:
        return SESSION_STATUS_PROBABLE_ABRUPT_EXIT
    if session.termination_analysis == TERMINATION_INCOMPLETE_LOG_CAPTURE:
        return SESSION_STATUS_INCONCLUSIVE
    if session.export_results and any("failed" in item.casefold() for item in session.export_results):
        return SESSION_STATUS_EXPORT_FAILURE
    if session.errors:
        return SESSION_STATUS_PROCESSING_FAILURE
    if session.warnings and not session.errors:
        return SESSION_STATUS_WARNING_ONLY
    if session.termination_state == SESSION_STATUS_CONFIRMED_GRACEFUL_SHUTDOWN:
        return SESSION_STATUS_CONFIRMED_GRACEFUL_SHUTDOWN
    if session.errors or session.warnings or session.processing_operations:
        return SESSION_STATUS_INCONCLUSIVE
    return SESSION_STATUS_SUCCESSFUL


def parse_tzlog(file_path: Path) -> ApplicationSession:
    result = ApplicationSession(file=str(file_path))
    in_system_block = False
    current_gpu: GPURecord | None = None
    raw_lines: list[str] = []

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            raw_lines.append(raw_line)

            if result.log_directory is None:
                match = LOG_DIR_RE.search(line)
                if match:
                    result.log_directory = match.group(1)
                    result.inferred_user_os = infer_platform_from_log_dir(result.log_directory)
                    continue

            if result.version is None:
                match = APP_VERSION_RE.search(line)
                if match:
                    result.version = match.group(1).strip()
                    continue

            if result.crashpad_session_id is None:
                match = CRASHPAD_RE.search(line)
                if match:
                    result.crashpad_session_id = match.group(1).strip()
                    continue

            if SYSTEM_START_RE.search(line):
                in_system_block = True
                current_gpu = None
                continue

            if in_system_block and SYSTEM_END_RE.search(line):
                in_system_block = False
                current_gpu = None
                continue

            if result.user_email is None:
                match = USER_EMAIL_RE.search(line)
                if match:
                    result.user_email = match.group(1).strip()
                    continue

            if not in_system_block:
                continue

            if result.system_information.os is None:
                match = OS_RE.search(line)
                if match:
                    result.system_information.os = match.group(1).strip()
                    continue

            if result.system_information.cpu is None:
                match = CPU_RE.search(line)
                if match:
                    result.system_information.cpu = match.group(1).strip()
                    continue

            if result.system_information.ram is None:
                match = RAM_RE.search(line)
                if match:
                    result.system_information.ram = match.group(1).strip()
                    continue

            match = GPU_INDEX_RE.search(line)
            if match:
                current_gpu = GPURecord(index=int(match.group(1)), name=match.group(2).strip())
                result.system_information.indexed_gpus.append(current_gpu)
                continue

            if current_gpu is not None:
                match = GPU_VRAM_RE.search(line)
                if match and current_gpu.vram is None:
                    current_gpu.vram = match.group(1).strip()
                    continue

    result.session_start_time = log_sort_datetime(file_path).isoformat()
    _annotate_session_from_lines(result, raw_lines)
    result.input_files = [str(file_path)]
    result.workflow_type = result.workflow_type or "Standalone"
    result.processing_operations.append("parse_tzlog")
    result.session_status = classify_session_status(result)
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


def session_group_key(session: ApplicationSession) -> str:
    if session.crashpad_session_id:
        return f"crashpad:{session.crashpad_session_id}"
    timestamp = log_sort_datetime(Path(session.file))
    if timestamp == datetime.min:
        return f"unknown:{Path(session.file).parent.name}:{session.file}"
    return f"{session.log_directory or Path(session.file).parent.name}:{timestamp.strftime('%Y-%m-%dT%H:%M')}"


def session_sort_datetime(session: ApplicationSession) -> datetime:
    return log_sort_datetime(Path(session.file))


def merge_system_information(primary: SystemInformation, secondary: SystemInformation) -> SystemInformation:
    merged = SystemInformation(
        os=primary.os or secondary.os,
        cpu=primary.cpu or secondary.cpu,
        ram=primary.ram or secondary.ram,
    )
    indexed = {gpu.index: gpu for gpu in primary.indexed_gpus}
    for gpu in secondary.indexed_gpus:
        if gpu.index not in indexed:
            indexed[gpu.index] = gpu
    merged.indexed_gpus = [indexed[key] for key in sorted(indexed)]
    return merged


def merge_application_sessions(sessions: Iterable[ApplicationSession]) -> ApplicationSession:
    session_list = list(sessions)
    if not session_list:
        raise ValueError("No sessions to merge")

    primary = session_list[0]
    merged = ApplicationSession(
        file=primary.file,
        log_directory=primary.log_directory,
        inferred_user_os=primary.inferred_user_os,
        version=primary.version,
        crashpad_session_id=primary.crashpad_session_id,
        user_email=primary.user_email,
        system_information=SystemInformation(
            os=primary.system_information.os,
            cpu=primary.system_information.cpu,
            ram=primary.system_information.ram,
            indexed_gpus=list(primary.system_information.indexed_gpus),
        ),
        workflow_type=primary.workflow_type,
        input_files=list(primary.input_files),
        processing_operations=list(primary.processing_operations),
        warnings=list(primary.warnings),
        errors=list(primary.errors),
        export_results=list(primary.export_results),
        issue_classifications=[IssueClassification(**item.to_dict()) for item in primary.issue_classifications],
        termination_state=primary.termination_state,
        termination_analysis=primary.termination_analysis,
        termination_evidence=list(primary.termination_evidence),
        session_start_time=primary.session_start_time,
        last_stage=primary.last_stage,
        enhancements=list(primary.enhancements),
    )

    for entry in session_list[1:]:
        merged.system_information = merge_system_information(merged.system_information, entry.system_information)
        merged.workflow_type = merged.workflow_type or entry.workflow_type
        merged.version = merged.version or entry.version
        merged.crashpad_session_id = merged.crashpad_session_id or entry.crashpad_session_id
        merged.user_email = merged.user_email or entry.user_email
        merged.inferred_user_os = merged.inferred_user_os or entry.inferred_user_os
        merged.log_directory = merged.log_directory or entry.log_directory
        for item in entry.input_files:
            _append_unique(merged.input_files, item)
        for item in entry.processing_operations:
            _append_unique(merged.processing_operations, item)
        for item in entry.warnings:
            _append_unique(merged.warnings, item)
        for item in entry.errors:
            _append_unique(merged.errors, item)
        for item in entry.export_results:
            _append_unique(merged.export_results, item)
        for item in entry.issue_classifications:
            if not any(existing.id == item.id for existing in merged.issue_classifications):
                merged.issue_classifications.append(item)
        for item in entry.termination_evidence:
            _append_unique(merged.termination_evidence, item)
        for item in entry.enhancements:
            _append_unique(merged.enhancements, item)
        if not merged.last_stage and entry.last_stage:
            merged.last_stage = entry.last_stage

    merged.termination_analysis = determine_termination_analysis(merged)
    merged.session_start_time = session_list[0].session_start_time or log_sort_datetime(Path(primary.file)).isoformat()
    merged.session_status = classify_session_status(merged)
    merged.session_score = calculate_session_score(merged)
    return merged


def group_application_sessions(parsed_logs: Iterable[ApplicationSession]) -> list[ApplicationSession]:
    grouped: dict[str, list[ApplicationSession]] = defaultdict(list)
    for entry in sorted(parsed_logs, key=lambda item: (session_sort_datetime(item), item.file)):
        grouped[session_group_key(entry)].append(entry)

    sessions = [merge_application_sessions(entries) for entries in grouped.values()]
    sessions.sort(key=lambda item: (session_sort_datetime(item), item.file))
    return sessions


def likely_problem_session(sessions: Iterable[ApplicationSession]) -> ApplicationSession | None:
    ordered = list(sessions)
    if not ordered:
        return None
    return max(ordered, key=lambda entry: (entry.session_score, len(entry.errors), len(entry.warnings), entry.file))


def build_log_archive_from_path(input_path: Path) -> LogArchive:
    extracted_dirs = extract_archive_files(input_path)
    search_paths = get_system_info_search_paths(input_path, extracted_dirs)
    tzlog_files = sorted({path.resolve() for search_path in search_paths for path in discover_log_files(search_path)})
    if not tzlog_files:
        raise FileNotFoundError(
            "No .tzlog files found for system information in the extracted archive folders or original path."
        )
    parsed_logs = [parse_tzlog(file_path) for file_path in tzlog_files]
    sessions = group_application_sessions(parsed_logs)
    problem_session = likely_problem_session(sessions)
    return LogArchive(
        source_path=str(input_path),
        input_files=[str(path) for path in tzlog_files],
        application_sessions=sessions,
        extracted_dirs=[str(path) for path in extracted_dirs],
        likely_problem_session=problem_session.file if problem_session else None,
    )


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


def find_first_log_with_system_info(parsed_logs: list[ApplicationSession]) -> ApplicationSession | None:
    for entry in parsed_logs:
        sys_info = entry.system_information
        if sys_info.os or sys_info.cpu or sys_info.ram or sys_info.indexed_gpus:
            return entry
    return None


def first_user_email_from_parsed(parsed_logs: list[ApplicationSession]) -> str | None:
    for entry in parsed_logs:
        if entry.user_email:
            return str(entry.user_email).strip()
    return None


def most_recent_app_version_from_parsed(parsed_logs: list[ApplicationSession]) -> str | None:
    logs_with_versions = [entry for entry in parsed_logs if entry.version]
    if not logs_with_versions:
        return None

    newest_entry = max(logs_with_versions, key=lambda entry: log_sort_datetime(Path(entry.file)))
    return str(newest_entry.version).strip()


def merge_system_info_entry_for_report(parsed_logs: list[ApplicationSession]) -> ApplicationSession | None:
    entry = find_first_log_with_system_info(parsed_logs)
    if entry is None:
        return None

    merged_entry = ApplicationSession(
        file=entry.file,
        log_directory=entry.log_directory,
        inferred_user_os=entry.inferred_user_os,
        version=entry.version,
        crashpad_session_id=entry.crashpad_session_id,
        user_email=entry.user_email,
        system_information=SystemInformation(
            os=entry.system_information.os,
            cpu=entry.system_information.cpu,
            ram=entry.system_information.ram,
            indexed_gpus=list(entry.system_information.indexed_gpus),
        ),
        workflow_type=entry.workflow_type,
        input_files=list(entry.input_files),
        processing_operations=list(entry.processing_operations),
        warnings=list(entry.warnings),
        errors=list(entry.errors),
        export_results=list(entry.export_results),
        termination_state=entry.termination_state,
    )

    merged_email = entry.user_email or first_user_email_from_parsed(parsed_logs)
    newest_app_version = most_recent_app_version_from_parsed(parsed_logs)

    if merged_email:
        merged_entry.user_email = merged_email
    if newest_app_version:
        merged_entry.version = newest_app_version

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


def format_system_info_report(entry: ApplicationSession | None) -> str:
    if entry is None:
        return "SYSTEM INFORMATION:\n\n  Not found\n"

    sys_info = entry.system_information
    lines = [
        "SYSTEM INFORMATION:",
        "",
        f"  User OS: {entry.inferred_user_os or 'Not found'}",
        f"  Topaz Photo version: {entry.version or 'Not found'}",
        f"  User email (activation): {entry.user_email or 'Not found'}",
        f"  OS: {sys_info.os or 'Not found'}",
        f"  CPU: {sys_info.cpu or 'Not found'}",
        f"  RAM: {sys_info.ram or 'Not found'}",
    ]

    if sys_info.indexed_gpus:
        lines.append("  Indexed GPUs:")
        for gpu in sys_info.indexed_gpus:
            lines.append(f"    - Index {gpu.index}: {gpu.name} | VRAM: {gpu.vram or 'Not found'}")
    else:
        lines.append("  Indexed GPUs: Not found")

    lines.append("")
    return "\n".join(lines)


def format_issue_report(entry: ApplicationSession) -> str:
    log_name = Path(entry.file).name
    lines = [
        "",
        "ISSUE LOG INFORMATION:",
        "",
        f"  Log file: {log_name}",
        f"  Crashpad session ID: {entry.crashpad_session_id or 'Not found'}",
    ]
    if entry.issue_classifications:
        lines.append("  Rule-based classifications:")
        for item in entry.issue_classifications:
            lines.append(f"    - {item.category} ({item.severity})")
    return "\n".join(lines)


def format_session_summary(session: ApplicationSession) -> str:
    display_time = session.session_start_time or log_sort_datetime(Path(session.file)).isoformat()
    input_label = session.input_files[-1] if session.input_files else Path(session.file).name
    enhancements = ", ".join(session.enhancements) if session.enhancements else "None"
    issue_lines = [f"Issue category: {item.category} ({item.severity})" for item in session.issue_classifications]
    if issue_lines:
        issue_lines = ["Issue matches:"] + [f"  - {line}" for line in issue_lines]
    else:
        issue_lines = ["Issue matches: None"]
    return "\n".join(
        [
            f"Version: {session.version or 'Not found'}",
            f"Workflow: {session.workflow_type or 'Not found'}",
            f"Input: {input_label}",
            f"Enhancements: {enhancements}",
            f"Result: {session.session_status}",
            f"Termination: {session.termination_analysis}",
            f"Last stage: {session.last_stage or 'Not found'}",
            f"Crashpad ID: {session.crashpad_session_id or 'Not found'}",
            f"Started: {display_time}",
            *issue_lines,
        ]
    )


def format_session_scan_report(archive: LogArchive) -> str:
    if not archive.application_sessions:
        return "SESSION SCAN:\n\n  No sessions found."

    lines = ["SESSION SCAN:", ""]
    for index, session in enumerate(archive.application_sessions, start=1):
        display_time = session.session_start_time or log_sort_datetime(Path(session.file)).isoformat()
        lines.append(f"Session {index} — {display_time}")
        lines.append(format_session_summary(session))
        lines.append("")
    if archive.likely_problem_session:
        lines.append(f"Likely problem session: {archive.likely_problem_session}")
    return "\n".join(lines).rstrip()


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
