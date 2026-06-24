#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import monitor_511130 as monitor


BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[1]
DEFAULT_OUTPUT_ROOT = WORKSPACE / "reports" / "511130_daily_actual_a"
DEFAULT_DESKTOP_DIR = Path("/Users/happytang/Desktop/511130_每日实际a")
EXPECTED_MINUTE_COUNT = 240
EXPECTED_5M_COUNT = 48


@dataclass(frozen=True)
class ReportPcf:
    trading_day: str
    pre_trading_day: str
    record_number: int
    estimated_cash_component: Decimal
    pre_cash_component: Decimal
    creation_redemption_unit: Decimal
    components: list[monitor.Component]
    source_url: str
    raw_path: str


def date_iso(date_compact: str) -> str:
    return f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"


def parse_date_compact(value: str) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    raise RuntimeError(f"日期无效: {value}; 请使用 YYYYMMDD")


def now_date_compact() -> str:
    return datetime.now(monitor.TZ).strftime("%Y%m%d")


def safe_decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_report_pcf(date_compact: str, fund_code: str, raw_dir: Path, label: str) -> ReportPcf:
    url = monitor.BOSERA_PCF_URL.format(fund=fund_code, year=date_compact[:4], date=date_compact)
    response = monitor.curl_requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        impersonate="chrome",
        timeout=20,
    )
    content = response.content
    if response.status_code != 200 or b"<SSEPortfolioCompositionFile>" not in content:
        raise RuntimeError(f"PCF未更新或不可读: {date_compact}; HTTP {response.status_code}")
    raw_path = raw_dir / f"pcf_{label}_{fund_code}_{date_compact}.xml"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(content)

    root = ET.fromstring(content)

    def text(tag: str) -> str:
        node = root.find(tag)
        return node.text.strip() if node is not None and node.text else ""

    components: list[monitor.Component] = []
    for node in root.findall("./ComponentList/Component"):
        components.append(
            monitor.Component(
                code=(node.findtext("InstrumentID") or "").strip(),
                name=(node.findtext("InstrumentName") or "").strip(),
                pcf_quantity=monitor.dec(node.findtext("Quantity") or "0"),
            )
        )
    return ReportPcf(
        trading_day=text("TradingDay"),
        pre_trading_day=text("PreTradingDay"),
        record_number=int(text("RecordNumber") or len(components)),
        estimated_cash_component=monitor.dec(text("EstimatedCashComponent")),
        pre_cash_component=monitor.dec(text("PreCashComponent")),
        creation_redemption_unit=monitor.dec(text("CreationRedemptionUnit") or "10000"),
        components=components,
        source_url=url,
        raw_path=str(raw_path),
    )


def validate_report_pcf(pcf: ReportPcf, expected_trading_day: str, config: dict, *, validate_components: bool) -> None:
    monitor.validate_pcf(pcf, config if validate_components else {})
    if pcf.trading_day != expected_trading_day:
        raise RuntimeError(f"PCF TradingDay不匹配: 期望{expected_trading_day}, 实际{pcf.trading_day}")
    if not pcf.pre_trading_day:
        raise RuntimeError(f"PCF缺少PreTradingDay: {pcf.trading_day}")
    if pcf.record_number != len(pcf.components):
        raise RuntimeError(f"PCF RecordNumber与成分券数量不一致: {pcf.record_number} != {len(pcf.components)}")


def resolve_pcfs(
    *,
    run_date: str,
    target_date: str | None,
    config: dict,
    raw_dir: Path,
) -> tuple[str, ReportPcf, ReportPcf]:
    fund_code = str(config.get("fund_code") or monitor.ETF_CODE)
    run_pcf = fetch_report_pcf(run_date, fund_code, raw_dir, "run")
    validate_report_pcf(run_pcf, run_date, config, validate_components=False)
    resolved_target = target_date or run_pcf.pre_trading_day
    if run_pcf.pre_trading_day != resolved_target:
        raise RuntimeError(
            f"运行日PCF PreTradingDay不匹配: run={run_date}, "
            f"PreTradingDay={run_pcf.pre_trading_day}, target={resolved_target}"
        )
    target_pcf = fetch_report_pcf(resolved_target, fund_code, raw_dir, "target")
    validate_report_pcf(target_pcf, resolved_target, config, validate_components=True)
    return resolved_target, target_pcf, run_pcf


def fetch_sse_interest_raw(date_compact: str, code: str, raw_dir: Path) -> tuple[Decimal, str, str]:
    payload = monitor.request_json(
        monitor.SSE_QUERY_URL,
        params={
            "isPagination": "true",
            "pageHelp.pageSize": "25",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": "COMMON_SSE_SJ_ZQSJ_JJYQJ_L",
            "SEARCH_DATE": date_iso(date_compact),
            "SEC_CODE": code,
        },
        headers={"Referer": "https://www.sse.com.cn/market/bonddata/netfull/"},
    )
    raw_path = raw_dir / f"sse_interest_{date_compact}_{code}.json"
    write_json(raw_path, payload)
    rows = payload.get("result") or []
    if not rows:
        raise RuntimeError(f"上交所净价全价接口未返回利息: {date_iso(date_compact)} {code}")
    interest = monitor.dec(rows[0].get("ACCR_INT_AMT"))
    monitor.validate_interest_value(code, interest, "sse_netfull")
    return interest, "sse_netfull", str(raw_path)


def get_report_interests(date_compact: str, pcf: ReportPcf, config: dict, raw_dir: Path) -> dict[str, tuple[Decimal, str, str]]:
    overrides = ((config.get("interest_overrides") or {}).get(date_compact) or {})
    interests: dict[str, tuple[Decimal, str, str]] = {}
    missing: list[str] = []
    for component in pcf.components:
        override = str(overrides.get(component.code, "")).strip()
        if override:
            value = monitor.dec(override)
            source = "manual_trading_software_override"
            monitor.validate_interest_value(component.code, value, source)
            interests[component.code] = (value, source, "config.json")
            continue
        try:
            interests[component.code] = fetch_sse_interest_raw(date_compact, component.code, raw_dir)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{component.code}({exc})")
    if missing:
        raise RuntimeError("缺少逐券应计利息，拒绝生成正式实际a报告: " + "; ".join(missing))
    write_json(
        raw_dir / f"interest_sources_{date_compact}.json",
        {
            code: {"interest": str(value), "source": source, "raw_path": raw_path}
            for code, (value, source, raw_path) in interests.items()
        },
    )
    return interests


def eastmoney_params(code: str) -> dict[str, str]:
    return {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "ndays": "5",
        "iscr": "0",
        "secid": f"1.{code}",
    }


def fetch_eastmoney_1m_raw(code: str, raw_dir: Path) -> tuple[dict[str, Decimal], str]:
    payload = monitor.request_json(
        monitor.EASTMONEY_TRENDS_URL,
        params=eastmoney_params(code),
        headers={"Referer": "https://quote.eastmoney.com/"},
    )
    raw_path = raw_dir / f"eastmoney_trends2_1m_{code}_ndays5.json"
    write_json(raw_path, payload)
    trends = (payload.get("data") or {}).get("trends") or []
    rows: dict[str, Decimal] = {}
    for item in trends:
        parts = item.split(",")
        if len(parts) >= 3:
            rows[parts[0][:16]] = monitor.dec(parts[2])
    if not rows:
        raise RuntimeError(f"东方财富1分钟行情为空: {code}")
    return rows, str(raw_path)


def fetch_eastmoney_5m_raw(code: str, target_date: str, raw_dir: Path) -> tuple[dict[str, Decimal], str]:
    payload = monitor.request_json(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "5",
            "fqt": "1",
            "beg": target_date,
            "end": target_date,
            "secid": f"1.{code}",
        },
        headers={"Referer": "https://quote.eastmoney.com/"},
    )
    raw_path = raw_dir / f"eastmoney_kline_5m_{code}_{target_date}.json"
    write_json(raw_path, payload)
    klines = (payload.get("data") or {}).get("klines") or []
    rows: dict[str, Decimal] = {}
    for item in klines:
        parts = item.split(",")
        if len(parts) >= 3:
            rows[parts[0][:16]] = monitor.dec(parts[2])
    if not rows:
        raise RuntimeError(f"东方财富5分钟行情为空: {code}")
    return rows, str(raw_path)


def is_main_session_minute(timestamp: str, target_date: str) -> bool:
    expected_prefix = date_iso(target_date)
    if not timestamp.startswith(expected_prefix):
        return False
    hhmm = timestamp[11:16]
    return ("09:31" <= hhmm <= "11:30") or ("13:01" <= hhmm <= "15:00")


def common_minute_timestamps(target_date: str, price_maps: dict[str, dict[str, Decimal]]) -> list[str]:
    common: set[str] | None = None
    for rows in price_maps.values():
        keys = {ts for ts in rows if is_main_session_minute(ts, target_date)}
        common = keys if common is None else common & keys
    timestamps = sorted(common or [])
    if len(timestamps) != EXPECTED_MINUTE_COUNT:
        raise RuntimeError(
            f"1分钟共同时间戳不足或异常: {target_date} count={len(timestamps)}, "
            f"expected={EXPECTED_MINUTE_COUNT}; 不用5分钟冒充正式报告"
        )
    return timestamps


def is_5m_session_timestamp(timestamp: str, target_date: str) -> bool:
    expected_prefix = date_iso(target_date)
    if not timestamp.startswith(expected_prefix):
        return False
    hhmm = timestamp[11:16]
    minute = int(hhmm[3:5])
    if minute % 5 != 0:
        return False
    return ("09:35" <= hhmm <= "11:30") or ("13:05" <= hhmm <= "15:00")


def common_5m_timestamps(target_date: str, price_maps: dict[str, dict[str, Decimal]]) -> list[str]:
    common: set[str] | None = None
    for rows in price_maps.values():
        keys = {ts for ts in rows if is_5m_session_timestamp(ts, target_date)}
        common = keys if common is None else common & keys
    timestamps = sorted(common or [])
    if len(timestamps) != EXPECTED_5M_COUNT:
        raise RuntimeError(
            f"5分钟共同时间戳不足或异常: {target_date} count={len(timestamps)}, "
            f"expected={EXPECTED_5M_COUNT}; 5分钟只作为交叉核验"
        )
    return timestamps


def component_inputs_for_timestamp(
    pcf: ReportPcf,
    interests: dict[str, tuple[Decimal, str, str]],
    price_maps: dict[str, dict[str, Decimal]],
    timestamp: str,
) -> list[dict]:
    inputs = []
    for component in pcf.components:
        interest, source, _raw_path = interests[component.code]
        inputs.append(
            {
                "code": component.code,
                "name": component.name,
                "pcf_quantity": component.pcf_quantity,
                "units": component.units,
                "price": price_maps[component.code][timestamp],
                "interest": interest,
                "interest_source": source,
            }
        )
    return inputs


def build_rows(
    *,
    target_date: str,
    target_pcf: ReportPcf,
    run_pcf: ReportPcf,
    interests: dict[str, tuple[Decimal, str, str]],
    price_maps: dict[str, dict[str, Decimal]],
    timestamps: list[str] | None = None,
) -> list[dict]:
    timestamps = timestamps or common_minute_timestamps(target_date, price_maps)
    rows: list[dict] = []
    for timestamp in timestamps:
        component_inputs = component_inputs_for_timestamp(target_pcf, interests, price_maps, timestamp)
        estimated = monitor.calculate_estimated_a_from_inputs(
            etf_quote=price_maps[monitor.ETF_CODE][timestamp],
            estimated_cash_component=target_pcf.estimated_cash_component,
            creation_redemption_unit=target_pcf.creation_redemption_unit,
            component_inputs=component_inputs,
        )
        actual = monitor.calculate_estimated_a_from_inputs(
            etf_quote=price_maps[monitor.ETF_CODE][timestamp],
            estimated_cash_component=run_pcf.pre_cash_component,
            creation_redemption_unit=target_pcf.creation_redemption_unit,
            component_inputs=component_inputs,
        )
        row = {
            "timestamp": timestamp,
            "etf_quote": price_maps[monitor.ETF_CODE][timestamp],
            "estimated_cash_component": target_pcf.estimated_cash_component,
            "actual_pre_cash_component": run_pcf.pre_cash_component,
            "component_value": estimated["component_value"],
            "estimated_basket": estimated["basket"],
            "actual_basket": actual["basket"],
            "estimated_a": estimated["estimated_a"],
            "actual_a": actual["estimated_a"],
        }
        for component in target_pcf.components:
            interest, source, _raw_path = interests[component.code]
            row[f"{component.code}_net_price"] = price_maps[component.code][timestamp]
            row[f"{component.code}_interest"] = interest
            row[f"{component.code}_interest_source"] = source
            row[f"{component.code}_pcf_quantity"] = component.pcf_quantity
            row[f"{component.code}_units"] = component.units
        rows.append(row)
    return rows


def decimal_values(rows: list[dict], key: str) -> list[Decimal]:
    return [row[key] for row in rows]


def mean_decimal(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def summarize_rows(rows: list[dict]) -> dict:
    estimated = decimal_values(rows, "estimated_a")
    actual = decimal_values(rows, "actual_a")
    latest = rows[-1]
    return {
        "points": len(rows),
        "first_timestamp": rows[0]["timestamp"],
        "last_timestamp": rows[-1]["timestamp"],
        "estimated_close": safe_decimal(latest["estimated_a"]),
        "actual_close": safe_decimal(latest["actual_a"]),
        "estimated_mean": safe_decimal(mean_decimal(estimated)),
        "actual_mean": safe_decimal(mean_decimal(actual)),
        "estimated_min": safe_decimal(min(estimated)),
        "estimated_max": safe_decimal(max(estimated)),
        "actual_min": safe_decimal(min(actual)),
        "actual_max": safe_decimal(max(actual)),
        "actual_close_near_zero": abs(latest["actual_a"]) <= Decimal("50"),
    }


def write_csv_file(path: Path, rows: list[dict], component_codes: list[str]) -> None:
    base_fields = [
        "timestamp",
        "etf_quote",
        "estimated_cash_component",
        "actual_pre_cash_component",
        "component_value",
        "estimated_basket",
        "actual_basket",
        "estimated_a",
        "actual_a",
    ]
    component_fields: list[str] = []
    for code in component_codes:
        component_fields.extend(
            [
                f"{code}_net_price",
                f"{code}_interest",
                f"{code}_interest_source",
                f"{code}_pcf_quantity",
                f"{code}_units",
            ]
        )
    fields = base_fields + component_fields
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: str(row.get(field, "")) for field in fields})


def fit_text(canvas, text: str, x: float, y: float, max_width: float, font_name: str, font_size: float) -> None:
    size = font_size
    while size > 7 and canvas.stringWidth(text, font_name, size) > max_width:
        size -= 0.5
    canvas.setFont(font_name, size)
    canvas.drawString(x, y, text)


def draw_chart(canvas, rows: list[dict], x: float, y: float, width: float, height: float) -> None:
    from reportlab.lib import colors

    all_values = [float(row["estimated_a"]) for row in rows] + [float(row["actual_a"]) for row in rows]
    low = min(all_values + [0.0])
    high = max(all_values + [0.0])
    span = max(1.0, high - low)
    pad = max(50.0, span * 0.12)
    low -= pad
    high += pad

    def xp(index: int) -> float:
        if len(rows) == 1:
            return x
        return x + width * index / (len(rows) - 1)

    def yp(value: Decimal) -> float:
        return y + (float(value) - low) * height / (high - low)

    canvas.setStrokeColor(colors.HexColor("#d5dbe5"))
    canvas.setLineWidth(0.6)
    canvas.rect(x, y, width, height, stroke=1, fill=0)
    for i in range(5):
        yy = y + height * i / 4
        value = low + (high - low) * i / 4
        canvas.setStrokeColor(colors.HexColor("#edf1f6"))
        canvas.line(x, yy, x + width, yy)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.setFont("Helvetica", 7)
        canvas.drawRightString(x - 6, yy - 2, f"{value:,.0f}")

    zero_y = yp(Decimal("0"))
    if y <= zero_y <= y + height:
        canvas.setStrokeColor(colors.HexColor("#111827"))
        canvas.setLineWidth(0.8)
        canvas.line(x, zero_y, x + width, zero_y)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#111827"))
        canvas.drawString(x + width + 5, zero_y - 2, "0")

    for threshold in (Decimal("300"), Decimal("-300")):
        threshold_y = yp(threshold)
        if y <= threshold_y <= y + height:
            canvas.setStrokeColor(colors.HexColor("#f59e0b"))
            canvas.setDash(3, 3)
            canvas.setLineWidth(0.6)
            canvas.line(x, threshold_y, x + width, threshold_y)
            canvas.setDash()

    def draw_series(key: str, color: str) -> None:
        canvas.setStrokeColor(colors.HexColor(color))
        canvas.setLineWidth(1.4)
        points = [(xp(index), yp(row[key])) for index, row in enumerate(rows)]
        path = canvas.beginPath()
        path.moveTo(points[0][0], points[0][1])
        for px, py in points[1:]:
            path.lineTo(px, py)
        canvas.drawPath(path, stroke=1, fill=0)

    draw_series("estimated_a", "#2563eb")
    draw_series("actual_a", "#dc2626")

    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawString(x, y - 13, rows[0]["timestamp"][11:16])
    canvas.drawCentredString(x + width / 2, y - 13, rows[len(rows) // 2]["timestamp"][11:16])
    canvas.drawRightString(x + width, y - 13, rows[-1]["timestamp"][11:16])


def build_pdf(
    *,
    path: Path,
    target_date: str,
    run_date: str,
    target_pcf: ReportPcf,
    run_pcf: ReportPcf,
    interests: dict[str, tuple[Decimal, str, str]],
    rows: list[dict],
    summary: dict,
    granularity_label: str = "1分钟",
    conclusion_note: str = "主口径 09:31-15:00",
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.pdfgen import canvas
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少reportlab，无法生成PDF；请先安装 requirements.txt 或使用Codex bundled Python") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    font_name = "Helvetica"
    for font_path in (
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
    ):
        if font_path.exists():
            pdfmetrics.registerFont(TTFont("CodexCJK", str(font_path), subfontIndex=0))
            font_name = "CodexCJK"
            break
    width, height = landscape(A4)
    c = canvas.Canvas(str(path), pagesize=landscape(A4))
    margin = 38

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(font_name, 18)
    c.drawString(margin, height - 42, f"511130 {date_iso(target_date)} {granularity_label}预估a / 实际a")
    c.setFont(font_name, 9)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(
        margin,
        height - 60,
        f"运行日PCF: {date_iso(run_date)}；PreTradingDay={date_iso(run_pcf.pre_trading_day)}；"
        f"正式实际a使用运行日 PreCashComponent={safe_decimal(run_pcf.pre_cash_component)}",
    )
    c.drawString(
        margin,
        height - 74,
        "只读生成；公式: a = 511130价格 x 10000 - [sum((债券净价+逐券利息) x PCF数量 x 10) + 现金项]",
    )

    chart_x = margin + 34
    chart_y = 150
    chart_w = width - margin * 2 - 60
    chart_h = height - chart_y - 112
    draw_chart(c, rows, chart_x, chart_y, chart_w, chart_h)

    legend_y = height - 94
    c.setFillColor(colors.HexColor("#2563eb"))
    c.rect(margin, legend_y - 1, 8, 8, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(font_name, 9)
    c.drawString(margin + 12, legend_y, "预估a")
    c.setFillColor(colors.HexColor("#dc2626"))
    c.rect(margin + 70, legend_y - 1, 8, 8, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.drawString(margin + 82, legend_y, "实际a")
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(margin + 140, legend_y, "虚线为 +/-300 元参考线；黑线为 0")

    table_y = 104
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(font_name, 10)
    c.drawString(margin, table_y + 24, "收盘与区间")
    rows_text = [
        ("预估a收盘", summary["estimated_close"], "实际a收盘", summary["actual_close"]),
        ("预估a均值", summary["estimated_mean"], "实际a均值", summary["actual_mean"]),
        (
            "预估a区间",
            f"{summary['estimated_min']} 到 {summary['estimated_max']}",
            "实际a区间",
            f"{summary['actual_min']} 到 {summary['actual_max']}",
        ),
    ]
    col_x = [margin, margin + 90, margin + 250, margin + 340]
    for idx, row in enumerate(rows_text):
        yy = table_y - idx * 16
        c.setFont(font_name, 8.5)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(col_x[0], yy, row[0])
        c.drawString(col_x[2], yy, row[2])
        c.setFillColor(colors.HexColor("#0f172a"))
        c.drawString(col_x[1], yy, row[1])
        c.drawString(col_x[3], yy, row[3])

    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(font_name, 10)
    c.drawString(margin + 500, table_y + 24, "现金与利息来源")
    detail_lines = [
        f"目标日 EstimatedCashComponent: {safe_decimal(target_pcf.estimated_cash_component)}",
        f"次交易日 PreCashComponent: {safe_decimal(run_pcf.pre_cash_component)}",
    ]
    for component in target_pcf.components:
        value, source, _raw_path = interests[component.code]
        detail_lines.append(f"{component.code}: 数量 {component.pcf_quantity} x 10 = {component.units}; 利息 {value} ({source})")
    for idx, line in enumerate(detail_lines[:5]):
        c.setFillColor(colors.HexColor("#475569"))
        fit_text(c, line, margin + 500, table_y - idx * 16, width - margin * 2 - 500, font_name, 8.5)

    assessment = "实际a收盘接近0" if summary["actual_close_near_zero"] else "实际a收盘未接近0，需复查现金项/利息/价格"
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont(font_name, 9)
    c.drawString(margin, 28, f"检查结论: {assessment}；共同点 {summary['points']}，{conclusion_note}。")
    c.save()


def build_summary_payload(
    *,
    target_date: str,
    run_date: str,
    target_pcf: ReportPcf,
    run_pcf: ReportPcf,
    interests: dict[str, tuple[Decimal, str, str]],
    price_raw_paths: dict[str, str],
    rows: list[dict],
    summary: dict,
    csv_path: Path,
    pdf_path: Path,
) -> dict:
    return {
        "target_date": target_date,
        "run_date": run_date,
        "formula": {
            "estimated_a": "ETF_quote * 10000 - [sum((bond_net_price + accrued_interest) * units) + EstimatedCashComponent_T]",
            "actual_a": "ETF_quote * 10000 - [sum((bond_net_price + accrued_interest) * units) + PreCashComponent_next_trading_day]",
            "units": "PCF Quantity * 10",
            "minute_scope": "1-minute common timestamps, excluding 09:30; main session 09:31-11:30 and 13:01-15:00",
        },
        "pcf": {
            "target": {
                "trading_day": target_pcf.trading_day,
                "pre_trading_day": target_pcf.pre_trading_day,
                "record_number": target_pcf.record_number,
                "creation_redemption_unit": str(target_pcf.creation_redemption_unit),
                "estimated_cash_component": str(target_pcf.estimated_cash_component),
                "pre_cash_component": str(target_pcf.pre_cash_component),
                "source_url": target_pcf.source_url,
                "raw_path": target_pcf.raw_path,
            },
            "run": {
                "trading_day": run_pcf.trading_day,
                "pre_trading_day": run_pcf.pre_trading_day,
                "record_number": run_pcf.record_number,
                "creation_redemption_unit": str(run_pcf.creation_redemption_unit),
                "estimated_cash_component": str(run_pcf.estimated_cash_component),
                "pre_cash_component": str(run_pcf.pre_cash_component),
                "source_url": run_pcf.source_url,
                "raw_path": run_pcf.raw_path,
            },
        },
        "components": [
            {
                "code": component.code,
                "name": component.name,
                "pcf_quantity": str(component.pcf_quantity),
                "units": str(component.units),
                "interest": str(interests[component.code][0]),
                "interest_source": interests[component.code][1],
                "interest_raw_path": interests[component.code][2],
            }
            for component in target_pcf.components
        ],
        "price_sources": {
            code: {
                "source": "eastmoney_trends2_get_ndays5_1m",
                "raw_path": raw_path,
            }
            for code, raw_path in price_raw_paths.items()
        },
        "summary": summary,
        "latest_calculation": {
            key: str(value) for key, value in rows[-1].items()
        },
        "outputs": {
            "csv": str(csv_path),
            "pdf": str(pdf_path),
        },
    }


def build_5m_cross_check(
    *,
    target_date: str,
    run_date: str,
    target_pcf: ReportPcf,
    run_pcf: ReportPcf,
    interests: dict[str, tuple[Decimal, str, str]],
    raw_dir: Path,
    report_dir: Path,
    desktop_dir: Path,
    copy_to_desktop: bool,
) -> dict:
    codes = [monitor.ETF_CODE] + [component.code for component in target_pcf.components]
    price_maps: dict[str, dict[str, Decimal]] = {}
    price_raw_paths: dict[str, str] = {}
    for code in codes:
        price_maps[code], price_raw_paths[code] = fetch_eastmoney_5m_raw(code, target_date, raw_dir)
    timestamps = common_5m_timestamps(target_date, price_maps)
    rows = build_rows(
        target_date=target_date,
        target_pcf=target_pcf,
        run_pcf=run_pcf,
        interests=interests,
        price_maps=price_maps,
        timestamps=timestamps,
    )
    summary = summarize_rows(rows)
    csv_path = report_dir / f"511130_{target_date}_5m_estimated_actual_a_cross_check.csv"
    pdf_path = report_dir / f"511130_{target_date}_5m_estimated_actual_a_cross_check.pdf"
    write_csv_file(csv_path, rows, [component.code for component in target_pcf.components])
    build_pdf(
        path=pdf_path,
        target_date=target_date,
        run_date=run_date,
        target_pcf=target_pcf,
        run_pcf=run_pcf,
        interests=interests,
        rows=rows,
        summary=summary,
        granularity_label="5分钟交叉核验",
        conclusion_note="5分钟交叉核验，非正式主口径",
    )
    output_payload = {
        "status": "ok",
        "role": "cross_check_only",
        "note": "5分钟使用东方财富5分钟K线，供应商与1分钟正式报告统一；只用于颗粒度交叉核验，不替代1分钟主口径。",
        "source": "eastmoney_kline_get_5m",
        "summary": summary,
        "outputs": {
            "csv": str(csv_path),
            "pdf": str(pdf_path),
        },
        "price_sources": {
            code: {
                "source": "eastmoney_kline_get_5m",
                "raw_path": raw_path,
            }
            for code, raw_path in price_raw_paths.items()
        },
        "latest_calculation": {
            key: str(value) for key, value in rows[-1].items()
        },
    }
    if copy_to_desktop:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        desktop_pdf_path = desktop_dir / pdf_path.name
        shutil.copy2(pdf_path, desktop_pdf_path)
        output_payload["outputs"]["desktop_pdf"] = str(desktop_pdf_path)
    return output_payload


def generate_report(
    *,
    run_date: str,
    target_date: str | None,
    output_root: Path,
    desktop_dir: Path,
    copy_to_desktop: bool,
    include_5m_cross_check: bool = True,
) -> dict:
    config = monitor.load_config()
    temp_raw_dir = output_root / "_raw_pending" / run_date
    shutil.rmtree(temp_raw_dir, ignore_errors=True)
    resolved_target, target_pcf, run_pcf = resolve_pcfs(
        run_date=run_date,
        target_date=target_date,
        config=config,
        raw_dir=temp_raw_dir,
    )
    report_dir = output_root / resolved_target
    raw_dir = report_dir / "raw"
    if temp_raw_dir.exists():
        raw_dir.parent.mkdir(parents=True, exist_ok=True)
        if raw_dir.exists():
            shutil.rmtree(raw_dir)
        shutil.move(str(temp_raw_dir), str(raw_dir))
    else:
        raw_dir.mkdir(parents=True, exist_ok=True)
    target_pcf = replace(target_pcf, raw_path=str(raw_dir / Path(target_pcf.raw_path).name))
    run_pcf = replace(run_pcf, raw_path=str(raw_dir / Path(run_pcf.raw_path).name))
    validate_report_pcf(target_pcf, resolved_target, config, validate_components=True)
    validate_report_pcf(run_pcf, run_date, config, validate_components=False)
    if run_pcf.pre_trading_day != resolved_target:
        raise RuntimeError(f"运行日PCF PreTradingDay变化: {run_pcf.pre_trading_day} != {resolved_target}")

    interests = get_report_interests(resolved_target, target_pcf, config, raw_dir)
    codes = [monitor.ETF_CODE] + [component.code for component in target_pcf.components]
    price_maps: dict[str, dict[str, Decimal]] = {}
    price_raw_paths: dict[str, str] = {}
    for code in codes:
        price_maps[code], price_raw_paths[code] = fetch_eastmoney_1m_raw(code, raw_dir)

    rows = build_rows(
        target_date=resolved_target,
        target_pcf=target_pcf,
        run_pcf=run_pcf,
        interests=interests,
        price_maps=price_maps,
    )
    summary = summarize_rows(rows)
    csv_path = report_dir / f"511130_{resolved_target}_1m_estimated_actual_a.csv"
    pdf_path = report_dir / f"511130_{resolved_target}_1m_estimated_actual_a.pdf"
    write_csv_file(csv_path, rows, [component.code for component in target_pcf.components])
    build_pdf(
        path=pdf_path,
        target_date=resolved_target,
        run_date=run_date,
        target_pcf=target_pcf,
        run_pcf=run_pcf,
        interests=interests,
        rows=rows,
        summary=summary,
    )
    payload = build_summary_payload(
        target_date=resolved_target,
        run_date=run_date,
        target_pcf=target_pcf,
        run_pcf=run_pcf,
        interests=interests,
        price_raw_paths=price_raw_paths,
        rows=rows,
        summary=summary,
        csv_path=csv_path,
        pdf_path=pdf_path,
    )
    if copy_to_desktop:
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, desktop_dir / pdf_path.name)
        payload["outputs"]["desktop_pdf"] = str(desktop_dir / pdf_path.name)
    if include_5m_cross_check:
        try:
            payload["cross_check_5m"] = build_5m_cross_check(
                target_date=resolved_target,
                run_date=run_date,
                target_pcf=target_pcf,
                run_pcf=run_pcf,
                interests=interests,
                raw_dir=raw_dir,
                report_dir=report_dir,
                desktop_dir=desktop_dir,
                copy_to_desktop=copy_to_desktop,
            )
        except Exception as exc:  # noqa: BLE001
            payload["cross_check_5m"] = {
                "status": "skipped",
                "role": "cross_check_only",
                "error": f"{type(exc).__name__}: {exc}",
                "note": "5分钟交叉核验失败不影响1分钟正式报告；不要用5分钟补写1分钟正式数值。",
            }
    else:
        payload["cross_check_5m"] = {
            "status": "disabled",
            "role": "cross_check_only",
        }
    summary_path = report_dir / "summary.json"
    write_json(summary_path, payload)
    if (output_root / "_raw_pending").exists():
        shutil.rmtree(output_root / "_raw_pending", ignore_errors=True)
    return payload


def write_pending_note(output_root: Path, run_date: str, target_date: str | None, error: Exception) -> Path:
    pending_dir = output_root / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    target_text = target_date or "auto_by_run_pcf_pre_trading_day"
    path = pending_dir / f"{run_date}_{target_text}_pending.md"
    path.write_text(
        "\n".join(
            [
                "# 511130 次日实际a日报未生成",
                "",
                f"- run_date: {run_date}",
                f"- target_date: {target_text}",
                f"- error: {type(error).__name__}: {error}",
                "- status: 未生成正式 PDF/CSV 数值；请确认 PCF、逐券利息、成分券结构和 1分钟行情后重跑。",
                "- safety: 只读数据，未下单，未连接交易接口。",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def is_retryable_error(error: Exception) -> bool:
    message = str(error)
    retryable_markers = (
        "PCF未更新或不可读",
        "读取失败",
        "HTTP",
        "timed out",
        "timeout",
        "东方财富1分钟行情为空",
        "上交所净价全价接口未返回利息",
    )
    structural_markers = (
        "PCF成分券结构变化",
        "CreationRedemptionUnit变化",
        "RecordNumber与成分券数量不一致",
        "PCF TradingDay不匹配",
        "PreTradingDay不匹配",
        "1分钟共同时间戳不足或异常",
    )
    if any(marker in message for marker in structural_markers):
        return False
    return any(marker in message for marker in retryable_markers)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate next-day actual a report for 511130.")
    parser.add_argument("--run-date", default=now_date_compact(), help="运行日/下一交易日，YYYYMMDD，默认上海今天")
    parser.add_argument("--target-date", default="", help="目标交易日，YYYYMMDD；默认用运行日PCF的PreTradingDay")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="输出根目录")
    parser.add_argument("--desktop-dir", default=str(DEFAULT_DESKTOP_DIR), help="PDF同步复制目录")
    parser.add_argument("--no-desktop-copy", action="store_true", help="不复制PDF到桌面")
    parser.add_argument("--retry-until", default="10:00", help="PCF未就绪时重试到HH:MM；空值表示不重试")
    parser.add_argument("--retry-interval-seconds", type=int, default=300, help="重试间隔秒数")
    parser.add_argument("--no-retry", action="store_true", help="禁用重试")
    parser.add_argument("--no-5m-cross-check", action="store_true", help="不生成5分钟交叉核验CSV/PDF")
    return parser.parse_args(argv)


def retry_deadline_timestamp(run_date: str, retry_until: str) -> float | None:
    text = str(retry_until or "").strip()
    if not text:
        return None
    hh, mm = text.split(":", 1)
    dt = datetime(
        int(run_date[:4]),
        int(run_date[4:6]),
        int(run_date[6:8]),
        int(hh),
        int(mm),
        tzinfo=monitor.TZ,
    )
    return dt.timestamp()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_date = parse_date_compact(args.run_date)
    target_date = parse_date_compact(args.target_date) if str(args.target_date).strip() else None
    output_root = Path(args.output_root).expanduser()
    desktop_dir = Path(args.desktop_dir).expanduser()
    deadline = None if args.no_retry else retry_deadline_timestamp(run_date, args.retry_until)
    interval = max(30, int(args.retry_interval_seconds))
    last_error: Exception | None = None

    while True:
        try:
            payload = generate_report(
                run_date=run_date,
                target_date=target_date,
                output_root=output_root,
                desktop_dir=desktop_dir,
                copy_to_desktop=not args.no_desktop_copy,
                include_5m_cross_check=not args.no_5m_cross_check,
            )
            summary = payload["summary"]
            print(f"生成完成: target={payload['target_date']} run={payload['run_date']}")
            print(f"CSV: {payload['outputs']['csv']}")
            print(f"PDF: {payload['outputs']['pdf']}")
            if payload["outputs"].get("desktop_pdf"):
                print(f"Desktop PDF: {payload['outputs']['desktop_pdf']}")
            cross_check = payload.get("cross_check_5m") or {}
            if cross_check.get("status") == "ok":
                print(f"5m CSV: {cross_check['outputs']['csv']}")
                print(f"5m PDF: {cross_check['outputs']['pdf']}")
            elif cross_check:
                print(f"5m cross-check: {cross_check.get('status')} {cross_check.get('error', '')}".strip())
            print(
                "收盘: "
                f"预估a={summary['estimated_close']} 实际a={summary['actual_close']} "
                f"实际收盘接近0={summary['actual_close_near_zero']}"
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            now_ts = datetime.now(monitor.TZ).timestamp()
            retryable = deadline is not None and now_ts < deadline and is_retryable_error(exc)
            if retryable:
                print(f"WARN: {exc}; {interval}秒后重试，最晚到 {args.retry_until}", file=sys.stderr)
                time.sleep(interval)
                continue
            pending = write_pending_note(output_root, run_date, target_date, exc)
            print(f"日报未生成正式结果: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(f"待确认说明: {pending}", file=sys.stderr)
            return 1

    if last_error:
        raise last_error
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
