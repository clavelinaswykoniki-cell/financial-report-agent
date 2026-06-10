from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, date
from typing import Any


ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

TAG_OPTIONS: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
}

FLOW_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "GrossProfit",
    "OperatingIncomeLoss",
    "NetIncomeLoss",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "ResearchAndDevelopmentExpense",
}


def build_snapshot(
    ticker_record: dict[str, Any],
    submissions: dict[str, Any],
    companyfacts: dict[str, Any],
    years: int = 4,
) -> dict[str, Any]:
    series = {
        key: extract_annual_series(companyfacts, tags)[-years:]
        for key, tags in TAG_OPTIONS.items()
    }
    metrics = calculate_metrics(series)
    period = metrics.get("period")
    latest = {
        key: point_for_year(points, period) if period else latest_point(points)
        for key, points in series.items()
    }
    warnings = data_quality_warnings(series, period)
    annual_filing = latest_annual_filing(submissions)

    return {
        "ticker": ticker_record["ticker"],
        "cik": ticker_record["cik"],
        "company_name": submissions.get("name") or ticker_record.get("company_name"),
        "sic": submissions.get("sic"),
        "sic_description": submissions.get("sicDescription"),
        "exchange": first_or_none(submissions.get("exchanges", [])),
        "fiscal_year_end": submissions.get("fiscalYearEnd"),
        "latest_annual_filing": annual_filing,
        "latest_values": latest,
        "series": series,
        "metrics": metrics,
        "warnings": warnings,
    }


def extract_annual_series(
    companyfacts: dict[str, Any],
    tag_options: Iterable[str],
    preferred_units: tuple[str, ...] = ("USD", "shares", "pure"),
) -> list[dict[str, Any]]:
    us_gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    candidates: list[tuple[int, list[dict[str, Any]]]] = []
    for tag_index, tag in enumerate(tag_options):
        concept = us_gaap.get(tag)
        if not concept:
            continue
        for unit in preferred_units:
            facts = concept.get("units", {}).get(unit, [])
            annual = [normalize_fact(item, tag, unit) for item in facts if is_annual_fact(item, tag)]
            annual = [item for item in annual if item["value"] is not None and item["fy"] is not None]
            if annual:
                candidates.append((tag_index, sorted(dedupe_by_fiscal_year(annual), key=lambda item: item["fy"])))
    if not candidates:
        return []
    _, best = max(
        candidates,
        key=lambda candidate: (
            candidate[1][-1]["fy"],
            len(candidate[1]),
            -candidate[0],
        ),
    )
    return best


def is_annual_fact(item: dict[str, Any], tag: str) -> bool:
    form = item.get("form")
    if form not in ANNUAL_FORMS:
        return False
    frame = item.get("frame") or ""
    if tag in FLOW_TAGS and not has_annual_duration(item):
        return False
    return item.get("fp") == "FY" or frame.endswith("I") or bool(item.get("fy"))


def has_annual_duration(item: dict[str, Any]) -> bool:
    start = parse_date(item.get("start"))
    end = parse_date(item.get("end"))
    if not start or not end:
        return item.get("fp") == "FY"
    days = (end - start).days
    return 300 <= days <= 380


def normalize_fact(item: dict[str, Any], tag: str, unit: str) -> dict[str, Any]:
    return {
        "fy": item.get("fy"),
        "fp": item.get("fp"),
        "form": item.get("form"),
        "filed": item.get("filed"),
        "start": item.get("start"),
        "end": item.get("end"),
        "value": coerce_float(item.get("val")),
        "unit": unit,
        "tag": tag,
        "accession": item.get("accn"),
    }


def dedupe_by_fiscal_year(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[int, dict[str, Any]] = {}
    for point in points:
        fy = point["fy"]
        current = chosen.get(fy)
        if current is None or sort_key(point) > sort_key(current):
            chosen[fy] = point
    return list(chosen.values())


def sort_key(point: dict[str, Any]) -> tuple[str, str]:
    return (point.get("filed") or "", point.get("end") or "")


def calculate_metrics(series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    period = latest_common_year(series, ["revenue", "net_income"]) or latest_year(series.get("revenue", []))
    latest = {key: point_for_year(points, period) for key, points in series.items()}
    previous = {key: previous_point_before_year(points, period) for key, points in series.items()}

    revenue = value_of(latest["revenue"])
    prev_revenue = value_of(previous["revenue"])
    gross_profit = value_of(latest["gross_profit"])
    prev_gross_profit = value_of(previous["gross_profit"])
    operating_income = value_of(latest["operating_income"])
    net_income = value_of(latest["net_income"])
    assets = value_of(latest["assets"])
    current_assets = value_of(latest["assets_current"])
    liabilities = value_of(latest["liabilities"])
    current_liabilities = value_of(latest["liabilities_current"])
    equity = value_of(latest["equity"])
    cash = value_of(latest["cash"])
    operating_cash_flow = value_of(latest["operating_cash_flow"])
    capex = value_of(latest["capex"])
    rd_expense = value_of(latest["rd_expense"])

    fcf = None
    if operating_cash_flow is not None and capex is not None:
        fcf = operating_cash_flow - abs(capex)

    return {
        "period": period,
        "revenue_growth_yoy": ratio_delta(revenue, prev_revenue),
        "gross_margin": safe_ratio(gross_profit, revenue),
        "gross_margin_yoy_delta": margin_delta(gross_profit, revenue, prev_gross_profit, prev_revenue),
        "operating_margin": safe_ratio(operating_income, revenue),
        "net_margin": safe_ratio(net_income, revenue),
        "debt_to_assets": safe_ratio(liabilities, assets),
        "equity_ratio": safe_ratio(equity, assets),
        "cash_to_assets": safe_ratio(cash, assets),
        "current_ratio": safe_ratio(current_assets, current_liabilities),
        "operating_cash_flow_to_net_income": safe_ratio(operating_cash_flow, net_income),
        "free_cash_flow": fcf,
        "free_cash_flow_margin": safe_ratio(fcf, revenue),
        "rd_intensity": safe_ratio(rd_expense, revenue),
    }


def assess_risk(snapshot: dict[str, Any]) -> dict[str, Any]:
    metrics = snapshot["metrics"]
    warnings = snapshot["warnings"]
    items: list[dict[str, Any]] = []
    score = 0

    def add(points: int, title: str, detail: str) -> None:
        nonlocal score
        score += points
        items.append({"points": points, "title": title, "detail": detail})

    revenue_growth = metrics.get("revenue_growth_yoy")
    if revenue_growth is not None and revenue_growth < -0.05:
        add(15, "收入下滑", f"最近年度收入同比为 {format_percent(revenue_growth)}。")

    net_margin = metrics.get("net_margin")
    if net_margin is not None:
        if net_margin < 0:
            add(20, "净利润为负", f"最近年度净利率为 {format_percent(net_margin)}。")
        elif net_margin < 0.05:
            add(8, "盈利缓冲偏薄", f"最近年度净利率为 {format_percent(net_margin)}。")

    debt_to_assets = metrics.get("debt_to_assets")
    if debt_to_assets is not None:
        if debt_to_assets > 0.75:
            add(20, "资产负债率较高", f"负债/资产为 {format_percent(debt_to_assets)}。")
        elif debt_to_assets > 0.6:
            add(10, "杠杆水平需要关注", f"负债/资产为 {format_percent(debt_to_assets)}。")

    current_ratio = metrics.get("current_ratio")
    if current_ratio is not None and current_ratio < 1:
        add(12, "短期偿债压力", f"流动比率为 {current_ratio:.2f}。")

    ocf_to_ni = metrics.get("operating_cash_flow_to_net_income")
    if ocf_to_ni is not None and ocf_to_ni < 0.8:
        add(10, "利润现金含量偏弱", f"经营现金流/净利润为 {ocf_to_ni:.2f}。")

    fcf_margin = metrics.get("free_cash_flow_margin")
    if fcf_margin is not None and fcf_margin < 0:
        add(15, "自由现金流为负", f"自由现金流率为 {format_percent(fcf_margin)}。")

    gross_margin_delta = metrics.get("gross_margin_yoy_delta")
    if gross_margin_delta is not None and gross_margin_delta < -0.03:
        add(8, "毛利率下行", f"毛利率同比变化 {format_percent(gross_margin_delta)}。")

    if warnings:
        add(min(20, len(warnings) * 4), "数据完整性限制", "部分关键 XBRL 标签缺失或不可比。")

    score = min(100, score)
    if score >= 55:
        level = "High"
    elif score >= 25:
        level = "Medium"
    else:
        level = "Low"

    confidence = "High"
    if len(warnings) >= 4:
        confidence = "Medium"
    if len(warnings) >= 7:
        confidence = "Low"

    return {
        "score": score,
        "level": level,
        "confidence": confidence,
        "items": items,
        "disclaimer": "This is a rule-based financial statement analysis, not investment advice.",
    }


def latest_annual_filing(submissions: dict[str, Any]) -> dict[str, Any] | None:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for idx, form in enumerate(forms):
        if form in ANNUAL_FORMS:
            accession = recent.get("accessionNumber", [None])[idx]
            primary_doc = recent.get("primaryDocument", [None])[idx]
            cik = str(submissions.get("cik", "")).lstrip("0")
            archive_url = None
            if accession and primary_doc and cik:
                archive_url = (
                    f"https://www.sec.gov/Archives/edgar/data/"
                    f"{cik}/{accession.replace('-', '')}/{primary_doc}"
                )
            return {
                "form": form,
                "filing_date": recent.get("filingDate", [None])[idx],
                "report_date": recent.get("reportDate", [None])[idx],
                "accession": accession,
                "primary_document": primary_doc,
                "url": archive_url,
            }
    return None


def data_quality_warnings(series: dict[str, list[dict[str, Any]]], period: int | None = None) -> list[str]:
    warnings: list[str] = []
    required = ["revenue", "net_income", "assets", "liabilities", "operating_cash_flow"]
    for key in required:
        if not series.get(key):
            warnings.append(f"Missing or non-standard XBRL tag for {key}.")
        elif period is not None and point_for_year(series[key], period) is None:
            warnings.append(f"Missing same-year XBRL value for {key} in fiscal year {period}.")
    years = {point["fy"] for point in series.get("revenue", []) if point.get("fy")}
    if len(years) < 2:
        warnings.append("Revenue history has fewer than two annual periods; YoY trends are weak.")
    return warnings


def latest_point(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    return points[-1] if points else None


def latest_year(points: list[dict[str, Any]]) -> int | None:
    point = latest_point(points)
    return point.get("fy") if point else None


def previous_point(points: list[dict[str, Any]]) -> dict[str, Any] | None:
    return points[-2] if len(points) >= 2 else None


def point_for_year(points: list[dict[str, Any]], year: int | None) -> dict[str, Any] | None:
    if year is None:
        return None
    for point in reversed(points):
        if point.get("fy") == year:
            return point
    return None


def previous_point_before_year(points: list[dict[str, Any]], year: int | None) -> dict[str, Any] | None:
    if year is None:
        return previous_point(points)
    earlier = [point for point in points if point.get("fy") is not None and point["fy"] < year]
    return earlier[-1] if earlier else None


def latest_common_year(series: dict[str, list[dict[str, Any]]], keys: list[str]) -> int | None:
    year_sets = [
        {point["fy"] for point in series.get(key, []) if point.get("fy") is not None}
        for key in keys
    ]
    if not year_sets or any(not years for years in year_sets):
        return None
    common = set.intersection(*year_sets)
    return max(common) if common else None


def value_of(point: dict[str, Any] | None) -> float | None:
    return point.get("value") if point else None


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def ratio_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous)


def margin_delta(
    current_numerator: float | None,
    current_denominator: float | None,
    previous_numerator: float | None,
    previous_denominator: float | None,
) -> float | None:
    current = safe_ratio(current_numerator, current_denominator)
    previous = safe_ratio(previous_numerator, previous_denominator)
    if current is None or previous is None:
        return None
    return current - previous


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def first_or_none(items: list[Any]) -> Any:
    return items[0] if items else None


def format_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    return f"${value:,.0f}"


def format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
