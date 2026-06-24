# Next Session

先读：

1. `scripts/511130_live_monitor/CODEX.md`
2. `scripts/511130_live_monitor/README.md`
3. `scripts/511130_live_monitor/daily_actual_a_report.py`
4. `scripts/511130_live_monitor/live_a_dashboard.py`
5. `scripts/511130_live_monitor/monitor_511130.py`

## Current State

- 2026-06-24 local code now adds per-security quote cards for `511130`, `019776`, and `019837`: latest price, change, turnover, five-level bid/ask display, and a sparkline from cached Eastmoney 1-minute intraday data, Sina 1-minute fallback, plus saved strict-realtime calculation points.
- The market section is now a connected four-card horizontal strip: `511130`, `019776`, `019837`, and `套利值A`; desktop cards have zero gap, and narrow screens keep the four-card strip horizontal through local scrolling instead of stacking vertically.
- Five-level order book data is display-only from Sina snapshot parsing. It does not enter the a-value formula, current-a fail-closed decision, or Feishu alert path.
- Default dashboard auto-run/browser refresh is now 3 seconds in `live_a_dashboard.py`, `Dockerfile`, and `railway.toml`; strict realtime still requires Eastmoney source, max 3-second quote skew, and max 30-second stale age.
- Local Chrome verification passed on `127.0.0.1:8799`: `cardCount=4`, `quoteCards=3`, `hasA=true`, `orderRows=30`, `sparkLines=3`, `adjacentGap=0`, no viewport overflow. Screenshot: `scripts/511130_live_monitor/docs/artifacts/511130-four-card-strip-chrome-20260624.png`.
- Local verification passed: `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/smoke_check.py tests/test_511130_live_monitor.py`; `python3.12 -m unittest tests.test_511130_live_monitor` returned 91 tests OK.
- This 2026-06-24 change has not been deployed to Railway yet.
- Local 2026-06-23 A-curve run is complete but not deployed to Railway.
- 511130 last-week open-day 1-minute estimated/actual A summary is at `reports/511130_daily_actual_a/summary_511130_1m_estimated_actual_a_20260615_20260618.csv`; overview SVG is at `reports/511130_daily_actual_a/511130_1m_estimated_actual_a_20260615_20260618.svg`.
- 511090 2026-06-08 through 2026-06-18 daily-close estimated/actual A output is at `reports/511090_a_20260608_20260618/`; it is daily close only, not intraday, because no stable public historical minute source was available for the component bonds.
- Live alert thresholds are now `±300` and `±500`. Alert state keys are signed, so positive and negative threshold crossings are tracked separately.
- Dashboard status and threshold-distance logic use `abs(a)`, so negative crossings such as `-300` and `-500` are treated as alert-level events.
- Historical chart loading now merges packaged `history_seed/runs` and live `RUNS_DIR` points for the same date by timestamp, with live points overriding seed points.
- Read-only Debug inspectors were run: the code inspector found the seed/live merge bug and non-blocking cleanup/policy notes; the A-accuracy inspector independently matched sampled 511090 and 511130 calculations to the generated reports.
- Railway production is deployed and Online:

```text
https://511130-live-monitor-production.up.railway.app
```

- Latest verified deployment: `6ad1ba11-56b8-4a75-a52c-43252aa79673`.
- Runtime patch is live: `target_date=auto`, market-hours auto-run gate, Eastmoney system-curl fallback, guarded auto loop, lightweight `/health`, 15-second auto polling, and `--auto-run-notify`. Local code now changes the intended next deploy interval to 3 seconds.
- Railway Feishu webhook and signing secret are configured through env vars. Do not write or print raw webhook or secret values in repo docs.
- Real Feishu test succeeded on 2026-06-16 12:25 CST: `POST /api/notify-test` returned `ok=true`, Feishu business response code `0`, message `success`.
- A final doc-only Railway deploy happened after the Feishu test. No extra Feishu message was sent; latest `/health` still showed `process_ok=true`, `auto_loop.code=running`, and `notification.diagnostics=sent`.
- `smoke_check.py` passed against the latest production deployment: `/health` and `/api/data` returned 200, notification setup was visible and redacted, and strict accuracy guardrails were visible.
- Railway HTTP 4xx/5xx log check returned no entries after deployment.
- Next-day actual-a reporting is implemented locally through `daily_actual_a_report.py`; it writes `reports/511130_daily_actual_a/YYYYMMDD/` and copies the PDF to `/Users/happytang/Desktop/511130_每日实际a/`.
- The report fails closed unless it can read the run-day PCF `PreTradingDay`, target-day PCF, per-bond interest, and 240 shared one-minute timestamps.
- It also writes a separate 5-minute cross-check CSV/PDF from Eastmoney 5-minute K-line data when 48 shared timestamps are available; this is cross-check-only and does not replace the 1-minute official report.
- Confirmed fixture regressions are covered in tests: 2026-06-12 close `+297.37/-0.27`; 2026-06-15 close `+234.96/-254.61`.
- Codex local cron automation is created as `511130-a` / `511130 次日实际a日报`; it runs the daily report script and retries PCF readiness inside the script until 10:00.
- 2026-06-19 Feishu runtime alert was diagnosed as a Dragon Boat Festival closed-day false alert: SSE is closed `20260619-20260621`, reopening `20260622`.
- Railway production has been deployed and verified with deployment ID `d97fbc14-846c-4d80-8dfc-bd9f93b369ab`.
- Production now has `auto_run_closed_dates` for 2026 official SSE holidays and pauses dashboard auto-run on `20260619` until `06-22 09:25`.
- Production now keeps runtime/data errors out of Feishu entirely. Errors remain in dashboard/health diagnostics; Feishu only sends actual threshold alerts and explicit `/api/notify-test`. This is enforced in dashboard auto-run, CLI precheck, once-mode no-alert results, and top-level exception handling.
- Production no longer sends no-threshold run-check messages.
- Valid threshold alerts retry Feishu delivery 3 times by default; if all attempts fail, threshold activation is rolled back so the next valid above-threshold run continues trying.
- Current strict realtime a and Feishu alerts are locked to `realtime_eastmoney`; Sina/minute sources are not accepted for current-a alerting.
- If strict realtime quotes fail after PCF and per-bond interest context is ready, local code can issue a clearly labeled degraded candidate alert only when thresholds are crossed. This candidate path has separate threshold state and is explicitly blocked from replacing strict current-a display.
- `/health` and `/api/data.config` now expose `alert_policy_setup`; `smoke_check.py` validates that runtime errors and no-alert checks do not send Feishu, degraded candidate alerts are enabled, and notification attempts are at least 3.
- Local candidate dashboard smoke passed on `127.0.0.1:8797`: holiday gate message, Eastmoney-only strict source policy, and `alert_policy_setup` were all visible; local smoke returned `ok=true`.
- Production smoke passed after deploy: `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json` returned `ok=true`, `issues=[]`.
- Production `/health` after deploy showed `last_error=""`, `auto_error_count=0`, `last_run_message=休市暂停，06-22 09:25后恢复自动计算`, `intraday_source=eastmoney_realtime_snapshot_only`, `allowed_price_sources=['realtime_eastmoney']`, and `alert_policy_setup.runtime_error_notifications=false`.
- Historical open-day curve replay is implemented locally and should be deployed with the next Railway upload. The dashboard can merge live Volume data from `/data/runs` with packaged seeds from `scripts/511130_live_monitor/history_seed/runs`.
- Packaged historical seeds currently cover `20260612`, `20260615`, `20260616`, and `20260617`, each with 240 one-minute `estimated_a` points. `20260618` is intentionally not included because no complete local curve file was found.
- `/api/series` allows historical chart sources for replay, but current-a display and Feishu alerts remain strict realtime only.
- Auto-run prepares and caches the PCF + per-bond-interest context before realtime price calculation; if context preparation fails, no current a or alert is produced.
- Local code can reuse exact same-day verified per-bond interest when the SSE interest endpoint later fails, so valid threshold alerts are not blocked by a transient interest-query failure. Cross-date interest cache remains rejected.
- For the user-pasted `20260616` missing-interest alerts, local retained report evidence has official SSE interests `019776=0.273` and `019837=0.319`; treat the repeated alerts as an old/fixed-date missing-input notification problem, not a formula bug.

## Verification Commands

```bash
python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/smoke_check.py tests/test_511130_live_monitor.py
python3.12 -m unittest tests.test_511130_live_monitor  # 91 tests OK in latest local run
python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest
python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json
curl -fsS https://511130-live-monitor-production.up.railway.app/health
curl -fsS https://511130-live-monitor-production.up.railway.app/api/dates
curl -fsS 'https://511130-live-monitor-production.up.railway.app/api/series?date=20260617&range=day&interval=1m'
```

Use `POST /api/notify-test` only when the user wants a real Feishu test message.
Post-deploy, the tightened `smoke_check.py` should pass read-only. Do not use `POST /api/notify-test` unless the user explicitly requests a real Feishu test message.

Daily actual-a report:

```bash
python3.12 -m pip install -r scripts/511130_live_monitor/requirements.txt
python3.12 scripts/511130_live_monitor/daily_actual_a_report.py --no-retry
```

## Highest Priority Next

- If the user wants the new quote-card UI, four-card horizontal strip, and 3-second interval online, deploy Railway and run read-only production smoke before trusting the public URL.
- If the user wants the new `±300/±500` behavior online, deploy and run read-only production smoke before trusting Railway.
- If the user asks for 511090 intraday curves, first secure an auditable minute-level source for its component bonds; otherwise keep the result labeled daily-close only.
- During the next trading session, check whether strict realtime a resumes.
- After deploying historical replay, confirm production `/api/dates` includes `20260612`, `20260615`, `20260616`, and `20260617`.
- After the first 09:10 local automation run, check whether `reports/511130_daily_actual_a/YYYYMMDD/summary.json` and the Desktop PDF were created, or whether `pending/*.md` correctly explains the blocker.
- If `data_ok=false`, diagnose in this order: `diagnostics.pcf`, `diagnostics.quote`, then `diagnostics.notification`.
- Do not loosen `max_skew_seconds=3`, `max_stale_seconds=30`, or `missing_interest_fallback_allowed=false` just to show a number.

## Constraints

- Read-only monitor only. No trading, no order APIs.
- Current PCF component lock is `019776/019837`; if the PCF changes bonds, stop and manually verify before changing config.
- Historical curve persistence depends on Railway Volume `511130-live-monitor-volume` mounted at `/data`, with `A_MONITOR_RUNS_DIR=/data/runs`.
- Public read/write config is still open; if the page is shared wider than the team, consider `A_MONITOR_PUBLIC_READONLY=1` or access control.
- Local Docker daemon was not running during this work; local Docker image build remains unverified.
