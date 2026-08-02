"""Rule-based issue classification for support-facing tzlog workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .analysis_model import IssueClassification
except ImportError:  # pragma: no cover - direct script execution fallback
    from analysis_model import IssueClassification

DEFAULT_RULES_PATH = Path(__file__).resolve().parent / "issue_rules.json"


@dataclass(frozen=True)
class IssueRule:
    id: str
    severity: str
    match_any: list[str]
    category: str
    recommended_data: list[str]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IssueRule":
        return cls(
            id=str(payload.get("id", "")).strip(),
            severity=str(payload.get("severity", "unknown")).strip(),
            match_any=[str(item).strip() for item in payload.get("match_any", []) if str(item).strip()],
            category=str(payload.get("category", "Uncategorized")).strip(),
            recommended_data=[str(item).strip() for item in payload.get("recommended_data", []) if str(item).strip()],
        )


def _pattern_score(text: str, needle: str) -> bool:
    lowered = text.casefold()
    normalized = needle.casefold()
    return normalized in lowered


def load_issue_rules(path: Path | None = None) -> list[IssueRule]:
    rules_path = path or DEFAULT_RULES_PATH
    if not rules_path.is_file():
        return []
    try:
        payload = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [IssueRule.from_dict(item) for item in payload if isinstance(item, dict)]


def classify_issue_text(text: str, rules: list[IssueRule] | None = None) -> list[IssueClassification]:
    candidates: list[IssueClassification] = []
    for rule in rules or load_issue_rules():
        if not rule.match_any:
            continue
        if any(_pattern_score(text, needle) for needle in rule.match_any):
            candidates.append(
                IssueClassification(
                    id=rule.id,
                    severity=rule.severity,
                    category=rule.category,
                    recommended_data=list(rule.recommended_data),
                    matched_rule=rule.match_any[0],
                )
            )
    return candidates
