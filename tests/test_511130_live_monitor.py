from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
LIVE_DIR = BASE / "scripts" / "511130_live_monitor"
sys.path.insert(0, str(LIVE_DIR))

import live_a_dashboard as dashboard  # noqa: E402
import monitor_511130 as monitor  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
