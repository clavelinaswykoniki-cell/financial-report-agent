from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .metrics import format_percent
from .service import analyze_company


def write_screening_outputs(
    tickers: list[str],
    output_dir: str | Path,
    sec_user_agent: str | None = None,
    cache_dir: str | Path = ".cache/sec",
    years: int = 4,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    rows = []
    for ticker in tickers:
        result = analyze_company(
            ticker=ticker,
            sec_user_agent=sec_user_agent,
            cache_dir=cache_dir,
            years=years,
            output_dir=output_path,
            llm_mode="off",
        )
        rows.append(screening_row(result.snapshot, result.risk))

    rows.sort(key=lambda row: (-int(row["risk_score"]), row["ticker"]))
    csv_path = output_path / "watchlist_screening.csv"
    md_path = output_path / "watchlist_screening.md"
    write_csv(csv_path, rows)
    md_path.write_text(render_markdown(rows), encoding="utf-8")
    return {"screening_csv": csv_path, "screening_markdown": md_path}


def screening_row(snapshot: dict[str, Any], risk: dict[str, Any]) -> dict[str, str]:
    metrics = snapshot["metrics"]
    return {
        "ticker": snapshot["ticker"],
        "company": snapshot["company_name"],
        "period": str(metrics.get("period") or ""),
        "risk_level": risk["level"],
        "risk_score": str(risk["score"]),
        "confidence": risk["confidence"],
        "revenue_growth_yoy": format_percent(metrics.get("revenue_growth_yoy")),
        "net_margin": format_percent(metrics.get("net_margin")),
        "debt_to_assets": format_percent(metrics.get("debt_to_assets")),
        "current_ratio": format_number(metrics.get("current_ratio")),
        "fcf_margin": format_percent(metrics.get("free_cash_flow_margin")),
        "top_risks": "; ".join(item["title"] for item in risk.get("items", [])[:3]) or "n/a",
    }


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(rows: list[dict[str, str]]) -> str:
    lines = [
        "# Watchlist Screening",
        "",
        "| Ticker | Company | Risk | Score | Revenue YoY | Net margin | Debt/assets | Current ratio | FCF margin | Top risks |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['ticker']} | {escape(row['company'])} | {row['risk_level']} | {row['risk_score']} | "
            f"{row['revenue_growth_yoy']} | {row['net_margin']} | {row['debt_to_assets']} | "
            f"{row['current_ratio']} | {row['fcf_margin']} | {escape(row['top_risks'])} |"
        )
    lines.extend(["", "This screening table is a risk-observation workflow, not investment advice.", ""])
    return "\n".join(lines)


def format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}"


def escape(value: str) -> str:
    return value.replace("|", "\\|")
