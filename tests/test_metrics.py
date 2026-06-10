import pytest

import financial_report_agent.sec_client as sec_client
import financial_report_agent.service as service
from financial_report_agent.dashboard import parse_years
from financial_report_agent.metrics import assess_risk, build_snapshot, extract_annual_series
from financial_report_agent.agent import resolve_chat_model_config
from financial_report_agent.sec_client import SecClient
from financial_report_agent.workflow import build_workflow_trace


def test_extract_annual_series_dedupes_by_filed_date() -> None:
    companyfacts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"fy": 2024, "fp": "FY", "form": "10-K", "filed": "2025-01-01", "val": 90},
                            {"fy": 2024, "fp": "FY", "form": "10-K/A", "filed": "2025-02-01", "val": 100},
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-01-01", "val": 120},
                        ]
                    }
                }
            }
        }
    }

    series = extract_annual_series(companyfacts, ["Revenues"])

    assert [point["fy"] for point in series] == [2024, 2025]
    assert series[0]["value"] == 100


def test_extract_annual_series_rejects_short_flow_duration() -> None:
    companyfacts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-01",
                                "start": "2025-10-01",
                                "end": "2025-12-31",
                                "val": 99,
                            },
                            {
                                "fy": 2025,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2026-01-01",
                                "start": "2025-01-01",
                                "end": "2025-12-31",
                                "val": 400,
                            },
                        ]
                    }
                }
            }
        }
    }

    series = extract_annual_series(companyfacts, ["Revenues"])

    assert len(series) == 1
    assert series[0]["value"] == 400


def test_extract_annual_series_prefers_latest_tag_coverage() -> None:
    companyfacts = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            annual_fact(2021, 100),
                            annual_fact(2022, 120),
                        ]
                    }
                },
                "Revenues": {
                    "units": {
                        "USD": [
                            annual_fact(2023, 200),
                            annual_fact(2024, 260),
                        ]
                    }
                },
            }
        }
    }

    series = extract_annual_series(
        companyfacts,
        ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    )

    assert [point["fy"] for point in series] == [2023, 2024]
    assert series[-1]["value"] == 260


def test_build_snapshot_and_assess_risk_flags_weak_cash_flow() -> None:
    snapshot = build_snapshot(
        {"ticker": "TEST", "cik": "0000000001", "company_name": "Test Inc."},
        {
            "name": "Test Inc.",
            "cik": "0000000001",
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "filingDate": ["2026-02-01"],
                    "reportDate": ["2025-12-31"],
                    "accessionNumber": ["0000000001-26-000001"],
                    "primaryDocument": ["test-20251231.htm"],
                }
            },
        },
        fake_companyfacts(),
        years=3,
    )
    risk = assess_risk(snapshot)

    assert snapshot["metrics"]["revenue_growth_yoy"] == -0.2
    assert snapshot["metrics"]["free_cash_flow_margin"] < 0
    assert risk["score"] > 0
    assert any(item["title"] == "收入下滑" for item in risk["items"])


def test_build_snapshot_aligns_metrics_to_one_fiscal_year() -> None:
    snapshot = build_snapshot(
        {"ticker": "MIX", "cik": "0000000002", "company_name": "Mixed Fiscal Years Inc."},
        {"name": "Mixed Fiscal Years Inc.", "cik": "0000000002", "filings": {"recent": {"form": []}}},
        {
            "facts": {
                "us-gaap": {
                    "Revenues": annual_usd([(2022, 100), (2023, 200)]),
                    "NetIncomeLoss": annual_usd([(2022, 10), (2024, 500)]),
                    "GrossProfit": annual_usd([(2022, 40), (2023, 80)]),
                    "Assets": annual_usd([(2024, 1000)]),
                    "Liabilities": annual_usd([(2024, 100)]),
                    "NetCashProvidedByUsedInOperatingActivities": annual_usd([(2024, 900)]),
                }
            }
        },
        years=4,
    )

    assert snapshot["metrics"]["period"] == 2022
    assert snapshot["latest_values"]["revenue"]["fy"] == 2022
    assert snapshot["latest_values"]["net_income"]["fy"] == 2022
    assert snapshot["latest_values"]["assets"] is None
    assert snapshot["metrics"]["net_margin"] == 0.1
    assert any("Missing same-year XBRL value for assets in fiscal year 2022" in item for item in snapshot["warnings"])


def test_workflow_trace_explains_rules_only_mode() -> None:
    snapshot = build_snapshot(
        {"ticker": "TEST", "cik": "0000000001", "company_name": "Test Inc."},
        {
            "name": "Test Inc.",
            "cik": "0000000001",
            "filings": {
                "recent": {
                    "form": ["10-K"],
                    "filingDate": ["2026-02-01"],
                    "reportDate": ["2025-12-31"],
                    "accessionNumber": ["0000000001-26-000001"],
                    "primaryDocument": ["test-20251231.htm"],
                }
            },
        },
        fake_companyfacts(),
        years=3,
    )
    risk = assess_risk(snapshot)

    trace = build_workflow_trace(snapshot, risk)

    assert [step["role"] for step in trace][:3] == ["Data Step", "Metric Step", "Risk Step"]
    assert trace[-1]["role"] == "LLM Analyst Agent"
    assert trace[-1]["status"] == "skipped"


def test_resolve_chat_model_config_supports_openai_compatible(monkeypatch) -> None:
    monkeypatch.setenv("FIN_AGENT_API_KEY", "test-key")
    monkeypatch.setenv("FIN_AGENT_BASE_URL", "https://api.example.com/v1")

    config = resolve_chat_model_config("openai-compatible:test-model")

    assert config.provider == "openai-compatible"
    assert config.model == "test-model"
    assert config.api_key == "test-key"
    assert config.base_url == "https://api.example.com/v1"


def test_resolve_chat_model_config_supports_deepseek(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

    config = resolve_chat_model_config("deepseek:deepseek-chat")

    assert config.provider == "deepseek"
    assert config.model == "deepseek-chat"
    assert config.api_key == "deepseek-test-key"
    assert config.base_url == "https://api.deepseek.com"


def test_explicit_api_key_does_not_require_env(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    config = resolve_chat_model_config("openai:gpt-4o-mini", api_key="runtime-key")

    assert config.provider == "openai"
    assert config.api_key == "runtime-key"


def test_maybe_run_agent_reports_auto_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fail_agent(*args, **kwargs):
        raise RuntimeError("missing optional dependency")

    monkeypatch.setattr(service, "run_langchain_agent", fail_agent)
    result = service.maybe_run_agent("auto", {"ticker": "TEST"}, {"score": 0}, model="openai:gpt-4o-mini")

    assert result.notes is None
    assert result.status == "failed_auto"
    assert "missing optional dependency" in result.error


def test_maybe_run_agent_redacts_key_from_errors(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-key")

    def fail_agent(*args, **kwargs):
        raise RuntimeError("bad key test-secret-key")

    monkeypatch.setattr(service, "run_langchain_agent", fail_agent)
    result = service.maybe_run_agent("auto", {"ticker": "TEST"}, {"score": 0}, model="openai:gpt-4o-mini")

    assert result.error == "bad key [redacted]"


def test_sec_client_recovers_from_bad_cached_json(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "bad.json"
    cache_path.write_text("{bad json", encoding="utf-8")
    client = SecClient(cache_dir=tmp_path)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(sec_client, "urlopen", lambda *args, **kwargs: FakeResponse())

    data = client._get_json("https://example.com/bad.json", "bad.json", max_age_seconds=3600)

    assert data == {"ok": True}
    assert cache_path.read_text(encoding="utf-8") == '{"ok": true}'


def test_parse_years_rejects_invalid_values() -> None:
    assert parse_years("4") == 4
    with pytest.raises(ValueError):
        parse_years("1")
    with pytest.raises(ValueError):
        parse_years("abc")


def fake_companyfacts() -> dict:
    return {
        "facts": {
            "us-gaap": {
                "Revenues": annual_usd([(2024, 1000), (2025, 800)]),
                "GrossProfit": annual_usd([(2024, 500), (2025, 300)]),
                "OperatingIncomeLoss": annual_usd([(2024, 100), (2025, 10)]),
                "NetIncomeLoss": annual_usd([(2024, 80), (2025, 20)]),
                "Assets": annual_usd([(2025, 1000)]),
                "Liabilities": annual_usd([(2025, 800)]),
                "AssetsCurrent": annual_usd([(2025, 300)]),
                "LiabilitiesCurrent": annual_usd([(2025, 400)]),
                "StockholdersEquity": annual_usd([(2025, 200)]),
                "CashAndCashEquivalentsAtCarryingValue": annual_usd([(2025, 50)]),
                "NetCashProvidedByUsedInOperatingActivities": annual_usd([(2025, 10)]),
                "PaymentsToAcquirePropertyPlantAndEquipment": annual_usd([(2025, 80)]),
            }
        }
    }


def annual_fact(fy: int, value: float) -> dict:
    return {
        "fy": fy,
        "fp": "FY",
        "form": "10-K",
        "filed": f"{fy + 1}-02-01",
        "start": f"{fy}-01-01",
        "end": f"{fy}-12-31",
        "val": value,
    }


def annual_usd(values: list[tuple[int, float]]) -> dict:
    return {
        "units": {
            "USD": [
                {
                    "fy": fy,
                    "fp": "FY",
                    "form": "10-K",
                    "filed": f"{fy + 1}-02-01",
                    "start": f"{fy}-01-01",
                    "end": f"{fy}-12-31",
                    "val": value,
                }
                for fy, value in values
            ]
        }
    }
