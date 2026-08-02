# supportTools

Internal tools for speeding up Support workflows.

The current app is **Topaz tzlog Reader**, a local tool for reviewing Topaz `.tzlog`
support downloads, finding likely error patterns, and building paste-ready Support
reports.

## What tzlog Reader Does

- Reads individual `.tzlog` files, folders of logs, or support archives.
- Extracts `.zip`, `.tar`, `.tar.gz`, and `.tgz` archives.
- Finds `.tzlog` files inside extracted support folders.
- Pulls system information from logs:
  - user OS inferred from the log path
  - Topaz Photo version
  - activation email, when present
  - OS, CPU, RAM, indexed GPUs, and GPU VRAM
- Pulls issue log details, including Crashpad session IDs.
- Scans issue logs for likely notable error patterns.
- Tracks reviewed error patterns in a local bank for reuse and trend checks.
- Saves report text files next to the processed logs.
- Copies reports to the clipboard on macOS and Windows when possible.

## Requirements

- Python 3.10 or newer.
- Tkinter for the desktop GUI. It is included with most Python installs.
- Node.js 18 or newer for the web UI.

The Python tools currently use only the standard library. `requirements.txt` is
kept for the setup workflow, but it does not install any third-party packages.

## Setup

From the repository root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

No `npm install` step is required for the web UI because it uses only built-in
Node modules.

## Launch the Web App

The web UI is the easiest way to run the current app.

```bash
cd tools/tzLogReader
npm run start
```

If your environment exposes Python as `python` instead of `python3`, set
`PYTHON=python` before starting the server. You can also provide a launcher
command with arguments such as `PYTHON="py -3"` or a quoted full path.

Then open:

```text
http://127.0.0.1:8765
```

Optional: run on a different port.

```bash
PORT=9000 npm run start
```

## Web App Walkthrough

1. Paste the path to a support folder or supported archive in **Support Download**.
2. Click **Run Step 1**.
3. Review the discovered `.tzlog` files and generated system information.
4. Click **Use** next to the issue-specific log, or paste an issue log/folder path
   in **Issue Log**.
5. Click **Scan for Errors**.
6. Select useful candidate patterns and click **Add Selected to Step 3**.
7. Optionally record selected patterns into the error bank for future trend checks.
8. Click **Build Report**.
9. Click **Copy Report** and paste the output into the Support workflow.

The app writes:

- `support_system_information.txt`
- `support_full_tzlog_report.txt`

When archives are extracted, reports are written into the extracted archive
folder. Otherwise, they are written next to the selected input path.

## Launch the Desktop GUI

From the repository root:

```bash
python tools/tzLogReader/tzlog_reader.py
```

Desktop GUI flow:

1. Choose a support folder or archive.
2. Run Step 1 to extract archives, list logs, copy system info, and save
   `support_system_information.txt`.
3. Select the issue-specific `.tzlog` file or folder.
4. Scan/review notable error messages, or add reviewed patterns manually.
5. Build the Crashpad report.

The final report is copied to the clipboard when possible and saved as
`support_full_tzlog_report.txt`.

## CLI Usage

Run with a path to a `.tzlog` file, folder, or supported archive:

```bash
python tools/tzLogReader/tzlog_reader.py path/to/support-folder-or-archive
```

The CLI prints the system information report, copies it when possible, saves it,
then prompts for the issue log path.

JSON output is available:

```bash
python tools/tzLogReader/tzlog_reader.py path/to/logs --json
```

Multi-log session scanning is available:

```bash
python tools/tzLogReader/tzlog_reader.py path/to/support-folder-or-archive --session-scan
```

This groups discovered `.tzlog` files into launch sessions, sorts them
chronologically, and assigns each session a typed status so the same engine can
be reused by CLI, web, and downstream automation workflows.

The session engine also records explicit termination analysis, distinguishing
confirmed crash evidence, probable abrupt termination, forced quit,
incomplete log capture, and graceful shutdown so support workflows can avoid
mislabeling every incomplete log as a crash.

The engine also supports an external, rule-driven issue classifier. Rules are
stored in the data file below so Support can tune classifications without
changing parser code:

```text
tools/tzLogReader/issue_rules.json
```

The classifier loads those rules and attaches machine-readable issue matches to
session results, including a category, severity, and recommended follow-up
information.

View saved error-pattern trends:

```bash
python tools/tzLogReader/tzlog_reader.py --error-bank-stats
```

## Error Pattern Bank

Reviewed notable error patterns are stored locally in:

```text
tools/tzLogReader/support_error_bank.json
```

The bank stores normalized patterns and per-date counts, not raw user-specific
log lines.

## Project Layout

```text
tools/tzLogReader/
  tzlog_reader.py        CLI entry point and desktop GUI launcher
  gui_app.py             Tkinter desktop GUI
  server.js              local Node web server
  web_bridge.py          JSON bridge between the web UI and Python parser
  analysis_model.py      typed internal analysis model for sessions and archives
  log_parser.py          archive extraction, typed tzlog parsing, report formatting
  issue_classifier.py    rule-based session issue classification layer
  issue_rules.json       external support-facing issue rule definitions
  error_scanner.py       notable error candidate detection
  error_message_bank.py  local normalized pattern bank
  web/                   browser UI assets
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
