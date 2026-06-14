#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import hmac
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
RUNS_DIR = BASE / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

BOSERA_PCF_URL = "https://www.bosera.com/jjcp/etf/files/{fund}/{year}/ssepcf_{fund}_{date}.xml"
SSE_QUERY_URL = "https://query.sse.com.cn/commonQuery.do"
EASTMONEY_TRENDS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
SINA_KLINE_URL = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/CN_MarketDataService.getKLineData"
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


def load_config() -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    threshold_text = os.environ.get("A_MONITOR_THRESHOLDS", "").strip()
    if threshold_text:
        config["thresholds"] = [int(part.strip()) for part in threshold_text.split(",") if part.strip()]
    return config


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"dates": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def request_text(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = 20) -> str:
    merged = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", **(headers or {})}
    last = None
    for _ in range(3):
        try:
            response = curl_requests.get(url, params=params, headers=merged, impersonate="chrome", timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.35)
    raise RuntimeError(f"读取失败: {url}: {last}")


def request_json(url: str, *, params: dict, headers: dict | None = None, timeout: int = 20) -> dict:
    text = request_text(url, params=params, headers=headers, timeout=timeout)
    return json.loads(text)


def post_json(url: str, payload: dict, *, timeout: int = 15) -> None:
    response = curl_requests.post(
        url,
        json=payload,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        impersonate="chrome",
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return
    error_code = body.get("code", body.get("StatusCode", body.get("errcode")))
    if error_code not in (None, 0, "0"):
        message = body.get("msg") or body.get("StatusMessage") or body.get("errmsg") or body
        raise RuntimeError(f"webhook返回错误: code={error_code}, message={message}")


def send_notification(config: dict, title: str, text: str) -> bool:
    notification = config.get("notification") or {}
    url_env = notification.get("webhook_url_env", "A_MONITOR_WEBHOOK_URL")
    kind_env = notification.get("webhook_kind_env", "A_MONITOR_WEBHOOK_KIND")
    url = os.environ.get(url_env, "").strip()
    if not url:
        return False
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
    post_json(url, payload)
    return True


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


def fetch_aligned_prices(date_compact: str, codes: list[str]) -> tuple[str, str, dict[str, Decimal]]:
    date_iso = f"{date_compact[:4]}-{date_compact[4:6]}-{date_compact[6:]}"
    try:
        maps = {code: fetch_eastmoney_1m(code) for code in codes}
        common = set.intersection(*[{ts for ts in price_map if ts.startswith(date_iso) and ts <= f"{date_iso} 15:00"} for price_map in maps.values()])
        common = {ts for ts in common if not ts.endswith("09:30")}
        if common:
            ts = sorted(common)[-1]
            return "1m_eastmoney", ts, {code: maps[code][ts] for code in codes}
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: 东方财富1分钟行情不可用，降级到新浪5分钟: {exc}", file=sys.stderr)

    maps = {code: fetch_sina_kline(code, "5") for code in codes}
    common = set.intersection(*[{ts for ts in price_map if ts.startswith(date_iso) and ts <= f"{date_iso} 15:00"} for price_map in maps.values()])
    common = {ts for ts in common if not ts.endswith("09:30")}
    if common:
        ts = sorted(common)[-1]
        return "5m_sina", ts, {code: maps[code][ts] for code in codes}
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


def calculate_a(date_compact: str, config: dict) -> dict:
    pcf = fetch_pcf(date_compact, config.get("fund_code", ETF_CODE))
    if pcf.trading_day != date_compact:
        raise RuntimeError(f"PCF日期不匹配: 期望{date_compact}, 实际{pcf.trading_day}")
    interests = get_interests(date_compact, pcf, config)
    codes = [ETF_CODE] + [c.code for c in pcf.components]
    price_source, ts, prices = fetch_aligned_prices(date_compact, codes)

    etf_quote = prices[ETF_CODE]
    etf_value = etf_quote / Decimal("100") * Decimal("1000000")
    component_value = Decimal("0")
    component_rows = []
    for component in pcf.components:
        price = prices[component.code]
        interest, interest_source = interests[component.code]
        value = (price + interest) * component.units
        component_value += value
        component_rows.append(
            {
                "code": component.code,
                "name": component.name,
                "pcf_quantity": str(component.pcf_quantity),
                "units": str(component.units),
                "price": str(q3(price)),
                "interest": str(q3(interest)),
                "interest_source": interest_source,
                "value": str(q2(value)),
            }
        )
    basket = component_value + pcf.estimated_cash_component
    estimated_a = etf_value - basket
    formula_error = etf_value - component_value - pcf.estimated_cash_component - estimated_a
    if abs(formula_error) > Decimal("0.005"):
        raise RuntimeError(f"公式自检失败: {formula_error}")
    return {
        "date": date_compact,
        "timestamp": ts,
        "price_source": price_source,
        "etf_quote": str(q3(etf_quote)),
        "etf_value": str(q2(etf_value)),
        "estimated_cash": str(q2(pcf.estimated_cash_component)),
        "component_value_ex_cash": str(q2(component_value)),
        "basket_value": str(q2(basket)),
        "estimated_a": str(q2(estimated_a)),
        "record_number": pcf.record_number,
        "components": component_rows,
    }


def append_result(date_compact: str, result: dict) -> None:
    day_dir = RUNS_DIR / date_compact
    day_dir.mkdir(parents=True, exist_ok=True)
    jsonl = day_dir / "a_values.jsonl"
    with jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    csv_path = day_dir / "a_values.csv"
    write_header = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "price_source",
                "etf_quote",
                "etf_value",
                "component_value_ex_cash",
                "estimated_cash",
                "basket_value",
                "estimated_a",
                "record_number",
            ],
        )
        if write_header:
            writer.writeheader()
        writer.writerow({k: result[k] for k in writer.fieldnames})


def detect_alerts(date_compact: str, value: Decimal, thresholds: list[Decimal], reset: bool) -> list[Decimal]:
    state = load_state()
    day_state = state.setdefault("dates", {}).setdefault(date_compact, {"active_thresholds": []})
    active = {Decimal(str(x)) for x in day_state.get("active_thresholds", [])}
    alerts: list[Decimal] = []
    for threshold in sorted(thresholds):
        if value > threshold and threshold not in active:
            alerts.append(threshold)
            active.add(threshold)
        elif reset and value <= threshold and threshold in active:
            active.remove(threshold)
    day_state["active_thresholds"] = [str(x) for x in sorted(active)]
    day_state["last_a"] = str(q2(value))
    day_state["updated_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_state(state)
    return alerts


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


def format_result_message(result: dict, alerts: list[Decimal]) -> str:
    lines = []
    if alerts:
        lines.append("触发档位: " + ", ".join(f"+{money(x)}" for x in alerts))
    lines.extend(
        [
            f"时间: {result['timestamp']} ({result['price_source']})",
            f"预估a: {money(dec(result['estimated_a']), signed=True)}",
            f"511130报价: {result['etf_quote']}; ETF端: {result['etf_value']}",
            f"成分券数量: {result['record_number']}; 篮子不含现金: {result['component_value_ex_cash']}; 预估现金: {result['estimated_cash']}; 篮子合计: {result['basket_value']}",
        ]
    )
    for row in result["components"]:
        lines.append(
            f"{row['code']} {row['name']}: 净价={row['price']}, 利息={row['interest']}({row['interest_source']}), "
            f"数量={row['pcf_quantity']}x10={row['units']}, 价值={row['value']}"
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


def mode_once(config: dict, notify: bool = False) -> int:
    target_date = config["target_date"]
    result = calculate_a(target_date, config)
    append_result(target_date, result)
    thresholds = [dec(x) for x in config.get("thresholds", [300])]
    a_value = dec(result["estimated_a"])
    alerts = detect_alerts(target_date, a_value, thresholds, bool(config.get("reset_below_threshold", True)))
    if alerts:
        day_dir = RUNS_DIR / target_date
        with (day_dir / "alerts.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"thresholds": [str(a) for a in alerts], **result}, ensure_ascii=False) + "\n")
        if notify:
            send_notification(config, "511130 a值预警", format_result_message(result, alerts))
    print_result(result, alerts)
    return 0


def mode_notify_test(config: dict) -> int:
    thresholds = [dec(x) for x in config.get("thresholds", [300])]
    title = "511130 a值预警测试"
    text = "\n".join(
        [
            "这是一条飞书机器人测试消息。",
            "模拟触发档位: " + ", ".join(f"+{money(threshold)}" for threshold in thresholds),
            "只读监控，不自动下单。",
        ]
    )
    if not send_notification(config, title, text):
        raise RuntimeError("A_MONITOR_WEBHOOK_URL未配置，无法发送飞书测试提醒")
    print("NOTIFY_TEST_SENT: webhook已接受511130 a值预警测试消息")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="511130 live estimated-a monitor")
    parser.add_argument("--mode", choices=["precheck", "once", "notify-test"], required=True)
    parser.add_argument("--notify", action="store_true", help="send webhook notification for precheck results and threshold alerts")
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
    if args.mode == "notify-test":
        return mode_notify_test(config)
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
