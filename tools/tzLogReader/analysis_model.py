"""Typed internal model for tzlog analysis workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IssueClassification:
    id: str
    severity: str
    category: str
    recommended_data: list[str] = field(default_factory=list)
    matched_rule: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "category": self.category,
            "recommended_data": list(self.recommended_data),
            "matched_rule": self.matched_rule,
        }


@dataclass
class GPURecord:
    index: int
    name: str | None = None
    vram: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "vram": self.vram,
        }


@dataclass
class SystemInformation:
    os: str | None = None
    cpu: str | None = None
    ram: str | None = None
    indexed_gpus: list[GPURecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "cpu": self.cpu,
            "ram": self.ram,
            "indexed_gpus": [gpu.to_dict() for gpu in self.indexed_gpus],
        }


@dataclass
class ApplicationSession:
    file: str
    log_directory: str | None = None
    inferred_user_os: str | None = None
    version: str | None = None
    crashpad_session_id: str | None = None
    user_email: str | None = None
    system_information: SystemInformation = field(default_factory=SystemInformation)
    workflow_type: str | None = None
    input_files: list[str] = field(default_factory=list)
    processing_operations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    export_results: list[str] = field(default_factory=list)
    issue_classifications: list[IssueClassification] = field(default_factory=list)
    termination_state: str | None = None
    termination_analysis: str = "unknown termination"
    termination_evidence: list[str] = field(default_factory=list)
    session_status: str = "inconclusive"
    session_start_time: str | None = None
    last_stage: str | None = None
    enhancements: list[str] = field(default_factory=list)
    session_score: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "log_directory": self.log_directory,
            "inferred_user_os": self.inferred_user_os,
            "version": self.version,
            "crashpad_session_id": self.crashpad_session_id,
            "user_email": self.user_email,
            "system_information": self.system_information.to_dict(),
            "workflow_type": self.workflow_type,
            "input_files": list(self.input_files),
            "processing_operations": list(self.processing_operations),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "export_results": list(self.export_results),
            "issue_classifications": [item.to_dict() for item in self.issue_classifications],
            "termination_state": self.termination_state,
            "termination_analysis": self.termination_analysis,
            "termination_evidence": list(self.termination_evidence),
            "session_status": self.session_status,
            "session_start_time": self.session_start_time,
            "last_stage": self.last_stage,
            "enhancements": list(self.enhancements),
            "session_score": self.session_score,
        }


@dataclass
class LogArchive:
    source_path: str
    input_files: list[str] = field(default_factory=list)
    application_sessions: list[ApplicationSession] = field(default_factory=list)
    extracted_dirs: list[str] = field(default_factory=list)
    likely_problem_session: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "input_files": list(self.input_files),
            "application_sessions": [entry.to_dict() for entry in self.application_sessions],
            "extracted_dirs": list(self.extracted_dirs),
            "likely_problem_session": self.likely_problem_session,
        }
