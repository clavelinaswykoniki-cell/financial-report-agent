#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
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
HISTORY_SEED_RUNS_DIR = BASE_DIR / "history_seed" / "runs"
ACTIVE_RUNS_DIR_AT_IMPORT = monitor.RUNS_DIR
HISTORICAL_CHART_PRICE_SOURCES = [
    "daily_report_1m",
    "historical_eastmoney_1m",
    "realtime_sina_snapshot",
    "realtime_eastmoney",
]
QUOTE_BOARD_CACHE: dict[str, Any] = {
    "key": "",
    "expires_at": 0.0,
    "boards": {},
    "error": "",
}
SECURITY_INTRADAY_CACHE: dict[str, Any] = {
    "key": "",
    "expires_at": 0.0,
    "series": {},
    "error": "",
}


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
    auto_error_count: int = 0
    last_error_notify_at: float = 0.0
    last_error_notify_key: str = ""
    pcf_retry_at: float = 0.0
    latest_result: dict[str, Any] | None = None
    last_auto_tick_at: float = 0.0

    @property
    def threshold_text(self) -> str:
        if not self.thresholds:
            return ""
        return ", ".join(f"{t}" for t in self.thresholds)


def source_allowed(source: str, allowed_sources: list[str]) -> bool:
    return not allowed_sources or source in allowed_sources


def snapshot_allowed(snapshot: dict | None, allowed_sources: list[str], *, require_strict: bool = True) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if not source_allowed(str(snapshot.get("price_source", "")), allowed_sources):
        return False
    return not require_strict or not allowed_sources or snapshot.get("strict_realtime") is True


def snapshot_to_point(
    snapshot: dict | None,
    allowed_sources: list[str] | None = None,
    *,
    require_strict: bool = True,
) -> dict | None:
    allowed_sources = allowed_sources or []
    if not snapshot_allowed(snapshot, allowed_sources, require_strict=require_strict):
        return None
    if snapshot is None:
        return None
    a = _to_float(snapshot.get("estimated_a", ""))
    etf = _to_float(snapshot.get("etf_quote", ""))
    if a is None:
        return None
    return {
        "timestamp": str(snapshot.get("timestamp") or ""),
        "estimated_a": a,
        "etf_quote": etf,
        "price_source": str(snapshot.get("price_source", "")),
        "strict_realtime": snapshot.get("strict_realtime") is True,
        "quote_skew_seconds": _to_float(snapshot.get("quote_skew_seconds", "")),
        "calculation_elapsed_ms": _to_float(snapshot.get("calculation_elapsed_ms", "")),
        "basket_value": _to_float(snapshot.get("basket_value", "")),
        "estimated_cash": _to_float(snapshot.get("estimated_cash", "")),
    }


def warn_read_failure(label: str, path: Path, exc: Exception) -> None:
    print(f"WARN: {label}读取失败，忽略该文件: {path}: {exc}", file=sys.stderr)


def run_day_dirs(date: str) -> list[Path]:
    dirs = [monitor.RUNS_DIR / date]
    if monitor.RUNS_DIR == ACTIVE_RUNS_DIR_AT_IMPORT and HISTORY_SEED_RUNS_DIR != monitor.RUNS_DIR:
        dirs.append(HISTORY_SEED_RUNS_DIR / date)
    return dirs


def load_jsonl_points(
    jsonl_path: Path,
    allowed_sources: list[str],
    *,
    require_strict: bool,
) -> list[dict]:
    points = []
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                point = snapshot_to_point(row, allowed_sources, require_strict=require_strict)
                if point is not None:
                    points.append(point)
    except Exception as exc:  # noqa: BLE001
        warn_read_failure("a值JSONL", jsonl_path, exc)
        return []
    return points


def load_csv_points(
    csv_path: Path,
    allowed_sources: list[str],
    *,
    require_strict: bool,
) -> list[dict]:
    try:
        with csv_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as exc:  # noqa: BLE001
        warn_read_failure("a值CSV", csv_path, exc)
        return []
    points = []
    for row in rows:
        ts = row.get("timestamp", "")
        price_source = str(row.get("price_source", ""))
        if not source_allowed(str(row.get("price_source", "")), allowed_sources):
            continue
        if require_strict and allowed_sources and str(row.get("strict_realtime", "")).lower() != "true":
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
    return points


def load_day_dir_points(
    day_dir: Path,
    allowed_sources: list[str],
    *,
    require_strict: bool,
) -> list[dict]:
    jsonl_path = day_dir / "a_values.jsonl"
    if jsonl_path.exists():
        points = load_jsonl_points(jsonl_path, allowed_sources, require_strict=require_strict)
        if points:
            return points
    csv_path = day_dir / "a_values.csv"
    if csv_path.exists():
        return load_csv_points(csv_path, allowed_sources, require_strict=require_strict)
    return []


def load_points(
    date: str,
    max_points: int = 500,
    allowed_sources: list[str] | None = None,
    *,
    require_strict: bool = True,
) -> list[dict]:
    allowed_sources = allowed_sources or []
    merged: dict[str, dict] = {}
    unnamed_index = 0
    for day_dir in reversed(run_day_dirs(date)):
        points = load_day_dir_points(day_dir, allowed_sources, require_strict=require_strict)
        for point in points:
            timestamp = str(point.get("timestamp") or "")
            key = timestamp
            if not key:
                unnamed_index += 1
                key = f"__missing_timestamp_{unnamed_index}"
            merged[key] = point
    points = list(merged.values())
    points.sort(key=lambda x: x["timestamp"])
    if max_points and len(points) > max_points:
        points = points[-max_points:]
    return points


def available_dates() -> list[str]:
    dates: list[str] = []
    runs_dirs = [monitor.RUNS_DIR]
    if monitor.RUNS_DIR == ACTIVE_RUNS_DIR_AT_IMPORT and HISTORY_SEED_RUNS_DIR != monitor.RUNS_DIR:
        runs_dirs.append(HISTORY_SEED_RUNS_DIR)
    for runs_dir in runs_dirs:
        if not runs_dir.exists() or not runs_dir.is_dir():
            continue
        try:
            paths = list(runs_dir.iterdir())
        except Exception as exc:  # noqa: BLE001
            warn_read_failure("runs目录", runs_dir, exc)
            continue
        for path in paths:
            try:
                if not path.is_dir():
                    continue
                if len(path.name) != 8 or not path.name.isdigit():
                    continue
                if (path / "a_values.jsonl").exists() or (path / "a_values.csv").exists():
                    dates.append(path.name)
            except Exception as exc:  # noqa: BLE001
                warn_read_failure("runs日期目录", path, exc)
    return sorted(set(dates))


def load_latest_snapshot(date: str, allowed_sources: list[str] | None = None) -> dict | None:
    allowed_sources = allowed_sources or []
    day_dir = monitor.RUNS_DIR / date
    jsonl_path = day_dir / "a_values.jsonl"
    if not jsonl_path.exists():
        return None
    try:
        with jsonl_path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception as exc:  # noqa: BLE001
        warn_read_failure("最新a值JSONL", jsonl_path, exc)
        return None
    if not lines:
        return None
    for line in reversed(lines):
        try:
            snapshot = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if snapshot_allowed(snapshot, allowed_sources):
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
        code = str(row.get("code") or "").strip()
        interest_source = str(row.get("interest_source", ""))
        parsed.append(
            {
                "code": code,
                "name": str(row.get("name") or "").strip(),
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


def parse_latest_security_quotes(snapshot: dict | None) -> dict[str, dict[str, str]]:
    if not isinstance(snapshot, dict):
        return {}
    parsed: dict[str, dict[str, str]] = {}
    quotes = snapshot.get("quotes")
    if isinstance(quotes, list):
        for row in quotes:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            parsed[code] = {
                "code": code,
                "name": str(row.get("name") or "").strip(),
                "price": str(row.get("price") or "").strip(),
                "quote_time": str(row.get("quote_time") or "").strip(),
                "source": str(row.get("source") or snapshot.get("price_source", "")).strip(),
            }
    if monitor.ETF_CODE not in parsed and snapshot.get("etf_quote") not in {None, ""}:
        parsed[monitor.ETF_CODE] = {
            "code": monitor.ETF_CODE,
            "name": "511130",
            "price": str(snapshot.get("etf_quote") or "").strip(),
            "quote_time": str((snapshot.get("quote_times") or {}).get(monitor.ETF_CODE, snapshot.get("timestamp", "")))
            if isinstance(snapshot.get("quote_times"), dict)
            else str(snapshot.get("timestamp", "")),
            "source": str(snapshot.get("price_source", "")).strip(),
        }
    for row in parse_latest_components(snapshot):
        code = str(row.get("code") or "").strip()
        if code and code not in parsed:
            parsed[code] = {
                "code": code,
                "name": str(row.get("name") or "").strip(),
                "price": str(row.get("price") or "").strip(),
                "quote_time": str(row.get("quote_time") or "").strip(),
                "source": str(snapshot.get("price_source", "")).strip(),
            }
    return parsed


def security_codes_for_dashboard(snapshot: dict | None, config: dict) -> list[str]:
    codes = [str(config.get("fund_code", monitor.ETF_CODE) or monitor.ETF_CODE)]
    codes.extend(str(code).strip() for code in config.get("expected_component_codes", []) if str(code).strip())
    codes.extend(row.get("code", "") for row in parse_latest_components(snapshot))
    codes.extend(parse_latest_security_quotes(snapshot).keys())
    return list(dict.fromkeys(code for code in codes if code))


def load_snapshot_rows(
    date: str,
    allowed_sources: list[str] | None = None,
    *,
    require_strict: bool = False,
) -> list[dict]:
    allowed_sources = allowed_sources or []
    merged: dict[str, dict] = {}
    unnamed_index = 0
    for day_dir in reversed(run_day_dirs(date)):
        jsonl_path = day_dir / "a_values.jsonl"
        if not jsonl_path.exists():
            continue
        try:
            with jsonl_path.open("r", encoding="utf-8") as f:
                rows = [line.strip() for line in f if line.strip()]
        except Exception as exc:  # noqa: BLE001
            warn_read_failure("行情快照JSONL", jsonl_path, exc)
            continue
        for line in rows:
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if not snapshot_allowed(row, allowed_sources, require_strict=require_strict):
                continue
            timestamp = str(row.get("timestamp") or "")
            key = timestamp
            if not key:
                unnamed_index += 1
                key = f"__missing_timestamp_{unnamed_index}"
            merged[key] = row
    return [merged[key] for key in sorted(merged)]


def load_security_series(
    date: str,
    codes: list[str],
    allowed_sources: list[str] | None = None,
    *,
    max_points: int = 180,
) -> dict[str, list[dict[str, Any]]]:
    series: dict[str, list[dict[str, Any]]] = {code: [] for code in codes}
    rows = load_snapshot_rows(date, allowed_sources, require_strict=False)
    for row in rows:
        timestamp = str(row.get("timestamp") or "")
        quotes = parse_latest_security_quotes(row)
        for code in codes:
            quote = quotes.get(code)
            if not quote:
                continue
            price = _to_float(quote.get("price", ""))
            if price is None:
                continue
            series.setdefault(code, []).append({"timestamp": timestamp, "price": price})
    if max_points:
        for code, points in list(series.items()):
            series[code] = points[-max_points:]
    return series


def fetch_intraday_security_series(state: DashboardState, config: dict, codes: list[str]) -> tuple[dict[str, list[dict]], str]:
    key = f"{state.date}|{','.join(codes)}"
    ttl = max(10.0, float(config.get("security_intraday_cache_seconds", 30)))
    now = time.time()
    if SECURITY_INTRADAY_CACHE.get("key") == key and float(SECURITY_INTRADAY_CACHE.get("expires_at", 0.0)) > now:
        return dict(SECURITY_INTRADAY_CACHE.get("series") or {}), str(SECURITY_INTRADAY_CACHE.get("error") or "")
    date_iso = f"{state.date[:4]}-{state.date[4:6]}-{state.date[6:]}"
    try:
        series: dict[str, list[dict]] = {}
        fallback_codes: list[str] = []
        timeout_seconds = float(config.get("security_intraday_request_timeout_seconds", 2))
        for code in codes:
            try:
                rows = monitor.fetch_eastmoney_1m(code, timeout=timeout_seconds, attempts=1)
            except Exception:  # noqa: BLE001
                rows = monitor.fetch_sina_kline(code, "1")
                fallback_codes.append(code)
            points = [
                {"timestamp": ts, "price": float(price)}
                for ts, price in sorted(rows.items())
                if ts.startswith(date_iso)
            ]
            series[code] = points[-260:]
        error = ""
        if fallback_codes:
            error = "分时底图部分使用新浪1分钟兜底: " + ",".join(fallback_codes)
        SECURITY_INTRADAY_CACHE.update({"key": key, "expires_at": now + ttl, "series": series, "error": error})
        return series, error
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        SECURITY_INTRADAY_CACHE.update({"key": key, "expires_at": now + ttl, "series": {}, "error": error})
        return {}, error


def merge_security_series(base_points: list[dict], live_points: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    missing_index = 0
    for point in [*base_points, *live_points]:
        timestamp = str(point.get("timestamp") or "")
        key = timestamp
        if not key:
            missing_index += 1
            key = f"__missing_{missing_index}"
        merged[key] = point
    return [merged[key] for key in sorted(merged)][-300:]


def quote_boards_for_state(state: DashboardState, config: dict, codes: list[str]) -> tuple[dict[str, dict], str]:
    today = datetime.now(TZ).strftime("%Y%m%d")
    if state.date != today:
        return {}, "非今日目标日期，不请求实时五档盘口"
    key = f"{state.date}|{','.join(codes)}"
    ttl = max(1.0, float(config.get("quote_board_cache_seconds", 2)))
    now = time.time()
    if QUOTE_BOARD_CACHE.get("key") == key and float(QUOTE_BOARD_CACHE.get("expires_at", 0.0)) > now:
        return dict(QUOTE_BOARD_CACHE.get("boards") or {}), str(QUOTE_BOARD_CACHE.get("error") or "")
    try:
        boards = monitor.fetch_sina_quote_boards(state.date, codes, config)
        QUOTE_BOARD_CACHE.update({"key": key, "expires_at": now + ttl, "boards": boards, "error": ""})
        return boards, ""
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        QUOTE_BOARD_CACHE.update({"key": key, "expires_at": now + ttl, "boards": {}, "error": error})
        return {}, error


def build_quote_cards(state: DashboardState, config: dict, latest_snapshot: dict | None) -> tuple[list[dict[str, Any]], str]:
    codes = security_codes_for_dashboard(latest_snapshot, config)
    strict_quotes = parse_latest_security_quotes(latest_snapshot)
    live_series_by_code = load_security_series(
        state.date,
        codes,
        list(dict.fromkeys([*state.allowed_price_sources, *HISTORICAL_CHART_PRICE_SOURCES])),
        max_points=180,
    )
    intraday_series_by_code, intraday_error = fetch_intraday_security_series(state, config, codes)
    quote_boards, board_error = quote_boards_for_state(state, config, codes)
    cards = []
    for code in codes:
        strict = strict_quotes.get(code, {})
        board = quote_boards.get(code, {})
        series = merge_security_series(intraday_series_by_code.get(code, []), live_series_by_code.get(code, []))
        cards.append(
            {
                "code": code,
                "name": strict.get("name") or board.get("name") or code,
                "price": strict.get("price") or board.get("last") or "-",
                "price_source": strict.get("source") or "-",
                "quote_time": strict.get("quote_time") or board.get("quote_time") or "-",
                "open": board.get("open", "-"),
                "previous_close": board.get("previous_close", "-"),
                "change": board.get("change", "-"),
                "pct_change": board.get("pct_change", "-"),
                "volume": board.get("volume", "-"),
                "amount": board.get("amount", "-"),
                "orderbook_source": board.get("source", ""),
                "orderbook_time": board.get("quote_time", "-"),
                "orderbook_valid_for_target_date": board.get("valid_for_target_date", False),
                "bids": board.get("bids", []),
                "asks": board.get("asks", []),
                "series": series,
            }
        )
    notice = "；".join(part for part in [board_error, intraday_error] if part)
    return cards, notice


def load_latest_alert(date: str) -> dict | None:
    alert_path = monitor.RUNS_DIR / date / "alerts.jsonl"
    if not alert_path.exists():
        return None
    try:
        with alert_path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception as exc:  # noqa: BLE001
        warn_read_failure("预警事件JSONL", alert_path, exc)
        return None
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
    try:
        with notification_path.open("r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception as exc:  # noqa: BLE001
        warn_read_failure("通知事件JSONL", notification_path, exc)
        return None
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
    if "same_day_cache" in source:
        return "同日缓存利息"
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


def snapshot_timestamp_key(snapshot: dict | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    return str(snapshot.get("timestamp", ""))


def memory_snapshot_for_date(state: DashboardState) -> dict | None:
    snapshot = state.latest_result
    if not isinstance(snapshot, dict):
        return None
    if str(snapshot.get("date", state.date)) != state.date:
        return None
    if not snapshot_allowed(snapshot, state.allowed_price_sources):
        return None
    return snapshot


def merge_memory_point(points: list[dict], state: DashboardState) -> list[dict]:
    memory_point = snapshot_to_point(memory_snapshot_for_date(state), state.allowed_price_sources)
    if memory_point is None:
        return points
    merged = [point for point in points if point.get("timestamp") != memory_point.get("timestamp")]
    merged.append(memory_point)
    merged.sort(key=lambda x: x.get("timestamp", ""))
    if state.max_points and len(merged) > state.max_points:
        merged = merged[-state.max_points:]
    return merged


def effective_latest_snapshot(state: DashboardState) -> dict | None:
    disk_snapshot = load_latest_snapshot(state.date, allowed_sources=state.allowed_price_sources)
    memory_snapshot = memory_snapshot_for_date(state)
    if memory_snapshot is None:
        return disk_snapshot
    if disk_snapshot is None:
        return memory_snapshot
    if snapshot_timestamp_key(memory_snapshot) >= snapshot_timestamp_key(disk_snapshot):
        return memory_snapshot
    return disk_snapshot


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


def data_error_only(error: str) -> str:
    return error if data_blocking_error(error) else ""


def decimal_from_payload(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001
        return None
    if result.is_nan():
        return None
    return result


def first_alert_threshold(thresholds: list[Decimal]) -> Decimal:
    normalized = sorted({abs(threshold) for threshold in thresholds if abs(threshold) > 0})
    for threshold in normalized:
        if threshold == Decimal("300"):
            return threshold
    return Decimal("300")


def market_pause_status(run_message: str) -> dict[str, str] | None:
    message = str(run_message or "").strip()
    if not message:
        return None
    if message.startswith(("休市暂停", "盘外暂停")):
        return {"code": "market_closed", "label": "休市中", "level": "muted", "detail": message}
    return None


def classify_status(
    *,
    latest: dict | None,
    latest_snapshot: dict | None,
    snapshot_fresh: bool,
    last_error: str,
    latest_notification: dict | None,
    thresholds: list[Decimal],
    run_message: str = "",
) -> dict[str, str]:
    if last_error and data_blocking_error(last_error):
        if "PCF" in last_error or "清单" in last_error:
            return {"code": "pcf_not_ready", "label": "清单未就绪", "level": "warning", "detail": last_error}
        if "利息" in last_error:
            return {"code": "missing_interest", "label": "缺利息", "level": "danger", "detail": last_error}
        if "行情时间差" in last_error or "非严格" in last_error or "strict_realtime" in last_error:
            return {"code": "quote_unsynced", "label": "行情不同步", "level": "danger", "detail": last_error}
        return {"code": "quote_stale", "label": "行情过旧", "level": "warning", "detail": last_error}
    paused = market_pause_status(run_message)
    if paused and not latest:
        return paused
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
    abs_value = abs(value)
    if abs_value >= threshold:
        direction = "高于" if value > 0 else "低于"
        return {
            "code": "over_300",
            "label": "已越过300",
            "level": "danger",
            "detail": f"a值已{direction} {'+' if value > 0 else '-'}{threshold}",
        }
    if threshold - abs_value <= Decimal("50"):
        return {"code": "near_300", "label": "接近300", "level": "warning", "detail": f"距离 +/-{threshold} 不超过50"}
    return {"code": "normal", "label": "正常", "level": "ok", "detail": "严格实时行情同步，a值未接近首档阈值"}


def notification_diagnostic(latest_notification: dict | None, configured: bool) -> dict[str, str]:
    if not configured:
        return {
            "code": "unconfigured",
            "label": "飞书未配置",
            "level": "warning",
            "detail": "未配置 A_MONITOR_WEBHOOK_URL 或 notification.webhook_url",
        }
    if not latest_notification:
        return {"code": "waiting", "label": "飞书待测试", "level": "muted", "detail": "尚无飞书发送记录"}
    status = str(latest_notification.get("status") or "").strip()
    if status == "sent":
        elapsed = latest_notification.get("notification_elapsed_ms")
        detail = "飞书业务响应成功"
        if elapsed is not None:
            detail = f"飞书业务响应成功，耗时 {elapsed}ms"
        return {"code": "sent", "label": "飞书正常", "level": "ok", "detail": detail}
    if status == "failed":
        code = latest_notification.get("notification_response_code")
        msg = latest_notification.get("notification_response_message") or latest_notification.get("error") or "未知失败"
        detail = f"code={code}; {msg}" if code not in {None, ""} else str(msg)
        return {"code": "failed", "label": "飞书失败", "level": "danger", "detail": detail}
    return {"code": status or "unknown", "label": "飞书未知", "level": "warning", "detail": str(latest_notification)}


def build_runtime_diagnostics(
    *,
    status: dict[str, str],
    latest_snapshot: dict | None,
    snapshot_fresh: bool,
    last_error: str,
    latest_notification: dict | None,
    notification_configured: bool,
    pcf_retry_remaining: int = 0,
) -> dict[str, Any]:
    status_code = status.get("code", "")
    if status_code == "pcf_not_ready":
        pcf = {
            "code": "not_ready",
            "label": "PCF未就绪",
            "level": "warning",
            "detail": last_error or status.get("detail", ""),
            "retry_remaining_seconds": pcf_retry_remaining,
        }
        quote = {"code": "blocked_by_pcf", "label": "等待PCF", "level": "muted", "detail": "PCF未就绪前不取行情计算a"}
    else:
        pcf = {"code": "ok_or_not_checked", "label": "PCF未阻塞", "level": "ok", "detail": "当前错误不是PCF未就绪"}
        if status_code in {"normal", "near_300", "over_300"}:
            quote = {"code": "ok", "label": "行情同步", "level": "ok", "detail": status.get("detail", "")}
        elif status_code == "quote_unsynced":
            quote = {"code": "unsynced", "label": "行情不同步", "level": "danger", "detail": status.get("detail", "")}
        elif status_code == "quote_stale":
            quote = {"code": "stale", "label": "行情过旧", "level": "warning", "detail": status.get("detail", "")}
        elif status_code == "market_closed":
            quote = {"code": "market_closed", "label": "休市中", "level": "muted", "detail": status.get("detail", "")}
        elif latest_snapshot and not snapshot_fresh:
            quote = {"code": "stale", "label": "行情过旧", "level": "warning", "detail": "最新快照已过期"}
        else:
            quote = {"code": "waiting", "label": "等待行情", "level": "muted", "detail": status.get("detail", "")}
    notification = notification_diagnostic(latest_notification, notification_configured)
    summary = " / ".join([pcf["label"], quote["label"], notification["label"]])
    return {
        "summary": summary,
        "pcf": pcf,
        "quote": quote,
        "notification": notification,
    }


def format_epoch_seconds(value: float) -> str | None:
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, TZ).strftime("%Y-%m-%d %H:%M:%S")


def auto_loop_diagnostic(state: DashboardState, *, now: float | None = None) -> dict[str, Any]:
    if not state.auto_run:
        return {
            "code": "disabled",
            "label": "自动计算未启用",
            "level": "muted",
            "last_tick_at": None,
            "age_seconds": None,
            "stale_after_seconds": None,
            "detail": "auto_run=false",
        }
    if state.last_auto_tick_at <= 0:
        return {
            "code": "starting",
            "label": "自动线程未打点",
            "level": "warning",
            "last_tick_at": None,
            "age_seconds": None,
            "stale_after_seconds": max(60, state.interval_seconds * 4),
            "detail": "自动线程尚未完成第一次循环打点",
        }
    current = now if now is not None else time.time()
    age = max(0.0, current - state.last_auto_tick_at)
    stale_after = max(60, state.interval_seconds * 4)
    if age > stale_after:
        return {
            "code": "stale",
            "label": "自动线程可能卡住",
            "level": "danger",
            "last_tick_at": format_epoch_seconds(state.last_auto_tick_at),
            "age_seconds": round(age, 1),
            "stale_after_seconds": stale_after,
            "detail": f"最近心跳已超过 {stale_after} 秒",
        }
    return {
        "code": "running",
        "label": "自动线程有心跳",
        "level": "ok",
        "last_tick_at": format_epoch_seconds(state.last_auto_tick_at),
        "age_seconds": round(age, 1),
        "stale_after_seconds": stale_after,
        "detail": "自动计算循环仍在运行",
    }


def threshold_gap(latest: dict | None, threshold: Decimal) -> dict[str, str]:
    if not latest:
        return {"text": "-", "value": "-", "direction": "unknown"}
    value = decimal_from_payload(latest.get("estimated_a"))
    if value is None:
        return {"text": "-", "value": "-", "direction": "unknown"}
    gap = threshold - abs(value)
    if gap >= 0:
        return {"text": f"还差 {monitor.money(gap)}", "value": str(monitor.q2(gap)), "direction": "below"}
    return {"text": f"已超过 {monitor.money(abs(gap))}", "value": str(monitor.q2(abs(gap))), "direction": "above"}


def build_formula_snapshot(snapshot: dict | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    components = parse_latest_components(snapshot)
    etf_quote = snapshot.get("etf_quote", "-")
    etf_value = snapshot.get("etf_value", "-")
    estimated_cash = snapshot.get("estimated_cash", "-")
    component_value = snapshot.get("component_value_ex_cash", "-")
    basket_value = snapshot.get("basket_value", "-")
    estimated_a = snapshot.get("estimated_a", "-")
    lines = [
        "a = ETF端价值 - 成分券篮子价值 - EstimatedCashComponent",
        f"ETF端价值 = 511130报价 {etf_quote} / 100 * 1,000,000 = {etf_value}",
        "成分券篮子价值 = Σ(成分券净价 + 逐券应计利息) * PCF数量 * 10",
    ]
    for row in components:
        label = " ".join(part for part in [row.get("code") or "", row.get("name") or ""] if part).strip()
        source = row.get("interest_source_label") or "-"
        lines.append(
            f"  {label}: (净价 {row.get('price') or '-'} + 逐券应计利息 {row.get('interest') or '-'} [{source}])"
            f" * 数量 {row.get('units') or '-'} = {row.get('value') or '-'}"
        )
    lines.extend(
        [
            f"成分券篮子价值合计 = {component_value}",
            f"EstimatedCashComponent = {estimated_cash}",
            f"篮子端价值 = {component_value} + {estimated_cash} = {basket_value}",
            f"a = {etf_value} - {basket_value} = {estimated_a}",
        ]
    )
    return {
        "formula_text": "\n".join(lines),
        "etf_quote": etf_quote,
        "etf_value": etf_value,
        "components": components,
        "component_value_ex_cash": component_value,
        "estimated_cash": estimated_cash,
        "basket_value": basket_value,
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
    chart_sources = list(dict.fromkeys([*state.allowed_price_sources, *HISTORICAL_CHART_PRICE_SOURCES]))
    raw_points = load_points(date, max_points=0, allowed_sources=chart_sources, require_strict=False)
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
        "mode": "today_realtime" if date == state.date else "historical_replay",
        "note": "1秒原始严格实时点" if kind == "line" else f"{interval_key} a值OHLC聚合",
    }


def env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def config_snapshot(config: dict, state: DashboardState) -> dict[str, Any]:
    overrides = ((config.get("interest_overrides") or {}).get(state.date) or {})
    notification_setup = monitor.notification_setup(config)
    return {
        "target_date": state.date,
        "target_date_mode": str(config.get("target_date_mode", "fixed")),
        "thresholds": [str(item) for item in state.thresholds],
        "thresholds_text": ",".join(str(item) for item in state.thresholds),
        "interest_overrides": {str(k): str(v) for k, v in overrides.items()},
        "max_skew_seconds": str(config.get("realtime_max_skew_seconds", "3")),
        "max_stale_seconds": str(state.max_stale_seconds),
        "allowed_price_sources": state.allowed_price_sources,
        "public_readonly": state.public_readonly,
        "notification_configured": bool(notification_setup["webhook_configured"]),
        "notification_setup": notification_setup,
        "accuracy_setup": accuracy_setup(config, state),
        "alert_policy_setup": alert_policy_setup(config),
    }


def alert_policy_setup(config: dict) -> dict[str, Any]:
    notification = config.get("notification") or {}
    attempts = int(notification.get("attempts", config.get("notification_attempts", 3)))
    retry_delay = str(notification.get("retry_delay_seconds", config.get("notification_retry_delay_seconds", "1")))
    return {
        "threshold_only_notifications": True,
        "runtime_error_notifications": False,
        "no_alert_run_check_notifications": False,
        "degraded_alert_enabled": bool(config.get("degraded_alert_enabled", True)),
        "degraded_alert_title": str(config.get("degraded_alert_title", DEGRADED_ALERT_TITLE)) or DEGRADED_ALERT_TITLE,
        "degraded_alert_source_mode": str(config.get("degraded_alert_source_mode", "degraded_price_source_v1"))
        or "degraded_price_source_v1",
        "notification_attempts": max(1, attempts),
        "notification_retry_delay_seconds": retry_delay,
    }


def accuracy_setup(config: dict, state: DashboardState) -> dict[str, Any]:
    data_sources = config.get("data_sources") or {}
    interest_overrides = (config.get("interest_overrides") or {}).get(state.date) or {}
    expected_codes = [str(code).strip() for code in config.get("expected_component_codes", []) if str(code).strip()]
    return {
        "formula_version": "estimated_a_v1",
        "fund_code": str(config.get("fund_code", "511130")),
        "pcf_source": str(data_sources.get("pcf", "")),
        "interest_source": str(data_sources.get("interest", "")),
        "intraday_source": str(data_sources.get("intraday", "")),
        "expected_component_codes": expected_codes,
        "pcf_structure_locked": bool(expected_codes),
        "creation_redemption_unit_required": "10000",
        "strict_realtime_required": bool(config.get("require_realtime_snapshot", True)),
        "prefer_realtime_snapshot": bool(config.get("prefer_realtime_snapshot", True)),
        "allowed_price_sources": state.allowed_price_sources,
        "max_skew_seconds": int(config.get("realtime_max_skew_seconds", 3)),
        "max_stale_seconds": state.max_stale_seconds,
        "missing_interest_fallback_allowed": bool(
            config.get("allow_missing_interest_fallback", config.get("allow_interest_fallback", False))
        ),
        "interest_overrides_for_date": bool(interest_overrides),
        "interest_override_codes_for_date": sorted(str(code) for code in interest_overrides),
    }


def build_data_payload(state: DashboardState, config: dict) -> dict[str, Any]:
    points = load_points(state.date, max_points=state.max_points, allowed_sources=state.allowed_price_sources)
    points = merge_memory_point(points, state)
    latest_snapshot = effective_latest_snapshot(state)
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
        run_message=state.last_run_message,
    )
    pcf_remaining = pcf_retry_remaining_seconds(state)
    notification_setup = monitor.notification_setup(config)
    notification_configured = bool(notification_setup["webhook_configured"])
    diagnostics = build_runtime_diagnostics(
        status=status,
        latest_snapshot=latest_snapshot,
        snapshot_fresh=snapshot_fresh,
        last_error=last_error,
        latest_notification=latest_notification,
        notification_configured=notification_configured,
        pcf_retry_remaining=pcf_remaining,
    )
    point_stats = chart_stats(points)
    chart_current = latest is not None
    chart_notice = ""
    if not chart_current:
        chart_notice = "休市中，暂无实时a值" if status.get("code") == "market_closed" else "曲线仅为历史点，当前a未通过实时校验"
    quote_cards, quote_cards_notice = build_quote_cards(state, config, latest_snapshot)
    return {
        "ok": True,
        "points": points,
        "stats": point_stats,
        "chart_current": chart_current,
        "chart_notice": chart_notice,
        "latest_a": "-" if not latest else f"{latest['estimated_a']:.2f}",
        "latest_etf_quote": "-"
        if not latest or latest.get("etf_quote") is None
        else f"{latest['etf_quote']:.3f}",
        "status": status,
        "status_text": status["label"],
        "diagnostics": diagnostics,
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
        "quote_cards": quote_cards,
        "quote_cards_notice": quote_cards_notice,
        "latest_alert": latest_alert or {},
        "latest_notification": latest_notification or {},
        "thresholds": thresholds,
        "last_error": last_error,
        "count": len(points),
        "available_dates": available_dates(),
        "config": config_snapshot(config, state),
    }


def build_health_payload(state: DashboardState, config: dict) -> dict[str, Any]:
    latest_snapshot = effective_latest_snapshot(state)
    age = snapshot_age_seconds(latest_snapshot)
    snapshot_fresh = age is not None and age <= state.max_stale_seconds
    data_error = data_error_only(state.last_error)
    data_ok = bool(latest_snapshot) and snapshot_fresh and not data_error
    latest = snapshot_to_point(latest_snapshot, state.allowed_price_sources) if data_ok else None
    pcf_remaining = pcf_retry_remaining_seconds(state)
    latest_notification = load_latest_notification(state.date)
    status = classify_status(
        latest=latest,
        latest_snapshot=latest_snapshot,
        snapshot_fresh=snapshot_fresh,
        last_error=data_error,
        latest_notification=latest_notification,
        thresholds=state.thresholds,
        run_message=state.last_run_message,
    )
    notification_setup = monitor.notification_setup(config)
    notification_configured = bool(notification_setup["webhook_configured"])
    diagnostics = build_runtime_diagnostics(
        status=status,
        latest_snapshot=latest_snapshot,
        snapshot_fresh=snapshot_fresh,
        last_error=data_error,
        latest_notification=latest_notification,
        notification_configured=notification_configured,
        pcf_retry_remaining=pcf_remaining,
    )
    auto_loop = auto_loop_diagnostic(state)
    process_ok = auto_loop.get("code") != "stale"
    return {
        "ok": process_ok,
        "service": "511130-live-dashboard",
        "date": state.date,
        "auto_run": state.auto_run,
        "public_readonly": state.public_readonly,
        "target_date_mode": str(config.get("target_date_mode", "fixed")),
        "process_ok": process_ok,
        "data_ok": data_ok,
        "diagnostics": diagnostics,
        "notification_configured": notification_configured,
        "notification_setup": notification_setup,
        "accuracy_setup": accuracy_setup(config, state),
        "alert_policy_setup": alert_policy_setup(config),
        "auto_loop": auto_loop,
        "auto_error_count": state.auto_error_count,
        "pcf_retry_remaining_seconds": pcf_remaining,
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
    payload_target_date = str(payload.get("target_date", state.date) or "").strip()
    raw_target_date = str(raw_config.get("target_date", "") or "").strip()
    if raw_target_date.lower() in {"", "auto", "today", "shanghai_today"} and payload_target_date == state.date:
        target_date_for_config = raw_target_date or "auto"
    else:
        target_date_for_config = validate_target_date(payload_target_date)
    target_date, _ = monitor.resolve_target_date(target_date_for_config)
    thresholds = parse_threshold_list(payload.get("thresholds", state.threshold_text))
    raw_config["target_date"] = target_date_for_config
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
    state.date = str(config["target_date"])
    state.latest_result = None
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
        error = maybe_prepare_auto_context(
            state,
            config,
            context_ref,
            success_message=f"配置已保存，已预加载PCF和逐券利息",
            failure_message="配置已保存，预加载失败",
        )
        if error:
            warning = f"配置已保存，但自动计算预加载失败: {error}"
    return True, "已保存", warning


def maybe_roll_auto_target_date(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
    *,
    prepare_context: bool = True,
) -> bool:
    if str(config.get("target_date_mode", "")).lower() != "auto":
        return False
    target_date, _ = monitor.resolve_target_date("auto")
    if target_date == state.date:
        return False

    previous_date = state.date
    state.date = target_date
    config["target_date"] = target_date
    config["target_date_mode"] = "auto"
    context_ref["value"] = None
    state.latest_result = None
    state.pcf_retry_at = 0.0
    state.auto_error_count = 0
    state.last_error_notify_at = 0.0
    state.last_error_notify_key = ""

    prefix = f"日期自动切换: {previous_date}->{target_date}"
    if not state.auto_run or not prepare_context:
        state.last_error = ""
        state.last_run_message = prefix
        return True

    maybe_prepare_auto_context(
        state,
        config,
        context_ref,
        success_message=f"{prefix}，已预加载PCF和逐券利息",
        failure_message=f"{prefix}，预加载失败",
    )
    return True


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour_text, minute_text = str(value).strip().split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"交易时段时间无效: {value}")
    return hour, minute


def _auto_run_sessions(config: dict) -> list[tuple[str, str]]:
    raw_sessions = config.get("auto_run_sessions") or [["09:25", "11:35"], ["12:55", "15:10"]]
    sessions: list[tuple[str, str]] = []
    for item in raw_sessions:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"auto_run_sessions 条目无效: {item}")
        start = str(item[0]).strip()
        end = str(item[1]).strip()
        start_hhmm = _parse_hhmm(start)
        end_hhmm = _parse_hhmm(end)
        if end_hhmm <= start_hhmm:
            raise ValueError(f"auto_run_sessions 结束时间必须晚于开始时间: {item}")
        sessions.append((start, end))
    if not sessions:
        raise ValueError("auto_run_sessions 不能为空")
    return sessions


def auto_run_closed_dates(config: dict) -> set[str]:
    raw_dates = config.get("auto_run_closed_dates") or []
    if isinstance(raw_dates, str):
        parts = raw_dates.replace("，", ",").replace(";", ",").split(",")
    elif isinstance(raw_dates, list):
        parts = raw_dates
    else:
        raise ValueError("auto_run_closed_dates 必须是 YYYYMMDD 数组或逗号分隔文本")
    dates: set[str] = set()
    for item in parts:
        text = str(item).strip()
        if not text:
            continue
        if len(text) != 8 or not text.isdigit():
            raise ValueError(f"auto_run_closed_dates 日期无效: {text}")
        dates.add(text)
    return dates


def is_auto_run_trading_day(day: datetime, config: dict) -> bool:
    if day.weekday() >= 5:
        return False
    return day.strftime("%Y%m%d") not in auto_run_closed_dates(config)


def _with_hhmm(day: datetime, value: str) -> datetime:
    hour, minute = _parse_hhmm(value)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def auto_run_market_gate(config: dict, *, now: datetime | None = None) -> tuple[bool, int, str]:
    if not bool(config.get("auto_run_market_hours_only", True)):
        return True, 0, ""
    current = datetime.now(TZ) if now is None else now.astimezone(TZ)
    sessions = _auto_run_sessions(config)
    closed_today = current.strftime("%Y%m%d") in auto_run_closed_dates(config)
    if is_auto_run_trading_day(current, config):
        for start_text, end_text in sessions:
            start = _with_hhmm(current, start_text)
            end = _with_hhmm(current, end_text)
            if start <= current <= end:
                return True, 0, ""
            if current < start:
                wait_seconds = max(1, int((start - current).total_seconds()))
                return False, wait_seconds, f"盘外暂停，{start.strftime('%m-%d %H:%M')}后恢复自动计算"

    first_start = sessions[0][0]
    probe = current
    for _ in range(16):
        probe = probe + timedelta(days=1)
        if is_auto_run_trading_day(probe, config):
            next_start = _with_hhmm(probe, first_start)
            wait_seconds = max(1, int((next_start - current).total_seconds()))
            reason = "休市暂停" if closed_today else "盘外暂停"
            return False, wait_seconds, f"{reason}，{next_start.strftime('%m-%d %H:%M')}后恢复自动计算"
    return False, 60, "盘外暂停，等待下一个自动计算时段"


def maybe_pause_auto_run_outside_market(state: DashboardState, config: dict) -> int:
    market_open, wait_seconds, message = auto_run_market_gate(config)
    if market_open:
        return 0
    state.last_run_message = message
    state.last_error = ""
    state.auto_error_count = 0
    state.pcf_retry_at = 0.0
    state.last_error_notify_key = ""
    return wait_seconds


def maybe_prepare_auto_context(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
    *,
    success_message: str = "已预加载PCF和逐券利息",
    failure_message: str = "预加载失败",
) -> str:
    context_ref["value"] = None
    if maybe_pause_auto_run_outside_market(state, config) > 0:
        return ""
    try:
        context_ref["value"] = monitor.prepare_calculation_context(state.date, config)
        state.last_error = ""
        state.last_run_message = success_message
        return ""
    except Exception as exc:  # noqa: BLE001
        context_ref["value"] = None
        state.last_error = f"{type(exc).__name__}: {exc}"
        state.last_run_message = failure_message
        return state.last_error


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
    .quote-section { margin-top: 12px; }
    .chart-panel { margin-top: 12px; }
    .quote-section-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
      margin: 0 0 8px;
    }
    .quote-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(260px, 1fr));
      gap: 0;
      min-width: 1040px;
      align-items: stretch;
    }
    .quote-strip-scroll {
      width: 100%;
      overflow-x: auto;
      padding-bottom: 2px;
    }
    .quote-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 0;
      border-radius: 0;
      box-shadow: none;
      padding: 12px;
      min-width: 0;
      min-height: 430px;
      display: flex;
      flex-direction: column;
    }
    .quote-card:first-child {
      border-left: 1px solid var(--line);
      border-radius: 8px 0 0 8px;
    }
    .quote-card:last-child {
      border-radius: 0 8px 8px 0;
    }
    .quote-card-empty {
      justify-content: center;
      color: var(--muted);
    }
    .quote-card-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 8px;
      margin-bottom: 8px;
    }
    .quote-code {
      display: inline-block;
      border-radius: 999px;
      background: #eef2ff;
      color: #3730a3;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 750;
      margin-bottom: 5px;
    }
    .quote-name { font-weight: 750; line-height: 1.2; }
    .quote-price {
      text-align: right;
      font-size: 32px;
      line-height: 1;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }
    .quote-change { margin-top: 5px; text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; }
    .quote-up { color: var(--red); }
    .quote-down { color: var(--green); }
    .quote-flat { color: #334155; }
    .quote-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin: 8px 0;
    }
    .quote-metric {
      border: 1px solid #e6ebf2;
      border-radius: 8px;
      background: #f8fafc;
      padding: 7px;
      min-width: 0;
    }
    .quote-metric span { display: block; color: var(--muted); font-size: 11px; }
    .quote-metric strong {
      display: block;
      margin-top: 2px;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }
    .sparkline {
      width: 100%;
      height: 72px;
      display: block;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
      background: #fff;
      margin: 8px 0;
    }
    .orderbook {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      font-size: 12px;
    }
    .book-side { min-width: 0; }
    .book-row {
      display: grid;
      grid-template-columns: 34px minmax(0, 1fr) minmax(0, .9fr);
      gap: 5px;
      padding: 3px 0;
      border-bottom: 1px solid #eef2f7;
      font-variant-numeric: tabular-nums;
    }
    .book-row:last-child { border-bottom: 0; }
    .book-row span:nth-child(2), .book-row span:nth-child(3) { text-align: right; overflow-wrap: anywhere; }
    .book-ask { color: var(--red); }
    .book-bid { color: var(--green); }
    .a-summary-card {
      background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    }
    .a-summary-value {
      font-size: clamp(44px, 5.6vw, 72px);
      line-height: .95;
      font-weight: 820;
      margin: 16px 0 8px;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }
    .a-status-danger { color: var(--red); }
    .a-status-warning { color: var(--amber); }
    .a-status-ok { color: var(--green); }
    .a-status-muted { color: #334155; }
    .a-summary-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      margin-top: 10px;
    }
    .a-summary-metric {
      border: 1px solid #e6ebf2;
      border-radius: 8px;
      background: #fff;
      padding: 8px;
      min-width: 0;
    }
    .a-summary-metric span {
      display: block;
      color: var(--muted);
      font-size: 11px;
    }
    .a-summary-metric strong {
      display: block;
      margin-top: 3px;
      font-size: 14px;
      font-variant-numeric: tabular-nums;
      overflow-wrap: anywhere;
    }
    .a-summary-note {
      margin-top: auto;
      padding-top: 10px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }
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
      .quote-section-head { display: block; }
      .quote-strip-scroll { margin: 0 -10px; padding: 0 10px 4px; }
      .quote-grid { grid-template-columns: repeat(4, minmax(240px, 1fr)); min-width: 960px; }
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
      .quote-card { padding: 10px; }
      .quote-price { font-size: 28px; }
      .a-summary-value { font-size: 44px; }
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

    <section class="quote-section">
      <div class="quote-section-head">
        <div>
          <div class="section-title">行情 / 五档买卖 / 分时</div>
          <div class="meta" id="quoteCardsMeta">等待行情卡片</div>
        </div>
        <div class="meta">盘口仅作展示；a 值仍按严格实时快照计算</div>
      </div>
      <div class="quote-strip-scroll">
        <div class="quote-grid" id="quoteCards"></div>
      </div>
    </section>

    <section class="panel chart-panel">
      <div class="chart-head">
        <div>
          <div class="section-title">历史曲线 / a-K线</div>
          <div class="meta" id="chartModeNote">默认显示全天1秒历史回放</div>
        </div>
        <div class="chart-controls">
          <div class="chart-control">
            <label for="seriesRange">范围</label>
            <select id="seriesRange">
              <option value="1m">近1分钟</option>
              <option value="5m">近5分钟</option>
              <option value="15m">近15分钟</option>
              <option value="1h">近1小时</option>
              <option value="day" selected>全天</option>
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
      <div class="meta">成分券=PCF篮子里的债券；逐券应计利息=每只债券截至当日的应计利息。采样时间：<span id="compTs">-</span>；来源：<span id="compSource">-</span></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>实时净价</th>
              <th>逐券应计利息</th>
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
let seriesDateTouched = false;
const RANGE_LABELS = {
  "1m": "近1分钟",
  "5m": "近5分钟",
  "15m": "近15分钟",
  "1h": "近1小时",
  "today": "当天",
  "day": "全天",
  "all": "全部"
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

function compactNumber(value) {
  const n = asNumber(value);
  if (n === null) return value || "-";
  const abs = Math.abs(n);
  if (abs >= 100000000) return `${(n / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${(n / 10000).toFixed(2)}万`;
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function signedNumber(value, digits = 3) {
  const n = asNumber(value);
  if (n === null) return value || "-";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}`;
}

function signedPct(value) {
  const n = asNumber(value);
  if (n === null) return value || "-";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function directionClass(value) {
  const n = asNumber(value);
  if (n === null || n === 0) return "quote-flat";
  return n > 0 ? "quote-up" : "quote-down";
}

function escapeAttr(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[ch]));
}

function escapeHtml(value) {
  return escapeAttr(value);
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

function latestAvailableDate(dates) {
  const items = (dates || []).filter(Boolean).sort();
  return items.length ? items[items.length - 1] : "";
}

function populateDateOptions(dates, currentDate, hasCurrentPoints) {
  const select = document.getElementById("seriesDate");
  if (!select) return;
  const previous = select.value;
  const historyLatest = latestAvailableDate(dates);
  const nextDates = Array.from(new Set([currentDate, ...(dates || [])].filter(Boolean))).sort();
  const existing = new Set(Array.from(select.options).map(x => x.value));
  const optionsChanged = !(nextDates.length === existing.size && nextDates.every(x => existing.has(x)));
  let selected = previous && nextDates.includes(previous) ? previous : "";
  if (!selected || !seriesDateTouched) {
    selected = hasCurrentPoints ? (currentDate || historyLatest) : (historyLatest || currentDate || cfg.date);
  }
  if (!optionsChanged) {
    select.value = nextDates.includes(selected) ? selected : (historyLatest || currentDate || "");
    return;
  }
  select.innerHTML = "";
  for (const date of nextDates) {
    const option = document.createElement("option");
    option.value = date;
    option.textContent = date === currentDate ? `${date} 今天` : `${date} 历史`;
    select.appendChild(option);
  }
  select.value = nextDates.includes(selected) ? selected : (historyLatest || currentDate || nextDates[nextDates.length - 1] || "");
}

function selectedSeriesDate() {
  return document.getElementById("seriesDate")?.value || cfg.date;
}

function selectedSeriesRange() {
  return document.getElementById("seriesRange")?.value || "day";
}

function selectedSeriesInterval() {
  return document.getElementById("seriesInterval")?.value || "1s";
}

function setStatus(status) {
  const pill = document.getElementById("statusPill");
  const level = status?.level || "muted";
  const code = status?.code || "";
  if (pill) {
    pill.className = `status-pill status-${level}`;
    pill.textContent = status?.label || "等待数据";
  }
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
  if (!wrap) return;
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

function sparklineSvg(points) {
  const rows = (points || []).map(point => ({
    price: asNumber(point.price),
    timestamp: point.timestamp || ""
  })).filter(point => point.price !== null);
  if (rows.length < 2) {
    return '<svg class="sparkline" viewBox="0 0 260 72" role="img"><text x="12" y="40" fill="#667085" font-size="12">暂无分时点</text></svg>';
  }
  const values = rows.map(point => point.price);
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = Math.max(0.001, maxV - minV);
  const pad = { l: 8, r: 8, t: 8, b: 18 };
  const w = 260 - pad.l - pad.r;
  const h = 72 - pad.t - pad.b;
  const poly = rows.map((point, index) => {
    const x = pad.l + (index / Math.max(1, rows.length - 1)) * w;
    const y = pad.t + h - ((point.price - minV) / span) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const first = rows[0].price;
  const last = rows[rows.length - 1].price;
  const color = last >= first ? "#b91c1c" : "#15803d";
  const startLabel = shortTime(rows[0].timestamp);
  const endLabel = shortTime(rows[rows.length - 1].timestamp);
  return `
    <svg class="sparkline" viewBox="0 0 260 72" role="img">
      <line x1="8" y1="54" x2="252" y2="54" stroke="#e5e7eb" stroke-width="1"></line>
      <polyline points="${poly}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"></polyline>
      <circle cx="252" cy="${(pad.t + h - ((last - minV) / span) * h).toFixed(1)}" r="3.4" fill="#fff" stroke="${color}" stroke-width="2"></circle>
      <text x="8" y="68" fill="#667085" font-size="10">${escapeHtml(startLabel)}</text>
      <text x="252" y="68" fill="#667085" font-size="10" text-anchor="end">${escapeHtml(endLabel)}</text>
    </svg>
  `;
}

function renderOrderbookSide(rows, side) {
  const items = side === "ask" ? [...(rows || [])].reverse() : (rows || []);
  if (!items.length) {
    return '<div class="book-row"><span>-</span><span>-</span><span>-</span></div>';
  }
  const label = side === "ask" ? "卖" : "买";
  const cls = side === "ask" ? "book-ask" : "book-bid";
  return items.map(item => `
    <div class="book-row ${cls}">
      <span>${label}${escapeHtml(item.level || "-")}</span>
      <span>${escapeHtml(item.price || "-")}</span>
      <span>${escapeHtml(compactNumber(item.quantity))}</span>
    </div>
  `).join("");
}

function renderAValueCard(payload) {
  const status = payload?.status || {};
  const level = status.level || "muted";
  const latestA = payload?.latest_a || "-";
  const latestEtf = payload?.latest_etf_quote || "-";
  const distance = payload?.distance_to_300?.text || "-";
  const skew = payload?.quote_skew_seconds == null ? "-" : `${payload.quote_skew_seconds}s`;
  const calcTime = shortTime(payload?.calculated_at);
  const notify = notifyText(payload?.latest_notification);
  const detail = status.detail || payload?.chart_notice || "严格实时未通过时不展示当前套利值";
  const statusLabel = status.label || "等待数据";
  const valueClass = level === "danger" ? "a-status-danger" : (level === "warning" ? "a-status-warning" : (level === "ok" ? "a-status-ok" : "a-status-muted"));
  return `
    <div class="quote-card a-summary-card">
      <div class="quote-card-head">
        <div>
          <span class="quote-code">A</span>
          <div class="quote-name">套利值 A</div>
          <div class="meta">严格实时计算 · ${escapeHtml(calcTime)}</div>
        </div>
        <div class="status-pill status-${escapeHtml(level)}">${escapeHtml(statusLabel)}</div>
      </div>
      <div class="a-summary-value ${valueClass}">${escapeHtml(latestA)}</div>
      <div class="quote-change ${valueClass}">当前 a 值 / 阈值 ${escapeHtml((cfg.thresholds || []).join(", ") || "-")}</div>
      <div class="a-summary-metrics">
        <div class="a-summary-metric"><span>511130价格</span><strong>${escapeHtml(latestEtf)}</strong></div>
        <div class="a-summary-metric"><span>距离300</span><strong>${escapeHtml(distance)}</strong></div>
        <div class="a-summary-metric"><span>时间差</span><strong>${escapeHtml(skew)}</strong></div>
        <div class="a-summary-metric"><span>飞书状态</span><strong>${escapeHtml(notify)}</strong></div>
      </div>
      <div class="a-summary-note">${escapeHtml(detail)}</div>
    </div>
  `;
}

function renderQuoteCards(cards, notice, payload) {
  const wrap = document.getElementById("quoteCards");
  const meta = document.getElementById("quoteCardsMeta");
  if (!wrap || !meta) return;
  wrap.innerHTML = "";
  if (!cards || !cards.length) {
    meta.textContent = notice || "暂无证券行情卡片";
    wrap.innerHTML = '<div class="quote-card quote-card-empty"><div class="meta">暂无行情数据</div></div>' + renderAValueCard(payload || {});
    return;
  }
  meta.textContent = notice
    ? `四联横排：${cards.length} 只证券 + 套利值A；五档盘口：${notice}`
    : `四联横排：${cards.length} 只证券 + 套利值A；页面 ${cfg.refreshSec}s 刷新，盘口源为新浪展示快照`;
  for (const card of cards) {
    const div = document.createElement("div");
    const changeClass = directionClass(card.change);
    div.className = `quote-card ${changeClass}`;
    const bookNote = card.orderbook_source
      ? `${card.orderbook_source} ${shortTime(card.orderbook_time)}`
      : "盘口缺失";
    div.innerHTML = `
      <div class="quote-card-head">
        <div>
          <span class="quote-code">${escapeHtml(card.code || "-")}</span>
          <div class="quote-name">${escapeHtml(card.name || "-")}</div>
          <div class="meta">${escapeHtml(card.quote_time || "-")} · ${escapeHtml(card.price_source || "-")}</div>
        </div>
        <div>
          <div class="quote-price">${escapeHtml(card.price || "-")}</div>
          <div class="quote-change ${changeClass}">${escapeHtml(signedNumber(card.change))} ${escapeHtml(signedPct(card.pct_change))}</div>
        </div>
      </div>
      <div class="quote-metrics">
        <div class="quote-metric"><span>开盘价</span><strong>${escapeHtml(card.open || "-")}</strong></div>
        <div class="quote-metric"><span>昨收</span><strong>${escapeHtml(card.previous_close || "-")}</strong></div>
        <div class="quote-metric"><span>成交量</span><strong>${escapeHtml(compactNumber(card.volume))}</strong></div>
        <div class="quote-metric"><span>成交额</span><strong>${escapeHtml(compactNumber(card.amount))}</strong></div>
      </div>
      ${sparklineSvg(card.series || [])}
      <div class="meta" style="margin-bottom:4px;">五档盘口：${escapeHtml(bookNote)}</div>
      <div class="orderbook">
        <div class="book-side">${renderOrderbookSide(card.asks || [], "ask")}</div>
        <div class="book-side">${renderOrderbookSide(card.bids || [], "bid")}</div>
      </div>
    `;
    wrap.appendChild(div);
  }
  wrap.insertAdjacentHTML("beforeend", renderAValueCard(payload || {}));
}

function renderFormula(formula) {
  const text = formula?.formula_text || "暂无严格实时计算过程";
  document.getElementById("formulaText").textContent = text;
  const breakdown = document.getElementById("breakdown");
  breakdown.innerHTML = "";
  const rows = [
    ["ETF端价值", formula?.etf_value],
    ["019776成分券贡献", (formula?.components || []).find(x => x.code === "019776")?.value],
    ["019837成分券贡献", (formula?.components || []).find(x => x.code === "019837")?.value],
    ["成分券篮子价值合计", formula?.component_value_ex_cash],
    ["EstimatedCashComponent", formula?.estimated_cash],
    ["篮子端价值", formula?.basket_value],
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
  const autoInterestByCode = {};
  for (const item of payload.components || []) {
    if (!item.code) continue;
    autoInterestByCode[item.code] = item;
  }
  box.innerHTML = "";
  for (const code of codes) {
    if (!code) continue;
    const wrap = document.createElement("div");
    const value = activeCode === code ? activeValue : (config.interest_overrides || {})[code] || "";
    const auto = autoInterestByCode[code];
    const placeholder = auto?.interest
      ? `空=自动 ${auto.interest}（${auto.interest_source_label || "自动来源"}）`
      : "空=自动取上交所利息";
    wrap.innerHTML = `
      <label for="interest-${escapeAttr(code)}">${escapeAttr(code)} 利息覆盖（可选）</label>
      <input id="interest-${escapeAttr(code)}" data-code="${escapeAttr(code)}" class="interest-input" autocomplete="off" value="${escapeAttr(value)}" placeholder="${escapeAttr(placeholder)}">
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
  const modeLabel = payload.mode === "today_realtime" ? "今天实时" : "历史回放";
  setText(
    "chartModeNote",
    `${modeLabel} ${payload.date} ${rangeLabel} ${intervalLabel} ${kindLabel}；点数 ${payload.count}，原始 ${payload.range_count}/${payload.raw_count}`
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
    populateDateOptions(payload.available_dates || [], cfg.date, (payload.count || 0) > 0);
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
    renderQuoteCards(payload.quote_cards || [], payload.quote_cards_notice || "", payload);
    renderFormula(payload.formula || {});
    renderConfig(payload);
    applyReadOnlyMode(payload);
    setText("compTs", payload.component_timestamp || "-");
    setText("compSource", payload.component_source || "-");
    const err = payload.last_error || payload.status?.detail || "";
    const chartNotice = payload.chart_notice || "";
    const lastErr = document.getElementById("lastErr");
    if (lastErr) {
      lastErr.textContent = [err && payload.status?.level !== "ok" ? err : "", chartNotice].filter(Boolean).join("；");
    }
    await refreshSeries();
  } catch (error) {
    setText("statusPill", "取数失败");
    const pill = document.getElementById("statusPill");
    if (pill) pill.className = "status-pill status-danger";
    const lastErr = document.getElementById("lastErr");
    if (lastErr) lastErr.textContent = String(error);
  }
}

async function manualRecalc(notify) {
  const btn = document.getElementById(notify ? "recalcNotify" : "recalc");
  btn.disabled = true;
  setText("statusPill", notify ? "发送中" : "计算中");
  try {
    const resp = await fetch(notify ? "/api/notify-test" : "/api/recalc", { method: "POST" });
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
document.getElementById("seriesDate").addEventListener("change", () => {
  seriesDateTouched = true;
  refreshSeriesSafely();
});
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


DEGRADED_ALERT_TITLE = "511130 a值候选预警（降级行情）"
DEGRADED_ALERT_NOTE = "严格实时行情不可用，使用备选行情仅作候选预警；不等同严格实时a。"


def degraded_alert_enabled(config: dict) -> bool:
    return bool(config.get("degraded_alert_enabled", True))


def degraded_alert_config(config: dict) -> dict:
    degraded = dict(config)
    degraded["require_realtime_snapshot"] = False
    degraded["alert_source_mode_override"] = str(
        config.get("degraded_alert_source_mode", "degraded_price_source_v1")
    ).strip() or "degraded_price_source_v1"
    data_sources = dict(config.get("data_sources") or {})
    data_sources["intraday"] = str(config.get("degraded_intraday_source", "eastmoney_realtime_then_minute_fallback"))
    degraded["data_sources"] = data_sources
    return degraded


def run_degraded_alert_now(
    config: dict,
    context: monitor.CalculationContext,
) -> tuple[bool, str, dict | None]:
    try:
        candidate_config = degraded_alert_config(config)
        result = monitor.calculate_a_with_context(context, candidate_config)
        if result.get("strict_realtime") is True:
            try:
                monitor.validate_result_invariants(config, result)
            except Exception:  # noqa: BLE001
                pass
            else:
                monitor.handle_calculation_result(config, result, notify=True, notify_no_alert=False)
                return True, "严格实时重试成功，已按正式预警链路检查", result
        result = dict(result)
        result["data_quality"] = "degraded"
        result["data_quality_note"] = DEGRADED_ALERT_NOTE
        monitor.handle_calculation_result(
            candidate_config,
            result,
            notify=True,
            notify_no_alert=False,
            alert_title=str(config.get("degraded_alert_title", DEGRADED_ALERT_TITLE)) or DEGRADED_ALERT_TITLE,
        )
        return True, "严格实时失败，已检查降级行情候选预警（仅穿阈值才发飞书）", result
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", None


def run_once_now(
    config: dict,
    notify: bool = False,
    notify_no_alert: bool = True,
    context: monitor.CalculationContext | None = None,
) -> tuple[bool, str, dict | None]:
    try:
        if context is None:
            result = monitor.calculate_a(config["target_date"], config)
        else:
            result = monitor.calculate_a_with_context(context, config)
        monitor.validate_result_invariants(config, result)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}", None
    try:
        monitor.handle_calculation_result(config, result, notify=notify, notify_no_alert=notify_no_alert)
        return True, "ok", result
    except Exception as exc:  # noqa: BLE001
        if notify:
            return True, f"通知失败但计算成功: {type(exc).__name__}: {exc}", result
        return False, f"{type(exc).__name__}: {exc}", None


def is_pcf_not_ready_message(message: str) -> bool:
    return "PCF未更新或不可读" in message or "PCF_NOT_READY" in message or "清单未更新或不可读" in message


def pcf_retry_remaining_seconds(state: DashboardState, *, now: float | None = None) -> int:
    if state.pcf_retry_at <= 0:
        return 0
    current = time.time() if now is None else now
    remaining = int(state.pcf_retry_at - current)
    return max(0, remaining)


def maybe_defer_pcf_retry(
    state: DashboardState,
    config: dict,
    message: str,
    *,
    now: float | None = None,
) -> bool:
    if not is_pcf_not_ready_message(message):
        return False
    current = time.time() if now is None else now
    seconds = int(config.get("pcf_not_ready_retry_seconds", 300))
    seconds = max(30, seconds)
    state.pcf_retry_at = current + seconds
    state.last_run_message = f"PCF未就绪，{seconds}秒后重试"
    return True


def is_persistent_input_error_message(message: str) -> bool:
    fragments = [
        "缺少逐券应计利息",
        "PCF成分券结构变化",
        "CreationRedemptionUnit变化",
        "利息超出安全范围",
        "利息来自历史缓存",
    ]
    return any(fragment in message for fragment in fragments)


def auto_error_notify_key(state: DashboardState, message: str) -> str:
    # Normalize volatile wrapper text while preserving the date and underlying root cause.
    normalized = str(message).strip()
    if normalized.startswith("RuntimeError: "):
        normalized = normalized[len("RuntimeError: "):]
    return f"{state.date}|{normalized}"


def maybe_notify_auto_error(state: DashboardState, config: dict, message: str, *, now: float | None = None) -> bool:
    # Feishu is reserved for valid threshold alerts; runtime/data errors stay internal.
    return False


def send_notify_test_now(state: DashboardState, config: dict) -> tuple[bool, str]:
    title = "511130 预警机器人测试"
    text = (
        "飞书预警联通测试（不依赖PCF/价格）\n"
        f"日期: {state.date}\n"
        f"时间: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
        "如果你看到这条消息，说明 webhook 到达正常。"
    )
    notification_result: dict | bool | None = None
    try:
        notification_result = monitor.send_notification(config, title, text)
        monitor.safe_append_notification_event(
            state.date,
            title=title,
            result=None,
            alerts=[],
            status="sent",
            notification_result=notification_result,
        )
        state.last_run_message = "飞书测试已发送"
        state.last_error = ""
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        monitor.safe_append_notification_event(
            state.date,
            title=title,
            result=None,
            alerts=[],
            status="failed",
            notification_result=notification_result,
            error=exc,
        )
        state.last_run_message = "飞书测试失败"
        state.last_error = f"{type(exc).__name__}: {exc}"
        return False, state.last_error


def run_auto_iteration(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
    *,
    first_pass: bool,
) -> tuple[bool, int]:
    maybe_roll_auto_target_date(state, config, context_ref)
    market_wait = maybe_pause_auto_run_outside_market(state, config)
    if market_wait > 0:
        return first_pass, market_wait
    pcf_remaining = pcf_retry_remaining_seconds(state)
    if pcf_remaining > 0:
        state.last_run_message = f"PCF未就绪，{pcf_remaining}秒后重试"
        return first_pass, pcf_remaining
    if context_ref.get("value") is None:
        error = maybe_prepare_auto_context(
            state,
            config,
            context_ref,
            success_message="已预加载PCF和逐券利息，开始实时计算",
            failure_message="预加载PCF/逐券利息失败",
        )
        if error:
            state.auto_error_count += 1
            if not maybe_defer_pcf_retry(state, config, error):
                state.last_run_message = "预加载PCF/逐券利息失败"
            return False, state.interval_seconds
    notify_no_alert = False
    ok, msg, result = run_once_now(
        config,
        notify=True,
        notify_no_alert=notify_no_alert,
        context=context_ref.get("value"),
    )
    if ok:
        state.latest_result = result
        state.last_error = ""
        state.auto_error_count = 0
        state.pcf_retry_at = 0.0
        if msg != "ok":
            state.last_run_message = msg
        elif state.auto_run_notify and first_pass:
            state.last_run_message = "自动计算成功（未触发阈值不发飞书；触发阈值才预警）"
        else:
            state.last_run_message = f"自动计算成功: {datetime.now(TZ).strftime('%H:%M:%S')}"
    else:
        state.auto_error_count += 1
        state.last_error = msg
        if not maybe_defer_pcf_retry(state, config, msg):
            context = context_ref.get("value")
            if degraded_alert_enabled(config) and context is not None:
                degraded_ok, degraded_msg, degraded_result = run_degraded_alert_now(config, context)
                if degraded_ok:
                    if (
                        isinstance(degraded_result, dict)
                        and degraded_result.get("strict_realtime") is True
                        and snapshot_allowed(degraded_result, state.allowed_price_sources)
                    ):
                        state.latest_result = degraded_result
                        state.last_error = ""
                        state.auto_error_count = 0
                        state.pcf_retry_at = 0.0
                    state.last_run_message = degraded_msg
                else:
                    state.last_error = f"{msg}; 降级候选预警失败: {degraded_msg}"
                    state.last_run_message = "自动计算失败；降级候选预警也不可用"
            else:
                state.last_run_message = "自动计算失败"
    return False, state.interval_seconds


def make_handler(
    state: DashboardState,
    config: dict,
    context_ref: dict[str, monitor.CalculationContext | None],
):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json_error(self, route: str, exc: Exception) -> None:
            message = f"{type(exc).__name__}: {exc}"
            print(f"WARN: HTTP处理失败 {route}: {message}", file=sys.stderr)
            self._json(
                {
                    "ok": False,
                    "service": "511130-live-dashboard",
                    "route": route,
                    "date": state.date,
                    "error": message,
                    "last_run_message": state.last_run_message,
                    "last_error": state.last_error,
                }
            )

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
            try:
                maybe_roll_auto_target_date(
                    state,
                    config,
                    context_ref,
                    prepare_context=parsed.path != "/health",
                )
                self._handle_get(parsed)
            except Exception as exc:  # noqa: BLE001
                if parsed.path == "/":
                    self.send_error(500, f"{type(exc).__name__}: {exc}")
                else:
                    self._json_error(parsed.path, exc)

        def _handle_get(self, parsed) -> None:  # noqa: ANN001
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
                payload = build_health_payload(state, config)
                self._json(payload, status=200 if payload.get("ok") else 503)
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
            try:
                maybe_roll_auto_target_date(state, config, context_ref)
                self._handle_post(parsed)
            except Exception as exc:  # noqa: BLE001
                self._json_error(parsed.path, exc)

        def _handle_post(self, parsed) -> None:  # noqa: ANN001
            if state.public_readonly:
                self._json({"ok": False, "message": "", "warning": "", "error": "公网只读模式已关闭写操作"})
                return
            if parsed.path == "/api/recalc":
                query = parse_qs(parsed.query)
                notify = query.get("notify", ["0"])[0] in {"1", "true", "True", "yes", "on"}
                ok, msg, result = run_once_now(
                    config,
                    notify=notify,
                    notify_no_alert=False,
                    context=context_ref.get("value"),
                )
                if ok:
                    state.latest_result = result
                    state.last_error = ""
                    if msg == "ok":
                        state.last_run_message = f"手动计算成功: {datetime.now(TZ).strftime('%H:%M:%S')}"
                    else:
                        state.last_run_message = msg
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
            if parsed.path == "/api/notify-test":
                ok, msg = send_notify_test_now(state, config)
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
        wait_seconds = state.interval_seconds
        try:
            state.last_auto_tick_at = time.time()
            first_pass, wait_seconds = run_auto_iteration(state, config, context_ref, first_pass=first_pass)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            state.auto_error_count += 1
            state.last_error = msg
            state.last_run_message = "自动线程异常，下一轮继续"
        stop_event.wait(min(state.interval_seconds, max(1, int(wait_seconds))))


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
    parser.add_argument("--interval", type=int, default=3, help="Refresh interval for browser and auto-run (seconds, minimum 1)")
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
        target_date, _ = monitor.resolve_target_date(args.date)
        config["target_date"] = target_date
        config["target_date_mode"] = "fixed"
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
        maybe_prepare_auto_context(state, config, context_ref)

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
