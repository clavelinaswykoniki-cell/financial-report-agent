from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowStep:
    role: str
    status: str
    evidence: str
    output: str


def build_workflow_trace(
    snapshot: dict[str, Any],
    risk: dict[str, Any],
    agent_notes: str | None = None,
) -> list[dict[str, str]]:
    """Return a transparent role trace for the deterministic agent workflow."""

    latest_filing = snapshot.get("latest_annual_filing") or {}
    latest_values = snapshot.get("latest_values") or {}
    metrics = snapshot.get("metrics") or {}
    warnings = snapshot.get("warnings") or []
    risk_items = risk.get("items") or []

    steps = [
        WorkflowStep(
            role="Data Step",
            status="completed",
            evidence=f"CIK {snapshot.get('cik')} / {latest_filing.get('form', 'annual filing')}",
            output="Resolved ticker, pulled SEC submissions and companyfacts JSON.",
        ),
        WorkflowStep(
            role="Metric Step",
            status="completed",
            evidence=f"Revenue tag: {(latest_values.get('revenue') or {}).get('tag', 'n/a')}",
            output=(
                "Calculated growth, margin, leverage, liquidity, cash-flow, "
                "and R&D-intensity metrics."
            ),
        ),
        WorkflowStep(
            role="Risk Step",
            status="completed",
            evidence=f"{len(risk_items)} triggered rule(s), score {risk.get('score')} / 100",
            output=f"Assigned {risk.get('level')} risk with {risk.get('confidence')} confidence.",
        ),
        WorkflowStep(
            role="Quality Step",
            status="completed",
            evidence=f"{len(warnings)} data warning(s)",
            output="Checked key XBRL coverage and trend comparability limits.",
        ),
        WorkflowStep(
            role="Report Step",
            status="completed",
            evidence=f"Fiscal period {metrics.get('period') or 'latest'}",
            output="Rendered a Markdown report and machine-readable JSON payload.",
        ),
    ]
    if agent_notes:
        steps.append(
            WorkflowStep(
                role="LLM Analyst Agent",
                status="completed",
                evidence="LangChain tool-calling response",
                output="Added a Chinese narrative interpretation from structured facts.",
            )
        )
    else:
        steps.append(
            WorkflowStep(
                role="LLM Analyst Agent",
                status="skipped",
                evidence="No LLM pass requested or no API key detected",
                output="Rules-only mode remains fully runnable and reproducible.",
            )
        )
    return [asdict(step) for step in steps]
