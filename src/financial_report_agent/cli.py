from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .screening import write_screening_outputs
from .sec_client import SecClientError
from .service import analyze_company


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    load_env(Path(args.env_file))

    try:
        result = analyze_company(
            ticker=args.ticker,
            sec_user_agent=args.sec_user_agent,
            cache_dir=args.cache_dir,
            years=args.years,
            output_dir=args.output_dir,
            llm_mode=args.llm,
            model=args.model,
            llm_timeout=args.llm_timeout,
        )
        paths = result.paths or {}
        if args.watchlist:
            screening_paths = write_screening_outputs(
                tickers=parse_tickers(args.watchlist),
                output_dir=args.output_dir,
                sec_user_agent=args.sec_user_agent,
                cache_dir=args.cache_dir,
                years=args.years,
            )
            paths = {**paths, **screening_paths}
    except SecClientError as exc:
        print(f"SEC data error: {exc}", file=os.sys.stderr)
        raise SystemExit(2) from exc
    except RuntimeError as exc:
        print(f"Runtime error: {exc}", file=os.sys.stderr)
        raise SystemExit(3) from exc

    print(json.dumps({key: str(path) for key, path in paths.items()}, ensure_ascii=False, indent=2))
    if result.llm_status == "skipped_no_key":
        print("LLM pass skipped: no supported API key was detected.")
    elif result.llm_status == "failed_auto":
        print(f"LLM pass failed in auto mode; deterministic report was still generated: {result.llm_error}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="financial-report-agent",
        description="Fetch SEC filings and generate a financial statement analysis report.",
    )
    parser.add_argument("--ticker", required=True, help="US public company ticker, e.g. AAPL")
    parser.add_argument(
        "--watchlist",
        default="",
        help="Optional comma-separated tickers for a batch screening table, e.g. MSFT,NVDA,GOOGL",
    )
    parser.add_argument("--output-dir", default="reports", help="Directory for markdown/json reports")
    parser.add_argument("--cache-dir", default=".cache/sec", help="Directory for SEC JSON cache")
    parser.add_argument("--years", type=int, default=4, help="Number of annual periods to keep")
    parser.add_argument(
        "--llm",
        choices=["auto", "on", "off"],
        default="auto",
        help="Run optional LangChain LLM pass. `auto` runs only when a supported API key exists.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("FIN_AGENT_MODEL"),
        help=(
            "Model id, e.g. openai:gpt-4o-mini, deepseek:deepseek-chat, "
            "or openai-compatible:your-model. Defaults to FIN_AGENT_MODEL."
        ),
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=45,
        help="Timeout seconds for the optional LangChain LLM call.",
    )
    parser.add_argument(
        "--sec-user-agent",
        default=os.getenv("SEC_USER_AGENT"),
        help="SEC User-Agent header. Prefer setting SEC_USER_AGENT in .env.",
    )
    parser.add_argument("--env-file", default=".env", help="Optional dotenv file")
    return parser.parse_args(argv)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def parse_tickers(raw: str) -> list[str]:
    return [ticker.strip().upper() for ticker in raw.replace("\n", ",").split(",") if ticker.strip()]
