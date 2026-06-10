from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import has_llm_api_key, run_langchain_agent
from .metrics import assess_risk, build_snapshot
from .report import write_outputs
from .sec_client import SecClient


@dataclass(frozen=True)
class LlmRunResult:
    notes: str | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    snapshot: dict[str, Any]
    risk: dict[str, Any]
    agent_notes: str | None
    paths: dict[str, Path] | None
    llm_status: str
    llm_error: str | None = None


def analyze_company(
    ticker: str,
    sec_user_agent: str | None = None,
    cache_dir: str | Path = ".cache/sec",
    years: int = 4,
    output_dir: str | Path | None = None,
    llm_mode: str = "auto",
    model: str | None = None,
    llm_timeout: float = 45,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> AnalysisResult:
    client = SecClient(user_agent=sec_user_agent, cache_dir=cache_dir)
    ticker_record = client.lookup_ticker(ticker)
    submissions = client.get_submissions(ticker_record["cik"])
    companyfacts = client.get_companyfacts(ticker_record["cik"])
    snapshot = build_snapshot(ticker_record, submissions, companyfacts, years=years)
    risk = assess_risk(snapshot)
    llm_result = maybe_run_agent(llm_mode, snapshot, risk, model, llm_timeout, api_key, base_url, provider)
    paths = write_outputs(snapshot, risk, output_dir, agent_notes=llm_result.notes) if output_dir else None
    return AnalysisResult(
        ticker=snapshot["ticker"],
        snapshot=snapshot,
        risk=risk,
        agent_notes=llm_result.notes,
        paths=paths,
        llm_status=llm_result.status,
        llm_error=llm_result.error,
    )


def maybe_run_agent(
    llm_mode: str,
    snapshot: dict[str, Any],
    risk: dict[str, Any],
    model: str | None = None,
    llm_timeout: float = 45,
    api_key: str | None = None,
    base_url: str | None = None,
    provider: str | None = None,
) -> LlmRunResult:
    if llm_mode == "off":
        return LlmRunResult(notes=None, status="off")
    if llm_mode == "auto" and not has_llm_api_key(model, api_key=api_key, base_url=base_url, provider=provider):
        return LlmRunResult(notes=None, status="skipped_no_key")
    try:
        notes = run_langchain_agent(
            snapshot,
            risk,
            model=model,
            timeout_seconds=llm_timeout,
            api_key=api_key,
            base_url=base_url,
            provider=provider,
        )
        return LlmRunResult(notes=notes, status="completed" if notes else "completed_empty")
    except Exception as exc:
        error = redact_secrets(str(exc), extra_values=[api_key])
        if llm_mode == "auto":
            return LlmRunResult(notes=None, status="failed_auto", error=error)
        raise RuntimeError(f"LLM call failed: {error}") from exc


def redact_secrets(text: str, extra_values: list[str | None] | None = None) -> str:
    redacted = text
    secret_values = [
        os.getenv("OPENAI_API_KEY"),
        os.getenv("DEEPSEEK_API_KEY"),
        os.getenv("FIN_AGENT_API_KEY"),
        *(extra_values or []),
    ]
    for value in secret_values:
        if value and len(value) >= 4:
            redacted = redacted.replace(value, "[redacted]")
    return redacted
