"""Tkinter GUI for support-oriented tzlog review."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from typing import Any
from urllib.parse import unquote, urlparse

import error_message_bank as error_bank
from error_scanner import ErrorCandidate, scan_log_file_for_error_candidates, scan_path_for_error_candidates
from log_parser import (
    format_notable_errors_for_report,
    process_issue_logs_from_path,
    process_system_info_from_path,
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


def gui_display_root_for_tzlogs(input_path: Path, extracted_dirs: list[Path]) -> Path:
    if extracted_dirs:
        return extracted_dirs[0].resolve()
    if input_path.is_dir():
        return input_path.resolve()
    return input_path.resolve().parent


def open_path_in_os_file_manager(path: Path) -> bool:
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


def clean_user_path(raw_path: str) -> Path:
    """Normalize paths pasted from Finder, Terminal, or file dialogs."""
    cleaned = raw_path.strip().strip("'\"")
    if cleaned.startswith("file://"):
        parsed = urlparse(cleaned)
        cleaned = unquote(parsed.path)
    cleaned = cleaned.replace("\\ ", " ")
    return Path(cleaned).expanduser().resolve()


def run_gui() -> None:
    root = tk.Tk()
    root.title("Topaz tzlog Reader")
    root.geometry("980x980")

    state: dict[str, Any] = {
        "system_info_report": "",
        "final_report": "",
        "support_path": None,
        "extracted_dirs": [],
        "tzlog_browse_root": None,
        "review_candidates": [],
    }

    main_frame = tk.Frame(root, padx=16, pady=16)
    main_frame.pack(fill=tk.BOTH, expand=True)

    tk.Label(main_frame, text="Topaz tzlog Reader", font=("Arial", 18, "bold"), anchor="w").pack(fill=tk.X)
    tk.Label(
        main_frame,
        text=(
            "Choose a folder or archive in Step 1, then select the issue log for Step 2. "
            "Use the scanner to review likely error patterns before building the report."
        ),
        anchor="w",
        wraplength=930,
        justify=tk.LEFT,
    ).pack(fill=tk.X, pady=(4, 16))

    support_path_var = tk.StringVar()
    issue_path_var = tk.StringVar()
    status_var = tk.StringVar(value="Ready.")

    support_frame = tk.LabelFrame(main_frame, text="Step 1: Support download folder or archive", padx=10, pady=10)
    support_frame.pack(fill=tk.X, pady=(0, 12))
    support_path_row = tk.Frame(support_frame)
    support_path_row.pack(fill=tk.X)
    tk.Entry(support_path_row, textvariable=support_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

    def select_support_folder() -> None:
        selected = filedialog.askdirectory(title="Select support download folder")
        if selected:
            support_path_var.set(selected)

    def select_support_archive() -> None:
        selected = filedialog.askopenfilename(
            title="Select support archive",
            filetypes=[
                ("Supported archives", ("*.tar", "*.tar.gz", "*.tgz", "*.zip")),
                ("Tar archives", "*.tar"),
                ("Compressed tar archives", ("*.tar.gz", "*.tgz")),
                ("Zip archives", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            support_path_var.set(selected)

    tk.Button(support_path_row, text="Choose Folder", command=select_support_folder).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(support_path_row, text="Choose Archive File", command=select_support_archive).pack(side=tk.LEFT)
    step1_action_row = tk.Frame(support_frame)
    step1_action_row.pack(fill=tk.X, pady=(10, 0))

    logs_frame = tk.LabelFrame(main_frame, text="Discovered .tzlog files (after Step 1)", padx=10, pady=10)
    logs_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 12))
    tzlog_list_row = tk.Frame(logs_frame)
    tzlog_list_row.pack(fill=tk.BOTH, expand=True)
    tzlog_listbox = tk.Listbox(tzlog_list_row, height=6, exportselection=False)
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
        return tzlog_listbox_paths[idx] if 0 <= idx < len(tzlog_listbox_paths) else None

    def view_tzlog_window(path: Path) -> None:
        try:
            body_text, truncated = read_log_file_for_gui_preview(path)
        except OSError as error:
            messagebox.showerror("Cannot read log", str(error))
            return
        win = tk.Toplevel(root)
        win.title(f"Log - {path.name}")
        win.geometry("940x660")
        frame = tk.Frame(win, padx=8, pady=8)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(frame, text=str(path), anchor="w").pack(fill=tk.X)
        if truncated:
            tk.Label(frame, text="Preview truncated; open the file in an editor to see the rest.", anchor="w", fg="#555").pack(fill=tk.X)
        text_w = scrolledtext.ScrolledText(
            frame,
            wrap=tk.NONE,
            height=28,
            font=("Menlo", 11) if sys.platform == "darwin" else ("Consolas", 10),
        )
        text_w.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        text_w.insert(tk.END, body_text)

        for candidate in scan_log_file_for_error_candidates(path):
            if not candidate.known_match:
                continue
            start = "1.0"
            while True:
                pos = text_w.search(candidate.example_text, start, tk.END)
                if not pos:
                    break
                end = f"{pos}+{len(candidate.example_text)}c"
                text_w.tag_add("known_match", pos, end)
                start = end
        text_w.tag_configure("known_match", background="#fff2a8")

        btn_r = tk.Frame(frame)
        btn_r.pack(fill=tk.X, pady=(6, 0))
        tk.Button(btn_r, text="Copy preview to clipboard", command=lambda: copy_to_clipboard(body_text)).pack(side=tk.LEFT)

    tzlog_listbox.bind("<Double-Button-1>", lambda _event: view_tzlog_window(selected_tzlog_path()) if selected_tzlog_path() else None)

    def use_selected_tzlog_for_step2() -> None:
        path = selected_tzlog_path()
        if not path:
            messagebox.showinfo("No selection", "Select a .tzlog file in the list first.")
            return
        issue_path_var.set(str(path))
        status_var.set(f"Step 2 path set to: {path.name}")

    def open_tzlogs_folder() -> None:
        base = state.get("tzlog_browse_root")
        if isinstance(base, Path) and base.exists() and open_path_in_os_file_manager(base):
            status_var.set(f"Opened folder: {base}")
            return
        messagebox.showinfo("No folder yet", "Run Step 1 first so a browse folder is available.")

    tk.Button(logs_btn_row, text="Open logs folder", command=open_tzlogs_folder).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(logs_btn_row, text="Use selected for Step 2", command=use_selected_tzlog_for_step2).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(logs_btn_row, text="View selected log", command=lambda: view_tzlog_window(selected_tzlog_path()) if selected_tzlog_path() else messagebox.showinfo("No selection", "Select a .tzlog file in the list first.")).pack(side=tk.LEFT)

    issue_frame = tk.LabelFrame(main_frame, text="Step 2: Issue-specific .tzlog file or folder", padx=10, pady=10)
    issue_frame.pack(fill=tk.X, pady=(0, 12))
    tk.Entry(issue_frame, textvariable=issue_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
    tk.Button(
        issue_frame,
        text="Choose .tzlog",
        command=lambda: issue_path_var.set(filedialog.askopenfilename(title="Select issue .tzlog file", filetypes=[("Topaz logs", "*.tzlog"), ("All files", "*.*")]) or issue_path_var.get()),
    ).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(
        issue_frame,
        text="Choose Folder",
        command=lambda: issue_path_var.set(filedialog.askdirectory(title="Select folder containing issue .tzlog files") or issue_path_var.get()),
    ).pack(side=tk.LEFT)

    error_frame = tk.LabelFrame(main_frame, text="Step 3: Notable error patterns for report", padx=10, pady=10)
    error_frame.pack(fill=tk.X, pady=(0, 12))
    notable_error_text = scrolledtext.ScrolledText(error_frame, wrap=tk.WORD, height=5)
    notable_error_text.pack(fill=tk.X, expand=True)

    review_frame = tk.LabelFrame(main_frame, text="Review scanned error candidates", padx=10, pady=8)
    review_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 12))
    review_list_rows: list[ErrorCandidate] = []
    review_listbox = tk.Listbox(review_frame, height=7, selectmode=tk.EXTENDED, exportselection=False)
    review_scroll = tk.Scrollbar(review_frame, orient=tk.VERTICAL, command=review_listbox.yview)
    review_listbox.configure(yscrollcommand=review_scroll.set)
    review_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    review_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def refresh_review_list(candidates: list[ErrorCandidate]) -> None:
        review_listbox.delete(0, tk.END)
        review_list_rows.clear()
        for candidate in candidates:
            review_list_rows.append(candidate)
            prefix = "KNOWN" if candidate.known_match else "NEW  "
            short = candidate.pattern_text.replace("\n", " ")
            if len(short) > 110:
                short = short[:107] + "..."
            review_listbox.insert(tk.END, f"{prefix} x{candidate.count_in_log:<3} score {candidate.score:<2} {short}")
            if candidate.known_match:
                review_listbox.itemconfig(tk.END, bg="#fff2a8")

    def selected_review_candidates() -> list[ErrorCandidate]:
        return [review_list_rows[int(i)] for i in review_listbox.curselection()]

    def append_review_selection_to_step3(_event: object | None = None) -> None:
        selected = selected_review_candidates()
        if not selected:
            return
        existing = notable_error_text.get("1.0", tk.END)
        for candidate in selected:
            if candidate.pattern_text not in existing:
                notable_error_text.insert(tk.END, candidate.pattern_text + "\n")

    review_listbox.bind("<Double-Button-1>", append_review_selection_to_step3)

    review_btn_row = tk.Frame(review_frame)
    review_btn_row.pack(fill=tk.X, pady=(8, 0), side=tk.BOTTOM)

    def scan_issue_log() -> None:
        raw_path = issue_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing issue log", "Choose the issue-specific .tzlog file or folder first.")
            return
        issue_path = clean_user_path(raw_path)
        try:
            candidates = scan_path_for_error_candidates(issue_path)
        except Exception as error:
            messagebox.showerror("Scan failed", str(error))
            return
        state["review_candidates"] = candidates
        refresh_review_list(candidates)
        known_count = sum(1 for candidate in candidates if candidate.known_match)
        status_var.set(f"Scanned {len(candidates)} candidate pattern(s); {known_count} known match(es).")

    tk.Button(review_btn_row, text="Scan issue log for errors", command=scan_issue_log).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(review_btn_row, text="Add selected to Step 3", command=append_review_selection_to_step3).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(
        review_btn_row,
        text="Record selected patterns",
        command=lambda: record_patterns_to_bank([c.pattern_text for c in selected_review_candidates()]),
    ).pack(side=tk.LEFT)

    bank_frame = tk.LabelFrame(main_frame, text="Error pattern bank (trends & reuse)", padx=10, pady=8)
    bank_frame.pack(fill=tk.X, pady=(0, 12))
    tk.Label(
        bank_frame,
        text=f"Stores reusable normalized patterns and per-date counts. Bank file: {error_bank.default_bank_path()}",
        anchor="w",
        wraplength=930,
        justify=tk.LEFT,
    ).pack(fill=tk.X)

    auto_record_bank_var = tk.BooleanVar(value=False)
    tk.Checkbutton(
        bank_frame,
        text="When building the crashpad report, record reviewed Step 3 patterns into the bank",
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
    bank_listbox = tk.Listbox(list_row, height=5, exportselection=False)
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
            short = entry.pattern_text.replace("\n", " ")
            if len(short) > 100:
                short = short[:97] + "..."
            bank_listbox.insert(tk.END, f"{entry.count:>4}  [{entry.last_seen}]  {short}")

    def insert_bank_selection(_event: object | None = None) -> None:
        selection = bank_listbox.curselection()
        if not selection:
            return
        entry = bank_list_rows[int(selection[0])]
        notable_error_text.insert(tk.END, entry.pattern_text + "\n")

    bank_listbox.bind("<Double-Button-1>", insert_bank_selection)

    def record_patterns_to_bank(lines: list[str] | None = None) -> None:
        patterns = lines if lines is not None else [ln.strip() for ln in notable_error_text.get("1.0", tk.END).splitlines() if ln.strip()]
        if not patterns:
            messagebox.showinfo("Nothing to record", "Add or select one or more reviewed patterns first.")
            return
        _new_keys, path = error_bank.record_lines(patterns)
        refresh_bank_listbox()
        status_var.set(f"Recorded {len(patterns)} reviewed pattern(s) to {path.name}.")

    def show_trends_window() -> None:
        win = tk.Toplevel(root)
        win.title("Error bank - frequency view")
        win.geometry("900x520")
        body = tk.Frame(win, padx=10, pady=10)
        body.pack(fill=tk.BOTH, expand=True)
        text = scrolledtext.ScrolledText(body, wrap=tk.WORD, height=24)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, error_bank.format_trends_text(limit=500))
        text.configure(state=tk.DISABLED)
        tk.Button(win, text="Copy trends to clipboard", command=lambda: copy_to_clipboard(error_bank.format_trends_text(limit=500))).pack(anchor="w", padx=10, pady=(0, 10))

    bank_btn_row = tk.Frame(bank_frame)
    bank_btn_row.pack(fill=tk.X, pady=(0, 2))
    tk.Button(bank_btn_row, text="Record Step 3 to bank", command=record_patterns_to_bank).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(bank_btn_row, text="Refresh list", command=refresh_bank_listbox).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(bank_btn_row, text="Trends window", command=show_trends_window).pack(side=tk.LEFT)
    filter_entry.bind("<KeyRelease>", refresh_bank_listbox)
    refresh_bank_listbox()

    button_frame = tk.Frame(main_frame)
    button_frame.pack(fill=tk.X, pady=(0, 12))
    output = scrolledtext.ScrolledText(main_frame, wrap=tk.WORD, height=16)
    output.pack(fill=tk.BOTH, expand=True)

    def set_output(text: str) -> None:
        output.delete("1.0", tk.END)
        output.insert(tk.END, text)

    def run_system_info_step() -> None:
        raw_path = support_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing path", "Choose a support download folder or archive first.")
            return
        input_path = clean_user_path(raw_path)
        if not input_path.exists():
            messagebox.showerror("Path not found", f"Could not find:\n{input_path}")
            status_var.set("Step 1 path not found.")
            return
        state["support_path"] = input_path
        try:
            system_info_report, _system_info_entry, extracted_dirs, tzlog_files = process_system_info_from_path(input_path)
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
        issue_path_var.set(str(tzlog_files[0] if len(tzlog_files) == 1 else browse_root))

        extraction_note = ""
        if extracted_dirs:
            extraction_note = "\n\nExtracted archive folders:\n" + "\n".join(f"- {path}" for path in extracted_dirs)
        set_output(system_info_report + extraction_note)
        n_logs = len(tzlog_files)
        if copy_to_clipboard(system_info_report):
            status_var.set(f"System information copied. {n_logs} .tzlog file(s) listed.")
        else:
            status_var.set(f"System information found ({n_logs} .tzlog listed), but clipboard copy failed.")
        for dest in report_destination_dirs(extracted_dirs, input_path):
            write_report_to_txt(system_info_report, dest, "support_system_information.txt")

    tk.Button(step1_action_row, text="Run Step 1 - Extract archives, list .tzlog files, copy system info", command=run_system_info_step).pack(fill=tk.X)

    def run_issue_step() -> None:
        raw_path = issue_path_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing issue log", "Choose the issue-specific .tzlog file or folder first.")
            return
        issue_path = clean_user_path(raw_path)
        notable_errors_raw = notable_error_text.get("1.0", tk.END).strip()
        notable_errors = notable_errors_raw or "Not provided"
        if notable_errors != "Not provided":
            notable_errors = format_notable_errors_for_report(notable_errors.splitlines())

        known_matches = error_bank.known_matches_for_lines(notable_errors_raw.splitlines()) if notable_errors_raw else []
        try:
            issue_report = process_issue_logs_from_path(issue_path, notable_errors, known_matches)
        except Exception as error:
            messagebox.showerror("Issue log failed", str(error))
            status_var.set("Issue log parsing failed.")
            return

        full_report = issue_report
        if state["system_info_report"]:
            full_report = state["system_info_report"] + "\n" + issue_report
        state["final_report"] = full_report
        set_output(full_report)
        status_var.set("Full support report copied to clipboard." if copy_to_clipboard(full_report) else "Full support report saved, but clipboard copy failed.")

        if auto_record_bank_var.get() and notable_errors_raw:
            error_bank.record_lines([ln.strip() for ln in notable_errors_raw.splitlines() if ln.strip()])
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
        status_var.set("Current output copied to clipboard." if copy_to_clipboard(current_text) else "Clipboard copy failed.")

    tk.Button(button_frame, text="Build Crashpad Report", command=run_issue_step).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(button_frame, text="Copy Current Output", command=copy_current_output).pack(side=tk.LEFT)
    tk.Label(main_frame, textvariable=status_var, anchor="w").pack(fill=tk.X, pady=(8, 0))

    root.mainloop()
