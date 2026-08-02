const state = {
  supportPath: "",
  issuePath: "",
  systemReport: "",
  sessionReport: "",
  extractedDirs: [],
  candidates: [],
  selectedCandidateIndexes: new Set(),
};

const els = {
  status: document.querySelector("#status"),
  supportPath: document.querySelector("#supportPath"),
  issuePath: document.querySelector("#issuePath"),
  runSystemInfo: document.querySelector("#runSystemInfo"),
  useTestArchive: document.querySelector("#useTestArchive"),
  clearAll: document.querySelector("#clearAll"),
  logList: document.querySelector("#logList"),
  systemReport: document.querySelector("#systemReport"),
  runSessionScan: document.querySelector("#runSessionScan"),
  sessionReport: document.querySelector("#sessionReport"),
  scanErrors: document.querySelector("#scanErrors"),
  candidateList: document.querySelector("#candidateList"),
  addSelected: document.querySelector("#addSelected"),
  recordSelected: document.querySelector("#recordSelected"),
  selectAllCandidates: document.querySelector("#selectAllCandidates"),
  notablePatterns: document.querySelector("#notablePatterns"),
  recordOnBuild: document.querySelector("#recordOnBuild"),
  buildReport: document.querySelector("#buildReport"),
  copyReport: document.querySelector("#copyReport"),
  finalReport: document.querySelector("#finalReport"),
  bankFilter: document.querySelector("#bankFilter"),
  bankList: document.querySelector("#bankList"),
  logDialog: document.querySelector("#logDialog"),
  logDialogTitle: document.querySelector("#logDialogTitle"),
  logPreview: document.querySelector("#logPreview"),
  closeLogDialog: document.querySelector("#closeLogDialog"),
};

function setStatus(message, mode = "") {
  els.status.textContent = message;
  els.status.className = `status ${mode}`.trim();
}

async function api(action, payload = {}) {
  setStatus("Working...", "busy");
  const response = await fetch("/api", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ action, ...payload }),
  });
  const body = await response.json();
  if (!body.ok) {
    setStatus(body.error || "Request failed", "error");
    throw new Error(body.error || "Request failed");
  }
  setStatus("Ready");
  return body;
}

function patternLines() {
  return els.notablePatterns.value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function appendPatterns(patterns) {
  const existing = new Set(patternLines());
  const next = [...existing];
  for (const pattern of patterns) {
    if (!existing.has(pattern)) {
      next.push(pattern);
    }
  }
  els.notablePatterns.value = next.join("\n");
}

function renderLogs(files) {
  if (!files.length) {
    els.logList.className = "list empty";
    els.logList.textContent = "No .tzlog files found.";
    return;
  }
  els.logList.className = "list";
  els.logList.replaceChildren(
    ...files.map((file) => {
      const row = document.createElement("div");
      row.className = "row";

      const label = document.createElement("div");
      label.className = "pattern";
      label.textContent = file.label;

      const actions = document.createElement("div");
      actions.className = "review-actions";

      const useButton = document.createElement("button");
      useButton.textContent = "Use";
      useButton.addEventListener("click", () => {
        state.issuePath = file.path;
        els.issuePath.value = file.path;
        setStatus(`Issue log set: ${file.label}`);
      });

      const viewButton = document.createElement("button");
      viewButton.textContent = "View";
      viewButton.addEventListener("click", () => readLog(file.path));

      actions.append(useButton, viewButton);
      row.append(label, actions);
      return row;
    }),
  );
}

function renderCandidates() {
  if (!state.candidates.length) {
    els.candidateList.className = "candidate-list empty";
    els.candidateList.textContent = "No candidate patterns found.";
    return;
  }
  els.candidateList.className = "candidate-list";
  els.candidateList.replaceChildren(
    ...state.candidates.map((candidate, index) => {
      const row = document.createElement("div");
      row.className = [
        "candidate",
        candidate.knownMatch ? "known" : "",
        state.selectedCandidateIndexes.has(index) ? "selected" : "",
      ]
        .filter(Boolean)
        .join(" ");
      row.addEventListener("click", () => {
        if (state.selectedCandidateIndexes.has(index)) {
          state.selectedCandidateIndexes.delete(index);
        } else {
          state.selectedCandidateIndexes.add(index);
        }
        renderCandidates();
      });

      const content = document.createElement("div");
      const pill = document.createElement("span");
      pill.className = `pill ${candidate.knownMatch ? "known" : ""}`.trim();
      pill.textContent = candidate.knownMatch ? "KNOWN" : "NEW";
      const text = document.createElement("div");
      text.className = "pattern";
      text.textContent = candidate.patternText;
      content.append(pill, text);

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `x${candidate.countInLog} score ${candidate.score}`;

      row.append(content, meta);
      return row;
    }),
  );
}

function renderBank(entries) {
  if (!entries.length) {
    els.bankList.className = "list empty";
    els.bankList.textContent = "No saved patterns yet.";
    return;
  }
  els.bankList.className = "list";
  els.bankList.replaceChildren(
    ...entries.map((entry) => {
      const row = document.createElement("div");
      row.className = "row";
      row.addEventListener("dblclick", () => appendPatterns([entry.patternText]));

      const text = document.createElement("div");
      text.className = "pattern";
      text.textContent = entry.patternText;

      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${entry.count} seen · ${entry.lastSeen}`;

      row.append(text, meta);
      return row;
    }),
  );
}

function selectedCandidates() {
  return [...state.selectedCandidateIndexes].map((index) => state.candidates[index]).filter(Boolean);
}

async function refreshBank() {
  const result = await api("bankList", { query: els.bankFilter.value.trim() });
  renderBank(result.entries);
}

async function readLog(path) {
  try {
    const result = await api("readLog", { path });
    els.logDialogTitle.textContent = result.path;
    els.logPreview.textContent = result.text + (result.truncated ? "\n\n[Preview truncated]" : "");
    els.logDialog.showModal();
  } catch (error) {
    console.error(error);
  }
}

els.runSystemInfo.addEventListener("click", async () => {
  try {
    const path = els.supportPath.value.trim();
    const result = await api("systemInfo", { path });
    state.supportPath = result.supportPath;
    state.systemReport = result.report;
    state.extractedDirs = result.extractedDirs;
    els.supportPath.value = result.supportPath;
    els.systemReport.textContent = result.report;
    renderLogs(result.tzlogFiles);
    if (result.tzlogFiles.length === 1) {
      state.issuePath = result.tzlogFiles[0].path;
      els.issuePath.value = result.tzlogFiles[0].path;
    } else {
      state.issuePath = result.displayRoot;
      els.issuePath.value = result.displayRoot;
    }
    setStatus(`Loaded ${result.tzlogFiles.length} .tzlog file(s).`);
  } catch (error) {
    console.error(error);
  }
});

els.runSessionScan.addEventListener("click", async () => {
  try {
    const path = els.supportPath.value.trim() || state.supportPath;
    const result = await api("sessionScan", { path });
    state.sessionReport = result.report;
    els.sessionReport.textContent = result.report;
    setStatus(`Session scan complete with ${result.archive.application_sessions.length} grouped session(s).`);
  } catch (error) {
    console.error(error);
  }
});

els.scanErrors.addEventListener("click", async () => {
  try {
    const path = els.issuePath.value.trim();
    const result = await api("scan", { path });
    state.issuePath = path;
    state.candidates = result.candidates;
    state.selectedCandidateIndexes.clear();
    renderCandidates();
    const known = result.candidates.filter((candidate) => candidate.knownMatch).length;
    setStatus(`Scanned ${result.candidates.length} pattern(s), ${known} known.`);
  } catch (error) {
    console.error(error);
  }
});

els.addSelected.addEventListener("click", () => {
  appendPatterns(selectedCandidates().map((candidate) => candidate.patternText));
});

els.recordSelected.addEventListener("click", async () => {
  try {
    const patterns = selectedCandidates().map((candidate) => candidate.patternText);
    const result = await api("bankRecord", { patterns });
    renderBank(result.entries);
    setStatus(`Recorded ${patterns.length} pattern(s).`);
  } catch (error) {
    console.error(error);
  }
});

els.selectAllCandidates.addEventListener("click", () => {
  if (state.selectedCandidateIndexes.size === state.candidates.length) {
    state.selectedCandidateIndexes.clear();
  } else {
    state.candidates.forEach((_candidate, index) => state.selectedCandidateIndexes.add(index));
  }
  renderCandidates();
});

els.buildReport.addEventListener("click", async () => {
  try {
    const result = await api("buildReport", {
      issuePath: els.issuePath.value.trim(),
      supportPath: state.supportPath || els.supportPath.value.trim(),
      extractedDirs: state.extractedDirs,
      systemReport: state.systemReport,
      notablePatterns: patternLines(),
      recordPatterns: els.recordOnBuild.checked,
    });
    els.finalReport.textContent = result.report;
    if (els.recordOnBuild.checked) {
      await refreshBank();
    }
    setStatus(`Report built with ${result.knownMatches.length} known pattern match(es).`);
  } catch (error) {
    console.error(error);
  }
});

els.copyReport.addEventListener("click", async () => {
  const text = els.finalReport.textContent.trim();
  if (!text || text === "Build a report to see output here.") {
    setStatus("No report to copy.", "error");
    return;
  }
  await navigator.clipboard.writeText(text);
  setStatus("Report copied to clipboard.");
});

els.bankFilter.addEventListener("input", () => {
  window.clearTimeout(els.bankFilter._timer);
  els.bankFilter._timer = window.setTimeout(refreshBank, 150);
});

els.useTestArchive.addEventListener("click", () => {
  els.supportPath.value = `${location.pathname === "/" ? "" : ""}testFolder/21c294f6-e878-4b0d-bf79-7577a72f9121-attachments.tar`;
});

els.clearAll.addEventListener("click", () => {
  state.supportPath = "";
  state.issuePath = "";
  state.systemReport = "";
  state.sessionReport = "";
  state.extractedDirs = [];
  state.candidates = [];
  state.selectedCandidateIndexes.clear();
  els.supportPath.value = "";
  els.issuePath.value = "";
  els.systemReport.textContent = "Run Step 1 to populate system information.";
  els.sessionReport.textContent = "Run a session scan to classify launch sessions and their issue patterns.";
  els.notablePatterns.value = "";
  els.finalReport.textContent = "Build a report to see output here.";
  renderLogs([]);
  renderCandidates();
  setStatus("Cleared.");
});

els.closeLogDialog.addEventListener("click", () => els.logDialog.close());

refreshBank().catch((error) => console.error(error));
