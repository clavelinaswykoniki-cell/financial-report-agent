from __future__ import annotations

import argparse
import html
import json
import mimetypes
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .metrics import format_percent
from .screening import screening_row
from .service import analyze_company


DEFAULT_OUTPUT_DIR = Path("reports")
MAX_POST_BYTES = 16_384
MAX_RULES_TICKERS = 20
MAX_LLM_TICKERS = 3
SAFE_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
ALLOWED_PROVIDERS = {"openai", "deepseek", "openai-compatible"}
ALLOWED_LLM_MODES = {"off", "auto", "on"}
ALLOWED_REPORT_SUFFIXES = {".md", ".json", ".csv"}


class DashboardHandler(BaseHTTPRequestHandler):
    output_dir = DEFAULT_OUTPUT_DIR
    cache_dir = Path(".cache/sec")
    sec_user_agent: str | None = None
    csrf_token = secrets.token_urlsafe(24)
    allowed_hosts: set[str] = {"127.0.0.1", "localhost", "::1"}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(render_home(csrf_token=self.csrf_token))
            return
        if parsed.path == "/healthz":
            self.send_json({"ok": True})
            return
        if parsed.path.startswith("/reports/"):
            self.serve_report(parsed.path.removeprefix("/reports/"))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/analyze":
            self.send_error(404)
            return
        if not self.is_safe_request_origin():
            self.send_error(403)
            return

        try:
            form = self.read_form()
        except ValueError as exc:
            self.send_html(render_home(str(exc), csrf_token=self.csrf_token), status=413)
            return

        if first(form, "csrf_token") != self.csrf_token:
            self.send_error(403)
            return

        tickers = parse_ticker_input(first(form, "tickers") or first(form, "ticker"))
        if not tickers:
            self.send_html(render_home(error="请输入至少一个 ticker，例如 AAPL 或 MSFT,NVDA。", csrf_token=self.csrf_token))
            return
        invalid = [ticker for ticker in tickers if not SAFE_TICKER.match(ticker)]
        if invalid:
            self.send_html(render_home(error=f"Ticker 格式不合法: {', '.join(invalid)}", csrf_token=self.csrf_token))
            return

        provider = first(form, "provider") or "openai"
        if provider not in ALLOWED_PROVIDERS:
            self.send_html(render_home(error="Provider 不合法。", csrf_token=self.csrf_token), status=400)
            return
        model = normalize_model(provider, first(form, "model"))
        api_key = first(form, "api_key")
        base_url = first(form, "base_url") if api_key else ""
        llm_mode = first(form, "llm_mode") or "off"
        if llm_mode not in ALLOWED_LLM_MODES:
            self.send_html(render_home(error="LLM mode 不合法。", csrf_token=self.csrf_token), status=400)
            return
        if llm_mode != "off" and not api_key:
            llm_mode = "off"
        ticker_limit = MAX_LLM_TICKERS if llm_mode == "on" else MAX_RULES_TICKERS
        if len(tickers) > ticker_limit:
            self.send_html(render_home(error=f"一次最多分析 {ticker_limit} 个 ticker。", csrf_token=self.csrf_token))
            return
        try:
            years = parse_years(first(form, "years"))
        except ValueError as exc:
            self.send_html(render_home(error=str(exc), csrf_token=self.csrf_token), status=400)
            return

        results = []
        errors = []
        for ticker in tickers:
            try:
                result = analyze_company(
                    ticker=ticker,
                    sec_user_agent=self.sec_user_agent,
                    cache_dir=self.cache_dir,
                    years=years,
                    output_dir=self.output_dir,
                    llm_mode=llm_mode,
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    provider=provider,
                )
                results.append(result)
            except Exception as exc:
                errors.append((ticker, str(exc)))

        self.send_html(render_results(tickers, results, errors, llm_mode))

    def read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_POST_BYTES:
            raise ValueError("请求体过大；请减少 ticker 数量或表单内容。")
        body = self.rfile.read(length).decode("utf-8")
        return parse_qs(body)

    def serve_report(self, raw_name: str) -> None:
        name = Path(unquote(raw_name)).name
        if name.startswith(".") or Path(name).suffix not in ALLOWED_REPORT_SUFFIXES:
            self.send_error(404)
            return
        path = self.output_dir / name
        output_root = self.output_dir.resolve()
        resolved = path.resolve()
        if output_root not in resolved.parents or not resolved.exists() or not resolved.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        payload = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_html(self, markup: str, status: int = 200) -> None:
        payload = markup.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, data: dict) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def is_safe_request_origin(self) -> bool:
        host = self.headers.get("Host", "").split(":", 1)[0].strip("[]")
        if host not in self.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        return parsed.hostname in self.allowed_hosts

    def log_message(self, format: str, *args: object) -> None:
        return


def render_home(error: str | None = None, csrf_token: str = "") -> str:
    error_html = f"<div class='alert'>{html.escape(error)}</div>" if error else ""
    return page(
        "Financial Report Agent",
        f"""
        <section class="workspace">
          <aside class="panel">
            <div class="brand">Financial Report Agent</div>
            <p class="muted">SEC filings due diligence workspace for analysts, students, and finance builders.</p>
            <div class="stat"><span>Data</span><strong>SEC EDGAR</strong></div>
            <div class="stat"><span>Mode</span><strong>Rules + optional LLM</strong></div>
            <div class="stat"><span>Output</span><strong>Markdown / JSON</strong></div>
          </aside>
          <main class="surface">
            <h1>Company Filing Risk Review</h1>
            <p class="subhead">Analyze one company or a comma-separated watchlist. API keys are used only for this local request.</p>
            {error_html}
            <form method="post" action="/analyze" class="grid-form">
              <input type="hidden" name="csrf_token" value="{html.escape(csrf_token)}">
              <label class="wide">Tickers
                <textarea name="tickers" rows="3" placeholder="AAPL, MSFT, NVDA">AAPL</textarea>
              </label>
              <label>Years
                <input name="years" type="number" value="4" min="2" max="8">
              </label>
              <label>LLM mode
                <select name="llm_mode">
                  <option value="off">Rules only</option>
                  <option value="auto">Auto if key exists</option>
                  <option value="on">Force LLM</option>
                </select>
              </label>
              <label>Provider
                <select name="provider">
                  <option value="openai">OpenAI</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="openai-compatible">OpenAI-compatible</option>
                </select>
              </label>
              <label>Model
                <input name="model" value="openai:gpt-4o-mini">
              </label>
              <label class="wide">API key
                <input name="api_key" type="password" placeholder="Optional. Leave blank for rules-only analysis.">
              </label>
              <label class="wide">Base URL
                <input name="base_url" placeholder="Optional, e.g. https://api.example.com/v1">
              </label>
              <button type="submit">Run analysis</button>
            </form>
          </main>
        </section>
        """,
    )


def render_results(tickers: list[str], results: list, errors: list[tuple[str, str]], llm_mode: str) -> str:
    cards = []
    rows = []
    for result in results:
        row = screening_row(result.snapshot, result.risk)
        rows.append(row)
        report_link = ""
        if result.paths:
            md_path = result.paths.get("markdown")
            json_path = result.paths.get("json")
            report_link = (
                f"<a href='/reports/{html.escape(md_path.name)}'>Markdown</a> "
                f"<a href='/reports/{html.escape(json_path.name)}'>JSON</a>"
            )
        cards.append(
            f"""
            <article class="result-card">
              <div>
                <h2>{html.escape(result.snapshot['ticker'])}</h2>
                <p>{html.escape(result.snapshot['company_name'])}</p>
              </div>
              <div class="risk {result.risk['level'].lower()}">{result.risk['level']} / {result.risk['score']}</div>
              <dl>
                <div><dt>Revenue YoY</dt><dd>{format_percent(result.snapshot['metrics'].get('revenue_growth_yoy'))}</dd></div>
                <div><dt>Net margin</dt><dd>{format_percent(result.snapshot['metrics'].get('net_margin'))}</dd></div>
                <div><dt>Debt/assets</dt><dd>{format_percent(result.snapshot['metrics'].get('debt_to_assets'))}</dd></div>
                <div><dt>FCF margin</dt><dd>{format_percent(result.snapshot['metrics'].get('free_cash_flow_margin'))}</dd></div>
              </dl>
              <div class="links">{report_link}</div>
            </article>
            """
        )

    table = render_screening_table(rows) if rows else ""
    error_html = "".join(
        f"<li><strong>{html.escape(ticker)}</strong>: {html.escape(message)}</li>" for ticker, message in errors
    )
    error_block = f"<div class='alert'><ul>{error_html}</ul></div>" if errors else ""
    llm_badge = "LLM enabled" if llm_mode == "on" else "Rules-first"
    return page(
        "Analysis Results",
        f"""
        <section class="surface full">
          <div class="topbar">
            <a href="/">Back</a>
            <span>{html.escape(', '.join(tickers))}</span>
            <strong>{llm_badge}</strong>
          </div>
          {error_block}
          <div class="cards">{''.join(cards)}</div>
          {table}
        </section>
        """,
    )


def render_screening_table(rows: list[dict[str, str]]) -> str:
    body = ""
    for row in sorted(rows, key=lambda item: (-int(item["risk_score"]), item["ticker"])):
        body += (
            "<tr>"
            f"<td>{html.escape(row['ticker'])}</td>"
            f"<td>{html.escape(row['company'])}</td>"
            f"<td>{html.escape(row['risk_level'])}</td>"
            f"<td>{row['risk_score']}</td>"
            f"<td>{row['revenue_growth_yoy']}</td>"
            f"<td>{row['net_margin']}</td>"
            f"<td>{row['debt_to_assets']}</td>"
            f"<td>{html.escape(row['top_risks'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Ticker</th><th>Company</th><th>Risk</th><th>Score</th>"
        "<th>Revenue YoY</th><th>Net margin</th><th>Debt/assets</th><th>Top risks</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def page(title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html.escape(title)}</title>
      <style>
        :root {{
          --ink: #172026;
          --muted: #60707d;
          --line: #d8e0e6;
          --bg: #f6f8f9;
          --surface: #ffffff;
          --accent: #126a5a;
          --warn: #a45b16;
          --danger: #a83232;
        }}
        * {{ box-sizing: border-box; }}
        body {{ margin: 0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }}
        a {{ color: var(--accent); font-weight: 700; text-decoration: none; }}
        .workspace {{ min-height: 100vh; display: grid; grid-template-columns: 320px 1fr; }}
        .panel {{ background: #102129; color: white; padding: 32px; display: flex; flex-direction: column; gap: 18px; }}
        .brand {{ font-size: 24px; font-weight: 800; line-height: 1.1; }}
        .muted {{ color: #b4c5ce; line-height: 1.5; margin: 0; }}
        .stat {{ border-top: 1px solid rgba(255,255,255,.16); padding-top: 16px; display: flex; justify-content: space-between; gap: 16px; }}
        .stat span {{ color: #b4c5ce; }}
        .surface {{ width: min(1040px, calc(100vw - 48px)); margin: 32px auto; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 28px; box-shadow: 0 18px 40px rgba(20, 35, 45, .08); }}
        .surface.full {{ width: min(1180px, calc(100vw - 48px)); }}
        h1 {{ margin: 0; font-size: 30px; line-height: 1.2; }}
        h2 {{ margin: 0; font-size: 20px; }}
        .subhead {{ color: var(--muted); margin: 10px 0 24px; line-height: 1.5; }}
        .grid-form {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
        label {{ display: grid; gap: 8px; font-weight: 700; }}
        .wide {{ grid-column: 1 / -1; }}
        input, textarea, select {{ width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 12px; font: inherit; background: white; }}
        textarea {{ resize: vertical; }}
        button {{ width: fit-content; border: 0; border-radius: 6px; padding: 12px 18px; background: var(--accent); color: white; font: inherit; font-weight: 800; cursor: pointer; }}
        .alert {{ border: 1px solid #f0c38f; background: #fff7ec; color: #6b3a07; border-radius: 6px; padding: 12px 14px; margin-bottom: 18px; }}
        .topbar {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }}
        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 22px; }}
        .result-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 18px; display: grid; gap: 14px; }}
        .result-card p {{ margin: 4px 0 0; color: var(--muted); }}
        .risk {{ width: fit-content; border-radius: 999px; padding: 6px 10px; font-weight: 800; background: #eaf5f2; color: var(--accent); }}
        .risk.medium {{ background: #fff4df; color: var(--warn); }}
        .risk.high {{ background: #fdeaea; color: var(--danger); }}
        dl {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 0; }}
        dt {{ color: var(--muted); font-size: 12px; }}
        dd {{ margin: 4px 0 0; font-weight: 800; }}
        .links {{ display: flex; gap: 12px; }}
        table {{ width: 100%; border-collapse: collapse; border: 1px solid var(--line); }}
        th, td {{ text-align: left; padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
        th {{ background: #eef3f4; font-size: 13px; }}
        @media (max-width: 820px) {{
          .workspace {{ grid-template-columns: 1fr; }}
          .panel {{ padding: 24px; }}
          .surface, .surface.full {{ width: calc(100vw - 24px); margin: 12px auto; padding: 18px; }}
          .grid-form, dl {{ grid-template-columns: 1fr; }}
        }}
      </style>
    </head>
    <body>{body}</body>
    </html>
    """


def parse_ticker_input(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip().upper() for item in raw.replace("\n", ",").split(",") if item.strip()]


def first(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name) or [""]
    return values[0].strip()


def normalize_model(provider: str, raw_model: str) -> str:
    model = raw_model.strip()
    if not model:
        return default_model(provider)
    if provider == "deepseek" and not model.startswith("deepseek:"):
        return default_model(provider)
    if provider == "openai-compatible" and not model.startswith("openai-compatible:"):
        return default_model(provider)
    if provider == "openai" and not model.startswith("openai:"):
        return default_model(provider)
    return model


def default_model(provider: str) -> str:
    if provider == "deepseek":
        return "deepseek:deepseek-chat"
    if provider == "openai-compatible":
        return "openai-compatible:your-model"
    return "openai:gpt-4o-mini"


def parse_years(raw: str | None) -> int:
    try:
        years = int(raw or "4")
    except ValueError as exc:
        raise ValueError("Years 必须是 2 到 8 之间的整数。") from exc
    if years < 2 or years > 8:
        raise ValueError("Years 必须是 2 到 8 之间的整数。")
    return years


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local Financial Report Agent dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", default="reports")
    parser.add_argument("--cache-dir", default=".cache/sec")
    parser.add_argument("--sec-user-agent", default=None)
    args = parser.parse_args(argv)

    DashboardHandler.output_dir = Path(args.output_dir)
    DashboardHandler.cache_dir = Path(args.cache_dir)
    DashboardHandler.sec_user_agent = args.sec_user_agent
    DashboardHandler.allowed_hosts = {"127.0.0.1", "localhost", "::1", args.host}
    DashboardHandler.output_dir.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Financial Report Agent dashboard: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
