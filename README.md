# supportTools

Internal Python tools for speeding up Support workflows.

Current focus:

- Topaz `.tzlog` troubleshooting
- HelpScout ticket notes
- Linear bug report details
- Local trend tracking for repeated error messages

## tzLogReader

`tools/tzLogReader/tzlog_reader.py` reads Topaz `.tzlog` files and builds paste-ready Support reports.

It can:

- open a simple GUI when run without arguments
- process a `.tzlog` file, a folder of logs, or a support archive
- extract `.zip`, `.tar`, `.tar.gz`, and `.tgz` files
- find `.tzlog` files inside extracted folders
- copy reports to the clipboard on macOS and Windows
- save report text files next to the processed logs
- keep a local bank of repeated notable error messages

## What It Collects

System information:

- User OS inferred from the log path
- Topaz Photo version
- User email, when present
- OS
- CPU
- RAM
- Indexed GPUs
- GPU VRAM

Issue log information:

- `.tzlog` filename
- Crashpad session ID
- Notable error messages entered by Support

## GUI Workflow

Run the tool:

```bash
python tools/tzLogReader/tzlog_reader.py
```

Then:

1. Choose a support folder or archive.
2. Run Step 1 to extract archives, list logs, copy system info, and save `support_system_information.txt`.
3. Select the issue-specific `.tzlog` file or folder.
4. Add any notable error messages.
5. Build the Crashpad report.

The final report is copied to the clipboard and saved as `support_full_tzlog_report.txt`.

## CLI Workflow

Run the tool with a path:

```bash
python tools/tzLogReader/tzlog_reader.py path/to/support-folder-or-archive
```

The path can be:

- a `.tzlog` file
- a folder containing `.tzlog` files
- a folder containing supported archives
- a supported archive file

The CLI prints the system info report, copies it when possible, then asks for the issue log path.

JSON output is also available:

```bash
python tools/tzLogReader/tzlog_reader.py path/to/logs --json
```

## Error Message Bank

The GUI can store notable error lines in a local file:

```text
tools/tzLogReader/support_error_bank.json
```

View stored trends from the CLI:

```bash
python tools/tzLogReader/tzlog_reader.py --error-bank-stats
```

## Example Report

```text
SYSTEM INFORMATION:

  User OS: Windows
  Topaz Photo version: 1.3.3
  User email (activation): user@example.com
  OS: Windows Version 11.240000
  CPU: Intel(R) Core(TM) i5-6400 CPU @ 2.70GHz
  RAM: 15.9 GB Total / 7.2 GB Used
  Indexed GPUs:
    - Index 0: Default GPU | VRAM: 2.0 GB Total / 0.0 GB Used

ISSUE LOG INFORMATION:

  Log file: 2026-05-12-10-38-58.tzlog
  Crashpad session ID: abc123

NOTABLE ERROR MESSAGES:

- Example error line from the issue log
```

## Setup

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```
