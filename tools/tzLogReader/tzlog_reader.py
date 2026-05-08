#!/usr/bin/env python3
"""Extract first-step troubleshooting details from Topaz .tzlog files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import tkinter as tk
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext
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

    return "\n".join(f"  - {line}" for line in lines)


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


def write_report_to_txt(report: str, output_folder: Path, filename: str = "support_tzlog_report.txt") -> Path:
    """Write report text to a .txt file inside the selected Support folder."""
    if output_folder.is_file():
        output_folder = output_folder.parent

    output_folder.mkdir(parents=True, exist_ok=True)
    report_path = output_folder / filename
    report_path.write_text(report, encoding="utf-8")
    return report_path


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


def format_notable_error_report(notable_errors: str) -> str:
    lines = [
        "Notable error messages:",
        notable_errors,
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


# === GUI and helper functions for GUI mode ===

def process_system_info_from_path(input_path: Path) -> tuple[str, dict[str, Any] | None, list[Path]]:
    """Extract archives, locate tzlogs, parse system info, and return a report."""
    extracted_dirs = extract_archive_files(input_path)

    system_info_files: list[Path] = []
    for search_path in get_system_info_search_paths(input_path, extracted_dirs):
        system_info_files.extend(discover_log_files(search_path))

    if not system_info_files:
        raise FileNotFoundError(
            "No .tzlog files found for system information in the extracted archive folders or original path."
        )

    parsed_system_info_logs = [parse_tzlog(file_path) for file_path in system_info_files]
    system_info_entry = find_first_log_with_system_info(parsed_system_info_logs)
    system_info_report = format_system_info_report(system_info_entry)

    return system_info_report, system_info_entry, extracted_dirs


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

    return ("\n" + ("-" * 72) + "\n").join(report_blocks)


def run_gui() -> None:
    """Launch a simple GUI for Support users who do not use the CLI."""
    root = tk.Tk()
    root.title("Topaz tzlog Reader")
    root.geometry("900x760")

    state: dict[str, Any] = {
        "system_info_report": "",
        "final_report": "",
        "support_path": None,
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
            "Select a Support download folder or archive. The tool will extract archives, "
            "find .tzlog files, copy system information, then build the Linear crashpad report."
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

    tk.Entry(support_frame, textvariable=support_path_var).pack(
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

    tk.Button(support_frame, text="Choose Folder", command=select_support_folder).pack(
        side=tk.LEFT,
        padx=(0, 8),
    )
    tk.Button(support_frame, text="Choose Archive", command=select_support_archive).pack(
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
            system_info_report, _system_info_entry, extracted_dirs = process_system_info_from_path(input_path)
        except Exception as error:
            messagebox.showerror("System information failed", str(error))
            status_var.set("System information failed.")
            return

        state["system_info_report"] = system_info_report

        extraction_note = ""
        if extracted_dirs:
            extraction_note = "\n\nExtracted archive folders:\n" + "\n".join(
                f"- {path}" for path in extracted_dirs
            )

        set_output(system_info_report + extraction_note)

        if copy_to_clipboard(system_info_report):
            status_var.set("System information copied to clipboard.")
        else:
            status_var.set("System information found, but clipboard copy failed.")

        write_report_to_txt(system_info_report, input_path, "support_system_information.txt")

    def run_issue_step() -> None:
        raw_path = issue_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing issue log", "Choose the issue-specific .tzlog file or folder first.")
            return

        issue_path = Path(raw_path).expanduser().resolve()
        notable_errors = notable_error_text.get("1.0", tk.END).strip() or "Not provided"
        if notable_errors != "Not provided":
            notable_errors = "\n".join(f"  - {line.strip()}" for line in notable_errors.splitlines() if line.strip())

        try:
            issue_report = process_issue_logs_from_path(issue_path, notable_errors)
        except Exception as error:
            messagebox.showerror("Issue log failed", str(error))
            status_var.set("Issue log parsing failed.")
            return

        state["final_report"] = issue_report
        set_output(issue_report)

        if copy_to_clipboard(issue_report):
            status_var.set("Issue report copied to clipboard.")
        else:
            status_var.set("Issue report created, but clipboard copy failed.")

        output_folder = state["support_path"] or issue_path
        full_report = issue_report
        if state["system_info_report"]:
            full_report = state["system_info_report"] + "\n" + ("-" * 72) + "\n" + issue_report
        write_report_to_txt(full_report, output_folder, "support_full_tzlog_report.txt")

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
        text="1. Extract + Copy System Info",
        command=run_system_info_step,
    ).pack(side=tk.LEFT, padx=(0, 8))

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
    args = parser.parse_args()

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
    system_info_entry = find_first_log_with_system_info(parsed_system_info_logs)

    system_info_report = format_system_info_report(system_info_entry)
    print("\n" + system_info_report)

    if copy_to_clipboard(system_info_report):
        print("\nCopied system information to clipboard.")
    else:
        print("\nCould not copy system information to clipboard automatically.")

    system_info_report_path = write_report_to_txt(
        system_info_report,
        input_path,
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

    report = ("\n" + ("-" * 72) + "\n").join(report_blocks)
    print(report)

    if copy_to_clipboard(report):
        print("\nCopied report to clipboard.")
    else:
        print("\nCould not copy report to clipboard automatically.")

    full_report = system_info_report + "\n" + ("-" * 72) + "\n" + report
    full_report_path = write_report_to_txt(
        full_report,
        input_path,
        "support_full_tzlog_report.txt",
    )
    print(f"Full Support report saved to: {full_report_path}")


if __name__ == "__main__":
    main()
