import pytest

from financial_report_agent.dashboard import normalize_model, parse_ticker_input, parse_years


def test_parse_ticker_input_normalizes_and_splits() -> None:
    assert parse_ticker_input("aapl, msft\nnvda") == ["AAPL", "MSFT", "NVDA"]


def test_normalize_model_matches_provider() -> None:
    assert normalize_model("deepseek", "openai:gpt-4o-mini") == "deepseek:deepseek-chat"
    assert normalize_model("openai-compatible", "") == "openai-compatible:your-model"


def test_parse_years_bounds() -> None:
    assert parse_years("4") == 4
    with pytest.raises(ValueError):
        parse_years("20")
