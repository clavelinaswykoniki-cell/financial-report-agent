from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .metrics import format_money, format_percent, now_iso
from .workflow import build_workflow_trace


METRIC_ROWS = [
    ("Revenue", "latest_values.revenue.value", "money"),
    ("Revenue YoY", "metrics.revenue_growth_yoy", "percent"),
    ("Gross margin", "metrics.gross_margin", "percent"),
    ("Operating margin", "metrics.operating_margin", "percent"),
    ("Net margin", "metrics.net_margin", "percent"),
    ("Debt / assets", "metrics.debt_to_assets", "percent"),
    ("Current ratio", "metrics.current_ratio", "ratio"),
    ("OCF / net income", "metrics.operating_cash_flow_to_net_income", "ratio"),
    ("Free cash flow", "metrics.free_cash_flow", "money"),
    ("FCF margin", "metrics.free_cash_flow_margin", "percent"),
    ("R&D intensity", "metrics.rd_intensity", "percent"),
]


def write_outputs(
    snapshot: dict[str, Any],
    risk: dict[str, Any],
    output_dir: str | Path,
    agent_notes: str | None = None,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    ticker = snapshot["ticker"].upper()
    period = snapshot["metrics"].get("period") or "latest"
    stem = f"{ticker}_{period}_financial_report"

    json_path = output_path / f"{stem}.json"
    md_path = output_path / f"{stem}.md"

    payload = {
        "generated_at": now_iso(),
        "snapshot": snapshot,
        "risk": risk,
        "workflow_trace": build_workflow_trace(snapshot, risk, agent_notes),
        "agent_notes": agent_notes,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(snapshot, risk, agent_notes), encoding="utf-8")

    return {"json": json_path, "markdown": md_path}


def render_markdown(
    snapshot: dict[str, Any],
    risk: dict[str, Any],
    agent_notes: str | None = None,
) -> str:
    latest_filing = snapshot.get("latest_annual_filing") or {}
    risk_items = risk.get("items") or []
    warnings = snapshot.get("warnings") or []

    lines = [
        f"# {snapshot['company_name']} ({snapshot['ticker']}) 财报分析报告",
        "",
        f"- 生成时间: {now_iso()}",
        f"- CIK: `{snapshot['cik']}`",
        f"- 交易所: {snapshot.get('exchange') or 'n/a'}",
        f"- SIC: {snapshot.get('sic') or 'n/a'} {snapshot.get('sic_description') or ''}".rstrip(),
        f"- 最近年度报告: {latest_filing.get('form') or 'n/a'} "
        f"{latest_filing.get('report_date') or ''}".rstrip(),
    ]
    if latest_filing.get("url"):
        lines.append(f"- SEC 原始文件: {latest_filing['url']}")

    lines.extend(
        [
            "",
            "## 结论摘要",
            "",
            f"- 风险等级: **{risk['level']}**，风险分: **{risk['score']} / 100**。",
            f"- 分析置信度: **{risk['confidence']}**。",
            "- 该报告基于 SEC XBRL facts 与规则评分生成，不构成投资建议。",
            "",
            "## 核心指标",
            "",
            "| 指标 | 数值 |",
            "| --- | ---: |",
        ]
    )

    for label, path, kind in METRIC_ROWS:
        lines.append(f"| {label} | {format_value(get_path(snapshot, path), kind)} |")

    lines.extend(["", "## 年度趋势", "", trend_table(snapshot)])

    lines.extend(["", "## 分析工作流", ""])
    for step in build_workflow_trace(snapshot, risk, agent_notes):
        lines.append(
            f"- **{step['role']}** [{step['status']}]: {step['output']} "
            f"证据: {step['evidence']}。"
        )

    lines.extend(["", "## 风险观察", ""])
    if risk_items:
        for item in sorted(risk_items, key=lambda value: value["points"], reverse=True):
            lines.append(f"- **{item['title']}** (+{item['points']}): {item['detail']}")
    else:
        lines.append("- 未触发主要规则风险；仍需结合业务、行业、估值与管理层讨论继续核查。")

    lines.extend(["", "## 数据质量", ""])
    if warnings:
        lines.extend([f"- {warning}" for warning in warnings])
    else:
        lines.append("- 关键 XBRL 标签覆盖较完整。")

    if agent_notes:
        lines.extend(["", "## LangChain Agent 补充解读", "", agent_notes.strip()])

    lines.extend(
        [
            "",
            "## 方法说明",
            "",
            "- 数据源: SEC EDGAR submissions 与 XBRL companyfacts API。",
            "- 口径: 优先 US-GAAP 标准标签；缺失时使用同类 fallback 标签。",
            "- 风险评分: 只做财务报表风险观察，不输出买卖建议或目标价。",
            "- 局限: 行业差异、公司自定义 taxonomy、重述/修订文件、非美国公司 IFRS 标签会影响可比性。",
        ]
    )
    return "\n".join(lines) + "\n"


def trend_table(snapshot: dict[str, Any]) -> str:
    revenue = by_year(snapshot["series"].get("revenue", []))
    net_income = by_year(snapshot["series"].get("net_income", []))
    operating_cash_flow = by_year(snapshot["series"].get("operating_cash_flow", []))
    capex = by_year(snapshot["series"].get("capex", []))

    years = sorted(set(revenue) | set(net_income) | set(operating_cash_flow))[-4:]
    if not years:
        return "暂无足够年度数据。"

    lines = [
        "| Fiscal year | Revenue | Net income | Operating cash flow | Free cash flow |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for year in years:
        ocf = operating_cash_flow.get(year)
        capex_value = capex.get(year)
        fcf = None
        if ocf is not None and capex_value is not None:
            fcf = ocf - abs(capex_value)
        lines.append(
            f"| {year} | {format_money(revenue.get(year))} | "
            f"{format_money(net_income.get(year))} | {format_money(ocf)} | {format_money(fcf)} |"
        )
    return "\n".join(lines)


def by_year(points: list[dict[str, Any]]) -> dict[int, float]:
    return {
        int(point["fy"]): point["value"]
        for point in points
        if point.get("fy") is not None and point.get("value") is not None
    }


def get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def format_value(value: Any, kind: str) -> str:
    if value is None:
        return "n/a"
    if kind == "money":
        return format_money(value)
    if kind == "percent":
        return format_percent(value)
    if kind == "ratio":
        return f"{value:.2f}"
    return str(value)
