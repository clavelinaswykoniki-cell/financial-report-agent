from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"


class SecClientError(RuntimeError):
    """Raised when SEC data cannot be loaded or parsed."""


class SecClient:
    """Small SEC EDGAR JSON client with disk cache and polite request spacing."""

    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: str | Path = ".cache/sec",
        timeout: int = 30,
        min_interval_seconds: float = 0.12,
    ) -> None:
        self.user_agent = (
            user_agent
            or os.getenv("SEC_USER_AGENT")
            or "financial-report-agent/0.1 local-educational-project contact@example.com"
        )
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self._last_request_at = 0.0
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def lookup_ticker(self, ticker: str) -> dict[str, Any]:
        symbol = ticker.upper().strip()
        if not symbol:
            raise SecClientError("Ticker cannot be empty.")

        records = self._get_json(
            SEC_COMPANY_TICKERS_URL,
            cache_name="company_tickers.json",
            max_age_seconds=60 * 60 * 24 * 14,
        )
        for item in records.values():
            if item.get("ticker", "").upper() == symbol:
                cik = str(item["cik_str"]).zfill(10)
                return {
                    "ticker": symbol,
                    "cik": cik,
                    "company_name": item.get("title", symbol),
                }
        raise SecClientError(f"Could not find ticker {symbol} in SEC company_tickers.json.")

    def get_submissions(self, cik: str) -> dict[str, Any]:
        cik = str(cik).zfill(10)
        return self._get_json(
            SEC_SUBMISSIONS_URL.format(cik=cik),
            cache_name=f"submissions_{cik}.json",
            max_age_seconds=60 * 60 * 24,
        )

    def get_companyfacts(self, cik: str) -> dict[str, Any]:
        cik = str(cik).zfill(10)
        return self._get_json(
            SEC_COMPANYFACTS_URL.format(cik=cik),
            cache_name=f"companyfacts_{cik}.json",
            max_age_seconds=60 * 60 * 24,
        )

    def _get_json(self, url: str, cache_name: str, max_age_seconds: int) -> Any:
        cache_path = self.cache_dir / cache_name
        if self._is_fresh(cache_path, max_age_seconds):
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                cache_path.unlink(missing_ok=True)

        now = time.monotonic()
        wait = self.min_interval_seconds - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            raise SecClientError(f"SEC request failed {exc.code}: {url}") from exc
        except URLError as exc:
            raise SecClientError(f"SEC request failed: {url} ({exc.reason})") from exc
        finally:
            self._last_request_at = time.monotonic()

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SecClientError(f"SEC returned invalid JSON: {url}") from exc

        try:
            tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(cache_path)
        except OSError as exc:
            raise SecClientError(f"Could not write SEC cache: {cache_path}") from exc
        return parsed

    @staticmethod
    def _is_fresh(path: Path, max_age_seconds: int) -> bool:
        if not path.exists():
            return False
        age = time.time() - path.stat().st_mtime
        return age <= max_age_seconds
