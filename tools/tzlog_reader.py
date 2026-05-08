#!/usr/bin/env python3
"""Extract first-step troubleshooting details from Topaz .tzlog files."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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



def discover_log_files(path_input: Path) -> list[Path]:
    if path_input.is_file():
        return [path_input]
    if path_input.is_dir():
        return sorted(path_input.rglob("*.tzlog"))
    return []


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
        help="Path to a .tzlog file or a directory containing .tzlog files.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of text report.",
    )
    args = parser.parse_args()

    input_path = Path(args.path).expanduser().resolve()
    files = discover_log_files(input_path)

    if not files:
        raise SystemExit(f"No .tzlog files found at: {input_path}")

    parsed = [parse_tzlog(file_path) for file_path in files]

    if args.json:
        print(json.dumps(parsed, indent=2))
        return

    report_blocks: list[str] = []
    for entry in parsed:
        report_blocks.append(format_text_report(entry))

    report = ("\n" + ("-" * 72) + "\n").join(report_blocks)
    print(report)

    if copy_to_clipboard(report):
        print("\nCopied report to clipboard.")
    else:
        print("\nCould not copy report to clipboard automatically.")


if __name__ == "__main__":
    main()
