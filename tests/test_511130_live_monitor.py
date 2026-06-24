from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
LIVE_DIR = BASE / "scripts" / "511130_live_monitor"
sys.path.insert(0, str(LIVE_DIR))

import live_a_dashboard as dashboard  # noqa: E402
import daily_actual_a_report as daily_report  # noqa: E402
import monitor_511130 as monitor  # noqa: E402
import smoke_check  # noqa: E402


class Test511130MonitorHardening(unittest.TestCase):
    def _state(self, date: str = "20260615") -> dashboard.DashboardState:
        return dashboard.DashboardState(
            date=date,
            interval_seconds=1,
            max_points=2,
            thresholds=[Decimal("300"), Decimal("500")],
            auto_run=False,
            auto_run_notify=False,
            allowed_price_sources=["realtime_sina_snapshot"],
            max_stale_seconds=30,
            public_readonly=False,
        )

    def _history_row(self, timestamp: str, a_value: str, source: str = "realtime_sina_snapshot") -> dict:
        return {
            "timestamp": timestamp,
            "price_source": source,
            "strict_realtime": True,
            "quote_skew_seconds": 0,
            "calculation_elapsed_ms": 10,
            "etf_quote": "105.000",
            "basket_value": "1049000.00",
            "estimated_cash": "1071.04",
            "estimated_a": a_value,
        }

    def _write_jsonl(self, root: Path, date: str, rows: list[dict]) -> None:
        run_dir = root / date
        run_dir.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(row, ensure_ascii=False) for row in rows]
        (run_dir / "a_values.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _calculation_result(self, a_value: str = "120.00") -> dict:
        ts = datetime.now(monitor.TZ).strftime("%Y-%m-%d %H:%M:%S")
        return {
            "date": "20260615",
            "timestamp": ts,
            "price_source": "realtime_eastmoney",
            "strict_realtime": True,
            "quote_times": {"511130": ts, "019776": ts, "019837": ts},
            "quote_skew_seconds": 0,
            "calculation_elapsed_ms": 10,
            "formula_version": "estimated_a_v1",
            "creation_redemption_unit": "10000.00",
            "etf_quote": "105.000",
            "etf_value": "1050000.00",
            "estimated_cash": "1071.04",
            "component_value_ex_cash": "1048808.96",
            "basket_value": "1049880.00",
            "estimated_a": a_value,
            "record_number": 2,
            "components": [
                {
                    "code": "019776",
                    "name": "25特国02",
                    "pcf_quantity": "600",
                    "units": "6000",
                    "price": "92.702",
                    "interest": "0.267",
                    "interest_source": "manual_override",
                    "value": "557814.00",
                },
                {
                    "code": "019837",
                    "name": "25特国07",
                    "pcf_quantity": "500",
                    "units": "5000",
                    "price": "99.552",
                    "interest": "0.305",
                    "interest_source": "manual_override",
                    "value": "499285.00",
                },
            ],
        }

    def _state_cache_snapshot(self) -> tuple[dict | None, bool]:
        return monitor._STATE_CACHE, monitor._STATE_CACHE_DIRTY

    def _restore_state_cache(self, snapshot: tuple[dict | None, bool]) -> None:
        monitor._STATE_CACHE, monitor._STATE_CACHE_DIRTY = snapshot

    def _get_local_json(self, handler, path: str) -> dict:  # noqa: ANN001
        status, payload = self._request_local_json(handler, path)
        self.assertEqual(status, 200)
        return payload

    def _request_local_json(self, handler, path: str) -> tuple[int, dict]:  # noqa: ANN001
        server = dashboard.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_formula_rejects_changed_creation_unit(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "CreationRedemptionUnit变化"):
            monitor.calculate_estimated_a_from_inputs(
                etf_quote=Decimal("105.849"),
                estimated_cash_component=Decimal("1071.04"),
                creation_redemption_unit=Decimal("20000"),
                component_inputs=[],
            )

    def test_validate_pcf_rejects_component_structure_change(self) -> None:
        pcf = monitor.Pcf(
            trading_day="20260615",
            record_number=1,
            estimated_cash_component=Decimal("1071.04"),
            pre_cash_component=Decimal("0"),
            creation_redemption_unit=Decimal("10000"),
            components=[monitor.Component("019776", "25特国02", Decimal("600"))],
        )
        with self.assertRaisesRegex(RuntimeError, "PCF成分券结构变化"):
            monitor.validate_pcf(pcf, {"expected_component_codes": ["019776", "019837"]})

    def test_cached_interest_is_not_current_a(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "历史缓存"):
            monitor.validate_interest_value("019776", Decimal("0.267"), "cached_interest_20260614")

    def test_over_threshold_status_has_priority_over_notification_failure(self) -> None:
        status = dashboard.classify_status(
            latest={"estimated_a": 350.0},
            latest_snapshot={"timestamp": datetime.now(dashboard.TZ).strftime("%Y-%m-%d %H:%M:%S")},
            snapshot_fresh=True,
            last_error="",
            latest_notification={"status": "failed", "error": "boom"},
            thresholds=[Decimal("300"), Decimal("500")],
        )
        self.assertEqual(status["code"], "over_300")

    def test_notification_failure_does_not_change_normal_market_status(self) -> None:
        status = dashboard.classify_status(
            latest={"estimated_a": 120.0},
            latest_snapshot={"timestamp": datetime.now(dashboard.TZ).strftime("%Y-%m-%d %H:%M:%S")},
            snapshot_fresh=True,
            last_error="",
            latest_notification={"status": "failed", "error": "boom"},
            thresholds=[Decimal("300"), Decimal("500")],
        )
        self.assertEqual(status["code"], "normal")

    def test_negative_over_threshold_status_is_alert_level(self) -> None:
        status = dashboard.classify_status(
            latest={"estimated_a": -350.0},
            latest_snapshot={"timestamp": datetime.now(dashboard.TZ).strftime("%Y-%m-%d %H:%M:%S")},
            snapshot_fresh=True,
            last_error="",
            latest_notification={},
            thresholds=[Decimal("300"), Decimal("500")],
        )
        self.assertEqual(status["code"], "over_300")

    def test_pcf_not_ready_status_is_explicit(self) -> None:
        status = dashboard.classify_status(
            latest=None,
            latest_snapshot=None,
            snapshot_fresh=False,
            last_error="RuntimeError: PCF未更新或不可读: 20260616; HTTP 200",
            latest_notification={},
            thresholds=[Decimal("300")],
        )
        self.assertEqual(status["code"], "pcf_not_ready")

    def test_runtime_diagnostics_split_pcf_quote_and_notification(self) -> None:
        diagnostics = dashboard.build_runtime_diagnostics(
            status={"code": "pcf_not_ready", "label": "清单未就绪", "level": "warning", "detail": "PCF未更新"},
            latest_snapshot=None,
            snapshot_fresh=False,
            last_error="RuntimeError: PCF未更新或不可读",
            latest_notification=None,
            notification_configured=False,
            pcf_retry_remaining=300,
        )
        self.assertEqual(diagnostics["pcf"]["code"], "not_ready")
        self.assertEqual(diagnostics["pcf"]["retry_remaining_seconds"], 300)
        self.assertEqual(diagnostics["quote"]["code"], "blocked_by_pcf")
        self.assertEqual(diagnostics["notification"]["code"], "unconfigured")

    def test_runtime_diagnostics_surfaces_feishu_business_failure(self) -> None:
        diagnostics = dashboard.build_runtime_diagnostics(
            status={"code": "quote_stale", "label": "行情过旧", "level": "warning", "detail": "行情过旧"},
            latest_snapshot=None,
            snapshot_fresh=False,
            last_error="RuntimeError: 行情过旧",
            latest_notification={
                "status": "failed",
                "notification_response_code": 19021,
                "notification_response_message": "sign match fail",
            },
            notification_configured=True,
            pcf_retry_remaining=0,
        )
        self.assertEqual(diagnostics["pcf"]["code"], "ok_or_not_checked")
        self.assertEqual(diagnostics["quote"]["code"], "stale")
        self.assertEqual(diagnostics["notification"]["code"], "failed")
        self.assertIn("19021", diagnostics["notification"]["detail"])

    def test_auto_loop_diagnostic_distinguishes_disabled_running_and_stale(self) -> None:
        state = self._state()
        disabled = dashboard.auto_loop_diagnostic(state, now=1000.0)
        self.assertEqual(disabled["code"], "disabled")

        state.auto_run = True
        state.interval_seconds = 15
        state.last_auto_tick_at = 990.0
        running = dashboard.auto_loop_diagnostic(state, now=1000.0)
        self.assertEqual(running["code"], "running")
        self.assertEqual(running["age_seconds"], 10.0)

        stale = dashboard.auto_loop_diagnostic(state, now=1061.0)
        self.assertEqual(stale["code"], "stale")
        self.assertEqual(stale["stale_after_seconds"], 60)

    def test_health_ok_tracks_process_not_quote_availability(self) -> None:
        state = self._state()
        state.auto_run = True
        state.interval_seconds = 15
        state.last_auto_tick_at = dashboard.time.time()
        state.last_error = "RuntimeError: 行情过旧"
        health = dashboard.build_health_payload(state, {"target_date_mode": "auto"})
        self.assertTrue(health["ok"])
        self.assertTrue(health["process_ok"])
        self.assertFalse(health["data_ok"])
        self.assertEqual(health["auto_loop"]["code"], "running")

    def test_healthcheck_returns_503_when_auto_loop_is_stale(self) -> None:
        state = self._state()
        state.auto_run = True
        state.interval_seconds = 15
        state.last_auto_tick_at = dashboard.time.time() - 120
        handler = dashboard.make_handler(state, {"target_date_mode": "auto"}, {"value": None})
        status, payload = self._request_local_json(handler, "/health")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["process_ok"])
        self.assertEqual(payload["auto_loop"]["code"], "stale")

    def test_stale_snapshot_hides_current_a(self) -> None:
        old_ts = (datetime.now(dashboard.TZ) - timedelta(seconds=90)).strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "timestamp": old_ts,
            "price_source": "realtime_sina_snapshot",
            "strict_realtime": True,
            "quote_times": {
                "511130": old_ts,
                "019776": old_ts,
                "019837": old_ts,
            },
            "quote_skew_seconds": 0,
            "calculated_at": datetime.now(dashboard.TZ).isoformat(timespec="milliseconds"),
            "etf_quote": "105.849",
            "etf_value": "1058490.00",
            "estimated_cash": "1071.04",
            "component_value_ex_cash": "1057236.00",
            "basket_value": "1058307.04",
            "estimated_a": "182.96",
            "components": [],
        }
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "20260615"
            run_dir.mkdir(parents=True)
            (run_dir / "a_values.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            monitor.RUNS_DIR = Path(tmp)
            try:
                state = dashboard.DashboardState(
                    date="20260615",
                    interval_seconds=1,
                    max_points=300,
                    thresholds=[Decimal("300")],
                    auto_run=False,
                    auto_run_notify=False,
                    allowed_price_sources=["realtime_sina_snapshot"],
                    max_stale_seconds=30,
                    public_readonly=False,
                )
                payload = dashboard.build_data_payload(state, {"require_realtime_snapshot": True})
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["latest_a"], "-")
        self.assertFalse(payload["chart_current"])
        self.assertEqual(payload["status"]["code"], "quote_stale")

    def test_available_dates_and_full_series_are_not_truncated(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(root, "20260614", [self._history_row("2026-06-14 09:30:01", "100")])
            self._write_jsonl(
                root,
                "20260615",
                [
                    self._history_row("2026-06-15 09:30:01", "101"),
                    self._history_row("2026-06-15 09:30:02", "102"),
                    self._history_row("2026-06-15 09:30:03", "103"),
                ],
            )
            monitor.RUNS_DIR = root
            try:
                self.assertEqual(dashboard.available_dates(), ["20260614", "20260615"])
                self.assertEqual(len(dashboard.load_points("20260615", max_points=2)), 2)
                payload = dashboard.build_series_payload(
                    self._state(),
                    {"date": ["20260615"], "range": ["today"], "interval": ["1s"]},
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["kind"], "line")
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["stats"]["latest"], "103.00")

    def test_live_points_merge_with_history_seed_for_same_date(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        original_active_runs_dir = dashboard.ACTIVE_RUNS_DIR_AT_IMPORT
        original_history_seed_dir = dashboard.HISTORY_SEED_RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live_root = root / "runs"
            seed_root = root / "history_seed" / "runs"
            self._write_jsonl(
                seed_root,
                "20260615",
                [
                    self._history_row("2026-06-15 09:31:00", "100", source="historical_eastmoney_1m"),
                    self._history_row("2026-06-15 09:32:00", "200", source="historical_eastmoney_1m"),
                ],
            )
            self._write_jsonl(
                live_root,
                "20260615",
                [
                    self._history_row("2026-06-15 09:32:00", "250", source="realtime_eastmoney"),
                    self._history_row("2026-06-15 09:33:00", "300", source="realtime_eastmoney"),
                ],
            )
            monitor.RUNS_DIR = live_root
            dashboard.ACTIVE_RUNS_DIR_AT_IMPORT = live_root
            dashboard.HISTORY_SEED_RUNS_DIR = seed_root
            try:
                points = dashboard.load_points(
                    "20260615",
                    max_points=0,
                    allowed_sources=["historical_eastmoney_1m", "realtime_eastmoney"],
                    require_strict=False,
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
                dashboard.ACTIVE_RUNS_DIR_AT_IMPORT = original_active_runs_dir
                dashboard.HISTORY_SEED_RUNS_DIR = original_history_seed_dir
        self.assertEqual(
            [point["timestamp"] for point in points],
            ["2026-06-15 09:31:00", "2026-06-15 09:32:00", "2026-06-15 09:33:00"],
        )
        self.assertEqual([point["estimated_a"] for point in points], [100.0, 250.0, 300.0])

    def test_one_minute_series_aggregates_a_ohlc(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_jsonl(
                root,
                "20260615",
                [
                    self._history_row("2026-06-15 09:30:10", "100"),
                    self._history_row("2026-06-15 09:30:50", "120"),
                    self._history_row("2026-06-15 09:31:10", "90"),
                ],
            )
            monitor.RUNS_DIR = root
            try:
                payload = dashboard.build_series_payload(
                    self._state(),
                    {"date": ["20260615"], "range": ["today"], "interval": ["1m"]},
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["kind"], "ohlc")
        self.assertEqual(payload["count"], 2)
        first = payload["points"][0]
        self.assertEqual(first["open"], 100.0)
        self.assertEqual(first["high"], 120.0)
        self.assertEqual(first["low"], 100.0)
        self.assertEqual(first["close"], 120.0)
        self.assertEqual(first["count"], 2)

    def test_auto_target_date_resolves_to_shanghai_today(self) -> None:
        date, mode = monitor.resolve_target_date("auto")
        self.assertEqual(mode, "auto")
        self.assertEqual(date, datetime.now(monitor.TZ).strftime("%Y%m%d"))

    def test_auto_target_date_rolls_forward_without_restart(self) -> None:
        original_resolve = monitor.resolve_target_date
        original_prepare = monitor.prepare_calculation_context
        original_gate = dashboard.auto_run_market_gate
        sentinel = object()

        monitor.resolve_target_date = lambda value=None: ("20260616", "auto")
        monitor.prepare_calculation_context = lambda date, config: sentinel
        dashboard.auto_run_market_gate = lambda config: (True, 0, "")
        state = self._state(date="20260615")
        state.auto_run = True
        state.pcf_retry_at = 1234.0
        state.auto_error_count = 2
        state.last_error_notify_at = 1000.0
        config = {"target_date": "20260615", "target_date_mode": "auto"}
        context_ref = {"value": object()}
        try:
            rolled = dashboard.maybe_roll_auto_target_date(state, config, context_ref)
        finally:
            monitor.resolve_target_date = original_resolve
            monitor.prepare_calculation_context = original_prepare
            dashboard.auto_run_market_gate = original_gate
        self.assertTrue(rolled)
        self.assertEqual(state.date, "20260616")
        self.assertEqual(config["target_date"], "20260616")
        self.assertIs(context_ref["value"], sentinel)
        self.assertEqual(state.pcf_retry_at, 0.0)
        self.assertEqual(state.auto_error_count, 0)
        self.assertEqual(state.last_error_notify_at, 0.0)
        self.assertEqual(state.last_error, "")
        self.assertIn("日期自动切换", state.last_run_message)

    def test_auto_target_date_rolls_forward_even_when_new_pcf_is_not_ready(self) -> None:
        original_resolve = monitor.resolve_target_date
        original_prepare = monitor.prepare_calculation_context
        original_gate = dashboard.auto_run_market_gate

        def fail_prepare(date, config):  # noqa: ANN001
            raise RuntimeError("PCF未更新或不可读: 20260616")

        monitor.resolve_target_date = lambda value=None: ("20260616", "auto")
        monitor.prepare_calculation_context = fail_prepare
        dashboard.auto_run_market_gate = lambda config: (True, 0, "")
        state = self._state(date="20260615")
        state.auto_run = True
        state.pcf_retry_at = 1234.0
        config = {"target_date": "20260615", "target_date_mode": "auto"}
        context_ref = {"value": object()}
        try:
            rolled = dashboard.maybe_roll_auto_target_date(state, config, context_ref)
        finally:
            monitor.resolve_target_date = original_resolve
            monitor.prepare_calculation_context = original_prepare
            dashboard.auto_run_market_gate = original_gate
        self.assertTrue(rolled)
        self.assertEqual(state.date, "20260616")
        self.assertEqual(config["target_date"], "20260616")
        self.assertIsNone(context_ref["value"])
        self.assertEqual(state.pcf_retry_at, 0.0)
        self.assertIn("PCF未更新或不可读", state.last_error)
        self.assertIn("预加载失败", state.last_run_message)

    def test_health_rolls_auto_date_without_external_preload(self) -> None:
        original_resolve = monitor.resolve_target_date
        original_prepare = monitor.prepare_calculation_context

        def unexpected_prepare(date, config):  # noqa: ANN001
            raise AssertionError("healthcheck should not preload external PCF data")

        monitor.resolve_target_date = lambda value=None: ("20260616", "auto")
        monitor.prepare_calculation_context = unexpected_prepare
        state = self._state(date="20260615")
        state.auto_run = True
        config = {"target_date": "20260615", "target_date_mode": "auto"}
        context_ref = {"value": object()}
        handler = dashboard.make_handler(state, config, context_ref)
        try:
            payload = self._get_local_json(handler, "/health")
        finally:
            monitor.resolve_target_date = original_resolve
            monitor.prepare_calculation_context = original_prepare
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["date"], "20260616")
        self.assertEqual(state.date, "20260616")
        self.assertEqual(config["target_date"], "20260616")
        self.assertIsNone(context_ref["value"])
        self.assertEqual(state.last_error, "")
        self.assertIn("日期自动切换", state.last_run_message)

    def test_fixed_target_date_does_not_roll_forward(self) -> None:
        original_resolve = monitor.resolve_target_date
        monitor.resolve_target_date = lambda value=None: ("20260616", "auto")
        state = self._state(date="20260615")
        state.auto_run = True
        config = {"target_date": "20260615", "target_date_mode": "fixed"}
        context_ref = {"value": object()}
        try:
            rolled = dashboard.maybe_roll_auto_target_date(state, config, context_ref)
        finally:
            monitor.resolve_target_date = original_resolve
        self.assertFalse(rolled)
        self.assertEqual(state.date, "20260615")
        self.assertEqual(config["target_date"], "20260615")
        self.assertEqual(config["target_date_mode"], "fixed")

    def test_auto_context_prepare_is_skipped_outside_market(self) -> None:
        original_gate = dashboard.auto_run_market_gate
        original_prepare = monitor.prepare_calculation_context

        def unexpected_prepare(date, config):  # noqa: ANN001
            raise AssertionError("prepare_calculation_context should not run outside market hours")

        dashboard.auto_run_market_gate = lambda config: (False, 120, "盘外暂停，09:25后恢复自动计算")
        monitor.prepare_calculation_context = unexpected_prepare
        state = self._state()
        context_ref = {"value": object()}
        try:
            error = dashboard.maybe_prepare_auto_context(state, {}, context_ref)
        finally:
            dashboard.auto_run_market_gate = original_gate
            monitor.prepare_calculation_context = original_prepare
        self.assertEqual(error, "")
        self.assertIsNone(context_ref["value"])
        self.assertEqual(state.last_error, "")
        self.assertIn("盘外暂停", state.last_run_message)

    def test_auto_context_prepare_runs_inside_market(self) -> None:
        original_gate = dashboard.auto_run_market_gate
        original_prepare = monitor.prepare_calculation_context
        sentinel = object()

        dashboard.auto_run_market_gate = lambda config: (True, 0, "")
        monitor.prepare_calculation_context = lambda date, config: sentinel
        state = self._state()
        context_ref = {"value": None}
        try:
            error = dashboard.maybe_prepare_auto_context(state, {}, context_ref)
        finally:
            dashboard.auto_run_market_gate = original_gate
            monitor.prepare_calculation_context = original_prepare
        self.assertEqual(error, "")
        self.assertIs(context_ref["value"], sentinel)
        self.assertEqual(state.last_error, "")
        self.assertEqual(state.last_run_message, "已预加载PCF和逐券利息")

    def test_auto_iteration_prepares_context_before_realtime_calculation(self) -> None:
        original_roll = dashboard.maybe_roll_auto_target_date
        original_pause = dashboard.maybe_pause_auto_run_outside_market
        original_prepare = monitor.prepare_calculation_context
        original_run_once = dashboard.run_once_now
        sentinel = object()
        result = self._calculation_result(a_value="120.00")
        calls = {"prepare": 0, "run": 0}

        def fake_prepare(date, config):  # noqa: ANN001
            calls["prepare"] += 1
            return sentinel

        def fake_run_once(config, notify=False, notify_no_alert=True, context=None):  # noqa: ANN001
            calls["run"] += 1
            self.assertIs(context, sentinel)
            return True, "ok", result

        dashboard.maybe_roll_auto_target_date = lambda state, config, context_ref: False
        dashboard.maybe_pause_auto_run_outside_market = lambda state, config: 0
        monitor.prepare_calculation_context = fake_prepare
        dashboard.run_once_now = fake_run_once
        state = self._state()
        context_ref = {"value": None}
        try:
            first_pass, wait_seconds = dashboard.run_auto_iteration(state, {}, context_ref, first_pass=True)
        finally:
            dashboard.maybe_roll_auto_target_date = original_roll
            dashboard.maybe_pause_auto_run_outside_market = original_pause
            monitor.prepare_calculation_context = original_prepare
            dashboard.run_once_now = original_run_once
        self.assertFalse(first_pass)
        self.assertEqual(wait_seconds, state.interval_seconds)
        self.assertEqual(calls, {"prepare": 1, "run": 1})
        self.assertIs(context_ref["value"], sentinel)
        self.assertIs(state.latest_result, result)

    def test_auto_iteration_uses_degraded_candidate_alert_after_strict_quote_failure(self) -> None:
        original_roll = dashboard.maybe_roll_auto_target_date
        original_pause = dashboard.maybe_pause_auto_run_outside_market
        original_run_once = dashboard.run_once_now
        original_calculate_with_context = monitor.calculate_a_with_context
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        sentinel = object()
        degraded_result = self._calculation_result(a_value="350.00")
        degraded_result["price_source"] = "1m_eastmoney"
        degraded_result["strict_realtime"] = False
        degraded_result["quote_times"] = {}
        sent = []

        def fake_send(config, title, text):  # noqa: ANN001
            sent.append({"title": title, "text": text})
            return {"ok": True, "kind": "feishu", "response": {"code": 0}}

        dashboard.maybe_roll_auto_target_date = lambda state, config, context_ref: False
        dashboard.maybe_pause_auto_run_outside_market = lambda state, config: 0
        dashboard.run_once_now = lambda *args, **kwargs: (
            False,
            "RuntimeError: 实时快照不可用，拒绝使用分钟线计算a: eastmoney down",
            None,
        )
        monitor.calculate_a_with_context = lambda context, config: degraded_result
        monitor.send_notification = fake_send
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            state = self._state()
            context_ref = {"value": sentinel}
            try:
                first_pass, wait_seconds = dashboard.run_auto_iteration(
                    state,
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                        "degraded_alert_enabled": True,
                        "notification_retry_delay_seconds": "0",
                    },
                    context_ref,
                    first_pass=True,
                )
            finally:
                dashboard.maybe_roll_auto_target_date = original_roll
                dashboard.maybe_pause_auto_run_outside_market = original_pause
                dashboard.run_once_now = original_run_once
                monitor.calculate_a_with_context = original_calculate_with_context
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertFalse(first_pass)
        self.assertEqual(wait_seconds, state.interval_seconds)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["title"], "511130 a值候选预警（降级行情）")
        self.assertIn("降级行情候选预警", sent[0]["text"])
        self.assertIn("严格实时失败", state.last_run_message)
        self.assertIn("实时快照不可用", state.last_error)

    def test_auto_iteration_never_promotes_degraded_candidate_to_latest_result(self) -> None:
        original_roll = dashboard.maybe_roll_auto_target_date
        original_pause = dashboard.maybe_pause_auto_run_outside_market
        original_run_once = dashboard.run_once_now
        original_calculate_with_context = monitor.calculate_a_with_context
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        degraded_result = self._calculation_result(a_value="350.00")
        degraded_result["price_source"] = "1m_eastmoney"
        degraded_result["strict_realtime"] = False
        degraded_result["quote_times"] = {}

        dashboard.maybe_roll_auto_target_date = lambda state, config, context_ref: False
        dashboard.maybe_pause_auto_run_outside_market = lambda state, config: 0
        dashboard.run_once_now = lambda *args, **kwargs: (
            False,
            "RuntimeError: 实时快照不可用，拒绝使用分钟线计算a: eastmoney down",
            None,
        )
        monitor.calculate_a_with_context = lambda context, config: degraded_result
        monitor.send_notification = lambda *args, **kwargs: {"ok": True, "kind": "feishu", "response": {"code": 0}}
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            state = self._state()
            state.allowed_price_sources = []
            context_ref = {"value": object()}
            try:
                dashboard.run_auto_iteration(
                    state,
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                        "degraded_alert_enabled": True,
                        "notification_retry_delay_seconds": "0",
                    },
                    context_ref,
                    first_pass=True,
                )
            finally:
                dashboard.maybe_roll_auto_target_date = original_roll
                dashboard.maybe_pause_auto_run_outside_market = original_pause
                dashboard.run_once_now = original_run_once
                monitor.calculate_a_with_context = original_calculate_with_context
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertIsNone(state.latest_result)
        self.assertIn("实时快照不可用", state.last_error)

    def test_degraded_candidate_below_threshold_does_not_send_notification(self) -> None:
        original_roll = dashboard.maybe_roll_auto_target_date
        original_pause = dashboard.maybe_pause_auto_run_outside_market
        original_run_once = dashboard.run_once_now
        original_calculate_with_context = monitor.calculate_a_with_context
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        degraded_result = self._calculation_result(a_value="120.00")
        degraded_result["price_source"] = "1m_eastmoney"
        degraded_result["strict_realtime"] = False
        degraded_result["quote_times"] = {}

        dashboard.maybe_roll_auto_target_date = lambda state, config, context_ref: False
        dashboard.maybe_pause_auto_run_outside_market = lambda state, config: 0
        dashboard.run_once_now = lambda *args, **kwargs: (
            False,
            "RuntimeError: 实时快照不可用，拒绝使用分钟线计算a: eastmoney down",
            None,
        )
        monitor.calculate_a_with_context = lambda context, config: degraded_result
        monitor.send_notification = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send"))
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            state = self._state()
            context_ref = {"value": object()}
            try:
                dashboard.run_auto_iteration(
                    state,
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                        "degraded_alert_enabled": True,
                    },
                    context_ref,
                    first_pass=True,
                )
            finally:
                dashboard.maybe_roll_auto_target_date = original_roll
                dashboard.maybe_pause_auto_run_outside_market = original_pause
                dashboard.run_once_now = original_run_once
                monitor.calculate_a_with_context = original_calculate_with_context
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertIsNone(state.latest_result)
        self.assertIn("严格实时失败", state.last_run_message)

    def test_auto_run_market_gate_allows_trading_session(self) -> None:
        now = datetime(2026, 6, 16, 10, 0, tzinfo=dashboard.TZ)
        market_open, wait_seconds, message = dashboard.auto_run_market_gate({}, now=now)
        self.assertTrue(market_open)
        self.assertEqual(wait_seconds, 0)
        self.assertEqual(message, "")

    def test_auto_run_market_gate_pauses_overnight(self) -> None:
        now = datetime(2026, 6, 16, 2, 30, tzinfo=dashboard.TZ)
        market_open, wait_seconds, message = dashboard.auto_run_market_gate({}, now=now)
        self.assertFalse(market_open)
        self.assertGreater(wait_seconds, 0)
        self.assertIn("09:25", message)

    def test_auto_run_market_gate_pauses_weekend_until_monday(self) -> None:
        now = datetime(2026, 6, 13, 10, 0, tzinfo=dashboard.TZ)
        market_open, wait_seconds, message = dashboard.auto_run_market_gate({}, now=now)
        self.assertFalse(market_open)
        self.assertGreater(wait_seconds, 0)
        self.assertIn("06-15 09:25", message)

    def test_auto_run_market_gate_pauses_exchange_holiday_until_next_trading_day(self) -> None:
        now = datetime(2026, 6, 19, 10, 0, tzinfo=dashboard.TZ)
        market_open, wait_seconds, message = dashboard.auto_run_market_gate(
            {"auto_run_closed_dates": ["20260619"]},
            now=now,
        )
        self.assertFalse(market_open)
        self.assertGreater(wait_seconds, 0)
        self.assertIn("休市暂停", message)
        self.assertIn("06-22 09:25", message)

    def test_auto_run_market_gate_can_be_disabled(self) -> None:
        now = datetime(2026, 6, 13, 10, 0, tzinfo=dashboard.TZ)
        market_open, wait_seconds, message = dashboard.auto_run_market_gate(
            {"auto_run_market_hours_only": False},
            now=now,
        )
        self.assertTrue(market_open)
        self.assertEqual(wait_seconds, 0)
        self.assertEqual(message, "")

    def test_pause_auto_run_outside_market_clears_error_state(self) -> None:
        original_gate = dashboard.auto_run_market_gate
        dashboard.auto_run_market_gate = lambda config: (False, 120, "盘外暂停，09:25后恢复自动计算")
        state = self._state()
        state.last_error = "RuntimeError: 行情过旧"
        state.auto_error_count = 3
        state.pcf_retry_at = 1000.0
        try:
            wait_seconds = dashboard.maybe_pause_auto_run_outside_market(state, {})
        finally:
            dashboard.auto_run_market_gate = original_gate
        self.assertEqual(wait_seconds, 120)
        self.assertEqual(state.last_error, "")
        self.assertEqual(state.auto_error_count, 0)
        self.assertEqual(state.pcf_retry_at, 0.0)
        self.assertIn("盘外暂停", state.last_run_message)

    def test_load_state_uses_empty_state_when_json_is_corrupt(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("{not-json", encoding="utf-8")
            monitor.STATE_PATH = state_path
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                state = monitor.load_state()
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(state, {"dates": {}})

    def test_load_state_sanitizes_malformed_sections(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "dates": {
                            "20260615": {"active_thresholds": ["300"]},
                            "bad": [],
                        },
                        "interest_cache": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            monitor.STATE_PATH = state_path
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                state = monitor.load_state()
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(state["dates"], {"20260615": {"active_thresholds": ["300"]}})
        self.assertEqual(state["interest_cache"], {})

    def test_handle_result_survives_a_value_log_write_failure(self) -> None:
        original_append = monitor.append_result
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            monitor.append_result = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("volume read-only"))
            try:
                code = monitor.handle_calculation_result(
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                    self._calculation_result(a_value="120.00"),
                    notify=False,
                )
            finally:
                monitor.append_result = original_append
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(code, 0)

    def test_data_payload_uses_in_memory_result_when_disk_write_is_unavailable(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp)
            state = self._state()
            state.allowed_price_sources = ["realtime_eastmoney"]
            state.latest_result = self._calculation_result(a_value="120.00")
            try:
                payload = dashboard.build_data_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
                health = dashboard.build_health_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["latest_a"], "120.00")
        self.assertTrue(payload["chart_current"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["status"]["code"], "normal")
        self.assertEqual(payload["components"][0]["code"], "019776")
        self.assertTrue(health["data_ok"])

    def test_data_payload_survives_malformed_run_files(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day_dir = root / "20260615"
            day_dir.mkdir(parents=True)
            (day_dir / "a_values.jsonl").mkdir()
            (day_dir / "alerts.jsonl").mkdir()
            (day_dir / "notifications.jsonl").mkdir()
            monitor.RUNS_DIR = root
            state = self._state()
            state.allowed_price_sources = ["realtime_eastmoney"]
            state.latest_result = self._calculation_result(a_value="120.00")
            try:
                payload = dashboard.build_data_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
                health = dashboard.build_health_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["latest_a"], "120.00")
        self.assertEqual(payload["latest_alert"], {})
        self.assertEqual(payload["latest_notification"], {})
        self.assertTrue(health["data_ok"])

    def test_load_points_survives_malformed_timestamp_type(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            row = self._history_row("2026-06-15 09:30:01", "120")
            row["timestamp"] = None
            self._write_jsonl(root, "20260615", [row])
            monitor.RUNS_DIR = root
            try:
                points = dashboard.load_points("20260615")
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["timestamp"], "")
        self.assertEqual(points[0]["estimated_a"], 120.0)

    def test_data_payload_survives_malformed_component_fields(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp)
            state = self._state()
            state.allowed_price_sources = ["realtime_eastmoney"]
            result = self._calculation_result(a_value="120.00")
            result["components"][0]["code"] = 19776
            result["components"][0]["name"] = 25
            state.latest_result = result
            try:
                payload = dashboard.build_data_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(payload["latest_a"], "120.00")
        self.assertEqual(payload["components"][0]["code"], "19776")
        self.assertEqual(payload["components"][0]["name"], "25")

    def test_available_dates_survives_runs_path_not_directory(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            runs_path = Path(tmp) / "runs"
            runs_path.write_text("not a directory", encoding="utf-8")
            monitor.RUNS_DIR = runs_path
            try:
                dates = dashboard.available_dates()
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertEqual(dates, [])

    def test_health_data_ok_ignores_notification_only_error(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp)
            state = self._state()
            state.allowed_price_sources = ["realtime_eastmoney"]
            state.latest_result = self._calculation_result(a_value="120.00")
            state.last_error = "RuntimeError: 通知失败: 飞书通知失败: code=19021"
            try:
                health = dashboard.build_health_payload(
                    state,
                    {
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
        self.assertTrue(health["data_ok"])
        self.assertEqual(health["diagnostics"]["quote"]["code"], "ok")
        self.assertEqual(health["last_error"], "RuntimeError: 通知失败: 飞书通知失败: code=19021")

    def test_api_data_returns_json_when_payload_builder_raises(self) -> None:
        original_build = dashboard.build_data_payload

        def fail_build(state, config):  # noqa: ANN001
            raise RuntimeError("synthetic api failure")

        dashboard.build_data_payload = fail_build
        handler = dashboard.make_handler(self._state(), {}, {"value": None})
        try:
            payload = self._get_local_json(handler, "/api/data")
        finally:
            dashboard.build_data_payload = original_build
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["route"], "/api/data")
        self.assertIn("synthetic api failure", payload["error"])

    def test_run_once_keeps_result_when_notification_delivery_fails(self) -> None:
        original_calculate = monitor.calculate_a
        original_handle = monitor.handle_calculation_result
        result = self._calculation_result(a_value="120.00")

        monitor.calculate_a = lambda date, config: result

        def fail_notification(config, result, notify=False, notify_no_alert=True):  # noqa: ANN001
            self.assertTrue(notify)
            raise RuntimeError("飞书通知失败: code=19021")

        monitor.handle_calculation_result = fail_notification
        try:
            ok, msg, returned = dashboard.run_once_now(
                {
                    "target_date": "20260615",
                    "require_realtime_snapshot": True,
                    "strict_realtime_price_sources": ["realtime_eastmoney"],
                },
                notify=True,
            )
        finally:
            monitor.calculate_a = original_calculate
            monitor.handle_calculation_result = original_handle
        self.assertTrue(ok)
        self.assertIn("通知失败但计算成功", msg)
        self.assertIs(returned, result)

    def test_detect_alerts_uses_memory_state_when_state_file_write_fails(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.mkdir()
            monitor.STATE_PATH = state_path
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                first = monitor.detect_alerts(
                    "20260615",
                    Decimal("350"),
                    [Decimal("300")],
                    True,
                    {"require_realtime_snapshot": True},
                    self._calculation_result(a_value="350.00"),
                )
                second = monitor.detect_alerts(
                    "20260615",
                    Decimal("360"),
                    [Decimal("300")],
                    True,
                    {"require_realtime_snapshot": True},
                    self._calculation_result(a_value="360.00"),
                )
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(first, [Decimal("300")])
        self.assertEqual(second, [])

    def test_detect_alerts_triggers_negative_thresholds(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                first = monitor.detect_alerts(
                    "20260615",
                    Decimal("-350"),
                    [Decimal("300"), Decimal("500")],
                    True,
                    {"require_realtime_snapshot": True},
                    self._calculation_result(a_value="-350.00"),
                )
                second = monitor.detect_alerts(
                    "20260615",
                    Decimal("-360"),
                    [Decimal("300"), Decimal("500")],
                    True,
                    {"require_realtime_snapshot": True},
                    self._calculation_result(a_value="-360.00"),
                )
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(first, [Decimal("-300")])
        self.assertEqual(second, [])

    def test_save_interest_cache_is_best_effort(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.mkdir()
            monitor.STATE_PATH = state_path
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                monitor.save_interest_cache({"019776": (Decimal("0.267"), "manual_override")})
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)

    def test_eastmoney_external_curl_fallback_keeps_strict_realtime_checks(self) -> None:
        original_request_json = monitor.request_json
        original_external = monitor.request_json_external_curl
        stamp = int(datetime.now(monitor.TZ).timestamp())
        date = datetime.fromtimestamp(stamp, monitor.TZ).strftime("%Y%m%d")

        def fail_primary(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("primary failed")

        def external_payload(*args, **kwargs):  # noqa: ANN002, ANN003
            return {
                "data": {
                    "diff": [
                        {"f12": "511130", "f2": "105.853", "f124": stamp},
                        {"f12": "019776", "f2": "92.702", "f124": stamp},
                        {"f12": "019837", "f2": "99.552", "f124": stamp},
                    ]
                }
            }

        monitor.request_json = fail_primary
        monitor.request_json_external_curl = external_payload
        try:
            source, _, prices, meta = monitor.fetch_eastmoney_realtime_prices(
                date,
                ["511130", "019776", "019837"],
                {"realtime_max_skew_seconds": 3, "realtime_max_stale_seconds": 30},
            )
        finally:
            monitor.request_json = original_request_json
            monitor.request_json_external_curl = original_external
        self.assertEqual(source, "realtime_eastmoney")
        self.assertEqual(prices["511130"], Decimal("105.853"))
        self.assertEqual(meta["transport"], "external_curl")
        self.assertTrue(meta["strict_realtime"])

    def test_sina_quote_boards_parse_five_level_book(self) -> None:
        original_request_text = monitor.request_text
        sample = (
            'var hq_str_sh511130="30年国债ETF博时,106.133,106.125,106.120,106.243,106.093,'
            '106.120,106.126,15079794,1600811282.000,200,106.120,200,106.119,800,106.117,'
            '25200,106.115,900,106.110,200,106.126,100,106.127,1100,106.130,100,106.134,'
            '100,106.142,2026-06-24,15:00:00,00,";'
        )
        monitor.request_text = lambda *args, **kwargs: sample
        try:
            boards = monitor.fetch_sina_quote_boards("20260624", ["511130"], {})
        finally:
            monitor.request_text = original_request_text
        board = boards["511130"]
        self.assertTrue(board["valid_for_target_date"])
        self.assertEqual(board["last"], "106.120")
        self.assertEqual(board["change"], "-0.005")
        self.assertEqual(board["pct_change"], "-0.00")
        self.assertEqual(board["bids"][0], {"level": 1, "side": "bid", "price": "106.120", "quantity": "200"})
        self.assertEqual(board["asks"][4], {"level": 5, "side": "ask", "price": "106.142", "quantity": "100"})

    def test_dashboard_quote_cards_merge_orderbook_and_security_series(self) -> None:
        original_runs_dir = monitor.RUNS_DIR
        original_fetch_boards = monitor.fetch_sina_quote_boards
        original_fetch_1m = monitor.fetch_eastmoney_1m
        original_cache = dict(dashboard.QUOTE_BOARD_CACHE)
        original_intraday_cache = dict(dashboard.SECURITY_INTRADAY_CACHE)
        today = datetime.now(dashboard.TZ).strftime("%Y%m%d")
        date_iso = f"{today[:4]}-{today[4:6]}-{today[6:]}"
        ts1 = datetime.now(dashboard.TZ).strftime("%Y-%m-%d %H:%M:%S")
        ts2 = (datetime.now(dashboard.TZ) + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        first = self._calculation_result("120.00")
        second = self._calculation_result("121.00")
        for row, ts, etf_quote in ((first, ts1, "105.000"), (second, ts2, "105.100")):
            row["date"] = today
            row["timestamp"] = ts
            row["quote_times"] = {"511130": ts, "019776": ts, "019837": ts}
            row["price_source"] = "realtime_eastmoney"
            row["strict_realtime"] = True
            row["etf_quote"] = etf_quote
        boards = {
            "511130": {
                "code": "511130",
                "name": "30年国债ETF博时",
                "source": "sina_hq_snapshot",
                "quote_time": f"{today[:4]}-{today[4:6]}-{today[6:]} 09:30:02",
                "valid_for_target_date": True,
                "last": "105.100",
                "change": "0.100",
                "pct_change": "0.10",
                "open": "105.000",
                "previous_close": "105.000",
                "volume": "1000",
                "amount": "105100.00",
                "bids": [{"level": 1, "side": "bid", "price": "105.099", "quantity": "10"}],
                "asks": [{"level": 1, "side": "ask", "price": "105.101", "quantity": "20"}],
            }
        }
        monitor.fetch_sina_quote_boards = lambda date, codes, config: boards
        monitor.fetch_eastmoney_1m = lambda code, **kwargs: {
            f"{date_iso} 09:31": Decimal("105.001" if code == "511130" else "92.500")
        }
        dashboard.QUOTE_BOARD_CACHE.update({"key": "", "expires_at": 0.0, "boards": {}, "error": ""})
        dashboard.SECURITY_INTRADAY_CACHE.update({"key": "", "expires_at": 0.0, "series": {}, "error": ""})
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp)
            self._write_jsonl(Path(tmp), today, [first, second])
            try:
                state = dashboard.DashboardState(
                    date=today,
                    interval_seconds=3,
                    max_points=300,
                    thresholds=[Decimal("300")],
                    auto_run=False,
                    auto_run_notify=False,
                    allowed_price_sources=["realtime_eastmoney"],
                    max_stale_seconds=30,
                    public_readonly=False,
                )
                payload = dashboard.build_data_payload(
                    state,
                    {
                        "fund_code": "511130",
                        "expected_component_codes": ["019776", "019837"],
                        "require_realtime_snapshot": True,
                    },
                )
            finally:
                monitor.RUNS_DIR = original_runs_dir
                monitor.fetch_sina_quote_boards = original_fetch_boards
                monitor.fetch_eastmoney_1m = original_fetch_1m
                dashboard.QUOTE_BOARD_CACHE.clear()
                dashboard.QUOTE_BOARD_CACHE.update(original_cache)
                dashboard.SECURITY_INTRADAY_CACHE.clear()
                dashboard.SECURITY_INTRADAY_CACHE.update(original_intraday_cache)
        cards = {card["code"]: card for card in payload["quote_cards"]}
        self.assertIn("511130", cards)
        self.assertIn("019776", cards)
        self.assertEqual(cards["511130"]["price"], "105.100")
        self.assertEqual(cards["511130"]["bids"][0]["price"], "105.099")
        self.assertEqual(len(cards["511130"]["series"]), 3)
        self.assertEqual(cards["511130"]["series"][0]["price"], 105.001)
        self.assertEqual(cards["019776"]["series"][-1]["price"], 92.702)

    def test_dashboard_html_uses_connected_four_card_market_strip(self) -> None:
        html = dashboard.build_dashboard_html(self._state(), [])
        self.assertIn('class="quote-strip-scroll"', html)
        self.assertIn("grid-template-columns: repeat(4, minmax(260px, 1fr))", html)
        self.assertIn("function renderAValueCard", html)
        self.assertIn('renderQuoteCards(payload.quote_cards || [], payload.quote_cards_notice || "", payload);', html)
        self.assertLess(html.index('class="quote-section"'), html.index('class="panel chart-panel"'))
        self.assertNotIn('class="primary-value"', html)
        self.assertNotIn('id="latestA"', html)
        self.assertIn("历史曲线 / a-K线", html)
        self.assertNotIn(".quote-grid { grid-template-columns: 1fr; }", html)

    def test_realtime_source_lock_rejects_unconfigured_fallback(self) -> None:
        original_eastmoney = monitor.fetch_eastmoney_realtime_prices
        original_sina = monitor.fetch_sina_realtime_prices
        calls = {"sina": 0}

        def fail_eastmoney(date, codes, config):  # noqa: ANN001
            raise RuntimeError("eastmoney down")

        def succeed_sina(date, codes, config):  # noqa: ANN001
            calls["sina"] += 1
            return "realtime_sina_snapshot", "2026-06-16 09:30:00", {code: Decimal("100") for code in codes}, {
                "strict_realtime": True
            }

        monitor.fetch_eastmoney_realtime_prices = fail_eastmoney
        monitor.fetch_sina_realtime_prices = succeed_sina
        try:
            with self.assertRaisesRegex(RuntimeError, "eastmoney down"):
                monitor.fetch_aligned_prices(
                    "20260616",
                    ["511130", "019776", "019837"],
                    {
                        "prefer_realtime_snapshot": True,
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                )
        finally:
            monitor.fetch_eastmoney_realtime_prices = original_eastmoney
            monitor.fetch_sina_realtime_prices = original_sina
        self.assertEqual(calls["sina"], 0)

    def test_feishu_notification_requires_business_code(self) -> None:
        original_post = monitor.post_json
        monitor.post_json = lambda *args, **kwargs: {}
        try:
            with self.assertRaisesRegex(RuntimeError, "缺少业务响应码"):
                monitor.send_notification(
                    {"notification": {"webhook_url": "https://example.invalid/hook", "default_webhook_kind": "feishu"}},
                    "title",
                    "body",
                )
        finally:
            monitor.post_json = original_post

    def test_feishu_notification_accepts_status_code_zero(self) -> None:
        original_post = monitor.post_json
        monitor.post_json = lambda *args, **kwargs: {"StatusCode": 0, "StatusMessage": "success"}
        try:
            result = monitor.send_notification(
                {"notification": {"webhook_url": "https://example.invalid/hook", "default_webhook_kind": "feishu"}},
                "title",
                "body",
            )
        finally:
            monitor.post_json = original_post
        self.assertIsInstance(result, dict)
        self.assertTrue(result["ok"])
        self.assertEqual(result["response"]["StatusCode"], 0)

    def test_feishu_signature_timestamp_can_use_http_date(self) -> None:
        original_http_date = monitor.http_date_timestamp
        monitor.http_date_timestamp = lambda url, timeout=3: 1781582400
        try:
            timestamp, source = monitor.feishu_signature_timestamp({}, {})
        finally:
            monitor.http_date_timestamp = original_http_date
        self.assertEqual(timestamp, 1781582400)
        self.assertEqual(source, "http_date")

    def test_feishu_notification_reports_signature_timestamp_source(self) -> None:
        original_post = monitor.post_json
        original_timestamp = monitor.feishu_signature_timestamp
        captured = {}

        def fake_post(url, payload, timeout=15):  # noqa: ANN001
            captured.update(payload)
            return {"code": 0, "msg": "success"}

        monitor.post_json = fake_post
        monitor.feishu_signature_timestamp = lambda notification, config: (1781582400, "http_date")
        old_secret = os.environ.get("TEST_A_MONITOR_FEISHU_SIGN_SECRET")
        try:
            os.environ["TEST_A_MONITOR_FEISHU_SIGN_SECRET"] = "test-secret"
            result = monitor.send_notification(
                {
                    "notification": {
                        "webhook_url": "https://example.invalid/hook",
                        "default_webhook_kind": "feishu",
                        "feishu_secret_env": "TEST_A_MONITOR_FEISHU_SIGN_SECRET",
                    }
                },
                "title",
                "body",
            )
        finally:
            monitor.post_json = original_post
            monitor.feishu_signature_timestamp = original_timestamp
            if old_secret is None:
                os.environ.pop("TEST_A_MONITOR_FEISHU_SIGN_SECRET", None)
            else:
                os.environ["TEST_A_MONITOR_FEISHU_SIGN_SECRET"] = old_secret
        self.assertEqual(captured["timestamp"], "1781582400")
        self.assertEqual(result["signature_timestamp_source"], "http_date")

    def test_notification_setup_is_redacted(self) -> None:
        config = {
            "notification": {
                "webhook_url_env": "TEST_A_MONITOR_WEBHOOK_URL",
                "webhook_kind_env": "TEST_A_MONITOR_WEBHOOK_KIND",
                "feishu_secret_env": "TEST_A_MONITOR_FEISHU_SECRET",
                "default_webhook_kind": "feishu",
            }
        }
        env_keys = [
            "TEST_A_MONITOR_WEBHOOK_URL",
            "TEST_A_MONITOR_WEBHOOK_KIND",
            "TEST_A_MONITOR_FEISHU_SECRET",
        ]
        old_values = {key: os.environ.get(key) for key in env_keys}
        try:
            os.environ["TEST_A_MONITOR_WEBHOOK_URL"] = "https://open.feishu.cn/open-apis/bot/v2/hook/secret-url"
            os.environ["TEST_A_MONITOR_WEBHOOK_KIND"] = "feishu"
            os.environ["TEST_A_MONITOR_FEISHU_SECRET"] = "super-secret-signing-key"
            setup = monitor.notification_setup(config)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        encoded = json.dumps(setup, ensure_ascii=False)
        self.assertTrue(setup["webhook_configured"])
        self.assertEqual(setup["webhook_source"], "env")
        self.assertEqual(setup["kind"], "feishu")
        self.assertTrue(setup["feishu_secret_configured"])
        self.assertNotIn("secret-url", encoded)
        self.assertNotIn("super-secret-signing-key", encoded)

    def test_accuracy_setup_exposes_fail_closed_guards(self) -> None:
        state = self._state(date="20260616")
        state.allowed_price_sources = ["realtime_eastmoney"]
        config = {
            "fund_code": "511130",
            "expected_component_codes": ["019776", "019837"],
            "require_realtime_snapshot": True,
            "prefer_realtime_snapshot": True,
            "realtime_max_skew_seconds": 3,
            "allow_missing_interest_fallback": False,
            "interest_overrides": {"20260616": {"019776": "0.267"}},
            "data_sources": {
                "pcf": "bosera",
                "interest": "sse_netfull_then_manual_override",
                "intraday": "eastmoney_realtime_snapshot_only",
            },
        }
        setup = dashboard.accuracy_setup(config, state)
        self.assertEqual(setup["formula_version"], "estimated_a_v1")
        self.assertTrue(setup["pcf_structure_locked"])
        self.assertEqual(setup["expected_component_codes"], ["019776", "019837"])
        self.assertTrue(setup["strict_realtime_required"])
        self.assertEqual(setup["max_skew_seconds"], 3)
        self.assertEqual(setup["max_stale_seconds"], 30)
        self.assertFalse(setup["missing_interest_fallback_allowed"])
        self.assertTrue(setup["interest_overrides_for_date"])
        self.assertEqual(setup["interest_override_codes_for_date"], ["019776"])

    def test_alert_policy_setup_exposes_threshold_only_notification_policy(self) -> None:
        setup = dashboard.alert_policy_setup(
            {
                "degraded_alert_enabled": True,
                "degraded_alert_title": "511130 a值候选预警（降级行情）",
                "degraded_alert_source_mode": "degraded_price_source_v1",
                "notification_attempts": 3,
                "notification_retry_delay_seconds": "1",
            }
        )
        self.assertTrue(setup["threshold_only_notifications"])
        self.assertFalse(setup["runtime_error_notifications"])
        self.assertFalse(setup["no_alert_run_check_notifications"])
        self.assertTrue(setup["degraded_alert_enabled"])
        self.assertEqual(setup["degraded_alert_source_mode"], "degraded_price_source_v1")
        self.assertEqual(setup["notification_attempts"], 3)

    def test_smoke_check_accepts_new_health_and_data_payloads(self) -> None:
        health = {
            "target_date_mode": "auto",
            "process_ok": True,
            "data_ok": False,
            "diagnostics": {},
            "notification_setup": {"webhook_configured": True, "kind": "feishu", "feishu_secret_configured": True},
            "accuracy_setup": {
                "formula_version": "estimated_a_v1",
                "intraday_source": "eastmoney_realtime_snapshot_only",
                "allowed_price_sources": ["realtime_eastmoney"],
                "strict_realtime_required": True,
                "max_skew_seconds": 3,
                "max_stale_seconds": 30,
                "missing_interest_fallback_allowed": False,
                "expected_component_codes": ["019776", "019837"],
            },
            "alert_policy_setup": {
                "threshold_only_notifications": True,
                "runtime_error_notifications": False,
                "no_alert_run_check_notifications": False,
                "degraded_alert_enabled": True,
                "degraded_alert_source_mode": "degraded_price_source_v1",
                "notification_attempts": 3,
            },
            "auto_loop": {"code": "running"},
        }
        data = {
            "ok": True,
            "status": {"code": "waiting"},
            "config": {"notification_setup": {}, "accuracy_setup": {}, "alert_policy_setup": {}},
            "chart_current": False,
        }
        self.assertEqual(smoke_check.validate_health(health, 200), [])
        self.assertEqual(smoke_check.validate_data(data, 200), [])

    def test_smoke_check_rejects_old_realtime_source_policy(self) -> None:
        health = {
            "target_date_mode": "auto",
            "process_ok": True,
            "data_ok": False,
            "diagnostics": {},
            "notification_setup": {"webhook_configured": True, "kind": "feishu", "feishu_secret_configured": True},
            "accuracy_setup": {
                "formula_version": "estimated_a_v1",
                "intraday_source": "optional_xtquant_then_eastmoney_realtime_then_sina_realtime_snapshot",
                "allowed_price_sources": ["realtime_xtquant", "realtime_eastmoney", "realtime_sina_snapshot"],
                "strict_realtime_required": True,
                "max_skew_seconds": 3,
                "max_stale_seconds": 30,
                "missing_interest_fallback_allowed": False,
                "expected_component_codes": ["019776", "019837"],
            },
            "alert_policy_setup": {
                "threshold_only_notifications": True,
                "runtime_error_notifications": False,
                "no_alert_run_check_notifications": False,
                "degraded_alert_enabled": True,
                "degraded_alert_source_mode": "degraded_price_source_v1",
                "notification_attempts": 3,
            },
            "auto_loop": {"code": "running"},
        }
        issues = smoke_check.validate_health(health, 200)
        self.assertTrue(any("盘中行情源不是东方财富实时锁定" in issue for issue in issues))
        self.assertTrue(any("严格实时价格源不是单一东方财富" in issue for issue in issues))

    def test_smoke_check_rejects_missing_alert_policy(self) -> None:
        health = {
            "target_date_mode": "auto",
            "process_ok": True,
            "data_ok": False,
            "diagnostics": {},
            "notification_setup": {"webhook_configured": True, "kind": "feishu", "feishu_secret_configured": True},
            "accuracy_setup": {
                "formula_version": "estimated_a_v1",
                "intraday_source": "eastmoney_realtime_snapshot_only",
                "allowed_price_sources": ["realtime_eastmoney"],
                "strict_realtime_required": True,
                "max_skew_seconds": 3,
                "max_stale_seconds": 30,
                "missing_interest_fallback_allowed": False,
                "expected_component_codes": ["019776", "019837"],
            },
            "alert_policy_setup": {
                "threshold_only_notifications": False,
                "runtime_error_notifications": True,
                "no_alert_run_check_notifications": True,
                "degraded_alert_enabled": False,
                "degraded_alert_source_mode": "legacy_price_source",
                "notification_attempts": 1,
            },
            "auto_loop": {"code": "running"},
        }
        issues = smoke_check.validate_health(health, 200)
        self.assertTrue(any("threshold_only_notifications不是true" in issue for issue in issues))
        self.assertTrue(any("runtime_error_notifications不是false" in issue for issue in issues))
        self.assertTrue(any("degraded_alert_enabled不是true" in issue for issue in issues))
        self.assertTrue(any("notification_attempts小于3" in issue for issue in issues))

    def test_smoke_check_rejects_old_deploy_health_payload(self) -> None:
        old_health = {
            "ok": True,
            "service": "511130-live-dashboard",
            "date": "20260615",
            "last_run_message": "自动计算失败",
        }
        issues = smoke_check.validate_health(old_health, 200)
        self.assertTrue(any("缺少新版本字段" in issue for issue in issues))

    def test_health_exposes_redacted_notification_setup(self) -> None:
        config = {
            "target_date_mode": "auto",
            "notification": {
                "webhook_url_env": "TEST_A_MONITOR_HEALTH_WEBHOOK_URL",
                "webhook_kind_env": "TEST_A_MONITOR_HEALTH_KIND",
                "feishu_secret_env": "TEST_A_MONITOR_HEALTH_SECRET",
                "default_webhook_kind": "feishu",
            },
        }
        old_values = {
            "TEST_A_MONITOR_HEALTH_WEBHOOK_URL": os.environ.get("TEST_A_MONITOR_HEALTH_WEBHOOK_URL"),
            "TEST_A_MONITOR_HEALTH_KIND": os.environ.get("TEST_A_MONITOR_HEALTH_KIND"),
            "TEST_A_MONITOR_HEALTH_SECRET": os.environ.get("TEST_A_MONITOR_HEALTH_SECRET"),
        }
        state = self._state()
        try:
            os.environ["TEST_A_MONITOR_HEALTH_WEBHOOK_URL"] = "https://example.invalid/private-hook"
            os.environ["TEST_A_MONITOR_HEALTH_KIND"] = "feishu"
            os.environ.pop("TEST_A_MONITOR_HEALTH_SECRET", None)
            health = dashboard.build_health_payload(state, config)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        encoded = json.dumps(health, ensure_ascii=False)
        self.assertTrue(health["notification_configured"])
        self.assertEqual(health["notification_setup"]["webhook_env"], "TEST_A_MONITOR_HEALTH_WEBHOOK_URL")
        self.assertFalse(health["notification_setup"]["feishu_secret_configured"])
        self.assertTrue(health["accuracy_setup"]["strict_realtime_required"])
        self.assertNotIn("private-hook", encoded)

    def test_wecom_notification_requires_business_code(self) -> None:
        original_post = monitor.post_json
        monitor.post_json = lambda *args, **kwargs: {}
        try:
            with self.assertRaisesRegex(RuntimeError, "缺少业务响应码"):
                monitor.send_notification(
                    {"notification": {"webhook_url": "https://example.invalid/hook", "default_webhook_kind": "wecom"}},
                    "title",
                    "body",
                )
        finally:
            monitor.post_json = original_post

    def test_auto_error_notification_never_sends_even_if_enabled(self) -> None:
        original_configured = monitor.notification_configured
        original_send = monitor.send_notification
        original_append = monitor.safe_append_notification_event
        calls = {"send": 0, "append": 0}

        monitor.notification_configured = lambda config: True
        monitor.send_notification = lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1)
        monitor.safe_append_notification_event = lambda *args, **kwargs: calls.__setitem__("append", calls["append"] + 1)
        state = self._state()
        config = {"notify_on_auto_error": True, "auto_error_notify_min_interval_seconds": 600}
        try:
            state.auto_error_count = 1
            self.assertFalse(dashboard.maybe_notify_auto_error(state, config, "boom", now=1000.0))
            state.auto_error_count = 2
            self.assertFalse(dashboard.maybe_notify_auto_error(state, config, "boom", now=1001.0))
            state.auto_error_count = 3
            self.assertFalse(dashboard.maybe_notify_auto_error(state, config, "boom", now=1601.0))
        finally:
            monitor.notification_configured = original_configured
            monitor.send_notification = original_send
            monitor.safe_append_notification_event = original_append
        self.assertEqual(calls, {"send": 0, "append": 0})
        self.assertEqual(state.last_error_notify_at, 0.0)

    def test_auto_error_notification_is_disabled_by_default(self) -> None:
        original_configured = monitor.notification_configured
        original_send = monitor.send_notification
        monitor.notification_configured = lambda config: True
        monitor.send_notification = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not send"))
        state = self._state()
        state.auto_error_count = 1
        try:
            self.assertFalse(dashboard.maybe_notify_auto_error(state, {}, "RuntimeError: 缺少逐券应计利息", now=1000.0))
        finally:
            monitor.notification_configured = original_configured
            monitor.send_notification = original_send

    def test_precheck_notify_never_sends_pcf_or_interest_checks(self) -> None:
        original_fetch_pcf = monitor.fetch_pcf
        original_send = monitor.send_notification
        calls = {"send": 0}

        def fail_fetch_pcf(date, fund_code):  # noqa: ANN001
            raise RuntimeError("PCF未更新或不可读")

        monitor.fetch_pcf = fail_fetch_pcf
        monitor.send_notification = lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1)
        try:
            code = monitor.mode_precheck(
                {"target_date": "20260619", "compare_date": "20260612", "fund_code": "511130"},
                notify=True,
            )
        finally:
            monitor.fetch_pcf = original_fetch_pcf
            monitor.send_notification = original_send
        self.assertEqual(code, 0)
        self.assertEqual(calls["send"], 0)

    def test_notify_test_does_not_require_market_data(self) -> None:
        original_send = monitor.send_notification
        original_append = monitor.safe_append_notification_event
        events = []

        def fake_send(config, title, text):  # noqa: ANN001
            self.assertIn("不依赖PCF/价格", text)
            return {
                "ok": True,
                "kind": "feishu",
                "sent_at": "2026-06-16T09:30:00.000+08:00",
                "elapsed_ms": 8,
                "response": {"code": 0},
            }

        monitor.send_notification = fake_send
        monitor.safe_append_notification_event = lambda *args, **kwargs: events.append((args, kwargs)) or True
        state = self._state()
        try:
            ok, msg = dashboard.send_notify_test_now(state, {})
        finally:
            monitor.send_notification = original_send
            monitor.safe_append_notification_event = original_append
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
        self.assertEqual(state.last_run_message, "飞书测试已发送")
        self.assertEqual(state.last_error, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["status"], "sent")

    def test_notify_test_surfaces_business_failure(self) -> None:
        original_send = monitor.send_notification
        original_append = monitor.safe_append_notification_event
        events = []

        def fail_send(config, title, text):  # noqa: ANN001
            raise RuntimeError("飞书通知失败: code=19021")

        monitor.send_notification = fail_send
        monitor.safe_append_notification_event = lambda *args, **kwargs: events.append((args, kwargs)) or True
        state = self._state()
        try:
            ok, msg = dashboard.send_notify_test_now(state, {})
        finally:
            monitor.send_notification = original_send
            monitor.safe_append_notification_event = original_append
        self.assertFalse(ok)
        self.assertIn("code=19021", msg)
        self.assertEqual(state.last_run_message, "飞书测试失败")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["status"], "failed")

    def test_notify_test_success_survives_notification_log_write_failure(self) -> None:
        original_send = monitor.send_notification
        original_append = monitor.safe_append_notification_event

        monitor.send_notification = lambda config, title, text: {
            "ok": True,
            "kind": "feishu",
            "sent_at": "2026-06-16T09:30:00.000+08:00",
            "elapsed_ms": 8,
            "response": {"code": 0},
        }
        monitor.safe_append_notification_event = lambda *args, **kwargs: False
        state = self._state()
        try:
            ok, msg = dashboard.send_notify_test_now(state, {})
        finally:
            monitor.send_notification = original_send
            monitor.safe_append_notification_event = original_append
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")
        self.assertEqual(state.last_error, "")

    def test_successful_no_alert_does_not_send_run_check(self) -> None:
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        sent = []
        monitor.send_notification = lambda *args, **kwargs: sent.append((args, kwargs)) or {"ok": True}
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                code = monitor.handle_calculation_result(
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                    },
                    self._calculation_result(a_value="120.00"),
                    notify=True,
                    notify_no_alert=False,
                )
            finally:
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(code, 0)
        self.assertEqual(sent, [])

    def test_threshold_alert_sends_notification(self) -> None:
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        sent = []

        def fake_send(config, title, text):  # noqa: ANN001
            sent.append({"title": title, "text": text})
            return {"ok": True, "kind": "feishu", "response": {"code": 0}}

        monitor.send_notification = fake_send
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                code = monitor.handle_calculation_result(
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                    },
                    self._calculation_result(a_value="350.00"),
                    notify=True,
                    notify_no_alert=False,
                )
            finally:
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["title"], "511130 a值预警")
        self.assertIn("触发档位", sent[0]["text"])

    def test_degraded_threshold_alert_uses_candidate_title_and_note(self) -> None:
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        sent = []
        result = self._calculation_result(a_value="350.00")
        result["price_source"] = "1m_eastmoney"
        result["strict_realtime"] = False
        result["data_quality"] = "degraded"
        result["data_quality_note"] = "严格实时行情不可用，使用备选行情仅作候选预警；不等同严格实时a。"

        def fake_send(config, title, text):  # noqa: ANN001
            sent.append({"title": title, "text": text})
            return {"ok": True, "kind": "feishu", "response": {"code": 0}}

        monitor.send_notification = fake_send
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                code = monitor.handle_calculation_result(
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                        "require_realtime_snapshot": False,
                        "alert_source_mode_override": "degraded_price_source_v1",
                    },
                    result,
                    notify=True,
                    notify_no_alert=False,
                    alert_title="511130 a值候选预警（降级行情）",
                )
            finally:
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(code, 0)
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["title"], "511130 a值候选预警（降级行情）")
        self.assertIn("数据质量: 降级行情候选预警", sent[0]["text"])
        self.assertIn("不等同严格实时a", sent[0]["text"])

    def test_alert_state_is_independent_between_strict_and_degraded_modes(self) -> None:
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        result = self._calculation_result(a_value="350.00")
        strict_config = {"require_realtime_snapshot": True}
        degraded_config = {"require_realtime_snapshot": False, "alert_source_mode_override": "degraded_price_source_v1"}
        with tempfile.TemporaryDirectory() as tmp:
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                strict_first = monitor.detect_alerts("20260615", Decimal("350"), [Decimal("300")], True, strict_config, result)
                strict_second = monitor.detect_alerts("20260615", Decimal("350"), [Decimal("300")], True, strict_config, result)
                degraded_first = monitor.detect_alerts("20260615", Decimal("350"), [Decimal("300")], True, degraded_config, result)
                degraded_second = monitor.detect_alerts("20260615", Decimal("350"), [Decimal("300")], True, degraded_config, result)
                strict_after_degraded = monitor.detect_alerts(
                    "20260615",
                    Decimal("350"),
                    [Decimal("300")],
                    True,
                    strict_config,
                    result,
                )
            finally:
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(strict_first, [Decimal("300")])
        self.assertEqual(strict_second, [])
        self.assertEqual(degraded_first, [Decimal("300")])
        self.assertEqual(degraded_second, [])
        self.assertEqual(strict_after_degraded, [])

    def test_threshold_alert_retries_notification_before_success(self) -> None:
        original_send = monitor.send_notification
        original_runs_dir = monitor.RUNS_DIR
        original_state_path = monitor.STATE_PATH
        state_cache = self._state_cache_snapshot()
        attempts = {"count": 0}

        def flaky_send(config, title, text):  # noqa: ANN001
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("temporary feishu failure")
            return {"ok": True, "kind": "feishu", "response": {"code": 0}}

        monitor.send_notification = flaky_send
        with tempfile.TemporaryDirectory() as tmp:
            monitor.RUNS_DIR = Path(tmp) / "runs"
            monitor.RUNS_DIR.mkdir()
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            try:
                code = monitor.handle_calculation_result(
                    {
                        "target_date": "20260615",
                        "thresholds": [300],
                        "reset_below_threshold": True,
                        "notification_attempts": 3,
                        "notification_retry_delay_seconds": "0",
                    },
                    self._calculation_result(a_value="350.00"),
                    notify=True,
                    notify_no_alert=False,
                )
            finally:
                monitor.send_notification = original_send
                monitor.RUNS_DIR = original_runs_dir
                monitor.STATE_PATH = original_state_path
                self._restore_state_cache(state_cache)
        self.assertEqual(code, 0)
        self.assertEqual(attempts["count"], 3)

    def test_same_day_interest_cache_can_unblock_verified_alert_inputs(self) -> None:
        original_state_path = monitor.STATE_PATH
        original_fetch = monitor.fetch_sse_interest
        state_cache = self._state_cache_snapshot()
        pcf = monitor.Pcf(
            trading_day="20260616",
            record_number=2,
            estimated_cash_component=Decimal("1549.26"),
            pre_cash_component=Decimal("1560.61"),
            creation_redemption_unit=Decimal("10000"),
            components=[
                monitor.Component("019776", "25特国02", Decimal("600")),
                monitor.Component("019837", "26特国02", Decimal("500")),
            ],
        )
        with tempfile.TemporaryDirectory() as tmp:
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor.STATE_PATH.write_text(
                json.dumps(
                    {
                        "dates": {},
                        "interest_cache": {
                            "by_date": {
                                "20260616": {
                                    "019776": {"value": "0.273", "source": "sse_netfull", "trading_day": "20260616"},
                                    "019837": {"value": "0.319", "source": "sse_netfull", "trading_day": "20260616"},
                                }
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            monitor.fetch_sse_interest = lambda date, code: (_ for _ in ()).throw(RuntimeError("sse down"))
            try:
                interests = monitor.get_interests("20260616", pcf, {"allow_same_day_interest_cache": True})
            finally:
                monitor.STATE_PATH = original_state_path
                monitor.fetch_sse_interest = original_fetch
                self._restore_state_cache(state_cache)
        self.assertEqual(interests["019776"][0], Decimal("0.273"))
        self.assertTrue(interests["019776"][1].startswith("sse_netfull_same_day_cache_20260616"))
        self.assertEqual(interests["019837"][0], Decimal("0.319"))

    def test_same_day_interest_cache_rejects_other_dates(self) -> None:
        original_state_path = monitor.STATE_PATH
        original_fetch = monitor.fetch_sse_interest
        state_cache = self._state_cache_snapshot()
        pcf = monitor.Pcf(
            trading_day="20260616",
            record_number=1,
            estimated_cash_component=Decimal("1549.26"),
            pre_cash_component=Decimal("1560.61"),
            creation_redemption_unit=Decimal("10000"),
            components=[monitor.Component("019776", "25特国02", Decimal("600"))],
        )
        with tempfile.TemporaryDirectory() as tmp:
            monitor.STATE_PATH = Path(tmp) / "state.json"
            monitor.STATE_PATH.write_text(
                json.dumps(
                    {
                        "dates": {},
                        "interest_cache": {
                            "by_date": {
                                "20260615": {
                                    "019776": {"value": "0.267", "source": "sse_netfull", "trading_day": "20260615"}
                                }
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            monitor._STATE_CACHE = None
            monitor._STATE_CACHE_DIRTY = False
            monitor.fetch_sse_interest = lambda date, code: (_ for _ in ()).throw(RuntimeError("sse down"))
            try:
                with self.assertRaisesRegex(RuntimeError, "缺少逐券应计利息"):
                    monitor.get_interests("20260616", pcf, {"allow_same_day_interest_cache": True})
            finally:
                monitor.STATE_PATH = original_state_path
                monitor.fetch_sse_interest = original_fetch
                self._restore_state_cache(state_cache)

    def test_auto_error_notify_does_not_send_when_policy_disabled(self) -> None:
        original_configured = monitor.notification_configured
        original_send = monitor.send_notification
        original_append = monitor.safe_append_notification_event
        calls = {"send": 0, "append": 0}

        monitor.notification_configured = lambda config: True
        monitor.send_notification = lambda *args, **kwargs: calls.__setitem__("send", calls["send"] + 1)
        monitor.safe_append_notification_event = lambda *args, **kwargs: calls.__setitem__("append", calls["append"] + 1)
        state = self._state()
        state.auto_error_count = 1
        try:
            ok = dashboard.maybe_notify_auto_error(state, {}, "boom", now=1000.0)
        finally:
            monitor.notification_configured = original_configured
            monitor.send_notification = original_send
            monitor.safe_append_notification_event = original_append
        self.assertFalse(ok)
        self.assertEqual(calls, {"send": 0, "append": 0})
        self.assertEqual(state.last_error_notify_at, 0.0)

    def test_pcf_not_ready_retry_backoff(self) -> None:
        state = self._state()
        config = {"pcf_not_ready_retry_seconds": 300}
        msg = "RuntimeError: PCF未更新或不可读: 20260616; HTTP 200"
        self.assertTrue(dashboard.maybe_defer_pcf_retry(state, config, msg, now=1000.0))
        self.assertEqual(state.last_run_message, "PCF未就绪，300秒后重试")
        self.assertEqual(dashboard.pcf_retry_remaining_seconds(state, now=1050.0), 250)
        self.assertEqual(dashboard.pcf_retry_remaining_seconds(state, now=1301.0), 0)

    def test_non_pcf_error_does_not_backoff_pcf(self) -> None:
        state = self._state()
        self.assertFalse(dashboard.maybe_defer_pcf_retry(state, {}, "RuntimeError: 行情过旧", now=1000.0))
        self.assertEqual(state.pcf_retry_at, 0.0)

    def test_auto_mode_survives_unexpected_loop_exception(self) -> None:
        original_roll = dashboard.maybe_roll_auto_target_date
        original_pause = dashboard.maybe_pause_auto_run_outside_market
        original_run_once = dashboard.run_once_now
        calls = {"roll": 0, "run": 0}
        stop_event = threading.Event()
        state = self._state()
        state.interval_seconds = 0
        state.auto_run = True
        result = self._calculation_result(a_value="120.00")

        def flaky_roll(state, config, context_ref):  # noqa: ANN001
            calls["roll"] += 1
            if calls["roll"] == 1:
                raise RuntimeError("unexpected auto loop error")
            return False

        def fake_run_once(config, notify=False, notify_no_alert=True, context=None):  # noqa: ANN001
            calls["run"] += 1
            stop_event.set()
            return True, "ok", result

        dashboard.maybe_roll_auto_target_date = flaky_roll
        dashboard.maybe_pause_auto_run_outside_market = lambda state, config: 0
        dashboard.run_once_now = fake_run_once
        thread = threading.Thread(
            target=dashboard.run_auto_mode,
            args=(state, {}, {"value": object()}, stop_event),
            daemon=True,
        )
        try:
            thread.start()
            thread.join(timeout=2)
        finally:
            stop_event.set()
            dashboard.maybe_roll_auto_target_date = original_roll
            dashboard.maybe_pause_auto_run_outside_market = original_pause
            dashboard.run_once_now = original_run_once
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(calls["roll"], 2)
        self.assertEqual(calls["run"], 1)
        self.assertEqual(state.last_error, "")
        self.assertIs(state.latest_result, result)
        self.assertGreater(state.last_auto_tick_at, 0)

    def test_save_config_preserves_auto_target_date_when_date_is_unchanged(self) -> None:
        original_config_path = monitor.CONFIG_PATH
        today = datetime.now(monitor.TZ).strftime("%Y%m%d")
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "fund_code": "511130",
                        "target_date": "auto",
                        "thresholds": [300],
                        "interest_overrides": {},
                        "require_realtime_snapshot": True,
                        "strict_realtime_price_sources": ["realtime_eastmoney"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            monitor.CONFIG_PATH = config_path
            try:
                config = monitor.load_config()
                state = self._state(date=today)
                context_ref = {"value": None}
                dashboard.save_config_update(
                    {"target_date": today, "thresholds": "300,500", "interest_overrides": {}},
                    state,
                    config,
                    context_ref,
                )
                raw = json.loads(config_path.read_text(encoding="utf-8"))
            finally:
                monitor.CONFIG_PATH = original_config_path
        self.assertEqual(raw["target_date"], "auto")
        self.assertEqual(raw["thresholds"], [300, 500])


class TestDailyActualAReport(unittest.TestCase):
    def _pcf(
        self,
        trading_day: str,
        *,
        pre_trading_day: str = "20260612",
        estimated_cash: str = "994.63",
        pre_cash: str = "1292.27",
        components: list[tuple[str, str, str]] | None = None,
    ) -> daily_report.ReportPcf:
        component_rows = components or [
            ("019776", "25特国02", "600"),
            ("019837", "25特国07", "500"),
        ]
        return daily_report.ReportPcf(
            trading_day=trading_day,
            pre_trading_day=pre_trading_day,
            record_number=len(component_rows),
            estimated_cash_component=Decimal(estimated_cash),
            pre_cash_component=Decimal(pre_cash),
            creation_redemption_unit=Decimal("10000"),
            components=[
                monitor.Component(code=code, name=name, pcf_quantity=Decimal(quantity))
                for code, name, quantity in component_rows
            ],
            source_url=f"https://fixture/{trading_day}.xml",
            raw_path=f"/tmp/{trading_day}.xml",
        )

    def _minute_timestamps(self, date: str) -> list[str]:
        base = datetime.strptime(date, "%Y%m%d")
        timestamps = []
        current = base.replace(hour=9, minute=31)
        end = base.replace(hour=11, minute=30)
        while current <= end:
            timestamps.append(current.strftime("%Y-%m-%d %H:%M"))
            current += timedelta(minutes=1)
        current = base.replace(hour=13, minute=1)
        end = base.replace(hour=15, minute=0)
        while current <= end:
            timestamps.append(current.strftime("%Y-%m-%d %H:%M"))
            current += timedelta(minutes=1)
        self.assertEqual(len(timestamps), 240)
        return timestamps

    def _price_maps(self, date: str, etf: str, bond_019776: str, bond_019837: str) -> dict[str, dict[str, Decimal]]:
        timestamps = self._minute_timestamps(date)
        return {
            monitor.ETF_CODE: {ts: Decimal(etf) for ts in timestamps},
            "019776": {ts: Decimal(bond_019776) for ts in timestamps},
            "019837": {ts: Decimal(bond_019837) for ts in timestamps},
        }

    def _five_minute_timestamps(self, date: str) -> list[str]:
        base = datetime.strptime(date, "%Y%m%d")
        timestamps = []
        current = base.replace(hour=9, minute=35)
        end = base.replace(hour=11, minute=30)
        while current <= end:
            timestamps.append(current.strftime("%Y-%m-%d %H:%M"))
            current += timedelta(minutes=5)
        current = base.replace(hour=13, minute=5)
        end = base.replace(hour=15, minute=0)
        while current <= end:
            timestamps.append(current.strftime("%Y-%m-%d %H:%M"))
            current += timedelta(minutes=5)
        self.assertEqual(len(timestamps), 48)
        return timestamps

    def _five_minute_price_maps(
        self,
        date: str,
        etf: str,
        bond_019776: str,
        bond_019837: str,
    ) -> dict[str, dict[str, Decimal]]:
        timestamps = self._five_minute_timestamps(date)
        return {
            monitor.ETF_CODE: {ts: Decimal(etf) for ts in timestamps},
            "019776": {ts: Decimal(bond_019776) for ts in timestamps},
            "019837": {ts: Decimal(bond_019837) for ts in timestamps},
        }

    def test_20260612_fixture_reproduces_confirmed_close_values(self) -> None:
        target_pcf = self._pcf("20260612", estimated_cash="994.63")
        run_pcf = self._pcf("20260615", pre_trading_day="20260612", pre_cash="1292.27")
        interests = {
            "019776": (Decimal("0.252"), "manual_trading_software_override", "fixture"),
            "019837": (Decimal("0.295"), "manual_trading_software_override", "fixture"),
        }
        rows = daily_report.build_rows(
            target_date="20260612",
            target_pcf=target_pcf,
            run_pcf=run_pcf,
            interests=interests,
            price_maps=self._price_maps("20260612", "105.620", "92.456", "99.437"),
        )
        summary = daily_report.summarize_rows(rows)
        self.assertEqual(summary["points"], 240)
        self.assertEqual(summary["estimated_close"], "297.37")
        self.assertEqual(summary["actual_close"], "-0.27")
        self.assertTrue(summary["actual_close_near_zero"])

    def test_20260615_fixture_reproduces_confirmed_close_values(self) -> None:
        target_pcf = self._pcf("20260615", pre_trading_day="20260612", estimated_cash="1071.04", pre_cash="1292.27")
        run_pcf = self._pcf("20260616", pre_trading_day="20260615", estimated_cash="0", pre_cash="1560.61")
        interests = {
            "019776": (Decimal("0.267"), "manual_trading_software_override", "fixture"),
            "019837": (Decimal("0.305"), "manual_trading_software_override", "fixture"),
        }
        rows = daily_report.build_rows(
            target_date="20260615",
            target_pcf=target_pcf,
            run_pcf=run_pcf,
            interests=interests,
            price_maps=self._price_maps("20260615", "105.853", "92.697", "99.583"),
        )
        summary = daily_report.summarize_rows(rows)
        self.assertEqual(summary["points"], 240)
        self.assertEqual(summary["estimated_close"], "234.96")
        self.assertEqual(summary["actual_close"], "-254.61")
        self.assertFalse(summary["actual_close_near_zero"])

    def test_run_pcf_pre_trading_day_maps_target_day(self) -> None:
        original_fetch = daily_report.fetch_report_pcf

        def fake_fetch(date, fund_code, raw_dir, label):  # noqa: ANN001
            if date == "20260616":
                return self._pcf("20260616", pre_trading_day="20260615", pre_cash="1560.61")
            if date == "20260615":
                return self._pcf("20260615", pre_trading_day="20260612", estimated_cash="1071.04")
            raise AssertionError(date)

        daily_report.fetch_report_pcf = fake_fetch
        with tempfile.TemporaryDirectory() as tmp:
            try:
                target, target_pcf, run_pcf = daily_report.resolve_pcfs(
                    run_date="20260616",
                    target_date=None,
                    config={"fund_code": "511130", "expected_component_codes": ["019776", "019837"]},
                    raw_dir=Path(tmp),
                )
            finally:
                daily_report.fetch_report_pcf = original_fetch
        self.assertEqual(target, "20260615")
        self.assertEqual(target_pcf.trading_day, "20260615")
        self.assertEqual(run_pcf.pre_trading_day, "20260615")

    def test_minute_gap_fails_closed(self) -> None:
        maps = self._price_maps("20260615", "105.853", "92.697", "99.583")
        maps["019837"].pop("2026-06-15 15:00")
        with self.assertRaisesRegex(RuntimeError, "1分钟共同时间戳不足"):
            daily_report.common_minute_timestamps("20260615", maps)

    def test_five_minute_cross_check_uses_48_common_points(self) -> None:
        target_pcf = self._pcf("20260615", pre_trading_day="20260612", estimated_cash="1071.04", pre_cash="1292.27")
        run_pcf = self._pcf("20260616", pre_trading_day="20260615", estimated_cash="0", pre_cash="1560.61")
        interests = {
            "019776": (Decimal("0.267"), "manual_trading_software_override", "fixture"),
            "019837": (Decimal("0.305"), "manual_trading_software_override", "fixture"),
        }
        price_maps = self._five_minute_price_maps("20260615", "105.853", "92.697", "99.583")
        timestamps = daily_report.common_5m_timestamps("20260615", price_maps)
        rows = daily_report.build_rows(
            target_date="20260615",
            target_pcf=target_pcf,
            run_pcf=run_pcf,
            interests=interests,
            price_maps=price_maps,
            timestamps=timestamps,
        )
        summary = daily_report.summarize_rows(rows)
        self.assertEqual(summary["points"], 48)
        self.assertEqual(summary["estimated_close"], "234.96")
        self.assertEqual(summary["actual_close"], "-254.61")

    def test_five_minute_gap_skips_cross_check(self) -> None:
        maps = self._five_minute_price_maps("20260615", "105.853", "92.697", "99.583")
        maps["019776"].pop("2026-06-15 15:00")
        with self.assertRaisesRegex(RuntimeError, "5分钟共同时间戳不足"):
            daily_report.common_5m_timestamps("20260615", maps)

    def test_component_change_fails_closed(self) -> None:
        pcf = self._pcf(
            "20260615",
            components=[("019776", "25特国02", "600")],
            estimated_cash="1071.04",
        )
        with self.assertRaisesRegex(RuntimeError, "PCF成分券结构变化"):
            daily_report.validate_report_pcf(
                pcf,
                "20260615",
                {"expected_component_codes": ["019776", "019837"]},
                validate_components=True,
            )

    def test_retry_only_waits_for_temporary_data_readiness(self) -> None:
        self.assertTrue(daily_report.is_retryable_error(RuntimeError("PCF未更新或不可读: 20260616; HTTP 200")))
        self.assertTrue(daily_report.is_retryable_error(RuntimeError("上交所净价全价接口未返回利息: 2026-06-16 019837")))
        self.assertFalse(daily_report.is_retryable_error(RuntimeError("PCF成分券结构变化，拒绝自动计算")))
        self.assertFalse(daily_report.is_retryable_error(RuntimeError("1分钟共同时间戳不足或异常: count=239")))


if __name__ == "__main__":
    unittest.main()
