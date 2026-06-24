#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parents[1]

SEED_SOURCES = {
    "20260612": {
        "path": WORKSPACE / "reports/511130_a_20260612_20260615/data/a_curve_1m_20260612.csv",
        "price_source": "historical_eastmoney_1m",
    },
    "20260615": {
        "path": WORKSPACE / "reports/511130_a_20260612_20260615/data/a_curve_1m_20260615.csv",
        "price_source": "historical_eastmoney_1m",
    },
    "20260616": {
        "path": WORKSPACE / "reports/511130_daily_actual_a/20260616/511130_20260616_1m_estimated_actual_a.csv",
        "price_source": "daily_report_1m",
    },
    "20260617": {
        "path": WORKSPACE / "reports/511130_daily_actual_a/20260617/511130_20260617_1m_estimated_actual_a.csv",
        "price_source": "daily_report_1m",
    },
}


def clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text.replace(",", "")


def pick(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key))
        if value:
            return value
    return ""


def decimal_or_none(value: str) -> Decimal | None:
    try:
        number = Decimal(clean(value))
    except (InvalidOperation, ValueError):
        return None
    if number.is_nan():
        return None
    return number


def add_decimal(left: str, right: str) -> str:
    left_number = decimal_or_none(left)
    right_number = decimal_or_none(right)
    if left_number is None or right_number is None:
        return ""
    return str(left_number + right_number)


def etf_value_from_quote(quote: str) -> str:
    quote_number = decimal_or_none(quote)
    if quote_number is None:
        return ""
    return str(quote_number * Decimal("10000"))


def normalize_row(date: str, row: dict[str, str], price_source: str, source_path: Path) -> dict[str, Any] | None:
    timestamp = pick(row, "timestamp", "datetime")
    estimated_a = pick(row, "estimated_a")
    if not timestamp or decimal_or_none(estimated_a) is None:
        return None
    etf_quote = pick(row, "etf_quote")
    component_value = pick(row, "component_value_ex_cash", "component_value")
    estimated_cash = pick(row, "estimated_cash", "estimated_cash_component")
    basket_value = pick(row, "basket_value", "estimated_basket") or add_decimal(component_value, estimated_cash)
    etf_value = pick(row, "etf_value", "etf_side") or etf_value_from_quote(etf_quote)
    return {
        "date": date,
        "timestamp": timestamp,
        "price_source": price_source,
        "strict_realtime": False,
        "quote_skew_seconds": None,
        "calculation_elapsed_ms": None,
        "calculated_at": timestamp,
        "formula_version": "historical_estimated_a_v1",
        "etf_quote": etf_quote,
        "etf_value": etf_value,
        "estimated_cash": estimated_cash,
        "component_value_ex_cash": component_value,
        "basket_value": basket_value,
        "estimated_a": estimated_a,
        "actual_a": pick(row, "actual_a"),
        "record_number": 2,
        "historical_replay": True,
        "source_file": str(source_path.relative_to(WORKSPACE)),
    }


def read_source(date: str, path: Path, price_source: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    points = [point for row in rows if (point := normalize_row(date, row, price_source, path)) is not None]
    points.sort(key=lambda item: str(item.get("timestamp", "")))
    return points


def write_day(output_root: Path, date: str, points: list[dict[str, Any]]) -> None:
    day_dir = output_root / date
    day_dir.mkdir(parents=True, exist_ok=True)
    with (day_dir / "a_values.jsonl").open("w", encoding="utf-8") as handle:
        for point in points:
            handle.write(json.dumps(point, ensure_ascii=False) + "\n")
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
    with (day_dir / "a_values.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for point in points:
            writer.writerow({key: point.get(key, "") for key in fieldnames})


def parse_dates(raw: str) -> list[str]:
    if not raw:
        return list(SEED_SOURCES)
    dates = [item.strip() for item in raw.replace("，", ",").split(",") if item.strip()]
    unknown = [date for date in dates if date not in SEED_SOURCES]
    if unknown:
        raise ValueError(f"unsupported seed dates: {', '.join(unknown)}")
    return dates


def main() -> int:
    parser = argparse.ArgumentParser(description="Build dashboard history seed files for 511130 a curves.")
    parser.add_argument("--dates", default="", help="Comma-separated dates; default seeds all known local report curves.")
    parser.add_argument(
        "--output-root",
        default=str(WORKSPACE / "tmp/511130_history_seed/runs"),
        help="Output runs root containing YYYYMMDD/a_values.jsonl.",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).expanduser()
    dates = parse_dates(args.dates)
    total = 0
    for date in dates:
        source = SEED_SOURCES[date]
        points = read_source(date, source["path"], source["price_source"])
        if not points:
            raise RuntimeError(f"{date} produced no points from {source['path']}")
        write_day(output_root, date, points)
        total += len(points)
        print(f"{date}: {len(points)} points -> {output_root / date}")
    print(f"OK: wrote {total} historical points under {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
