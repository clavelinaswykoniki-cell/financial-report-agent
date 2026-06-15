#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import hashlib
import hmac
import importlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi import requests as curl_requests

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[1]
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
RUNS_DIR = Path(os.environ.get("A_MONITOR_RUNS_DIR", BASE / "runs")).expanduser()
RUNS_DIR.mkdir(parents=True, exist_ok=True)

BOSERA_PCF_URL = "https://www.bosera.com/jjcp/etf/files/{fund}/{year}/ssepcf_{fund}_{date}.xml"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EASTMONEY_SNAPSHOT_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/CN_MarketDataService.getKLineData"
SINA_SNAPSHOT_URL = "https://hq.sinajs.cn/list={symbols}"
ETF_CODE = "511130"
TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class Component:
    code: str
    name: str
    pcf_quantity: Decimal

    @property
    def units(self) -> Decimal:
        return self.pcf_quantity * Decimal("10")


@dataclass(frozen=True)
class Pcf:
    trading_day: str
    record_number: int
    estimated_cash_component: Decimal
    pre_cash_component: Decimal
    creation_redemption_unit: Decimal
    components: list[Component]


@dataclass(frozen=True)
class CalculationContext:
    date: str
    pcf: Pcf
    interests: dict[str, tuple[Decimal, str]]
    codes: list[str]


def dec(value) -> Decimal:
    if value is None:
        return Decimal("NaN")
    text = str(value).strip().replace(",", "").replace("￥", "").replace("%", "")
    if text in {"", "-", "None", "nan"}:
        return Decimal("NaN")
    return Decimal(text)


def q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q3(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def money(value: Decimal | None, signed: bool = False) -> str:
    if value is None or value.is_nan():
        return "-"
    sign = "+" if signed and value >= 0 else ""
    return f"{sign}{q2(value):,.2f}"


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def load_local_env() -> None:
    for path in (BASE / ".env", BASE / ".env.local"):
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_config() -> dict:
    load_local_env()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    target_date = os.environ.get("A_MONITOR_TARGET_DATE", "").strip()
    if target_date:
        config["target_date"] = target_date
    threshold_text = os.environ.get("A_MONITOR_THRESHOLDS", "").strip()
    if threshold_text:
        config["thresholds"] = [int(part.strip()) for part in threshold_text.split(",") if part.strip()]
    expected_codes_text = os.environ.get("A_MONITOR_EXPECTED_COMPONENT_CODES", "").strip()
    if expected_codes_text:
        config["expected_component_codes"] = [
            part.strip() for part in expected_codes_text.replace("，", ",").split(",") if part.strip()
        ]
    interest_json = os.environ.get("A_MONITOR_INTEREST_OVERRIDES_JSON", "").strip()
    if interest_json:
        overrides = json.loads(interest_json)
        if not isinstance(overrides, dict):
            raise RuntimeError("A_MONITOR_INTEREST_OVERRIDES_JSON 必须是 JSON object")
        current = config.setdefault("interest_overrides", {})
        for date_key, date_values in overrides.items():
            if not isinstance(date_values, dict):
                raise RuntimeError(f"A_MONITOR_INTEREST_OVERRIDES_JSON[{date_key}] 必须是 object")
            current[str(date_key)] = {str(k): str(v) for k, v in date_values.items()}
    date_for_single = str(config.get("target_date", "")).strip()
    single_overrides = {
        "019776": os.environ.get("A_MONITOR_INTEREST_019776", "").strip(),
        "019837": os.environ.get("A_MONITOR_INTEREST_019837", "").strip(),
    }
    if date_for_single and any(single_overrides.values()):
        target_overrides = config.setdefault("interest_overrides", {}).setdefault(date_for_single, {})
        for code, value in single_overrides.items():
            if value:
                target_overrides[code] = value
    return config


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"dates": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_interest_cache() -> dict:
    return load_state().get("interest_cache") or {}


def save_interest_cache(interests: dict[str, tuple[Decimal, str]]) -> None:
    state = load_state()
    cache = state.setdefault("interest_cache", {})
    for code, (value, source) in interests.items():
        if str(source).startswith("missing_interest_default"):
            continue
        cache[code] = {
            "value": str(value),
            "source": source,
            "updated_at": datetime.now(TZ).strftime("%Y%m%d"),
        }
    state["interest_cache"] = cache
    save_state(state)


def request_text(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = 20,
    attempts: int = 3,
) -> str:
    merged = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", **(headers or {})}
    last = None
    for _ in range(max(1, attempts)):
        try:
            response = curl_requests.get(url, params=params, headers=merged, impersonate="chrome", timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.35)
    raise RuntimeError(f"读取失败: {url}: {last}")


def request_json(
    url: str,
    *,
    params: dict,
    headers: dict | None = None,
    timeout: float = 20,
    attempts: int = 3,
) -> dict:
    text = request_text(url, params=params, headers=headers, timeout=timeout, attempts=attempts)
    return json.loads(text)


def post_json(url: str, payload: dict, *, timeout: float = 15) -> dict:
    response = curl_requests.post(
        url,
        json=payload,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        impersonate="chrome",
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return {}


def validate_pcf(pcf: Pcf, config: dict | None = None) -> None:
    if pcf.trading_day.strip() == "":
        raise RuntimeError("PCF缺少TradingDay")
    ensure_decimal("CreationRedemptionUnit", pcf.creation_redemption_unit)
    if pcf.creation_redemption_unit != Decimal("10000"):
        raise RuntimeError(f"CreationRedemptionUnit变化，拒绝自动计算: {pcf.creation_redemption_unit}")
    ensure_decimal("EstimatedCashComponent", pcf.estimated_cash_component)
    ensure_decimal("PreCashComponent", pcf.pre_cash_component)
    if pcf.record_number != len(pcf.components):
        raise RuntimeError(f"PCF RecordNumber与成分券数量不一致: {pcf.record_number} != {len(pcf.components)}")
    seen: set[str] = set()
    for component in pcf.components:
        if not component.code:
            raise RuntimeError("PCF成分券代码为空")
        if component.code in seen:
            raise RuntimeError(f"PCF成分券重复: {component.code}")
        seen.add(component.code)
        ensure_decimal(f"{component.code} PCF数量", component.pcf_quantity)
        if component.pcf_quantity <= 0:
            raise RuntimeError(f"{component.code} PCF数量无效: {component.pcf_quantity}")
    expected_codes = (config or {}).get("expected_component_codes") or []
    if expected_codes:
        expected = {str(code).strip() for code in expected_codes if str(code).strip()}
        actual = {component.code for component in pcf.components}
        if actual != expected:
            raise RuntimeError(f"PCF成分券结构变化，拒绝自动计算: expected={sorted(expected)}, actual={sorted(actual)}")


def validate_interest_value(code: str, interest: Decimal, source: str) -> None:
    ensure_decimal(f"{code}利息", interest)
    if interest < Decimal("0") or interest > Decimal("5"):
        raise RuntimeError(f"{code}利息超出安全范围: {interest} ({source})")
    if str(source).startswith("cached_interest"):
        raise RuntimeError(f"{code}利息来自历史缓存，拒绝作为当前a计算")


def send_notification(config: dict, title: str, text: str) -> dict | bool:
    load_local_env()
    notification = config.get("notification") or {}
    url_env = notification.get("webhook_url_env", "A_MONITOR_WEBHOOK_URL")
    kind_env = notification.get("webhook_kind_env", "A_MONITOR_WEBHOOK_KIND")
    url = os.environ.get(url_env, "").strip() or str(notification.get("webhook_url", "")).strip()
    if not url:
        raise RuntimeError(f"通知失败: 未配置 {url_env} 或 notification.webhook_url")
    kind = os.environ.get(kind_env, "").strip().lower() or notification.get("default_webhook_kind", "generic")
    if kind == "feishu":
        payload = {"msg_type": "text", "content": {"text": f"{title}\n{text}"}}
        secret_env = notification.get("feishu_secret_env", "A_MONITOR_FEISHU_SECRET")
        secret = os.environ.get(secret_env, "").strip()
        if secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{secret}"
            sign = base64.b64encode(
                hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            ).decode("utf-8")
            payload["timestamp"] = timestamp
            payload["sign"] = sign
    elif kind in {"wecom", "wechat_work", "enterprise_wechat"}:
        payload = {"msgtype": "text", "text": {"content": f"{title}\n{text}"}}
    else:
        payload = {"title": title, "text": text, "content": text}
    timeout = dec(notification.get("timeout_seconds", config.get("notification_timeout_seconds", "5")))
    started = time.perf_counter()
    result = post_json(url, payload, timeout=float(timeout))
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if kind == "feishu":
        code = result.get("code", result.get("StatusCode", 0))
        if str(code) != "0":
            message = result.get("msg") or result.get("StatusMessage") or result
            raise RuntimeError(f"飞书通知失败: code={code}, msg={message}")
    elif kind in {"wecom", "wechat_work", "enterprise_wechat"}:
        code = result.get("errcode", 0)
        if str(code) != "0":
            raise RuntimeError(f"企业微信通知失败: errcode={code}, errmsg={result.get('errmsg', result)}")
    return {
        "ok": True,
        "kind": kind,
        "sent_at": datetime.now(TZ).isoformat(timespec="milliseconds"),
        "elapsed_ms": elapsed_ms,
        "response": result,
    }


def fetch_pcf(date_compact: str, fund_code: str = ETF_CODE) -> Pcf:
    url = BOSERA_PCF_URL.format(fund=fund_code, year=date_compact[:4], date=date_compact)
    response = curl_requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, impersonate="chrome", timeout=20)
    if response.status_code != 200 or b"<SSEPortfolioCompositionFile>" not in response.content:
        raise RuntimeError(f"PCF未更新或不可读: {date_compact}; HTTP {response.status_code}")
    root = ET.fromstring(response.content)

    def text(tag: str) -> str:
        node = root.find(tag)
        return node.text.strip() if node is not None and node.text else ""

    components: list[Component] = []
    for node in root.findall("./ComponentList/Component"):
        components.append(
            Component(
                code=(node.findtext("InstrumentID") or "").strip(),
                name=(node.findtext("InstrumentName") or "").strip(),
                pcf_quantity=dec(node.findtext("Quantity") or "0"),
            )
        )
    return Pcf(
        trading_day=text("TradingDay"),
        record_number=int(text("RecordNumber") or len(components)),
        estimated_cash_component=dec(text("EstimatedCashComponent")),
        pre_cash_component=dec(text("PreCashComponent")),
        creation_redemption_unit=dec(text("CreationRedemptionUnit") or "10000"),
        components=components,
    )


def fetch_sse_interest(date_compact: str, code: str) -> tuple[Decimal, str]:
    date_iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
    payload = request_json(
        SSE_QUERY_URL,
        params={
            "isPagination": "true",
            "pageHelp.pageSize": "25",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": "COMMON_SSE_SJ_ZQSJ_JJYQJ_L",
            "SEARCH_DATE": date_iso,
            "SEC_CODE": code,
        },
        headers={"Referer": "https://www.sse.com.cn/market/bonddata/netfull/"},
    )
    rows = payload.get("result") or []
    if not rows:
        raise RuntimeError(f"上交所净价与全价未返回 {date_iso} {code}")
    return dec(rows[0].get("ACCR_INT_AMT")), "sse_netfull"


def get_interests(date_compact: str, pcf: Pcf, config: dict) -> dict[str, tuple[Decimal, str]]:
    overrides = ((config.get("interest_overrides") or {}).get(date_compact) or {})
    allow_cache_fallback = bool(config.get("allow_interest_fallback", False))
    cached_interests = load_interest_cache() if allow_cache_fallback else {}
    interests: dict[str, tuple[Decimal, str]] = {}
    missing: list[str] = []
    for component in pcf.components:
        if component.code in overrides and str(overrides[component.code]).strip():
            interests[component.code] = (dec(overrides[component.code]), "manual_trading_software_override")
            continue
        try:
            interests[component.code] = fetch_sse_interest(date_compact, component.code)
        except Exception as exc:  # noqa: BLE001
            missing.append(f"{component.code}({exc})")
    if allow_cache_fallback:
        fallbacked: list[str] = []
        if missing:
            for msg in missing[:]:
                code = msg.split("(", 1)[0]
                if code in cached_interests and str(cached_interests[code].get("value", "")).strip():
                    entry = cached_interests[code]
                    interests[code] = (dec(entry.get("value")), f"cached_interest_{entry.get('updated_at', 'unknown')}")
                    fallbacked.append(code)
                    missing.remove(msg)
        if fallbacked:
            print(f"WARN: {', '.join(fallbacked)} 利息采用历史缓存，可能与今日实时值有偏差")
    if missing and bool(config.get("allow_missing_interest_fallback", False)):
        default_value = dec(config.get("missing_interest_default", "0"))
        for msg in missing[:]:
            code = msg.split("(", 1)[0]
            interests[code] = (default_value, f"missing_interest_default_{default_value}")
            missing.remove(msg)
            print(f"WARN: {code} 利息缺失，已按默认值 {default_value} 兜底")
    if missing:
        raise RuntimeError("缺少逐券应计利息；请在config.json填交易软件周一利息: " + "; ".join(missing))
    return interests


def fetch_eastmoney_1m(code: str) -> dict[str, Decimal]:
    payload = request_json(
        EASTMONEY_TRENDS_URL,
        params={
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "ndays": "5",
            "iscr": "0",
            "secid": f"1.{code}",
        },
        headers={"Referer": "https://quote.eastmoney.com/"},
    )
    trends = (payload.get("data") or {}).get("trends") or []
    rows: dict[str, Decimal] = {}
    for item in trends:
        parts = item.split(",")
        if len(parts) >= 3:
            rows[parts[0][:16]] = dec(parts[2])
    return rows


def parse_sina_jsonp(text: str) -> list[dict]:
    match = re.search(r"=\((.*)\);?\s*$", text, re.S)
    if not match:
        raise RuntimeError(f"Sina JSONP格式异常: {text[:100]}")
    return json.loads(match.group(1)) or []


def fetch_sina_kline(code: str, scale: str = "5") -> dict[str, Decimal]:
    symbol = f"sh{code}"
    text = request_text(
        SINA_KLINE_URL,
        params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": "200"},
        headers={"Referer": "https://finance.sina.com.cn/"},
    )
    rows = parse_sina_jsonp(text)
    return {
        row["day"][:16]: dec(row["close"])
        for row in rows
        if len(row.get("day", "")) >= 16
    }


def fetch_eastmoney_realtime_prices(date_compact: str, codes: list[str], config: dict) -> tuple[str, str, dict[str, Decimal], dict]:
    max_skew_seconds = int(config.get("realtime_max_skew_seconds", 3))
    max_stale_seconds = int(config.get("realtime_max_stale_seconds", 30))
    timeout_seconds = float(config.get("realtime_request_timeout_seconds", 2))
    attempts = int(config.get("realtime_request_attempts", 1))
    payload = request_json(
        EASTMONEY_SNAPSHOT_URL,
        params={
            "fltt": "2",
            "secids": ",".join(f"1.{code}" for code in codes),
            "fields": "f12,f14,f2,f124",
        },
        headers={"Referer": "https://quote.eastmoney.com/"},
        timeout=timeout_seconds,
        attempts=attempts,
    )
    rows = ((payload.get("data") or {}).get("diff") or [])
    by_code = {str(row.get("f12")): row for row in rows}
    missing = [code for code in codes if code not in by_code]
    if missing:
        raise RuntimeError(f"东方财富实时快照缺少: {missing}")
    stamps = []
    prices: dict[str, Decimal] = {}
    quote_times: dict[str, str] = {}
    for code in codes:
        row = by_code[code]
        price = dec(row.get("f2"))
        ensure_decimal(f"{code}实时价", price)
        if price <= 0:
            raise RuntimeError(f"{code}实时价无效: {price}")
        stamp = int(row.get("f124") or 0)
        if stamp <= 0:
            raise RuntimeError(f"{code}实时行情时间戳无效")
        dt = datetime.fromtimestamp(stamp, TZ)
        if dt.strftime("%Y%m%d") != date_compact:
            raise RuntimeError(f"{code}实时行情日期不匹配: {dt.strftime('%Y%m%d')}")
        stamps.append(stamp)
        quote_times[code] = dt.strftime("%Y-%m-%d %H:%M:%S")
        prices[code] = price
    skew_seconds = max(stamps) - min(stamps)
    if skew_seconds > max_skew_seconds:
        raise RuntimeError(f"三只证券实时行情不同步: 时间差 {skew_seconds} 秒")
    now_stamp = int(datetime.now(TZ).timestamp())
    if now_stamp - max(stamps) > max_stale_seconds:
        raise RuntimeError(f"实时行情过旧: {now_stamp - max(stamps)} 秒前")
    ts = datetime.fromtimestamp(max(stamps), TZ).strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "strict_realtime": True,
        "quote_times": quote_times,
        "quote_skew_seconds": skew_seconds,
        "max_stale_seconds": max_stale_seconds,
        "max_skew_seconds": max_skew_seconds,
    }
    return "realtime_eastmoney", ts, prices, meta


def fetch_sina_realtime_prices(date_compact: str, codes: list[str], config: dict) -> tuple[str, str, dict[str, Decimal], dict]:
    max_skew_seconds = int(config.get("realtime_max_skew_seconds", 3))
    max_stale_seconds = int(config.get("realtime_max_stale_seconds", 30))
    timeout_seconds = float(config.get("realtime_request_timeout_seconds", 4))
    attempts = int(config.get("realtime_request_attempts", 1))
    symbols = ",".join(f"sh{code}" for code in codes)
    text = request_text(
        SINA_SNAPSHOT_URL.format(symbols=symbols),
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=timeout_seconds,
        attempts=attempts,
    )
    rows: dict[str, list[str]] = {}
    for match in re.finditer(r'var hq_str_sh(\d+)="(.*?)";', text):
        rows[match.group(1)] = match.group(2).split(",")
    missing = [code for code in codes if code not in rows]
    if missing:
        raise RuntimeError(f"新浪实时快照缺少: {missing}")
    prices: dict[str, Decimal] = {}
    stamps = []
    quote_times: dict[str, str] = {}
    for code in codes:
        parts = rows[code]
        if len(parts) < 32:
            raise RuntimeError(f"{code}新浪实时行情字段不足")
        price = dec(parts[3])
        ensure_decimal(f"{code}新浪实时价", price)
        if price <= 0:
            raise RuntimeError(f"{code}新浪实时价无效: {price}")
        dt = datetime.strptime(f"{parts[30]} {parts[31]}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        if dt.strftime("%Y%m%d") != date_compact:
            raise RuntimeError(f"{code}新浪实时行情日期不匹配: {dt.strftime('%Y%m%d')}")
        stamp = int(dt.timestamp())
        stamps.append(stamp)
        quote_times[code] = dt.strftime("%Y-%m-%d %H:%M:%S")
        prices[code] = price
    skew_seconds = max(stamps) - min(stamps)
    if skew_seconds > max_skew_seconds:
        raise RuntimeError(f"三只证券新浪实时行情不同步: 时间差 {skew_seconds} 秒")
    now_stamp = int(datetime.now(TZ).timestamp())
    if now_stamp - max(stamps) > max_stale_seconds:
        raise RuntimeError(f"新浪实时行情过旧: {now_stamp - max(stamps)} 秒前")
    ts = datetime.fromtimestamp(max(stamps), TZ).strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "strict_realtime": True,
        "quote_times": quote_times,
        "quote_skew_seconds": skew_seconds,
        "max_stale_seconds": max_stale_seconds,
        "max_skew_seconds": max_skew_seconds,
    }
    return "realtime_sina_snapshot", ts, prices, meta


def _qmt_symbol(code: str) -> str:
    return f"{code}.SH"


def _parse_quote_time(value) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("行情时间为空")
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = Decimal(text)
        if number > Decimal("1000000000000"):
            return datetime.fromtimestamp(float(number / Decimal("1000")), TZ)
        if number > Decimal("1000000000"):
            return datetime.fromtimestamp(float(number), TZ)
        if len(text.split(".", 1)[0]) == 14:
            return datetime.strptime(text.split(".", 1)[0], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y%m%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    raise RuntimeError(f"无法解析行情时间: {value}")


def fetch_xtquant_realtime_prices(date_compact: str, codes: list[str], config: dict) -> tuple[str, str, dict[str, Decimal], dict]:
    if not bool(config.get("enable_xtquant_snapshot", False)):
        raise RuntimeError("xtquant实时源未启用")
    max_skew_seconds = int(config.get("realtime_max_skew_seconds", 3))
    max_stale_seconds = int(config.get("realtime_max_stale_seconds", 30))
    try:
        xtdata = importlib.import_module("xtquant.xtdata")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"xtquant.xtdata不可用: {exc}") from exc
    symbols = [_qmt_symbol(code) for code in codes]
    ticks = xtdata.get_full_tick(symbols)
    if not isinstance(ticks, dict):
        raise RuntimeError("xtquant get_full_tick 未返回dict")
    prices: dict[str, Decimal] = {}
    stamps = []
    quote_times: dict[str, str] = {}
    for code, symbol in zip(codes, symbols):
        tick = ticks.get(symbol) or ticks.get(code)
        if not isinstance(tick, dict):
            raise RuntimeError(f"xtquant缺少tick: {symbol}")
        price = dec(tick.get("lastPrice") or tick.get("last_price") or tick.get("price"))
        ensure_decimal(f"{code} xtquant实时价", price)
        if price <= 0:
            raise RuntimeError(f"{code} xtquant实时价无效: {price}")
        raw_time = (
            tick.get("time")
            or tick.get("timetag")
            or tick.get("stime")
            or tick.get("datetime")
            or tick.get("dateTime")
        )
        dt = _parse_quote_time(raw_time)
        if dt.strftime("%Y%m%d") != date_compact:
            raise RuntimeError(f"{code} xtquant实时行情日期不匹配: {dt.strftime('%Y%m%d')}")
        stamp = int(dt.timestamp())
        stamps.append(stamp)
        quote_times[code] = dt.strftime("%Y-%m-%d %H:%M:%S")
        prices[code] = price
    skew_seconds = max(stamps) - min(stamps)
    if skew_seconds > max_skew_seconds:
        raise RuntimeError(f"三只证券xtquant实时行情不同步: 时间差 {skew_seconds} 秒")
    now_stamp = int(datetime.now(TZ).timestamp())
    if now_stamp - max(stamps) > max_stale_seconds:
        raise RuntimeError(f"xtquant实时行情过旧: {now_stamp - max(stamps)} 秒前")
    ts = datetime.fromtimestamp(max(stamps), TZ).strftime("%Y-%m-%d %H:%M:%S")
    meta = {
        "strict_realtime": True,
        "quote_times": quote_times,
        "quote_skew_seconds": skew_seconds,
        "max_stale_seconds": max_stale_seconds,
        "max_skew_seconds": max_skew_seconds,
    }
    return "realtime_xtquant", ts, prices, meta


def fetch_aligned_prices(date_compact: str, codes: list[str], config: dict) -> tuple[str, str, dict[str, Decimal], dict]:
    if bool(config.get("prefer_realtime_snapshot", True)):
        realtime_errors = []
        fetchers = {
            "xtquant": fetch_xtquant_realtime_prices,
            "eastmoney": fetch_eastmoney_realtime_prices,
            "sina": fetch_sina_realtime_prices,
        }
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(fetchers))
        try:
            futures = {
                executor.submit(fetcher, date_compact, codes, config): name
                for name, fetcher in fetchers.items()
            }
            for future in concurrent.futures.as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()
                    return result
                except Exception as exc:  # noqa: BLE001
                    realtime_errors.append(f"{name}: {exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if bool(config.get("require_realtime_snapshot", False)):
            raise RuntimeError("实时快照不可用，拒绝使用分钟线计算a: " + " | ".join(realtime_errors))
        print(f"WARN: 实时快照不可用，降级到分钟行情: {' | '.join(realtime_errors)}", file=sys.stderr)
    date_iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
    try:
        maps = {code: fetch_eastmoney_1m(code) for code in codes}
        common = set.intersection(*[{ts for ts in price_map if ts.startswith(date_iso) and ts <= f"{date_iso} 15:00"} for price_map in maps.values()])
        common = {ts for ts in common if not ts.endswith("09:30")}
        if common:
            ts = sorted(common)[-1]
            return "1m_eastmoney", ts, {code: maps[code][ts] for code in codes}, {"strict_realtime": False}
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: 东方财富1分钟行情不可用，降级到新浪5分钟: {exc}", file=sys.stderr)

    maps = {code: fetch_sina_kline(code, "5") for code in codes}
    common = set.intersection(*[{ts for ts in price_map if ts.startswith(date_iso) and ts <= f"{date_iso} 15:00"} for price_map in maps.values()])
    common = {ts for ts in common if not ts.endswith("09:30")}
    if common:
        ts = sorted(common)[-1]
        return "5m_sina", ts, {code: maps[code][ts] for code in codes}, {"strict_realtime": False}
    raise RuntimeError(f"没有找到 {date_iso} 的共同分钟行情点")


def compare_pcfs(current: Pcf, base: Pcf) -> list[str]:
    lines = []
    current_map = {c.code: c for c in current.components}
    base_map = {c.code: c for c in base.components}
    if set(current_map) == set(base_map):
        lines.append("成分券代码: 与对比日一致")
    else:
        added = sorted(set(current_map) - set(base_map))
        removed = sorted(set(base_map) - set(current_map))
        lines.append(f"成分券代码: 变化; 新增={added or '-'}; 删除={removed or '-'}")
    for code in sorted(set(current_map) | set(base_map)):
        cur = current_map.get(code)
        old = base_map.get(code)
        if cur and old:
            marker = "一致" if cur.pcf_quantity == old.pcf_quantity else "变化"
            lines.append(f"{code}: 数量 {old.pcf_quantity} -> {cur.pcf_quantity} ({marker})")
        elif cur:
            lines.append(f"{code}: 新增 数量 {cur.pcf_quantity}")
        elif old:
            lines.append(f"{code}: 删除 原数量 {old.pcf_quantity}")
    lines.append(f"EstimatedCashComponent: {money(base.estimated_cash_component)} -> {money(current.estimated_cash_component)}")
    return lines


def ensure_decimal(name: str, value: Decimal) -> None:
    if value.is_nan():
        raise RuntimeError(f"{name} 不是有效数字")


def calculate_estimated_a_from_inputs(
    *,
    etf_quote: Decimal,
    estimated_cash_component: Decimal,
    creation_redemption_unit: Decimal = Decimal("10000"),
    component_inputs: list[dict],
) -> dict:
    ensure_decimal("511130价格", etf_quote)
    ensure_decimal("EstimatedCashComponent", estimated_cash_component)
    ensure_decimal("CreationRedemptionUnit", creation_redemption_unit)
    if creation_redemption_unit != Decimal("10000"):
        raise RuntimeError(f"CreationRedemptionUnit变化，拒绝自动计算: {creation_redemption_unit}")
    etf_value = etf_quote / Decimal("100") * creation_redemption_unit * Decimal("100")
    component_value = Decimal("0")
    component_rows = []
    for item in component_inputs:
        code = item["code"]
        price = item["price"]
        interest = item["interest"]
        units = item["units"]
        ensure_decimal(f"{code}净价", price)
        ensure_decimal(f"{code}利息", interest)
        ensure_decimal(f"{code}数量", units)
        interest_source = item["interest_source"]
        if str(interest_source).startswith("missing_interest_default"):
            raise RuntimeError(f"{code} 利息缺失，拒绝用默认值计算a")
        validate_interest_value(code, interest, interest_source)
        value = (price + interest) * units
        component_value += value
        component_rows.append(
            {
                "code": code,
                "name": item["name"],
                "pcf_quantity": str(item["pcf_quantity"]),
                "units": str(units),
                "price": str(q3(price)),
                "interest": str(q3(interest)),
                "interest_source": interest_source,
                "value": str(q2(value)),
            }
        )
    basket = component_value + estimated_cash_component
    estimated_a = etf_value - basket
    formula_error = etf_value - component_value - estimated_cash_component - estimated_a
    if abs(formula_error) > Decimal("0.005"):
        raise RuntimeError(f"公式自检失败: {formula_error}")
    return {
        "formula_version": "net_price_plus_accrued_interest_v1",
        "creation_redemption_unit": creation_redemption_unit,
        "etf_value": etf_value,
        "component_value": component_value,
        "basket": basket,
        "estimated_a": estimated_a,
        "component_rows": component_rows,
    }


def prepare_calculation_context(date_compact: str, config: dict) -> CalculationContext:
    pcf = fetch_pcf(date_compact, config.get("fund_code", ETF_CODE))
    validate_pcf(pcf, config)
    if pcf.trading_day != date_compact:
        raise RuntimeError(f"PCF日期不匹配: 期望{date_compact}, 实际{pcf.trading_day}")
    interests = get_interests(date_compact, pcf, config)
    codes = [ETF_CODE] + [c.code for c in pcf.components]
    return CalculationContext(date=date_compact, pcf=pcf, interests=interests, codes=codes)


def calculate_a_with_context(context: CalculationContext, config: dict) -> dict:
    pcf = context.pcf
    interests = context.interests
    started = time.perf_counter()
    price_source, ts, prices, quote_meta = fetch_aligned_prices(context.date, context.codes, config)
    etf_quote = prices[ETF_CODE]
    component_inputs = []
    for component in pcf.components:
        price = prices[component.code]
        interest, interest_source = interests[component.code]
        component_inputs.append(
            {
                "code": component.code,
                "name": component.name,
                "pcf_quantity": component.pcf_quantity,
                "units": component.units,
                "price": price,
                "interest": interest,
                "interest_source": interest_source,
            }
        )
    formula = calculate_estimated_a_from_inputs(
        etf_quote=etf_quote,
        estimated_cash_component=pcf.estimated_cash_component,
        creation_redemption_unit=pcf.creation_redemption_unit,
        component_inputs=component_inputs,
    )
    component_rows = formula["component_rows"]
    calculation_elapsed_ms = int((time.perf_counter() - started) * 1000)
    save_interest_cache(
        {row["code"]: (dec(row["interest"]), row["interest_source"]) for row in component_rows}
    )
    return {
        "date": context.date,
        "timestamp": ts,
        "price_source": price_source,
        "strict_realtime": quote_meta.get("strict_realtime", False),
        "quote_times": quote_meta.get("quote_times", {}),
        "quote_skew_seconds": quote_meta.get("quote_skew_seconds"),
        "calculated_at": datetime.now(TZ).isoformat(timespec="milliseconds"),
        "calculation_elapsed_ms": calculation_elapsed_ms,
        "formula_version": formula["formula_version"],
        "creation_redemption_unit": str(q2(formula["creation_redemption_unit"])),
        "etf_quote": str(q3(etf_quote)),
        "etf_value": str(q2(formula["etf_value"])),
        "estimated_cash": str(q2(pcf.estimated_cash_component)),
        "component_value_ex_cash": str(q2(formula["component_value"])),
        "basket_value": str(q2(formula["basket"])),
        "estimated_a": str(q2(formula["estimated_a"])),
        "record_number": pcf.record_number,
        "components": component_rows,
    }


def calculate_a(date_compact: str, config: dict) -> dict:
    context = prepare_calculation_context(date_compact, config)
    return calculate_a_with_context(context, config)


def append_result(date_compact: str, result: dict) -> None:
    day_dir = RUNS_DIR / date_compact
    day_dir.mkdir(parents=True, exist_ok=True)
    jsonl = day_dir / "a_values.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    csv_path = day_dir / "a_values.csv"
    fieldnames = [
        "timestamp",
        "price_source",
        "strict_realtime",
        "quote_skew_seconds",
        "calculation_elapsed_ms",
        "etf_quote",
        "etf_value",
        "component_value_ex_cash",
        "estimated_cash",
        "basket_value",
        "estimated_a",
        "record_number",
    ]
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8-sig") as existing:
            reader = csv.reader(existing)
            existing_header = next(reader, [])
        if existing_header != fieldnames:
            archived = csv_path.with_name(f"a_values_legacy_{datetime.now(TZ).strftime('%H%M%S')}.csv")
            csv_path.replace(archived)
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )
        if write_header:
            writer.writeheader()
        writer.writerow({k: result[k] for k in writer.fieldnames})


def validate_result_invariants(config: dict, result: dict) -> None:
    if not bool(config.get("require_realtime_snapshot", False)):
        return
    source = str(result.get("price_source", ""))
    allowed_sources = set(config.get("strict_realtime_price_sources", ["realtime_eastmoney"]))
    if source not in allowed_sources:
        raise RuntimeError(f"非严格实时行情源，拒绝使用: {source}")
    if result.get("strict_realtime") is not True:
        raise RuntimeError("行情结果未标记 strict_realtime=True，拒绝使用")
    quote_skew = dec(result.get("quote_skew_seconds", "NaN"))
    ensure_decimal("行情时间差", quote_skew)
    max_skew = dec(config.get("realtime_max_skew_seconds", "3"))
    if quote_skew > max_skew:
        raise RuntimeError(f"行情时间差超过限制: {quote_skew} > {max_skew}")
    quote_times = result.get("quote_times") or {}
    required_codes = [config.get("fund_code", ETF_CODE)] + [row["code"] for row in result.get("components", [])]
    missing_times = [code for code in required_codes if code not in quote_times]
    if missing_times:
        raise RuntimeError(f"缺少行情时间戳: {missing_times}")


def alert_source_mode(config: dict) -> str:
    if bool(config.get("require_realtime_snapshot", False)):
        return "strict_realtime_v1"
    return "legacy_price_source"


def detect_alerts(
    date_compact: str,
    value: Decimal,
    thresholds: list[Decimal],
    reset: bool,
    config: dict,
    result: dict,
) -> list[Decimal]:
    confirm_count = max(1, int(config.get("alert_confirmations", 1)))
    reset_buffer = dec(config.get("alert_reset_buffer", "0"))
    state = load_state()
    day_state = state.setdefault("dates", {}).setdefault(date_compact, {"active_thresholds": []})
    source_mode = alert_source_mode(config)
    if day_state.get("alert_source_mode") != source_mode:
        day_state["active_thresholds"] = []
        day_state["above_counts"] = {}
        day_state["alert_source_mode"] = source_mode
        day_state["source_mode_switched_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    active = {Decimal(str(x)) for x in day_state.get("active_thresholds", [])}
    above_counts = {Decimal(str(k)): int(v) for k, v in (day_state.get("above_counts") or {}).items()}
    alerts: list[Decimal] = []
    for threshold in sorted(thresholds):
        count = above_counts.get(threshold, 0)
        if value > threshold and threshold not in active:
            count += 1
            above_counts[threshold] = count
            if count >= confirm_count:
                alerts.append(threshold)
                active.add(threshold)
        elif value <= threshold:
            above_counts[threshold] = 0
        if reset and value <= threshold - reset_buffer and threshold in active:
            active.remove(threshold)
            above_counts[threshold] = 0
    day_state["active_thresholds"] = [str(x) for x in sorted(active)]
    day_state["above_counts"] = {str(k): v for k, v in sorted(above_counts.items())}
    day_state["alert_source_mode"] = source_mode
    day_state["last_price_source"] = result.get("price_source")
    day_state["last_strict_realtime"] = result.get("strict_realtime") is True
    day_state["last_quote_skew_seconds"] = result.get("quote_skew_seconds")
    day_state["last_a"] = str(q2(value))
    day_state["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)
    return alerts


def rollback_alerts(date_compact: str, alerts: list[Decimal]) -> None:
    if not alerts:
        return
    state = load_state()
    day_state = state.setdefault("dates", {}).setdefault(date_compact, {"active_thresholds": []})
    active = {Decimal(str(x)) for x in day_state.get("active_thresholds", [])}
    for threshold in alerts:
        active.discard(threshold)
    day_state["active_thresholds"] = [str(x) for x in sorted(active)]
    day_state["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)


def append_alert_result(
    date_compact: str,
    alerts: list[Decimal],
    result: dict,
    notification_status: str,
    notification_result: dict | bool | None = None,
) -> None:
    if not alerts:
        return
    day_dir = RUNS_DIR / date_compact
    day_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "notification_status": notification_status,
        "thresholds": [str(a) for a in alerts],
        **result,
    }
    if isinstance(notification_result, dict):
        payload["notification_sent_at"] = notification_result.get("sent_at")
        payload["notification_elapsed_ms"] = notification_result.get("elapsed_ms")
        response = notification_result.get("response") or {}
        payload["notification_response_code"] = response.get("code", response.get("StatusCode", response.get("errcode")))
    with (day_dir / "alerts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def append_notification_event(
    date_compact: str,
    *,
    title: str,
    result: dict | None,
    alerts: list[Decimal],
    status: str,
    notification_result: dict | bool | None = None,
    error: Exception | str | None = None,
) -> None:
    day_dir = RUNS_DIR / date_compact
    day_dir.mkdir(parents=True, exist_ok=True)
    result = result or {}
    payload = {
        "created_at": datetime.now(TZ).isoformat(timespec="milliseconds"),
        "title": title,
        "status": status,
        "thresholds": [str(a) for a in alerts],
        "timestamp": result.get("timestamp"),
        "estimated_a": result.get("estimated_a"),
        "etf_quote": result.get("etf_quote"),
        "price_source": result.get("price_source"),
        "strict_realtime": result.get("strict_realtime"),
        "quote_skew_seconds": result.get("quote_skew_seconds"),
        "calculation_elapsed_ms": result.get("calculation_elapsed_ms"),
    }
    if isinstance(notification_result, dict):
        response = notification_result.get("response") or {}
        payload.update(
            {
                "kind": notification_result.get("kind"),
                "notification_sent_at": notification_result.get("sent_at"),
                "notification_elapsed_ms": notification_result.get("elapsed_ms"),
                "notification_response_code": response.get("code", response.get("StatusCode", response.get("errcode"))),
                "notification_response_message": response.get("msg", response.get("StatusMessage", response.get("errmsg"))),
            }
        )
    if error is not None:
        payload["error"] = str(error)
    with (day_dir / "notifications.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def print_result(result: dict, alerts: list[Decimal] | None = None) -> None:
    a_value = dec(result["estimated_a"])
    if alerts:
        print("ALERT: 511130预估a达到实验观察阈值 " + ", ".join(f"+{money(x)}" for x in alerts))
    else:
        print("OK: 本轮未触发新阈值")
    print(f"时间: {result['timestamp']} ({result['price_source']})")
    print(f"预估a: {money(a_value, signed=True)}")
    print(f"511130报价: {result['etf_quote']}; ETF端: {result['etf_value']}")
    print(f"成分券数量: {result['record_number']}; 篮子不含现金: {result['component_value_ex_cash']}; 预估现金: {result['estimated_cash']}; 篮子合计: {result['basket_value']}")
    for row in result["components"]:
        print(
            f"{row['code']} {row['name']}: 净价={row['price']}, 利息={row['interest']}({row['interest_source']}), "
            f"数量={row['pcf_quantity']}x10={row['units']}, 价值={row['value']}"
        )
    print("公式: 511130报价/100*1,000,000 - [Σ(净价+逐券利息)*PCF数量*10 + EstimatedCashComponent]")
    print("只读监控，不自动下单。")


def _format_component_price_snapshot(result: dict) -> str:
    parts = []
    for row in result["components"]:
        parts.append(f"{row['code']}={row['price']}")
    return "、".join(parts) if parts else "-"


def format_result_message(result: dict, alerts: list[Decimal]) -> str:
    lines = []
    if alerts:
        lines.append("触发档位: " + ", ".join(f"+{money(x)}" for x in alerts))
        lines.append("触发状态: 已触发")
    else:
        lines.append("触发状态: 未触发")
    lines.append(f"预警时511130价格: {result['etf_quote']}")
    lines.append(f"预警时成分券价格: {_format_component_price_snapshot(result)}")
    lines.extend(
        [
            f"时间: {result['timestamp']} ({result['price_source']})",
            f"预估a: {money(dec(result['estimated_a']), signed=True)}",
            f"行情时间差: {result.get('quote_skew_seconds', '-')}秒; 计算耗时: {result.get('calculation_elapsed_ms', '-')}ms",
            f"511130报价: {result['etf_quote']}; ETF端: {result['etf_value']}",
            f"成分券数量: {result['record_number']}; 篮子不含现金: {result['component_value_ex_cash']}; 预估现金: {result['estimated_cash']}; 篮子合计: {result['basket_value']}",
        ]
    )
    lines.append("只读监控，不自动下单。")
    return "\n".join(lines)


def mode_precheck(config: dict, notify: bool = False) -> int:
    target_date = config["target_date"]
    compare_date = config.get("compare_date", "20260612")
    try:
        current = fetch_pcf(target_date, config.get("fund_code", ETF_CODE))
    except Exception as exc:  # noqa: BLE001
        message = f"PCF_NOT_READY: {target_date} 清单未更新或不可读: {exc}"
        print(message)
        if notify:
            send_notification(config, "511130 PCF清单检查", message)
        return 0
    base = fetch_pcf(compare_date, config.get("fund_code", ETF_CODE))
    print(f"PCF_READY: {target_date} TradingDay={current.trading_day}; RecordNumber={current.record_number}; EstimatedCash={money(current.estimated_cash_component)}")
    lines = [
        f"PCF_READY: {target_date} TradingDay={current.trading_day}; RecordNumber={current.record_number}; EstimatedCash={money(current.estimated_cash_component)}"
    ]
    for line in compare_pcfs(current, base):
        print(line)
        lines.append(line)
    try:
        interests = get_interests(target_date, current, config)
        print("逐券利息:")
        lines.append("逐券利息:")
        for code, (interest, source) in interests.items():
            print(f"{code}: {q3(interest)} ({source})")
            lines.append(f"{code}: {q3(interest)} ({source})")
    except Exception as exc:  # noqa: BLE001
        line = f"INTEREST_NOT_READY: {exc}"
        print(line)
        lines.append(line)
        if notify:
            send_notification(config, "511130 PCF清单检查", "\n".join(lines))
        return 0
    if notify:
        send_notification(config, "511130 PCF清单检查", "\n".join(lines))
    return 0


def handle_calculation_result(config: dict, result: dict, notify: bool = False, notify_no_alert: bool = True) -> int:
    target_date = config["target_date"]
    validate_result_invariants(config, result)
    append_result(target_date, result)
    thresholds = [dec(x) for x in config.get("thresholds", [300])]
    a_value = dec(result["estimated_a"])
    alerts = detect_alerts(
        target_date,
        a_value,
        thresholds,
        bool(config.get("reset_below_threshold", True)),
        config,
        result,
    )
    notification_result: dict | bool | None = None
    title = "511130 a值预警" if alerts else "511130 预警机器人运行检查"
    if notify and (alerts or notify_no_alert):
        try:
            notification_result = send_notification(config, title, format_result_message(result, alerts))
            if not isinstance(notification_result, dict) or notification_result.get("ok") is not True:
                raise RuntimeError(f"通知失败: 未返回成功结果 {notification_result}")
            append_notification_event(
                target_date,
                title=title,
                result=result,
                alerts=alerts,
                status="sent",
                notification_result=notification_result,
            )
        except Exception as exc:
            append_notification_event(
                target_date,
                title=title,
                result=result,
                alerts=alerts,
                status="failed",
                notification_result=notification_result,
                error=exc,
            )
            rollback_alerts(target_date, alerts)
            raise
    append_alert_result(
        target_date,
        alerts,
        result,
        "sent" if alerts and isinstance(notification_result, dict) and notification_result.get("ok") is True else "local_only",
        notification_result=notification_result,
    )
    print_result(result, alerts)
    return 0


def mode_once(config: dict, notify: bool = False, notify_no_alert: bool = True) -> int:
    target_date = config["target_date"]
    result = calculate_a(target_date, config)
    return handle_calculation_result(config, result, notify=notify, notify_no_alert=notify_no_alert)


def mode_once_with_context(
    config: dict,
    context: CalculationContext,
    notify: bool = False,
    notify_no_alert: bool = True,
) -> int:
    result = calculate_a_with_context(context, config)
    return handle_calculation_result(config, result, notify=notify, notify_no_alert=notify_no_alert)


def mode_test_notify(config: dict) -> int:
    title = "511130 预警机器人测试"
    message = (
        "飞书预警联通测试（不依赖PCF/价格）\n"
        f"时间: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')}\n"
        "如果你看到这条消息，说明 webhook 到达正常。"
    )
    result = send_notification(config, title, message)
    if isinstance(result, dict) and result.get("ok") is True:
        append_notification_event(
            str(config.get("target_date") or datetime.now(TZ).strftime("%Y%m%d")),
            title=title,
            result=None,
            alerts=[],
            status="sent",
            notification_result=result,
        )
        print(f"OK: 已发送飞书测试消息; elapsed_ms={result.get('elapsed_ms')}")
        return 0
    print(f"ERROR: 飞书测试消息未发送: {result}")
    return 1


def mode_selftest() -> int:
    result = calculate_estimated_a_from_inputs(
        etf_quote=Decimal("105.849"),
        estimated_cash_component=Decimal("1071.04"),
        component_inputs=[
            {
                "code": "019776",
                "name": "25特国02",
                "pcf_quantity": Decimal("600"),
                "units": Decimal("6000"),
                "price": Decimal("92.709"),
                "interest": Decimal("0.267"),
                "interest_source": "manual_trading_software_override",
            },
            {
                "code": "019837",
                "name": "26特国02",
                "pcf_quantity": Decimal("500"),
                "units": Decimal("5000"),
                "price": Decimal("99.571"),
                "interest": Decimal("0.305"),
                "interest_source": "manual_trading_software_override",
            },
        ],
    )
    expected = Decimal("182.96")
    actual = q2(result["estimated_a"])
    if actual != expected:
        raise RuntimeError(f"公式自检失败: expected {expected}, actual {actual}")
    try:
        calculate_estimated_a_from_inputs(
            etf_quote=Decimal("105.849"),
            estimated_cash_component=Decimal("1071.04"),
            component_inputs=[
                {
                    "code": "019776",
                    "name": "25特国02",
                    "pcf_quantity": Decimal("600"),
                    "units": Decimal("6000"),
                    "price": Decimal("92.709"),
                    "interest": Decimal("0"),
                    "interest_source": "missing_interest_default_0",
                }
            ],
        )
    except RuntimeError as exc:
        if "拒绝用默认值计算a" not in str(exc):
            raise
    else:
        raise RuntimeError("公式自检失败: 缺失利息默认值未被拒绝")
    print("OK: selftest passed; formula=182.96; missing-interest fallback rejected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="511130 live estimated-a monitor")
    parser.add_argument("--mode", choices=["precheck", "once", "test", "selftest"], required=True)
    parser.add_argument("--notify", action="store_true", help="send webhook notification for precheck results and once-mode checks")
    parser.add_argument("--date", help="override target date, e.g. 20260615")
    parser.add_argument("--compare-date", help="override compare date, e.g. 20260612")
    args = parser.parse_args()
    config = load_config()
    if args.date:
        config["target_date"] = args.date
    if args.compare_date:
        config["compare_date"] = args.compare_date
    if args.mode == "precheck":
        return mode_precheck(config, args.notify)
    if args.mode == "once":
        return mode_once(config, args.notify)
    if args.mode == "test":
        return mode_test_notify(config)
    if args.mode == "selftest":
        return mode_selftest()
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        try:
            config = load_config()
            send_notification(config, "511130监控错误", f"ERROR: {exc}")
        except Exception:
            pass
        raise SystemExit(1)
