#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import csv

from decimal import Decimal, InvalidOperation

import sys

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import monitor_511130 as monitor


TZ = ZoneInfo("Asia/Shanghai")


def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        try:
            return float(Decimal(str(value)))
        except (TypeError, ValueError, InvalidOperation):
            return None


@dataclass
class DashboardState:
    date: str
    interval_seconds: int
    max_points: int
    thresholds: list[Decimal]
    auto_run: bool
    auto_run_notify: bool
    allowed_price_sources: list[str]
    max_stale_seconds: int
    public_readonly: bool
    last_run_message: str = ""
    last_error: str = ""

    @property
    def threshold_text(self) -> str:
        if not self.thresholds:
            return ""
        return ", ".join(f"{t}" for t in self.thresholds)


def source_allowed(source: str, allowed_sources: list[str]) -> bool:
    return not allowed_sources or source in allowed_sources


def load_points(date: str, max_points: int = 500, allowed_sources: list[str] | None = None) -> list[dict]:
    allowed_sources = allowed_sources or []
    day_dir = monitor.RUNS_DIR / date
    jsonl_path = day_dir / "a_values.jsonl"
    if jsonl_path.exists():
        points = []
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                price_source = str(row.get("price_source", ""))
                if not source_allowed(price_source, allowed_sources):
                    continue
                if allowed_sources and row.get("strict_realtime") is not True:
                    continue
                a = _to_float(row.get("estimated_a", ""))
                etf = _to_float(row.get("etf_quote", ""))
                if a is None:
                    continue
                points.append(
                    {
                        "timestamp": row.get("timestamp", ""),
                        "estimated_a": a,
                        "etf_quote": etf,
                        "price_source": price_source,
                        "strict_realtime": row.get("strict_realtime") is True,
                        "quote_skew_seconds": _to_float(row.get("quote_skew_seconds", "")),
                        "calculation_elapsed_ms": _to_float(row.get("calculation_elapsed_ms", "")),
                        "basket_value": _to_float(row.get("basket_value", "")),
                        "estimated_cash": _to_float(row.get("estimated_cash", "")),
                    }
                )
        points.sort(key=lambda x: x["timestamp"])
        if max_points and len(points) > max_points:
            points = points[-max_points:]
        return points
    csv_path = day_dir / "a_values.csv"
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    points = []
    for row in rows:
        ts = row.get("timestamp", "")
        price_source = row.get("price_source", "")
        if not source_allowed(price_source, allowed_sources):
            continue
        if allowed_sources and str(row.get("strict_realtime", "")).lower() != "true":
            continue
        a = _to_float(row.get("estimated_a", ""))
        etf = _to_float(row.get("etf_quote", ""))
        if a is None:
            continue
        points.append(
            {
                "timestamp": ts,
                "estimated_a": a,
                "etf_quote": etf,
                "price_source": price_source,
                "strict_realtime": str(row.get("strict_realtime", "")).lower() == "true",
                "quote_skew_seconds": _to_float(row.get("quote_skew_seconds", "")),
                "calculation_elapsed_ms": _to_float(row.get("calculation_elapsed_ms", "")),
                "basket_value": _to_float(row.get("basket_value", "")),
                "estimated_cash": _to_float(row.get("estimated_cash", "")),
            }
        )
    points.sort(key=lambda x: x["timestamp"])
    if max_points and len(points) > max_points:
        points = points[-max_points:]
    return points


def available_dates() -> list[str]:
    if not monitor.RUNS_DIR.exists():
        return []
    dates: list[str] = []
    for path in monitor.RUNS_DIR.iterdir():
        if not path.is_dir():
            continue
        if len(path.name) != 8 or not path.name.isdigit():
            continue
        if (path / "a_values.jsonl").exists() or (path / "a_values.csv").exists():
            dates.append(path.name)
    return sorted(dates)


def load_latest_snapshot(date: str, allowed_sources: list[str] | None = None) -> dict | None:
    allowed_sources = allowed_sources or []
    day_dir = monitor.RUNS_DIR / date
    jsonl_path = day_dir / "a_values.jsonl"
    if not jsonl_path.exists():
        return None
    with jsonl_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        try:
            snapshot = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if source_allowed(str(snapshot.get("price_source", "")), allowed_sources):
            if allowed_sources and snapshot.get("strict_realtime") is not True:
                continue
            return snapshot
    return None


def parse_latest_components(snapshot: dict | None) -> list[dict[str, str]]:
    if not isinstance(snapshot, dict):
        return []
    components = snapshot.get("components")
    if not isinstance(components, list):
        return []
    quote_times = snapshot.get("quote_times") if isinstance(snapshot.get("quote_times"), dict) else {}
    parsed = []
    for row in components:
        if not isinstance(row, dict):
            continue
        code = row.get("code", "").strip()
        interest_source = str(row.get("interest_source", ""))
        parsed.append(
            {
                "code": code,
                "name": (row.get("name") or "").strip(),
                "pcf_quantity": row.get("pcf_quantity", ""),
                "units": row.get("units", ""),
                "price": row.get("price", ""),
                "interest": row.get("interest", ""),
                "interest_source": interest_source,
                "interest_source_label": format_interest_source(interest_source),
                "manual_interest": interest_source == "manual_trading_software_override",
                "value": row.get("value", ""),
                "quote_time": quote_times.get(code, ""),
            }
        )
    return parsed


def load_latest_alert(date: str) -> dict | None:
    alert_path = monitor.RUNS_DIR / date / "alerts.jsonl"
    if not alert_path.exists():
        return None
    with alert_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:  # noqa: BLE001
            continue
    return None


def load_latest_notification(date: str) -> dict | None:
    notification_path = monitor.RUNS_DIR / date / "notifications.jsonl"
    if not notification_path.exists():
        return None
    with notification_path.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:  # noqa: BLE001
            continue
    return None


def format_interest_source(source: str) -> str:
    if source == "manual_trading_software_override":
        return "手工利息"
    if source == "sse_netfull":
        return "上交所利息"
    if source.startswith("cached_interest"):
        return "缓存利息"
    if source.startswith("missing_interest_default"):
        return "缺利息"
    return source or "-"


def latest_value(points: list[dict]) -> dict | None:
    if not points:
        return None
    return points[-1]


def snapshot_age_seconds(snapshot: dict | None) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    timestamp = str(snapshot.get("timestamp", ""))
    try:
        if len(timestamp) == 16:
            dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        else:
            dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except Exception:  # noqa: BLE001
        return None
    return (datetime.now(TZ) - dt).total_seconds()


def snapshot_is_fresh(snapshot: dict | None, max_stale_seconds: int) -> bool:
    age = snapshot_age_seconds(snapshot)
    if age is None:
        return False
    return age <= max_stale_seconds


def data_blocking_error(error: str) -> bool:
    if not error:
        return False
    blocking_fragments = [
        "行情时间差",
        "非严格实时",
        "strict_realtime",
        "缺少行情时间戳",
        "缺少逐券应计利息",
        "利息缺失",
        "PCF",
        "过期",
        "拒绝使用",
        "未标记",
    ]
    return any(fragment in error for fragment in blocking_fragments)


def decimal_from_payload(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        return None
    if result.is_nan():
        return None
    return result


def first_alert_threshold(thresholds: list[Decimal]) -> Decimal:
    for threshold in sorted(thresholds):
        if threshold == Decimal("300"):
            return threshold
    return Decimal("300")


def classify_status(
    *,
    latest: dict | None,
    latest_snapshot: dict | None,
    snapshot_fresh: bool,
    last_error: str,
    latest_notification: dict | None,
    thresholds: list[Decimal],
) -> dict[str, str]:
    if last_error and data_blocking_error(last_error):
        if "利息" in last_error:
            return {"code": "missing_interest", "label": "缺利息", "level": "danger", "detail": last_error}
        if "行情时间差" in last_error or "非严格" in last_error or "strict_realtime" in last_error:
            return {"code": "quote_unsynced", "label": "行情不同步", "level": "danger", "detail": last_error}
        return {"code": "quote_stale", "label": "行情过旧", "level": "warning", "detail": last_error}
    if latest_snapshot and not snapshot_fresh:
        age = snapshot_age_seconds(latest_snapshot)
        detail = "最新严格实时快照已过期"
        if age is not None:
            detail = f"最新严格实时快照距现在 {age:.0f} 秒，已超过允许范围"
        return {"code": "quote_stale", "label": "行情过旧", "level": "warning", "detail": detail}
    if not latest:
        return {"code": "waiting", "label": "等待数据", "level": "muted", "detail": "暂无可展示的严格实时a值"}
    value = decimal_from_payload(latest.get("estimated_a"))
    threshold = first_alert_threshold(thresholds)
    if value is None:
        return {"code": "waiting", "label": "等待数据", "level": "muted", "detail": "最新a值为空"}
    if value >= threshold:
        return {"code": "over_300", "label": "已超过300", "level": "danger", "detail": f"a值已高于 {threshold}"}
    if threshold - value <= Decimal("50"):
        return {"code": "near_300", "label": "接近300", "level": "warning", "detail": f"距离 {threshold} 不超过50"}
    return {"code": "normal", "label": "正常", "level": "ok", "detail": "严格实时行情同步，a值未接近首档阈值"}


def threshold_gap(latest: dict | None, threshold: Decimal) -> dict[str, str]:
    if not latest:
        return {"text": "-", "value": "-", "direction": "unknown"}
    value = decimal_from_payload(latest.get("estimated_a"))
    if value is None:
        return {"text": "-", "value": "-", "direction": "unknown"}
    gap = threshold - value
    if gap >= 0:
        return {"text": f"还差 {monitor.money(gap)}", "value": str(monitor.q2(gap)), "direction": "below"}
    return {"text": f"已超过 {monitor.money(abs(gap))}", "value": str(monitor.q2(abs(gap))), "direction": "above"}


def build_formula_snapshot(snapshot: dict | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    components = parse_latest_components(snapshot)
    etf_quote = snapshot.get("etf_quote", "-")
    estimated_cash = snapshot.get("estimated_cash", "-")
    estimated_a = snapshot.get("estimated_a", "-")
    lines = [f"a = {etf_quote} / 100 * 1,000,000"]
    for row in components:
        lines.append(f"    - ({row.get('price') or '-'} + {row.get('interest') or '-'}) * {row.get('units') or '-'}")
    lines.append(f"    - {estimated_cash}")
    lines.append(f"  = {estimated_a}")
    return {
        "formula_text": "\n".join(lines),
        "etf_quote": etf_quote,
        "etf_value": snapshot.get("etf_value", "-"),
        "components": components,
        "component_value_ex_cash": snapshot.get("component_value_ex_cash", "-"),
        "estimated_cash": estimated_cash,
        "basket_value": snapshot.get("basket_value", "-"),
        "estimated_a": estimated_a,
        "formula_version": snapshot.get("formula_version", ""),
    }


def chart_stats(points: list[dict]) -> dict[str, str]:
    values = [p["estimated_a"] for p in points if isinstance(p.get("estimated_a"), (int, float))]
    if not values:
        return {"min": "-", "max": "-", "latest": "-"}
    return {
        "min": f"{min(values):.2f}",
        "max": f"{max(values):.2f}",
        "latest": f"{values[-1]:.2f}",
    }


def parse_point_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def series_range_delta(range_key: str) -> timedelta | None:
    ranges = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
    }
    return ranges.get(range_key)


def filter_points_by_range(points: list[dict], range_key: str) -> list[dict]:
    if range_key in {"today", "day", "all"}:
        return points
    delta = series_range_delta(range_key)
    if delta is None or not points:
        return points
    end_time = None
    for point in reversed(points):
        end_time = parse_point_time(point.get("timestamp"))
        if end_time is not None:
            break
    if end_time is None:
        return points
    start_time = end_time - delta
    filtered = []
    for point in points:
        point_time = parse_point_time(point.get("timestamp"))
        if point_time is not None and point_time >= start_time:
            filtered.append(point)
    return filtered


def bucket_start(dt: datetime, interval_key: str) -> datetime:
    if interval_key == "1m":
        return dt.replace(second=0, microsecond=0)
    if interval_key == "15m":
        minute = (dt.minute // 15) * 15
        return dt.replace(minute=minute, second=0, microsecond=0)
    return dt.replace(microsecond=0)


def aggregate_ohlc(points: list[dict], interval_key: str) -> list[dict]:
    buckets: dict[str, dict[str, Any]] = {}
    for point in points:
        value = _to_float(point.get("estimated_a"))
        point_time = parse_point_time(point.get("timestamp"))
        if value is None or point_time is None:
            continue
        start = bucket_start(point_time, interval_key)
        key = start.strftime("%Y-%m-%d %H:%M:%S")
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                "timestamp": key,
                "open": value,
                "high": value,
                "low": value,
                "close": value,
                "count": 1,
                "first_timestamp": point.get("timestamp", key),
                "last_timestamp": point.get("timestamp", key),
            }
            continue
        bucket["high"] = max(bucket["high"], value)
        bucket["low"] = min(bucket["low"], value)
        bucket["close"] = value
        bucket["count"] += 1
        bucket["last_timestamp"] = point.get("timestamp", key)
    return [buckets[key] for key in sorted(buckets)]


def series_stats(items: list[dict], kind: str) -> dict[str, str]:
    if not items:
        return {"min": "-", "max": "-", "latest": "-"}
    if kind == "ohlc":
        lows = [item["low"] for item in items if isinstance(item.get("low"), (int, float))]
        highs = [item["high"] for item in items if isinstance(item.get("high"), (int, float))]
        closes = [item["close"] for item in items if isinstance(item.get("close"), (int, float))]
        if not lows or not highs or not closes:
            return {"min": "-", "max": "-", "latest": "-"}
        return {"min": f"{min(lows):.2f}", "max": f"{max(highs):.2f}", "latest": f"{closes[-1]:.2f}"}
    return chart_stats(items)


def build_series_payload(state: DashboardState, query: dict[str, list[str]]) -> dict[str, Any]:
    requested_date = query.get("date", [state.date])[0]
    date = validate_target_date(requested_date)
    range_key = query.get("range", ["15m"])[0]
    interval_key = query.get("interval", ["1s"])[0]
    valid_ranges = {"1m", "5m", "15m", "1h", "today", "day", "all"}
    valid_intervals = {"1s", "1m", "15m"}
    if range_key not in valid_ranges:
        raise ValueError(f"不支持的范围: {range_key}")
    if interval_key not in valid_intervals:
        raise ValueError(f"不支持的周期: {interval_key}")
    raw_points = load_points(date, max_points=0, allowed_sources=state.allowed_price_sources)
    ranged_points = filter_points_by_range(raw_points, range_key)
    if interval_key == "1s":
        kind = "line"
        items = ranged_points
    else:
        kind = "ohlc"
        items = aggregate_ohlc(ranged_points, interval_key)
    return {
        "ok": True,
        "date": date,
        "range": range_key,
        "interval": interval_key,
        "kind": kind,
        "points": items,
        "stats": series_stats(items, kind),
        "raw_count": len(raw_points),
        "range_count": len(ranged_points),
        "count": len(items),
        "available_dates": available_dates(),
        "thresholds": [float(t) for t in state.thresholds],
        "note": "1秒原始严格实时点" if kind == "line" else f"{interval_key} a值OHLC聚合",
    }


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def config_snapshot(config: dict, state: DashboardState) -> dict[str, Any]:
    overrides = ((config.get("interest_overrides") or {}).get(state.date) or {})
    return {
        "target_date": state.date,
        "thresholds": [str(item) for item in state.thresholds],
        "thresholds_text": ",".join(str(item) for item in state.thresholds),
        "interest_overrides": {str(k): str(v) for k, v in overrides.items()},
        "max_skew_seconds": str(config.get("realtime_max_skew_seconds", "3")),
        "max_stale_seconds": str(state.max_stale_seconds),
        "allowed_price_sources": state.allowed_price_sources,
        "public_readonly": state.public_readonly,
    }


def build_data_payload(state: DashboardState, config: dict) -> dict[str, Any]:
    points = load_points(state.date, max_points=state.max_points, allowed_sources=state.allowed_price_sources)
    latest_snapshot = load_latest_snapshot(state.date, allowed_sources=state.allowed_price_sources)
    snapshot_fresh = snapshot_is_fresh(latest_snapshot, state.max_stale_seconds)
    latest_notification = load_latest_notification(state.date)
    latest_alert = load_latest_alert(state.date)
    last_error = state.last_error
    if latest_snapshot and not snapshot_fresh:
        last_error = last_error or "最新严格实时快照已过期，当前a不展示"
    latest = latest_value(points) if snapshot_fresh and not data_blocking_error(last_error) else None
    thresholds = [float(t) for t in state.thresholds]
    target_threshold = first_alert_threshold(state.thresholds)
    status = classify_status(
        latest=latest,
        latest_snapshot=latest_snapshot,
        snapshot_fresh=snapshot_fresh,
        last_error=last_error,
        latest_notification=latest_notification,
        thresholds=state.thresholds,
    )
    point_stats = chart_stats(points)
    chart_current = latest is not None
    return {
        "ok": True,
        "points": points,
        "stats": point_stats,
        "chart_current": chart_current,
        "chart_notice": "" if chart_current else "曲线仅为历史点，当前a未通过实时校验",
        "latest_a": "-" if not latest else f"{latest['estimated_a']:.2f}",
        "latest_etf_quote": "-"
        if not latest or latest.get("etf_quote") is None
        else f"{latest['etf_quote']:.3f}",
        "status": status,
        "status_text": status["label"],
        "run_message": state.last_run_message or "已加载",
        "distance_to_300": threshold_gap(latest, target_threshold),
        "target_threshold": str(target_threshold),
        "components": parse_latest_components(latest_snapshot) if latest else [],
        "formula": build_formula_snapshot(latest_snapshot) if latest else {},
        "component_timestamp": (latest_snapshot or {}).get("timestamp", "-") if latest else "-",
        "component_source": (latest_snapshot or {}).get("price_source", "-") if latest else "-",
        "strict_realtime": (latest_snapshot or {}).get("strict_realtime", False) if latest else False,
        "quote_times": (latest_snapshot or {}).get("quote_times", {}) if latest else {},
        "quote_skew_seconds": (latest_snapshot or {}).get("quote_skew_seconds") if latest else None,
        "calculated_at": (latest_snapshot or {}).get("calculated_at", "-") if latest else "-",
        "calculation_elapsed_ms": (latest_snapshot or {}).get("calculation_elapsed_ms") if latest else None,
        "latest_alert": latest_alert or {},
        "latest_notification": latest_notification or {},
        "thresholds": thresholds,
        "last_error": last_error,
        "count": len(points),
        "available_dates": available_dates(),
        "config": config_snapshot(config, state),
    }


def build_health_payload(state: DashboardState) -> dict[str, Any]:
    latest_snapshot = load_latest_snapshot(state.date, allowed_sources=state.allowed_price_sources)
    age = snapshot_age_seconds(latest_snapshot)
    return {
        "ok": True,
        "service": "511130-live-dashboard",
        "date": state.date,
        "auto_run": state.auto_run,
        "public_readonly": state.public_readonly,
        "latest_snapshot_timestamp": (latest_snapshot or {}).get("timestamp"),
        "latest_snapshot_age_seconds": None if age is None else round(age, 1),
        "last_run_message": state.last_run_message,
        "last_error": state.last_error,
    }


def parse_threshold_list(raw: Any) -> list[Decimal]:
    if isinstance(raw, str):
        parts = [part.strip() for part in raw.replace("，", ",").split(",")]
    elif isinstance(raw, list):
        parts = [str(part).strip() for part in raw]
    else:
        raise ValueError("阈值必须是逗号分隔文本或数组")
    thresholds: list[Decimal] = []
    for part in parts:
        if not part:
            continue
        value = monitor.dec(part)
        if value.is_nan():
            raise ValueError(f"阈值无效: {part}")
        thresholds.append(value)
    if not thresholds:
        raise ValueError("至少需要一个阈值")
    return thresholds


def config_threshold_values(thresholds: list[Decimal]) -> list[int | str]:
    values: list[int | str] = []
    for threshold in thresholds:
        if threshold == threshold.to_integral_value():
            values.append(int(threshold))
        else:
            values.append(str(threshold))
    return values


def validate_target_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError("目标日期必须是 YYYYMMDD，例如 20260615")
    return text


def save_config_update(
    payload: dict[str, Any],
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
) -> tuple[bool, str, str]:
    raw_config = json.loads(monitor.CONFIG_PATH.read_text(encoding="utf-8"))
    target_date = validate_target_date(payload.get("target_date", state.date))
    thresholds = parse_threshold_list(payload.get("thresholds", state.threshold_text))
    raw_config["target_date"] = target_date
    raw_config["thresholds"] = config_threshold_values(thresholds)

    interest_payload = payload.get("interest_overrides") or {}
    if not isinstance(interest_payload, dict):
        raise ValueError("利息覆盖必须是对象")
    overrides = raw_config.setdefault("interest_overrides", {}).setdefault(target_date, {})
    for code, value in interest_payload.items():
        code_text = str(code).strip()
        if not code_text:
            continue
        value_text = str(value).strip()
        if not value_text:
            overrides.pop(code_text, None)
            continue
        interest = monitor.dec(value_text)
        if interest.is_nan():
            raise ValueError(f"{code_text} 利息无效: {value_text}")
        overrides[code_text] = str(interest)
    if not overrides:
        raw_config.get("interest_overrides", {}).pop(target_date, None)

    monitor.CONFIG_PATH.write_text(
        json.dumps(raw_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    config.clear()
    config.update(monitor.load_config())
    state.date = target_date
    state.thresholds = thresholds
    state.allowed_price_sources = (
        list(config.get("strict_realtime_price_sources", ["realtime_eastmoney"]))
        if config.get("require_realtime_snapshot")
        else []
    )
    state.max_stale_seconds = int(config.get("realtime_max_stale_seconds", 30))
    state.last_run_message = f"配置已保存: {datetime.now(TZ).strftime('%H:%M:%S')}"
    state.last_error = ""

    warning = ""
    if state.auto_run:
        try:
            context_ref["value"] = monitor.prepare_calculation_context(target_date, config)
        except Exception as exc:  # noqa: BLE001
            context_ref["value"] = None
            warning = f"配置已保存，但自动计算预加载失败: {type(exc).__name__}: {exc}"
            state.last_error = warning
            state.last_run_message = "配置已保存，预加载失败"
    return True, "已保存", warning


def build_dashboard_html(state: DashboardState, points: list[dict]) -> str:
    threshold_lines = [float(item) for item in state.thresholds]
    bootstrap = {
        "refreshSec": state.interval_seconds,
        "date": state.date,
        "thresholds": threshold_lines,
        "autoRun": state.auto_run,
        "autoRunNotify": state.auto_run_notify,
        "pointCount": len(points),
    }
    html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>511130 a值团队看板</title>
  <style>
    :root {
      --bg: #f4f6f8;
      --panel: #ffffff;
      --line: #d9e0ea;
      --text: #111827;
      --muted: #667085;
      --blue: #2563eb;
      --green: #15803d;
      --amber: #b45309;
      --red: #b91c1c;
      --red-bg: #fff1f2;
      --shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--text);
      background: var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    body.alert-active { background: var(--red-bg); }
    .page { max-width: 1280px; margin: 0 auto; padding: 16px; }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      padding: 4px 0 12px;
    }
    h1 { margin: 0; font-size: 22px; line-height: 1.25; }
    .meta { color: var(--muted); font-size: 13px; margin-top: 4px; }
    .hero {
      display: grid;
      grid-template-columns: minmax(280px, 1.25fr) minmax(320px, 2fr);
      gap: 12px;
      align-items: stretch;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 14px;
    }
    .primary-value {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: start;
    }
    .a-label { color: var(--muted); font-size: 13px; }
    .a-value {
      font-size: clamp(44px, 8vw, 82px);
      line-height: .95;
      font-weight: 750;
      margin-top: 6px;
      font-variant-numeric: tabular-nums;
    }
    body.alert-active .a-value { color: var(--red); }
    .status-pill {
      min-width: 92px;
      border-radius: 999px;
      padding: 7px 10px;
      text-align: center;
      font-size: 13px;
      font-weight: 700;
      border: 1px solid var(--line);
      background: #f8fafc;
    }
    .status-ok { color: var(--green); background: #ecfdf3; border-color: #bbf7d0; }
    .status-warning { color: var(--amber); background: #fffbeb; border-color: #fde68a; }
    .status-danger { color: var(--red); background: #fff1f2; border-color: #fecdd3; }
    .status-muted { color: #475569; background: #f1f5f9; }
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }
    .metric {
      border: 1px solid #e6ebf2;
      background: #f8fafc;
      border-radius: 8px;
      padding: 10px;
      min-height: 70px;
    }
    .metric .label { color: var(--muted); font-size: 12px; }
    .metric .value { margin-top: 4px; font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
    .bond-strip { display: grid; gap: 8px; margin-top: 10px; }
    .bond-mini {
      display: grid;
      grid-template-columns: 72px 1fr auto;
      gap: 8px;
      align-items: center;
      border-top: 1px solid #e6ebf2;
      padding-top: 8px;
      font-size: 13px;
    }
    .bond-mini strong { font-size: 14px; }
    .tag { display: inline-block; border-radius: 999px; padding: 2px 7px; font-size: 12px; background: #eef2ff; color: #3730a3; margin-left: 4px; }
    .tag.warn { background: #fff7ed; color: #9a3412; }
    canvas { width: 100%; height: 360px; display: block; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; }
    .chart-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 10px;
      margin-bottom: 10px;
    }
    .chart-controls {
      display: grid;
      grid-template-columns: repeat(3, minmax(88px, 1fr));
      gap: 8px;
      min-width: 340px;
    }
    .chart-control label { margin-bottom: 3px; }
    .chart-footer {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 8px;
    }
    .chart-stat { border: 1px solid #e6ebf2; border-radius: 8px; padding: 8px; background: #f8fafc; }
    .chart-stat span { display: block; color: var(--muted); font-size: 12px; }
    .chart-stat strong { display: block; margin-top: 2px; font-size: 16px; font-variant-numeric: tabular-nums; }
    .section-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
    .section-title { font-size: 16px; font-weight: 750; margin: 0 0 10px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-x: auto;
      background: #0f172a;
      color: #e5e7eb;
      border-radius: 8px;
      padding: 12px;
      line-height: 1.55;
      font-size: 13px;
    }
    .breakdown {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .breakdown div { border: 1px solid #e6ebf2; border-radius: 8px; padding: 8px; background: #f8fafc; }
    .breakdown span { display: block; color: var(--muted); font-size: 12px; }
    .breakdown strong { display: block; margin-top: 2px; font-variant-numeric: tabular-nums; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; vertical-align: top; }
    th { color: #475569; font-weight: 700; background: #f8fafc; }
    td { color: #1f2937; }
    .table-wrap { overflow-x: auto; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    button {
      border: 1px solid #1d4ed8;
      background: var(--blue);
      color: #fff;
      padding: 8px 11px;
      border-radius: 8px;
      cursor: pointer;
      font-weight: 650;
      min-height: 36px;
    }
    button.secondary { background: #334155; border-color: #334155; }
    button:disabled { opacity: .5; cursor: default; }
    .config-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    input, select {
      width: 100%;
      border: 1px solid #ccd5e1;
      border-radius: 8px;
      padding: 8px 9px;
      min-height: 36px;
      font-size: 14px;
      background: #fff;
    }
    .interest-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin: 10px 0; }
    .message { color: var(--muted); font-size: 13px; min-height: 18px; overflow-wrap: anywhere; }
    .error { color: var(--red); }
    .ok { color: var(--green); }
    @media (max-width: 860px) {
      .page { padding: 10px; }
      .topbar { display: block; }
      .hero, .section-grid { grid-template-columns: 1fr; }
      .chart-head { display: block; }
      .chart-controls { grid-template-columns: repeat(3, minmax(0, 1fr)); min-width: 0; margin-bottom: 10px; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .section-grid { gap: 10px; }
      canvas { height: 300px; }
      .config-grid, .interest-grid { grid-template-columns: 1fr; }
      .a-value { font-size: 58px; }
    }
    @media (max-width: 480px) {
      .page { padding: 8px; }
      .panel { padding: 12px; }
      .toolbar { gap: 6px; }
      button { padding: 7px 9px; font-size: 13px; }
      .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
      .metric { min-height: 58px; padding: 8px; }
      .metric .value { font-size: 18px; }
      .chart-footer { grid-template-columns: 1fr; }
      .chart-controls { grid-template-columns: 1fr; }
      .breakdown { grid-template-columns: 1fr; }
      .bond-mini { grid-template-columns: 64px 1fr auto; gap: 6px; font-size: 12px; }
      .bond-mini strong { font-size: 13px; }
    }
  </style>
</head>
<body>
  <main class="page">
    <div class="topbar">
      <div>
        <h1>511130 a值团队看板</h1>
        <div class="meta" id="topMeta">加载中</div>
      </div>
      <div class="toolbar">
        <button id="refreshData">刷新</button>
        <button id="recalc" class="secondary write-action">手动算一次</button>
        <button id="recalcNotify" class="secondary write-action">发送飞书测试</button>
      </div>
    </div>

    <section class="hero">
      <div class="panel">
        <div class="primary-value">
          <div>
            <div class="a-label">当前 a 值</div>
            <div class="a-value" id="latestA">-</div>
          </div>
          <div class="status-pill status-muted" id="statusPill">等待数据</div>
        </div>
        <div class="metric-grid">
          <div class="metric"><div class="label">511130 价格</div><div class="value" id="latestETF">-</div></div>
          <div class="metric"><div class="label">距离 300</div><div class="value" id="distance300">-</div></div>
          <div class="metric"><div class="label">行情时间差</div><div class="value" id="quoteSkew">-</div></div>
          <div class="metric"><div class="label">最新计算时间</div><div class="value" id="calcTime">-</div></div>
          <div class="metric"><div class="label">飞书最近状态</div><div class="value" id="latestNotify">-</div></div>
          <div class="metric"><div class="label">点位数</div><div class="value" id="count">-</div></div>
        </div>
        <div class="bond-strip" id="componentStrip"></div>
        <div class="message error" id="lastErr"></div>
      </div>

      <div class="panel">
        <div class="chart-head">
          <div>
            <div class="section-title">a 曲线 / a-K线</div>
            <div class="meta" id="chartModeNote">默认显示近15分钟1秒实时线</div>
          </div>
          <div class="chart-controls">
            <div class="chart-control">
              <label for="seriesRange">范围</label>
              <select id="seriesRange">
                <option value="1m">近1分钟</option>
                <option value="5m">近5分钟</option>
                <option value="15m" selected>近15分钟</option>
                <option value="1h">近1小时</option>
                <option value="today">今天</option>
              </select>
            </div>
            <div class="chart-control">
              <label for="seriesInterval">周期</label>
              <select id="seriesInterval">
                <option value="1s" selected>1秒</option>
                <option value="1m">1分钟</option>
                <option value="15m">15分钟</option>
              </select>
            </div>
            <div class="chart-control">
              <label for="seriesDate">日期</label>
              <select id="seriesDate"></select>
            </div>
          </div>
        </div>
        <canvas id="chart" height="360"></canvas>
        <div class="chart-footer">
          <div class="chart-stat"><span>最近最高</span><strong id="chartMax">-</strong></div>
          <div class="chart-stat"><span>最近最低</span><strong id="chartMin">-</strong></div>
          <div class="chart-stat"><span>最新值</span><strong id="chartLatest">-</strong></div>
        </div>
      </div>
    </section>

    <section class="section-grid">
      <div class="panel">
        <div class="section-title">计算过程</div>
        <pre id="formulaText">暂无严格实时计算过程</pre>
        <div class="breakdown" id="breakdown"></div>
      </div>

      <div class="panel">
        <div class="section-title">配置和操作</div>
        <div class="config-grid">
          <div>
            <label for="targetDate">目标日期</label>
            <input id="targetDate" inputmode="numeric" autocomplete="off">
          </div>
          <div>
            <label for="thresholdsInput">阈值</label>
            <input id="thresholdsInput" autocomplete="off">
          </div>
        </div>
        <div class="interest-grid" id="interestInputs"></div>
        <div class="toolbar">
          <button id="saveConfig" class="write-action">保存配置</button>
        </div>
        <div class="message" id="configMessage"></div>
      </div>
    </section>

    <section class="panel" style="margin-top:12px;">
      <div class="section-title">成分券明细</div>
      <div class="meta">采样时间：<span id="compTs">-</span>；来源：<span id="compSource">-</span></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>实时净价</th>
              <th>当日利息</th>
              <th>数量</th>
              <th>价值</th>
              <th>行情时间</th>
            </tr>
          </thead>
          <tbody id="componentsBody">
            <tr><td colspan="7" style="color:#667085;">暂无成分券数据</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>

<script>
const bootstrap = __BOOTSTRAP_JSON__;
const cfg = {
  refreshSec: bootstrap.refreshSec || 1,
  thresholds: bootstrap.thresholds || [],
  date: bootstrap.date || ""
};
let latest = [];
let chartKind = "line";
let lastPayload = null;
let seriesPayload = null;
const RANGE_LABELS = {
  "1m": "近1分钟",
  "5m": "近5分钟",
  "15m": "近15分钟",
  "1h": "近1小时",
  "today": "今天"
};
const INTERVAL_LABELS = {
  "1s": "1秒",
  "1m": "1分钟",
  "15m": "15分钟"
};

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value == null || value === "" ? "-" : String(value);
}

function asNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value) {
  const n = asNumber(value);
  if (n === null) return value || "-";
  return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function shortTime(value) {
  if (!value) return "-";
  const text = String(value);
  if (text.includes("T")) return text.slice(11, 19);
  if (text.length >= 19) return text.slice(11, 19);
  return text;
}

function notifyText(notice) {
  if (!notice || !notice.status) return "-";
  const code = notice.notification_response_code ?? "-";
  const elapsed = notice.notification_elapsed_ms ? `${notice.notification_elapsed_ms}ms` : "-";
  return `${notice.status} code=${code} ${elapsed}`;
}

function populateDateOptions(dates, currentDate) {
  const select = document.getElementById("seriesDate");
  if (!select) return;
  const existing = new Set(Array.from(select.options).map(x => x.value));
  const nextDates = Array.from(new Set([currentDate, ...(dates || [])].filter(Boolean))).sort();
  if (nextDates.length === existing.size && nextDates.every(x => existing.has(x))) return;
  const selected = select.value || currentDate || cfg.date;
  select.innerHTML = "";
  for (const date of nextDates) {
    const option = document.createElement("option");
    option.value = date;
    option.textContent = date === currentDate ? `${date} 今天` : date;
    select.appendChild(option);
  }
  select.value = nextDates.includes(selected) ? selected : (currentDate || nextDates[nextDates.length - 1] || "");
}

function selectedSeriesDate() {
  return document.getElementById("seriesDate")?.value || cfg.date;
}

function selectedSeriesRange() {
  return document.getElementById("seriesRange")?.value || "15m";
}

function selectedSeriesInterval() {
  return document.getElementById("seriesInterval")?.value || "1s";
}

function setStatus(status) {
  const pill = document.getElementById("statusPill");
  const level = status?.level || "muted";
  const code = status?.code || "";
  pill.className = `status-pill status-${level}`;
  pill.textContent = status?.label || "等待数据";
  document.body.classList.toggle("alert-active", code === "over_300");
}

function applyReadOnlyMode(payload) {
  const readonly = Boolean(payload.config?.public_readonly);
  for (const el of document.querySelectorAll(".write-action")) {
    el.disabled = readonly;
    el.style.display = readonly ? "none" : "";
  }
  const msg = document.getElementById("configMessage");
  if (readonly && msg && !msg.textContent) {
    msg.textContent = "公网只读模式：配置和通知操作已关闭";
  }
}

function renderComponentStrip(components) {
  const wrap = document.getElementById("componentStrip");
  wrap.innerHTML = "";
  if (!components || !components.length) return;
  for (const item of components.slice(0, 2)) {
    const row = document.createElement("div");
    row.className = "bond-mini";
    const tag = item.manual_interest ? '<span class="tag warn">手工利息</span>' : `<span class="tag">${item.interest_source_label || "-"}</span>`;
    row.innerHTML = `
      <strong>${item.code || "-"}</strong>
      <div>${item.name || "-"}<br><span class="meta">净价 ${item.price || "-"}，利息 ${item.interest || "-"} ${tag}</span></div>
      <div class="right"><strong>${money(item.value)}</strong><br><span class="meta">${item.quote_time || "-"}</span></div>
    `;
    wrap.appendChild(row);
  }
}

function renderComponents(components) {
  const body = document.getElementById("componentsBody");
  if (!components || components.length === 0) {
    body.innerHTML = '<tr><td colspan="7" style="color:#667085;">暂无成分券数据</td></tr>';
    return;
  }
  body.innerHTML = "";
  for (const item of components) {
    const tr = document.createElement("tr");
    const tag = item.manual_interest ? '<span class="tag warn">手工利息</span>' : `<span class="tag">${item.interest_source_label || "-"}</span>`;
    tr.innerHTML = `
      <td>${item.code || "-"}</td>
      <td>${item.name || "-"}</td>
      <td>${item.price || "-"}</td>
      <td>${item.interest || "-"} ${tag}</td>
      <td>${item.units || "-"}</td>
      <td>${money(item.value)}</td>
      <td>${item.quote_time || "-"}</td>
    `;
    body.appendChild(tr);
  }
}

function renderFormula(formula) {
  const text = formula?.formula_text || "暂无严格实时计算过程";
  document.getElementById("formulaText").textContent = text;
  const breakdown = document.getElementById("breakdown");
  breakdown.innerHTML = "";
  const rows = [
    ["ETF端价值", formula?.etf_value],
    ["019776贡献值", (formula?.components || []).find(x => x.code === "019776")?.value],
    ["019837贡献值", (formula?.components || []).find(x => x.code === "019837")?.value],
    ["篮子合计", formula?.basket_value],
    ["EstimatedCashComponent", formula?.estimated_cash],
    ["最终a", formula?.estimated_a],
  ];
  for (const [label, value] of rows) {
    const div = document.createElement("div");
    div.innerHTML = `<span>${label}</span><strong>${money(value)}</strong>`;
    breakdown.appendChild(div);
  }
}

function renderConfig(payload) {
  const config = payload.config || {};
  const dateInput = document.getElementById("targetDate");
  const thresholdInput = document.getElementById("thresholdsInput");
  if (document.activeElement !== dateInput) dateInput.value = config.target_date || "";
  if (document.activeElement !== thresholdInput) thresholdInput.value = config.thresholds_text || "";

  const codes = new Set(["019776", "019837"]);
  for (const item of payload.components || []) codes.add(item.code);
  for (const code of Object.keys(config.interest_overrides || {})) codes.add(code);
  const box = document.getElementById("interestInputs");
  const activeCode = document.activeElement?.dataset?.code;
  const activeValue = document.activeElement?.value;
  box.innerHTML = "";
  for (const code of codes) {
    if (!code) continue;
    const wrap = document.createElement("div");
    const value = activeCode === code ? activeValue : (config.interest_overrides || {})[code] || "";
    wrap.innerHTML = `
      <label for="interest-${code}">${code} 当日利息</label>
      <input id="interest-${code}" data-code="${code}" class="interest-input" autocomplete="off" value="${value}">
    `;
    box.appendChild(wrap);
  }
}

function visiblePoints() {
  return latest;
}

function updateChartStats(points) {
  if (seriesPayload?.stats) {
    setText("chartMax", money(seriesPayload.stats.max));
    setText("chartMin", money(seriesPayload.stats.min));
    setText("chartLatest", money(seriesPayload.stats.latest));
    return;
  }
  const values = points.map(x => asNumber(x.estimated_a)).filter(x => x !== null);
  if (!values.length) {
    setText("chartMax", "-");
    setText("chartMin", "-");
    setText("chartLatest", "-");
    return;
  }
  setText("chartMax", money(Math.max(...values)));
  setText("chartMin", money(Math.min(...values)));
  setText("chartLatest", money(values[values.length - 1]));
}

function chartValues(points) {
  if (chartKind === "ohlc") {
    const values = [];
    for (const item of points) {
      const low = asNumber(item.low);
      const high = asNumber(item.high);
      if (low !== null) values.push(low);
      if (high !== null) values.push(high);
    }
    return values;
  }
  return points.map(x => asNumber(x.estimated_a)).filter(x => x !== null);
}

function pointTimestamp(item) {
  return String(item?.timestamp || item?.last_timestamp || "");
}

function draw() {
  const canvas = document.getElementById("chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const points = visiblePoints();
  updateChartStats(points);
  if (!points.length) {
    ctx.fillStyle = "#667085";
    ctx.font = "14px -apple-system, Arial";
    ctx.fillText("暂无数据", 12, 28);
    return;
  }

  const pad = { l: 58, r: 18, t: 14, b: 38 };
  const w = Math.max(1, width - pad.l - pad.r);
  const h = Math.max(1, height - pad.t - pad.b);
  const x0 = pad.l;
  const y0 = pad.t + h;
  const vals = chartValues(points);
  if (!vals.length) {
    ctx.fillStyle = "#667085";
    ctx.font = "14px -apple-system, Arial";
    ctx.fillText("暂无有效a点", 12, 28);
    return;
  }
  const minV = Math.min(...vals, ...(cfg.thresholds || []));
  const maxV = Math.max(...vals, ...(cfg.thresholds || []));
  const margin = Math.max(1, (maxV - minV) * 0.12);
  const lo = minV - margin;
  const hi = maxV + margin;
  const span = Math.max(1, hi - lo);
  const x = i => x0 + (i / Math.max(1, points.length - 1)) * w;
  const y = v => pad.t + h - ((v - lo) / span) * h;
  const baseThreshold = (cfg.thresholds || []).includes(300) ? 300 : ((cfg.thresholds || [300])[0] || 300);

  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0, pad.t);
  ctx.lineTo(x0, y0);
  ctx.lineTo(x0 + w, y0);
  ctx.stroke();

  ctx.font = "12px -apple-system, Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {
    const v = lo + (span * (4 - i) / 4);
    const gy = pad.t + (i / 4) * h;
    ctx.beginPath();
    ctx.strokeStyle = "#e5e7eb";
    ctx.moveTo(x0, gy);
    ctx.lineTo(x0 + w, gy);
    ctx.stroke();
    ctx.fillStyle = "#475569";
    ctx.fillText(v.toFixed(2), x0 - 8, gy);
  }

  for (const t of cfg.thresholds || []) {
    const ty = y(t);
    ctx.beginPath();
    ctx.strokeStyle = "#dc2626";
    ctx.setLineDash([5, 5]);
    ctx.moveTo(x0, ty);
    ctx.lineTo(x0 + w, ty);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "#b91c1c";
    ctx.textAlign = "right";
    ctx.fillText(`阈值 ${t}`, x0 + w - 6, ty - 8);
  }

  if (chartKind === "ohlc") {
    const candleWidth = Math.max(3, Math.min(18, w / Math.max(1, points.length) * 0.62));
    points.forEach((item, i) => {
      const open = asNumber(item.open);
      const high = asNumber(item.high);
      const low = asNumber(item.low);
      const close = asNumber(item.close);
      if ([open, high, low, close].some(v => v === null)) return;
      const px = x(i);
      const over = high >= baseThreshold;
      if (over) {
        ctx.fillStyle = "rgba(220, 38, 38, 0.08)";
        ctx.fillRect(px - candleWidth / 2, pad.t, candleWidth, h);
      }
      const color = over ? "#dc2626" : (close >= open ? "#15803d" : "#2563eb");
      ctx.strokeStyle = color;
      ctx.fillStyle = close >= open ? "#ffffff" : color;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(px, y(high));
      ctx.lineTo(px, y(low));
      ctx.stroke();
      const top = Math.min(y(open), y(close));
      const bodyHeight = Math.max(2, Math.abs(y(close) - y(open)));
      ctx.fillRect(px - candleWidth / 2, top, candleWidth, bodyHeight);
      ctx.strokeRect(px - candleWidth / 2, top, candleWidth, bodyHeight);
    });
  } else {
    for (let i = 1; i < points.length; i++) {
      const prev = asNumber(points[i - 1].estimated_a);
      const curr = asNumber(points[i].estimated_a);
      if (prev === null || curr === null) continue;
      if (prev >= baseThreshold || curr >= baseThreshold) {
        const left = x(i - 1);
        const right = x(i);
        ctx.fillStyle = "rgba(220, 38, 38, 0.08)";
        ctx.fillRect(left, pad.t, Math.max(1, right - left), h);
      }
    }

    ctx.strokeStyle = "#2563eb";
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    points.forEach((item, i) => {
      const v = asNumber(item.estimated_a);
      if (v === null) return;
      const px = x(i);
      const py = y(v);
      if (!started) {
        ctx.moveTo(px, py);
        started = true;
      } else {
        ctx.lineTo(px, py);
      }
    });
    ctx.stroke();

    for (let i = 1; i < points.length; i++) {
      const prev = asNumber(points[i - 1].estimated_a);
      const curr = asNumber(points[i].estimated_a);
      if (prev === null || curr === null) continue;
      if (prev >= baseThreshold || curr >= baseThreshold) {
        ctx.beginPath();
        ctx.strokeStyle = "#dc2626";
        ctx.lineWidth = 3;
        ctx.moveTo(x(i - 1), y(prev));
        ctx.lineTo(x(i), y(curr));
        ctx.stroke();
      }
    }

    if (points.length <= 500) {
      points.forEach((item, i) => {
        const v = asNumber(item.estimated_a);
        if (v === null) return;
        ctx.beginPath();
        ctx.fillStyle = v >= baseThreshold ? "#dc2626" : "#2563eb";
        ctx.arc(x(i), y(v), 2.2, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    const last = points[points.length - 1];
    const lv = asNumber(last.estimated_a);
    if (lv !== null) {
      ctx.beginPath();
      ctx.fillStyle = "#ffffff";
      ctx.strokeStyle = lv >= baseThreshold ? "#b91c1c" : "#1d4ed8";
      ctx.lineWidth = 3;
      ctx.arc(x(points.length - 1), y(lv), 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  if (points.length > 1) {
    ctx.fillStyle = "#667085";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(pointTimestamp(points[0]).slice(11, 19), x0, y0 + 12);
    ctx.textAlign = "right";
    ctx.fillText(pointTimestamp(points[points.length - 1]).slice(11, 19), x0 + w, y0 + 12);
  }
}

async function refreshSeries() {
  const date = selectedSeriesDate();
  const range = selectedSeriesRange();
  const interval = selectedSeriesInterval();
  if (!date) return;
  const params = new URLSearchParams({ date, range, interval });
  const resp = await fetch(`/api/series?${params.toString()}`);
  const payload = await resp.json();
  if (!payload.ok) throw new Error(payload.error || "曲线取数失败");
  seriesPayload = payload;
  latest = payload.points || [];
  chartKind = payload.kind || "line";
  cfg.thresholds = payload.thresholds || cfg.thresholds || [];
  const rangeLabel = RANGE_LABELS[payload.range] || payload.range;
  const intervalLabel = INTERVAL_LABELS[payload.interval] || payload.interval;
  const kindLabel = payload.kind === "ohlc" ? "a值K线" : "a值折线";
  setText(
    "chartModeNote",
    `${payload.date} ${rangeLabel} ${intervalLabel} ${kindLabel}；点数 ${payload.count}，原始 ${payload.range_count}/${payload.raw_count}`
  );
  draw();
}

async function refreshData() {
  try {
    const resp = await fetch("/api/data");
    const payload = await resp.json();
    if (!payload.ok) throw new Error(payload.error || "取数失败");
    lastPayload = payload;
    cfg.thresholds = payload.thresholds || [];
    cfg.date = payload.config?.target_date || cfg.date;
    populateDateOptions(payload.available_dates || [], cfg.date);
    setText("topMeta", `日期 ${cfg.date}；刷新 ${cfg.refreshSec}s；阈值 ${cfg.thresholds.join(", ") || "-"}`);
    setText("latestA", payload.latest_a);
    setText("latestETF", payload.latest_etf_quote);
    setText("distance300", payload.distance_to_300?.text || "-");
    setText("quoteSkew", payload.quote_skew_seconds == null ? "-" : `${payload.quote_skew_seconds}s`);
    setText("calcTime", shortTime(payload.calculated_at));
    setText("latestNotify", notifyText(payload.latest_notification));
    setText("count", payload.count);
    setStatus(payload.status);
    renderComponentStrip(payload.components || []);
    renderComponents(payload.components || []);
    renderFormula(payload.formula || {});
    renderConfig(payload);
    applyReadOnlyMode(payload);
    setText("compTs", payload.component_timestamp || "-");
    setText("compSource", payload.component_source || "-");
    const err = payload.last_error || payload.status?.detail || "";
    const chartNotice = payload.chart_notice || "";
    document.getElementById("lastErr").textContent = [err && payload.status?.level !== "ok" ? err : "", chartNotice].filter(Boolean).join("；");
    await refreshSeries();
  } catch (error) {
    setText("statusPill", "取数失败");
    document.getElementById("statusPill").className = "status-pill status-danger";
    document.getElementById("lastErr").textContent = String(error);
  }
}

async function manualRecalc(notify) {
  const btn = document.getElementById(notify ? "recalcNotify" : "recalc");
  btn.disabled = true;
  setText("statusPill", notify ? "发送中" : "计算中");
  try {
    const resp = await fetch(notify ? "/api/recalc?notify=1" : "/api/recalc", { method: "POST" });
    const result = await resp.json();
    if (!result.ok) throw new Error(result.error || "操作失败");
    await refreshData();
    const notice = result.latest_notification ? notifyText(result.latest_notification) : "";
    document.getElementById("configMessage").className = "message ok";
    document.getElementById("configMessage").textContent = notify && notice ? `飞书测试完成：${notice}` : "手动计算完成";
  } catch (error) {
    document.getElementById("configMessage").className = "message error";
    document.getElementById("configMessage").textContent = String(error);
    await refreshData();
  } finally {
    btn.disabled = false;
  }
}

async function saveConfig() {
  const btn = document.getElementById("saveConfig");
  btn.disabled = true;
  const interest_overrides = {};
  for (const input of document.querySelectorAll(".interest-input")) {
    interest_overrides[input.dataset.code] = input.value.trim();
  }
  const payload = {
    target_date: document.getElementById("targetDate").value.trim(),
    thresholds: document.getElementById("thresholdsInput").value.trim(),
    interest_overrides
  };
  try {
    const resp = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const result = await resp.json();
    if (!result.ok) throw new Error(result.error || "保存失败");
    document.getElementById("configMessage").className = result.warning ? "message error" : "message ok";
    document.getElementById("configMessage").textContent = result.warning || result.message || "已保存";
    await refreshData();
  } catch (error) {
    document.getElementById("configMessage").className = "message error";
    document.getElementById("configMessage").textContent = String(error);
  } finally {
    btn.disabled = false;
  }
}

function refreshSeriesSafely() {
  refreshSeries().catch(error => {
    document.getElementById("lastErr").textContent = String(error);
  });
}

document.getElementById("refreshData").addEventListener("click", refreshData);
document.getElementById("recalc").addEventListener("click", () => manualRecalc(false));
document.getElementById("recalcNotify").addEventListener("click", () => manualRecalc(true));
document.getElementById("saveConfig").addEventListener("click", saveConfig);
document.getElementById("seriesRange").addEventListener("change", refreshSeriesSafely);
document.getElementById("seriesInterval").addEventListener("change", refreshSeriesSafely);
document.getElementById("seriesDate").addEventListener("change", refreshSeriesSafely);
window.addEventListener("resize", draw);
refreshData();
setInterval(refreshData, cfg.refreshSec * 1000);
</script>
</body>
</html>
"""
    return html.replace("__BOOTSTRAP_JSON__", json.dumps(bootstrap, ensure_ascii=False))


def build_html(state: DashboardState, points: list[dict]) -> str:
    return build_dashboard_html(state, points)

    auto_hint = (
        "已开启自动计算（每%ss）" % state.interval_seconds if state.auto_run else "手动刷新（不自动计算）"
    )
    latest = latest_value(points)
    latest_a = "-" if not latest else f"{latest['estimated_a']:.2f}"
    latest_etf = "-" if not latest else f"{latest['etf_quote']:.3f}" if latest.get("etf_quote") is not None else "-"
    thresholds = state.thresholds
    threshold_lines = []
    for item in thresholds:
        threshold_lines.append(float(item))
    threshold_line_text = ", ".join(f"{x}" for x in threshold_lines) or "未设置"
    return f"""
<!DOCTYPE html>
<html lang=\"zh-CN\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>511130 a值实时曲线</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; margin: 20px; color: #111827; background: #f6f7fb; }}
        .card {{ max-width: 1100px; margin: 0 auto; background: #ffffff; border: 1px solid #dbe0eb; border-radius: 12px; padding: 16px; box-shadow: 0 6px 20px rgba(17,24,39,.06); }}
        .header {{ display: flex; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
        .title {{ margin: 0; font-size: 22px; }}
        .small {{ color: #6b7280; font-size: 13px; }}
        .grid {{ display: grid; grid-template-columns: repeat(5, minmax(120px, 1fr)); gap: 10px; margin: 14px 0; }}
        .cell {{ background: #f3f4f6; border: 1px solid #e5e7eb; border-radius: 8px; padding: 10px; }}
        .label {{ color: #6b7280; font-size: 12px; }}
        .value {{ font-weight: 600; font-size: 20px; margin-top: 4px; }}
        .toolbar {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }}
        .panel {{ margin-top: 14px; }}
        .panel-title {{ color: #111827; font-size: 16px; font-weight: 600; margin-bottom: 8px; }}
        button {{ border: none; background: #2563eb; color: white; padding: 8px 12px; border-radius: 8px; cursor: pointer; }}
        button.secondary {{ background: #334155; }}
        button:disabled {{ opacity: .5; cursor: default; }}
        canvas {{ width: 100%; height: 420px; border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        th, td {{ border-bottom: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
        th {{ color: #475569; font-weight: 600; background: #f8fafc; }}
        td {{ color: #1f2937; }}
        .footer {{ margin-top: 10px; color: #6b7280; font-size: 12px; }}
    </style>
</head>
<body>
<div class=\"card\">
  <div class=\"header\">
    <div>
      <h1 class=\"title\">511130 a值实时曲线</h1>
      <div class=\"small\">日期: {state.date}，{auto_hint}；阈值: {threshold_line_text}</div>
    </div>
    <div class=\"small\">刷新周期：{state.interval_seconds}s</div>
  </div>
  <div class=\"grid\">
    <div class=\"cell\"><div class=\"label\">最新a值</div><div class=\"value\" id=\"latestA\">{latest_a}</div></div>
    <div class=\"cell\"><div class=\"label\">最新511130报价</div><div class=\"value\" id=\"latestETF\">{latest_etf}</div></div>
    <div class=\"cell\"><div class=\"label\">点位数</div><div class=\"value\" id=\"count\">{len(points)}</div></div>
    <div class=\"cell\"><div class=\"label\">状态</div><div class=\"value\" id=\"status\">{state.last_run_message or '等待采集'}</div></div>
    <div class=\"cell\"><div class=\"label\">最新飞书</div><div class=\"value\" id=\"latestNotify\">-</div></div>
  </div>
  <div class=\"toolbar\">
    <button id=\"refreshData\">刷新页面</button>
    <button id=\"recalc\" class=\"secondary\">手动算一次（不发飞书）</button>
    <button id=\"recalcNotify\" class=\"secondary\">发送一次飞书测试（含当前值）</button>
    <span class=\"small\">说明：图表纵轴是a值（单位：元），横轴按时间排序</span>
  </div>
  <canvas id=\"chart\" height=\"420\"></canvas>
  <div class=\"panel\">
    <div class=\"panel-title\">成分券（最新采样）</div>
    <div class=\"small\">时间：<span id=\"compTs\">-</span>，来源：<span id=\"compSource\">-</span></div>
    <table>
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>实时净价</th>
          <th>逐券利息</th>
          <th>利息来源</th>
          <th>价值</th>
        </tr>
      </thead>
      <tbody id=\"componentsBody\">
        <tr><td colspan=\"6\" style=\"color:#6b7280;\">暂无成分券数据</td></tr>
      </tbody>
    </table>
  </div>
  <div class=\"footer\" id=\"lastErr\">{state.last_error}</div>
</div>

<script>
        const cfg = {{
    refreshSec: {state.interval_seconds},
    date: "{state.date}",
    thresholds: {json.dumps(threshold_lines)},
}};

let latest = [];
function renderComponents(components) {{
  const body = document.getElementById("componentsBody");
  if (!body) {{
    return;
  }}
  if (!components || components.length === 0) {{
    body.innerHTML = '<tr><td colspan="6" style="color:#6b7280;">暂无成分券数据</td></tr>';
    return;
  }}
  body.innerHTML = "";
  for (const item of components) {{
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${{item.code || "-"}}</td>
      <td>${{item.name || "-"}}</td>
      <td>${{item.price || "-"}}</td>
      <td>${{item.interest || "-"}}</td>
      <td>${{item.interest_source || "-"}}</td>
      <td>${{item.value || "-"}}</td>
    `;
    body.appendChild(tr);
  }}
}}

function draw() {{
  const canvas = document.getElementById("chart");
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  canvas.width = Math.floor(width * dpr);
  canvas.height = Math.floor(height * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  if (!latest.length) {{
    ctx.fillStyle = "#6b7280";
    ctx.font = "14px -apple-system, Arial";
    ctx.fillText("暂无数据", 10, 28);
    return;
  }}

  const pad = {{l: 55, r: 16, t: 12, b: 38}};
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const x0 = pad.l;
  const y0 = pad.t + h;

  const vals = latest.map(x => x.estimated_a);
  const minV = Math.min(...vals, ...(cfg.thresholds || []));
  const maxV = Math.max(...vals, ...(cfg.thresholds || []));
  const margin = Math.max(1, (maxV - minV) * 0.1);
  const lo = minV - margin;
  const hi = maxV + margin;
  const span = Math.max(1, hi - lo);

  const x = (i) => x0 + (i / Math.max(1, latest.length - 1)) * w;
  const y = (v) => pad.t + h - ((v - lo) / span) * h;

  ctx.strokeStyle = "#e5e7eb";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x0, pad.t);
  ctx.lineTo(x0, y0);
  ctx.lineTo(x0 + w, y0);
  ctx.stroke();

  ctx.fillStyle = "#111827";
  ctx.font = "12px -apple-system, Arial";
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  for (let i = 0; i <= 4; i++) {{
    const v = lo + (span * (4 - i) / 4);
    const gy = pad.t + (i / 4) * h;
    ctx.beginPath();
    ctx.strokeStyle = "#e5e7eb";
    ctx.moveTo(x0, gy);
    ctx.lineTo(x0 + w, gy);
    ctx.stroke();
    ctx.fillText(v.toFixed(2), x0 - 8, gy);
  }}

  if (cfg.thresholds && cfg.thresholds.length) {{
    for (const t of cfg.thresholds) {{
      const ty = y(t);
      ctx.beginPath();
      ctx.strokeStyle = "#dc2626";
      ctx.setLineDash([4, 4]);
      ctx.moveTo(x0, ty);
      ctx.lineTo(x0 + w, ty);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#b91c1c";
      ctx.fillText("阈值 " + t, x0 + w - 6, ty - 6);
    }}
  }}

  ctx.strokeStyle = "#2563eb";
  ctx.lineWidth = 2;
  ctx.beginPath();
  latest.forEach((item, i) => {{
    const px = x(i);
    const py = y(item.estimated_a);
    if (i === 0) {{
      ctx.moveTo(px, py);
    }} else {{
      ctx.lineTo(px, py);
    }}
  }});
  ctx.stroke();

  latest.forEach((item, i) => {{
    const px = x(i);
    const py = y(item.estimated_a);
    ctx.beginPath();
    ctx.fillStyle = "#2563eb";
    ctx.arc(px, py, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }});

  const n = latest.length;
  if (n > 1) {{
    const left = latest[0].timestamp.slice(11, 16);
    const right = latest[n - 1].timestamp.slice(11, 16);
    ctx.fillStyle = "#6b7280";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(left, x0, y0 + 12);
    ctx.textAlign = "right";
    ctx.fillText(right, x0 + w, y0 + 12);
  }}
}}

async function refreshData() {{
  try {{
    const resp = await fetch("/api/data");
    const payload = await resp.json();
    latest = payload.points || [];
    document.getElementById("latestA").textContent = payload.latest_a;
    document.getElementById("latestETF").textContent = payload.latest_etf_quote;
    document.getElementById("count").textContent = latest.length;
    document.getElementById("status").textContent = payload.status || "已加载";
    document.getElementById("compTs").textContent = payload.component_timestamp || "-";
    document.getElementById("compSource").textContent = payload.component_source || "-";
    renderComponents(payload.components || []);
    const notice = payload.latest_notification || {{}};
    const noticeText = notice.status ? `${{notice.status}} ${{notice.notification_elapsed_ms || "-"}}ms` : "-";
    document.getElementById("latestNotify").textContent = noticeText;
    const err = payload.last_error || "";
    document.getElementById("lastErr").textContent = err ? ("错误：" + err) : "";
    cfg.thresholds = payload.thresholds || [];
    draw();
  }} catch (error) {{
    document.getElementById("status").textContent = "取数失败";
    document.getElementById("lastErr").textContent = String(error);
  }}
}}

async function manualRecalc() {{
  const btn = document.getElementById("recalc");
  btn.disabled = true;
  document.getElementById("status").textContent = "正在计算...";
  try {{
    const resp = await fetch("/api/recalc", {{ method: "POST" }});
    const result = await resp.json();
    document.getElementById("status").textContent = result.ok ? "已触发" : "失败";
    if (result.ok) {{
      await refreshData();
    }} else {{
      document.getElementById("lastErr").textContent = result.error || "触发失败";
    }}
  }} catch (error) {{
    document.getElementById("status").textContent = "触发失败";
    document.getElementById("lastErr").textContent = String(error);
  }} finally {{
    btn.disabled = false;
  }}
}}

async function manualRecalcNotify() {{
  const btn = document.getElementById("recalcNotify");
  btn.disabled = true;
  document.getElementById("status").textContent = "正在发送飞书测试...";
  try {{
    const resp = await fetch("/api/recalc?notify=1", {{ method: "POST" }});
    const result = await resp.json();
    document.getElementById("status").textContent = result.ok ? "已发送飞书测试" : "发送失败";
    if (result.ok) {{
      await refreshData();
    }} else {{
      document.getElementById("lastErr").textContent = result.error || "发送失败";
    }}
  }} catch (error) {{
    document.getElementById("status").textContent = "发送失败";
    document.getElementById("lastErr").textContent = String(error);
  }} finally {{
    btn.disabled = false;
  }}
}}

document.getElementById("refreshData").addEventListener("click", refreshData);
document.getElementById("recalc").addEventListener("click", manualRecalc);
document.getElementById("recalcNotify").addEventListener("click", manualRecalcNotify);
refreshData();
setInterval(refreshData, cfg.refreshSec * 1000);
</script>
</body>
</html>
""".replace("\\n", "\n")


def run_once_now(
    config: dict,
    notify: bool = False,
    notify_no_alert: bool = True,
    context: monitor.CalculationContext | None = None,
) -> tuple[bool, str]:
    try:
        if context is None:
            monitor.mode_once(config, notify=notify, notify_no_alert=notify_no_alert)
        else:
            monitor.mode_once_with_context(config, context, notify=notify, notify_no_alert=notify_no_alert)
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def make_handler(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是 JSON 对象")
            return payload

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                points = load_points(state.date, max_points=state.max_points, allowed_sources=state.allowed_price_sources)
                html = build_html(state, points)
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
            elif parsed.path == "/health":
                self._json(build_health_payload(state))
            elif parsed.path == "/api/data":
                self._json(build_data_payload(state, config))
            elif parsed.path == "/api/dates":
                self._json({"ok": True, "current_date": state.date, "dates": available_dates()})
            elif parsed.path == "/api/series":
                try:
                    self._json(build_series_payload(state, parse_qs(parsed.query)))
                except Exception as exc:  # noqa: BLE001
                    self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            else:
                self.send_error(404, "Not Found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if state.public_readonly:
                self._json({"ok": False, "message": "", "warning": "", "error": "公网只读模式已关闭写操作"})
                return
            if parsed.path == "/api/recalc":
                query = parse_qs(parsed.query)
                notify = query.get("notify", ["0"])[0] in {"1", "true", "True", "yes", "on"}
                ok, msg = run_once_now(
                    config,
                    notify=notify,
                    notify_no_alert=True,
                    context=context_ref.get("value"),
                )
                if ok:
                    state.last_run_message = f"手动计算成功: {datetime.now(TZ).strftime('%H:%M:%S')}"
                    state.last_error = ""
                else:
                    state.last_run_message = "手动计算失败"
                    state.last_error = msg
                self._json(
                    {
                        "ok": ok,
                        "message": msg,
                        "error": msg if not ok else "",
                        "latest_notification": load_latest_notification(state.date) or {},
                    }
                )
                return
            if parsed.path == "/api/config":
                try:
                    payload = self._read_json()
                    ok, msg, warning = save_config_update(payload, state, config, context_ref)
                    self._json({"ok": ok, "message": msg, "warning": warning, "error": ""})
                except Exception as exc:  # noqa: BLE001
                    state.last_run_message = "配置保存失败"
                    state.last_error = f"{type(exc).__name__}: {exc}"
                    self._json({"ok": False, "message": "", "warning": "", "error": state.last_error})
                return
            else:
                self.send_error(404, "Not Found")

    return Handler


def run_auto_mode(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
    stop_event: threading.Event,
) -> None:
    first_pass = True
    while not stop_event.is_set():
        notify_no_alert = state.auto_run_notify and first_pass
        ok, msg = run_once_now(
            config,
            notify=True,
            notify_no_alert=notify_no_alert,
            context=context_ref.get("value"),
        )
        if ok:
            state.last_error = ""
            if state.auto_run_notify and first_pass:
                state.last_run_message = "自动计算成功（已发送一次运行检查；触发阈值会继续预警）"
            else:
                state.last_run_message = f"自动计算成功: {datetime.now(TZ).strftime('%H:%M:%S')}"
        else:
            state.last_error = msg
            state.last_run_message = "自动计算失败"
        first_pass = False
        stop_event.wait(state.interval_seconds)


def local_ip_hint() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Local dashboard for 511130 a-value history")
    default_port = int(os.environ.get("PORT", "8787"))
    default_host = "0.0.0.0" if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PORT") else "127.0.0.1"
    parser.add_argument("--port", type=int, default=default_port)
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--date", default=None, help="Target trading date, e.g. 20260615")
    parser.add_argument("--max-points", type=int, default=300, help="Max points in chart")
    parser.add_argument("--interval", type=int, default=1, help="Refresh interval for browser and auto-run (seconds, minimum 1)")
    parser.add_argument("--auto-run", action="store_true", help="Auto calc once in interval")
    parser.add_argument(
        "--auto-run-notify",
        action="store_true",
        help="Send one notify message on the first auto-run",
    )
    parser.add_argument("--open", action="store_true", help="Open browser automatically")
    args = parser.parse_args()

    config = monitor.load_config()
    if args.date:
        config["target_date"] = args.date
    target_date = config.get("target_date")
    if not isinstance(target_date, str):
        raise SystemExit("config target_date 无效")

    state = DashboardState(
        date=target_date,
        interval_seconds=max(1, args.interval),
        max_points=max(10, args.max_points),
        thresholds=[],
        auto_run=args.auto_run,
        auto_run_notify=args.auto_run_notify,
        allowed_price_sources=list(config.get("strict_realtime_price_sources", ["realtime_eastmoney"]))
        if config.get("require_realtime_snapshot")
        else [],
        max_stale_seconds=int(config.get("realtime_max_stale_seconds", 30)),
        public_readonly=env_flag("A_MONITOR_PUBLIC_READONLY", False),
    )
    # 修复阈值读取
    raw_thresholds = config.get("thresholds", [])
    cleaned: list[Decimal] = []
    for item in raw_thresholds:
        try:
            cleaned.append(Decimal(str(item)))
        except Exception:  # noqa: BLE001
            pass
    state.thresholds = cleaned
    context_ref: dict[str, monitor.CalculationContext | None] = {"value": None}
    if state.auto_run:
        try:
            context_ref["value"] = monitor.prepare_calculation_context(target_date, config)
            state.last_run_message = "已预加载PCF和逐券利息"
        except Exception as exc:  # noqa: BLE001
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.last_run_message = "预加载失败"

    stop_event = threading.Event()
    if state.auto_run:
        t = threading.Thread(target=run_auto_mode, args=(state, config, context_ref, stop_event), daemon=True)
        t.start()

    handler = make_handler(state, config, context_ref)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Dashboard is running at http://{args.host}:{args.port}/?date={target_date}")
    if args.host == "0.0.0.0":
        ip = local_ip_hint()
        if ip:
            print(f"Team LAN URL: http://{ip}:{args.port}/")
    if args.open:
        webbrowser.open(f"http://{args.host}:{args.port}/")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        stop_event.set()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
