#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urljoin


REQUIRED_HEALTH_FIELDS = {
    "target_date_mode",
    "process_ok",
    "data_ok",
    "diagnostics",
    "notification_setup",
    "accuracy_setup",
    "alert_policy_setup",
    "auto_loop",
}


def fetch_json(base_url: str, path: str, *, timeout: float = 15) -> tuple[int, dict[str, Any]]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"{path} 返回值不是JSON对象")
            return response.status, payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"ok": False, "error": body}
        if not isinstance(payload, dict):
            payload = {"ok": False, "error": str(payload)}
        return exc.code, payload


def validate_health(payload: dict[str, Any], status_code: int) -> list[str]:
    issues: list[str] = []
    missing = sorted(field for field in REQUIRED_HEALTH_FIELDS if field not in payload)
    if missing:
        issues.append(f"/health 缺少新版本字段: {', '.join(missing)}")
    if status_code != 200:
        issues.append(f"/health HTTP状态不是200: {status_code}")
    if payload.get("process_ok") is not True:
        issues.append(f"process_ok不是true: {payload.get('process_ok')}")
    auto_loop = payload.get("auto_loop") or {}
    if auto_loop.get("code") not in {"running", "starting", "disabled"}:
        issues.append(f"auto_loop状态异常: {auto_loop.get('code')}")
    notification_setup = payload.get("notification_setup") or {}
    if "webhook_configured" not in notification_setup:
        issues.append("notification_setup缺少webhook_configured")
    if notification_setup.get("kind") == "feishu" and "feishu_secret_configured" not in notification_setup:
        issues.append("notification_setup缺少feishu_secret_configured")
    accuracy_setup = payload.get("accuracy_setup") or {}
    if accuracy_setup.get("formula_version") != "estimated_a_v1":
        issues.append(f"公式版本异常: {accuracy_setup.get('formula_version')}")
    if accuracy_setup.get("intraday_source") != "eastmoney_realtime_snapshot_only":
        issues.append(f"盘中行情源不是东方财富实时锁定: {accuracy_setup.get('intraday_source')}")
    allowed_sources = accuracy_setup.get("allowed_price_sources") or []
    if allowed_sources != ["realtime_eastmoney"]:
        issues.append(f"严格实时价格源不是单一东方财富: {allowed_sources}")
    if accuracy_setup.get("strict_realtime_required") is not True:
        issues.append("strict_realtime_required不是true")
    if int(accuracy_setup.get("max_skew_seconds", -1)) != 3:
        issues.append(f"max_skew_seconds不是3: {accuracy_setup.get('max_skew_seconds')}")
    if int(accuracy_setup.get("max_stale_seconds", -1)) != 30:
        issues.append(f"max_stale_seconds不是30: {accuracy_setup.get('max_stale_seconds')}")
    if accuracy_setup.get("missing_interest_fallback_allowed") is not False:
        issues.append("missing_interest_fallback_allowed不是false")
    expected_codes = set(accuracy_setup.get("expected_component_codes") or [])
    if expected_codes != {"019776", "019837"}:
        issues.append(f"成分券锁定异常: {sorted(expected_codes)}")
    alert_policy = payload.get("alert_policy_setup") or {}
    if alert_policy.get("threshold_only_notifications") is not True:
        issues.append("threshold_only_notifications不是true")
    if alert_policy.get("runtime_error_notifications") is not False:
        issues.append("runtime_error_notifications不是false")
    if alert_policy.get("no_alert_run_check_notifications") is not False:
        issues.append("no_alert_run_check_notifications不是false")
    if alert_policy.get("degraded_alert_enabled") is not True:
        issues.append("degraded_alert_enabled不是true")
    if alert_policy.get("degraded_alert_source_mode") != "degraded_price_source_v1":
        issues.append(f"degraded_alert_source_mode异常: {alert_policy.get('degraded_alert_source_mode')}")
    if int(alert_policy.get("notification_attempts", 0)) < 3:
        issues.append(f"notification_attempts小于3: {alert_policy.get('notification_attempts')}")
    return issues


def validate_data(payload: dict[str, Any], status_code: int) -> list[str]:
    issues: list[str] = []
    if status_code != 200:
        issues.append(f"/api/data HTTP状态不是200: {status_code}")
    if payload.get("ok") is not True:
        issues.append(f"/api/data ok不是true: {payload.get('ok')}")
    config = payload.get("config") or {}
    if "notification_setup" not in config:
        issues.append("/api/data.config缺少notification_setup")
    if "accuracy_setup" not in config:
        issues.append("/api/data.config缺少accuracy_setup")
    if "alert_policy_setup" not in config:
        issues.append("/api/data.config缺少alert_policy_setup")
    status = payload.get("status") or {}
    if not status.get("code"):
        issues.append("/api/data.status缺少code")
    if payload.get("chart_current") is True:
        if payload.get("strict_realtime") is not True:
            issues.append("chart_current=true但strict_realtime不是true")
        skew = payload.get("quote_skew_seconds")
        if skew is None or float(skew) > 3:
            issues.append(f"当前a行情时间差异常: {skew}")
    return issues


def run_smoke(base_url: str, *, timeout: float = 15) -> tuple[bool, list[str], dict[str, Any]]:
    health_status, health = fetch_json(base_url, "/health", timeout=timeout)
    data_status, data = fetch_json(base_url, "/api/data", timeout=timeout)
    issues = validate_health(health, health_status)
    issues.extend(validate_data(data, data_status))
    evidence = {
        "health_status": health_status,
        "data_status": data_status,
        "date": health.get("date"),
        "process_ok": health.get("process_ok"),
        "data_ok": health.get("data_ok"),
        "auto_loop": (health.get("auto_loop") or {}).get("code"),
        "notification_configured": health.get("notification_configured"),
        "notification_setup": health.get("notification_setup"),
        "accuracy_setup": health.get("accuracy_setup"),
        "alert_policy_setup": health.get("alert_policy_setup"),
        "data_status_code": (data.get("status") or {}).get("code"),
        "latest_a": data.get("latest_a"),
        "chart_current": data.get("chart_current"),
    }
    return not issues, issues, evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only smoke check for the 511130 Railway dashboard")
    parser.add_argument("base_url", help="Dashboard base URL, e.g. https://511130-live-monitor-production.up.railway.app")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    ok, issues, evidence = run_smoke(args.base_url, timeout=args.timeout)
    result = {"ok": ok, "issues": issues, "evidence": evidence}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OK" if ok else "FAILED")
        for issue in issues:
            print(f"- {issue}")
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
