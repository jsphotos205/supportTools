#!/usr/bin/env python3
"""Extract first-step troubleshooting details from Topaz .tzlog files."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
import subprocess
import sys
import tarfile
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
from typing import Any, Iterable

import error_message_bank as error_bank

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


def format_notable_errors_for_report(lines: Iterable[str]) -> str:
    """Format Step 3 lines for pasted reports: one trimmed line each, dash-prefixed."""
    trimmed = [ln.strip() for ln in lines if ln.strip()]
    return "\n".join(f"- {item}" for item in trimmed)


def ask_for_notable_error_messages() -> str:
    """Ask Support to add notable error messages found in the problem log."""
    print("\nNotable error message step.")
    print("Add any notable error messages found in the problem log.")
    print("Press Enter once on a blank line when finished.")

    lines: list[str] = []
    while True:
        user_input = input("Error message: ").strip()
        if not user_input:
            break
        lines.append(user_input)

    if not lines:
        return "Not provided"

    return format_notable_errors_for_report(lines)


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
            or sys_info["indexed_gpus"]
        ):
            return entry

    return None


def first_user_email_from_parsed(parsed_logs: list[dict[str, Any]]) -> str | None:
    """Return the first non-empty user email found across parsed logs (activation troubleshooting)."""
    for entry in parsed_logs:
        email = entry.get("user_email")
        if email:
            return str(email).strip()
    return None


# === Timestamp and app version helpers for merging system info ===

def timestamp_from_log_filename(file_path: Path) -> datetime | None:
    """Extract a timestamp from common tzlog filename formats, if present."""
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
    """Return the best available datetime for comparing logs by recency."""
    filename_timestamp = timestamp_from_log_filename(file_path)
    if filename_timestamp is not None:
        return filename_timestamp

    try:
        return datetime.fromtimestamp(file_path.stat().st_mtime)
    except OSError:
        return datetime.min


def most_recent_app_version_from_parsed(parsed_logs: list[dict[str, Any]]) -> str | None:
    """Return the Topaz Photo version from the most recent log that contains one."""
    logs_with_versions = [entry for entry in parsed_logs if entry.get("app_version")]
    if not logs_with_versions:
        return None

    newest_entry = max(
        logs_with_versions,
        key=lambda entry: log_sort_datetime(Path(entry["file"])),
    )
    return str(newest_entry["app_version"]).strip()


def merge_system_info_entry_for_report(parsed_logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the primary system-info log and attach newest app version plus user email from the batch."""
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


def write_report_to_txt(report: str, output_folder: Path, filename: str = "support_tzlog_report.txt") -> Path:
    """Write report text to a .txt file inside the selected Support folder."""
    if output_folder.is_file():
        output_folder = output_folder.parent

    output_folder.mkdir(parents=True, exist_ok=True)
    report_path = output_folder / filename
    report_path.write_text(report, encoding="utf-8")
    return report_path


def report_destination_dirs(extracted_dirs: list[Path], fallback: Path) -> list[Path]:
    """When archives were extracted, save reports next to the unpacked files; otherwise use fallback."""
    return extracted_dirs if extracted_dirs else [fallback]


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
    lines = [
        "NOTABLE ERROR MESSAGES:",
        "",
        notable_errors,
    ]

    return "\n".join(lines)



def format_text_report(entry: dict[str, Any]) -> str:
    sys_info = entry["system_information"]
    lines = [
        f"User OS: {entry['inferred_user_os'] or 'Not found'}",
        f"Topaz Photo version: {entry['app_version'] or 'Not found'}",
        f"Crashpad session ID: {entry['crashpad_session_id'] or 'Not found'}",
        f"User email (activation): {entry.get('user_email') or 'Not found'}",
        "SYSTEM INFORMATION:",
        "",
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

    return "\n".join(lines)


# === GUI and helper functions for GUI mode ===

def process_system_info_from_path(
    input_path: Path,
) -> tuple[str, dict[str, Any] | None, list[Path], list[Path]]:
    """Extract archives, locate tzlogs, parse system info, and return a report plus discovered log paths."""
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


def gui_display_root_for_tzlogs(input_path: Path, extracted_dirs: list[Path]) -> Path:
    """Folder used to show relative paths in the GUI log list (extracted tree or original folder)."""
    if extracted_dirs:
        return extracted_dirs[0].resolve()
    if input_path.is_dir():
        return input_path.resolve()
    return input_path.resolve().parent


def open_path_in_os_file_manager(path: Path) -> bool:
    """Open a folder (or file's parent) in Finder / Explorer / xdg-open."""
    target = path.resolve()
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        return False
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(target)], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", str(target)], check=False)
        else:
            subprocess.run(["xdg-open", str(target)], check=False)
    except OSError:
        return False
    return True


def read_log_file_for_gui_preview(file_path: Path, max_chars: int = 350_000) -> tuple[str, bool]:
    """Read UTF-8 log text for preview; returns (text, truncated)."""
    chunks: list[str] = []
    total = 0
    truncated = False
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        while total < max_chars:
            piece = handle.read(max_chars - total)
            if not piece:
                break
            chunks.append(piece)
            total += len(piece)
        if handle.read(1):
            truncated = True
    return "".join(chunks), truncated


def process_issue_logs_from_path(issue_log_path: Path, notable_errors: str = "Not provided") -> str:
    """Parse the issue-specific tzlog path and return the issue/crashpad report."""
    issue_log_files = discover_log_files(issue_log_path)

    if not issue_log_files:
        raise FileNotFoundError(f"No .tzlog files found for issue log information at: {issue_log_path}")

    parsed_issue_logs = [parse_tzlog(file_path) for file_path in issue_log_files]

    report_blocks: list[str] = []
    for entry in parsed_issue_logs:
        report_blocks.append(format_issue_report(entry))

    report_blocks.append(format_notable_error_report(notable_errors))

    return "\n\n".join(report_blocks)


def run_gui() -> None:
    """Launch a simple GUI for Support users who do not use the CLI."""
    root = tk.Tk()
    root.title("Topaz tzlog Reader")
    root.geometry("900x900")

    state: dict[str, Any] = {
        "system_info_report": "",
        "final_report": "",
        "support_path": None,
        "extracted_dirs": [],
        "tzlog_browse_root": None,
    }

    main_frame = tk.Frame(root, padx=16, pady=16)
    main_frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(
        main_frame,
        text="Topaz tzlog Reader",
        font=("Arial", 18, "bold"),
        anchor="w",
    ).pack(fill=tk.X)

    tk.Label(
        main_frame,
        text=(
            "Choose a folder or archive in Step 1, then click Run Step 1 there. Extracted logs appear in the list "
            "below so you can open folders, read raw .tzlog files, and pick the issue log for Step 2."
        ),
        anchor="w",
        wraplength=850,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(4, 16))

    support_path_var = tk.StringVar()
    issue_path_var = tk.StringVar()
    status_var = tk.StringVar(value="Ready.")

    support_frame = tk.LabelFrame(
        main_frame,
        text="Step 1: Support download folder or archive",
        padx=10,
        pady=10,
    )
    support_frame.pack(fill=tk.X, pady=(0, 12))

    support_path_row = tk.Frame(support_frame)
    support_path_row.pack(fill=tk.X)

    tk.Entry(support_path_row, textvariable=support_path_var).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 8),
    )

    def select_support_folder() -> None:
        selected = filedialog.askdirectory(title="Select support download folder")
        if selected:
            support_path_var.set(selected)

    def select_support_archive() -> None:
        selected = filedialog.askopenfilename(
            title="Select support archive",
            filetypes=[
                ("Supported archives", "*.tar *.tar.gz *.tgz *.zip"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            support_path_var.set(selected)

    tk.Button(support_path_row, text="Choose Folder", command=select_support_folder).pack(
        side=tk.LEFT,
        padx=(0, 8),
    )
    tk.Button(support_path_row, text="Choose Archive", command=select_support_archive).pack(
        side=tk.LEFT,
    )

    step1_action_row = tk.Frame(support_frame)
    step1_action_row.pack(fill=tk.X, pady=(10, 0))

    logs_frame = tk.LabelFrame(
        main_frame,
        text="Discovered .tzlog files (after Step 1)",
        padx=10,
        pady=10,
    )
    logs_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 12))

    tk.Label(
        logs_frame,
        text=(
            "Click Run Step 1 (in the Step 1 section above) to extract archives and list logs here. "
            "Double-click a row to read the raw log. "
            "Use the buttons to open the folder in your file manager or copy the path into Step 2."
        ),
        anchor="w",
        wraplength=850,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(0, 6))

    tzlog_list_row = tk.Frame(logs_frame)
    tzlog_list_row.pack(fill=tk.BOTH, expand=True)
    tzlog_listbox = tk.Listbox(tzlog_list_row, height=7, exportselection=False)
    tzlog_list_scroll = tk.Scrollbar(tzlog_list_row, orient=tk.VERTICAL, command=tzlog_listbox.yview)
    tzlog_listbox.configure(yscrollcommand=tzlog_list_scroll.set)
    tzlog_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tzlog_list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tzlog_listbox_paths: list[Path] = []

    logs_btn_row = tk.Frame(logs_frame)
    logs_btn_row.pack(fill=tk.X, pady=(8, 0))

    def refresh_tzlog_list(paths: list[Path], display_root: Path) -> None:
        tzlog_listbox.delete(0, tk.END)
        tzlog_listbox_paths.clear()
        root_res = display_root.resolve()
        for path in paths:
            tzlog_listbox_paths.append(path)
            try:
                label = str(path.resolve().relative_to(root_res))
            except ValueError:
                label = str(path)
            tzlog_listbox.insert(tk.END, label)

    def selected_tzlog_path() -> Path | None:
        sel = tzlog_listbox.curselection()
        if not sel:
            return None
        idx = int(sel[0])
        if 0 <= idx < len(tzlog_listbox_paths):
            return tzlog_listbox_paths[idx]
        return None

    def view_tzlog_window(path: Path) -> None:
        try:
            body_text, truncated = read_log_file_for_gui_preview(path)
        except OSError as error:
            messagebox.showerror("Cannot read log", str(error))
            return
        win = tk.Toplevel(root)
        win.title(f"Log — {path.name}")
        win.geometry("920x640")
        frame = tk.Frame(win, padx=8, pady=8)
        frame.pack(fill=tk.BOTH, expand=True)
        header = tk.Label(frame, text=str(path), anchor="w")
        header.pack(fill=tk.X)
        if truncated:
            tk.Label(
                frame,
                text="(Preview truncated; open the file in an editor to see the rest.)",
                anchor="w",
                fg="#555",
            ).pack(fill=tk.X)
        text_w = scrolledtext.ScrolledText(
            frame,
            wrap=tk.NONE,
            height=28,
            font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10),
        )
        text_w.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        text_w.insert(tk.END, body_text)

        def copy_log_preview() -> None:
            if copy_to_clipboard(body_text):
                status_var.set("Log preview copied to clipboard.")
            else:
                messagebox.showwarning("Clipboard", "Could not copy to the clipboard.")

        btn_r = tk.Frame(frame)
        btn_r.pack(fill=tk.X, pady=(6, 0))
        tk.Button(btn_r, text="Copy preview to clipboard", command=copy_log_preview).pack(side=tk.LEFT)

    def on_tzlog_double_click(_event: object) -> None:
        path = selected_tzlog_path()
        if path and path.is_file():
            view_tzlog_window(path)

    tzlog_listbox.bind("<Double-Button-1>", on_tzlog_double_click)

    def use_selected_tzlog_for_step2() -> None:
        path = selected_tzlog_path()
        if not path:
            messagebox.showinfo("No selection", "Select a .tzlog file in the list first.")
            return
        issue_path_var.set(str(path))
        status_var.set(f"Step 2 path set to: {path.name}")

    def open_tzlogs_folder() -> None:
        base = state.get("tzlog_browse_root")
        if isinstance(base, Path) and base.exists():
            if open_path_in_os_file_manager(base):
                status_var.set(f"Opened folder: {base}")
            else:
                messagebox.showwarning("Open folder", f"Could not open: {base}")
            return
        messagebox.showinfo(
            "No folder yet",
            "Run Step 1 first so archives are extracted and a browse folder is available.",
        )

    tk.Button(logs_btn_row, text="Open logs folder", command=open_tzlogs_folder).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(logs_btn_row, text="Use selected for Step 2", command=use_selected_tzlog_for_step2).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    def view_selected_tzlog_click() -> None:
        path = selected_tzlog_path()
        if path and path.is_file():
            view_tzlog_window(path)
        else:
            messagebox.showinfo("No selection", "Select a .tzlog file in the list first.")

    tk.Button(logs_btn_row, text="View selected log", command=view_selected_tzlog_click).pack(
        side=tk.LEFT,
    )

    issue_frame = tk.LabelFrame(
        main_frame,
        text="Step 2: Issue-specific .tzlog file or folder",
        padx=10,
        pady=10,
    )
    issue_frame.pack(fill=tk.X, pady=(0, 12))

    tk.Entry(issue_frame, textvariable=issue_path_var).pack(
        side=tk.LEFT,
        fill=tk.X,
        expand=True,
        padx=(0, 8),
    )

    def select_issue_file() -> None:
        selected = filedialog.askopenfilename(
            title="Select issue .tzlog file",
            filetypes=[("Topaz logs", "*.tzlog"), ("All files", "*.*")],
        )
        if selected:
            issue_path_var.set(selected)

    def select_issue_folder() -> None:
        selected = filedialog.askdirectory(title="Select folder containing issue .tzlog files")
        if selected:
            issue_path_var.set(selected)

    tk.Button(issue_frame, text="Choose .tzlog", command=select_issue_file).pack(
        side=tk.LEFT,
        padx=(0, 8),
    )
    tk.Button(issue_frame, text="Choose Folder", command=select_issue_folder).pack(
        side=tk.LEFT,
    )

    error_frame = tk.LabelFrame(
        main_frame,
        text="Step 3: Notable error messages from problem log",
        padx=10,
        pady=10,
    )
    error_frame.pack(fill=tk.X, pady=(0, 12))

    notable_error_text = scrolledtext.ScrolledText(error_frame, wrap=tk.WORD, height=5)
    notable_error_text.pack(fill=tk.X, expand=True)

    bank_frame = tk.LabelFrame(
        main_frame,
        text="Error message bank (trends & reuse)",
        padx=10,
        pady=8,
    )
    bank_frame.pack(fill=tk.X, pady=(0, 12))

    bank_hint = tk.Label(
        bank_frame,
        text=(
            "Build a local history of error lines. Filter matches sample text; double-click a row "
            f"to append it to Step 3. Bank file: {error_bank.default_bank_path()}"
        ),
        anchor="w",
        wraplength=850,
        justify=tk.LEFT,
    )
    bank_hint.pack(fill=tk.X)

    auto_record_bank_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        bank_frame,
        text="When building the crashpad report, also record Step 3 lines into the bank",
        variable=auto_record_bank_var,
    ).pack(anchor="w", pady=(4, 0))

    filter_top = tk.Frame(bank_frame)
    filter_top.pack(fill=tk.X, pady=(6, 4))
    tk.Label(filter_top, text="Filter:").pack(side=tk.LEFT, padx=(0, 6))
    bank_filter_var = tk.StringVar()
    filter_entry = tk.Entry(filter_top, textvariable=bank_filter_var)
    filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

    list_row = tk.Frame(bank_frame)
    list_row.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

    bank_list_rows: list[error_bank.BankEntryView] = []

    bank_listbox = tk.Listbox(list_row, height=6, exportselection=False)
    bank_scroll = tk.Scrollbar(list_row, orient=tk.VERTICAL, command=bank_listbox.yview)
    bank_listbox.configure(yscrollcommand=bank_scroll.set)
    bank_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    bank_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_bank_listbox(*_args: object) -> None:
        bank_listbox.delete(0, tk.END)
        bank_list_rows.clear()
        query = bank_filter_var.get().strip()
        entries = error_bank.search_entries(query, limit=40) if query else error_bank.list_entries_sorted(limit=40)
        for entry in entries:
            bank_list_rows.append(entry)
            short = entry.sample_text.replace("\n", " ")
            if len(short) > 92:
                short = short[:89] + "..."
            bank_listbox.insert(tk.END, f"{entry.count:>4}  {short}")

    def insert_bank_selection(_event: object | None = None) -> None:
        selection = bank_listbox.curselection()
        if not selection:
            return
        entry = bank_list_rows[int(selection[0])]
        notable_error_text.insert(tk.END, entry.sample_text + "\n")

    bank_listbox.bind("<Double-Button-1>", insert_bank_selection)

    def record_step3_to_bank() -> None:
        raw = notable_error_text.get("1.0", tk.END)
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            messagebox.showinfo("Nothing to record", "Add one or more error lines in Step 3 first.")
            return
        _new_keys, path = error_bank.record_lines(lines)
        refresh_bank_listbox()
        status_var.set(f"Recorded {len(lines)} line(s) to error bank ({path.name}).")

    def show_trends_window() -> None:
        win = tk.Toplevel(root)
        win.title("Error bank — frequency view")
        win.geometry("880x520")
        body = tk.Frame(win, padx=10, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        text = scrolledtext.ScrolledText(body, wrap=tk.WORD, height=24)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, error_bank.format_trends_text(limit=500))
        text.configure(state=tk.DISABLED)

        def copy_trends() -> None:
            if copy_to_clipboard(error_bank.format_trends_text(limit=500)):
                status_var.set("Trends list copied to clipboard.")
            else:
                messagebox.showwarning("Clipboard", "Could not copy trends to the clipboard.")

        btn_row = tk.Frame(win, padx=10, pady=(0, 10))
        btn_row.pack(fill=tk.X)
        tk.Button(btn_row, text="Copy trends to clipboard", command=copy_trends).pack(side=tk.LEFT)

    bank_btn_row = tk.Frame(bank_frame)
    bank_btn_row.pack(fill=tk.X, pady=(0, 2))
    tk.Button(bank_btn_row, text="Record Step 3 to bank", command=record_step3_to_bank).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(bank_btn_row, text="Refresh list", command=refresh_bank_listbox).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(bank_btn_row, text="Trends window…", command=show_trends_window).pack(side=tk.LEFT)

    filter_entry.bind("<KeyRelease>", refresh_bank_listbox)
    refresh_bank_listbox()

    button_frame = tk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=(0, 12))

    output = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=20)
    output.pack(fill=tk.BOTH, expand=True)

    def set_output(text: str) -> None:
        output.delete("1.0", tk.END)
        output.insert(tk.END, text)

    def run_system_info_step() -> None:
        raw_path = support_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing path", "Choose a support download folder or archive first.")
            return

        input_path = Path(raw_path).expanduser().resolve()
        state["support_path"] = input_path

        try:
            system_info_report, _system_info_entry, extracted_dirs, tzlog_files = process_system_info_from_path(
                input_path
            )
        except Exception as error:
            messagebox.showerror("System information failed", str(error))
            status_var.set("System information failed.")
            state["tzlog_browse_root"] = None
            refresh_tzlog_list([], input_path)
            return

        state["system_info_report"] = system_info_report
        state["extracted_dirs"] = extracted_dirs
        browse_root = gui_display_root_for_tzlogs(input_path, extracted_dirs)
        state["tzlog_browse_root"] = browse_root
        refresh_tzlog_list(tzlog_files, browse_root)

        if len(tzlog_files) == 1:
            issue_path_var.set(str(tzlog_files[0]))
        else:
            issue_path_var.set(str(browse_root))

        extraction_note = ""
        if extracted_dirs:
            extraction_note = "\n\nExtracted archive folders:\n" + "\n".join(
                f"- {path}" for path in extracted_dirs
            )

        set_output(system_info_report + extraction_note)

        n_logs = len(tzlog_files)
        if copy_to_clipboard(system_info_report):
            status_var.set(f"System information copied. {n_logs} .tzlog file(s) listed — Step 2 path preset for review.")
        else:
            status_var.set(
                f"System information found ({n_logs} .tzlog listed), but clipboard copy failed. Step 2 path preset."
            )

        for dest in report_destination_dirs(extracted_dirs, input_path):
            write_report_to_txt(system_info_report, dest, "support_system_information.txt")

    tk.Button(
        step1_action_row,
        text="Run Step 1 — Extract archives, list .tzlog files, copy system info",
        command=run_system_info_step,
    ).pack(fill=tk.X)

    def run_issue_step() -> None:
        raw_path = issue_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing issue log", "Choose the issue-specific .tzlog file or folder first.")
            return

        issue_path = Path(raw_path).expanduser().resolve()
        notable_errors_raw = notable_error_text.get("1.0", tk.END).strip()
        notable_errors = notable_errors_raw or "Not provided"
        if notable_errors != "Not provided":
            notable_errors = format_notable_errors_for_report(notable_errors.splitlines())

        try:
            issue_report = process_issue_logs_from_path(issue_path, notable_errors)
        except Exception as error:
            messagebox.showerror("Issue log failed", str(error))
            status_var.set("Issue log parsing failed.")
            return

        full_report = issue_report
        if state["system_info_report"]:
            full_report = state["system_info_report"] + "\n" + issue_report

        state["final_report"] = full_report
        set_output(full_report)

        if copy_to_clipboard(full_report):
            status_var.set("Full support report copied to clipboard.")
        else:
            status_var.set("Full support report saved, but clipboard copy failed.")

        if auto_record_bank_var.get() and notable_errors_raw:
            bank_lines = [ln.strip() for ln in notable_errors_raw.splitlines() if ln.strip()]
            if bank_lines:
                error_bank.record_lines(bank_lines)
                refresh_bank_listbox()

        fallback_folder = state["support_path"] or issue_path
        extracted_dirs: list[Path] = state.get("extracted_dirs") or []
        for dest in report_destination_dirs(extracted_dirs, fallback_folder):
            write_report_to_txt(full_report, dest, "support_full_tzlog_report.txt")

    def copy_current_output() -> None:
        current_text = output.get("1.0", tk.END).strip()
        if not current_text:
            messagebox.showwarning("Nothing to copy", "There is no report text to copy yet.")
            return

        if copy_to_clipboard(current_text):
            status_var.set("Current output copied to clipboard.")
        else:
            status_var.set("Clipboard copy failed.")

    tk.Button(
        button_frame,
        text="2. Build Crashpad Report",
        command=run_issue_step,
    ).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(
        button_frame,
        text="Copy Current Output",
        command=copy_current_output,
    ).pack(side=tk.LEFT)

    tk.Label(main_frame, textvariable=status_var, anchor="w").pack(fill=tk.X, pady=(8, 0))

    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract support triage details from .tzlog files."
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a .tzlog file, a directory containing .tzlog files, or a folder containing archive files with .tzlog files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of text report.",
    )
    parser.add_argument(
        "--error-bank-stats",
        action="store_true",
        help="Print cached error lines sorted by frequency (from the local error bank) and exit.",
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
