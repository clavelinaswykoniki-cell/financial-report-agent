# Changelog

## 2026-06-24

- Moved the connected four-card market strip to the top of the dashboard, removed the old large top-left current-a summary block, and moved the historical curve section directly below the four-card strip.
- Added regression coverage so the dashboard HTML keeps the quote-card strip above the historical chart and does not reintroduce the removed primary current-a block.
- Added dashboard quote cards for `511130`, `019776`, and `019837`, including latest price, change, turnover, five-level bid/ask display, and per-security sparklines from cached Eastmoney 1-minute intraday data, Sina 1-minute fallback, plus saved strict-realtime points.
- Changed the market area into a connected four-card horizontal strip: `511130`, `019776`, `019837`, and `套利值A` sit side by side with zero card gap; narrow screens keep the strip horizontal with local scrolling instead of stacking it vertically.
- Kept the five-level order book as display-only Sina snapshot data; strict current-a calculation and Feishu alerts remain locked to Eastmoney realtime snapshots.
- Changed the default dashboard, Docker, and Railway auto-run refresh interval to 3 seconds while preserving 3-second quote-skew and 30-second stale-data guards.
- Added regression coverage for Sina five-level order-book parsing, `/api/data.quote_cards` synthesis, and the connected four-card dashboard HTML; full `tests.test_511130_live_monitor` passes with 91 tests.
- Deployed to Railway production as `7ab73f6a-ce74-4ec2-ac7f-d9c7311a13e9`; read-only production smoke passed, `/api/data` returned 3 quote cards and 30 order-book rows, and Chrome DOM verification showed four connected cards with `adjacentGap=0`.

## 2026-06-23

- Generated 511130 1-minute estimated/actual A summaries for `20260615` through `20260618`; `20260619` was excluded as an exchange-closed day.
- Generated 511130 overview SVG at `reports/511130_daily_actual_a/511130_1m_estimated_actual_a_20260615_20260618.svg`.
- Generated 511090 daily-close estimated/actual A output for `20260608` through `20260618`, using PYAMC PCF, ETF 15:00 close, and SSE daily bond net price plus accrued interest.
- Labeled 511090 as daily-close only because component-bond historical minute prices were not available from a stable public source in this run.
- Changed live alert threshold handling to signed `±300` and `±500` behavior, with positive and negative alert states tracked independently.
- Updated dashboard status classification and threshold-distance calculations to use `abs(a)` so negative threshold crossings are surfaced as alerts.
- Fixed historical chart loading so packaged history seeds and live `RUNS_DIR` points for the same date are merged by timestamp; live points override seed points.
- Added a regression test for seed/live same-date merge behavior.
- Ran read-only Debug inspectors for code risk and A-value accuracy; sampled A recomputations matched generated outputs.

## 2026-06-19

- Added historical open-day curve replay for the dashboard. `/api/series` now accepts report/replay sources for charting while current-a display and alerts remain strict realtime only.
- Added `seed_history_curves.py` and packaged built-in history seeds for `20260612`, `20260615`, `20260616`, and `20260617`; `20260618` is intentionally absent because no complete curve file is available locally.
- Updated the chart controls to default to `全天`, distinguish `今天实时` from `历史回放`, and select the latest available historical open day when the current date has no chart points.
- Added `auto_run_closed_dates` to `config.json` using the 2026 SSE holiday schedule, including Dragon Boat Festival `20260619`.
- Updated the dashboard auto-run gate to treat configured exchange-closed dates as non-trading days, returning `休市暂停，06-22 09:25后恢复自动计算` instead of trying to fetch same-day PCF.
- Added a focused regression test so `2026-06-19 10:00` pauses until `2026-06-22 09:25` and does not become a repeated PCF-not-ready alert.
- Disabled automatic Feishu messages for runtime/data errors. Errors stay visible in dashboard/health diagnostics, while Feishu is reserved for valid threshold alerts and explicit `/api/notify-test`.
- Stopped no-alert run-check messages: successful calculations below threshold no longer send a Feishu message.
- Locked current strict realtime a and Feishu alerts to Eastmoney realtime snapshots only; unconfigured realtime sources are not accepted as current-a fallbacks.
- Added a degraded candidate alert path after strict realtime quote failure. It runs only when PCF and per-bond interest context is ready, sends only on threshold crossing, labels the Feishu title/body as degraded candidate data, and does not replace the strict current-a display.
- Blocked degraded candidate snapshots from being promoted to the dashboard latest strict-a result, even if a future config omits explicit strict source filtering.
- Split threshold state by alert source mode so strict alerts and degraded candidate alerts do not reset each other's de-duplication state.
- Added read-only `alert_policy_setup` diagnostics to `/health` and `/api/data.config` for threshold-only notifications, disabled error/run-check notifications, degraded candidate alerts, and retry count.
- Tightened `smoke_check.py` so a deployment only passes if health reports Eastmoney-only strict realtime source policy and the intended alert policy.
- Auto-run now prepares and caches the PCF plus per-bond-interest context before realtime price calculation, so trading-session loops only refresh the locked realtime quote source after context readiness.
- Added retry for valid threshold alert delivery and rollback on total delivery failure so the next valid above-threshold run continues trying.
- Added exact-date interest cache fallback: if per-bond interest was already verified for the same trading day, a temporary SSE interest-query failure can reuse that same-day value so a valid threshold alert can still be calculated and sent.
- Deployed the alert-policy and degraded-candidate-alert patch to Railway production as `d97fbc14-846c-4d80-8dfc-bd9f93b369ab`; post-deploy read-only smoke passed and no Feishu test message was sent.

## 2026-06-17

- Added `daily_actual_a_report.py` for next-trading-day actual-a reporting.
- The report resolves the target day from the run-day PCF `PreTradingDay`, uses target-day `EstimatedCashComponent` for estimated a, and uses run-day `PreCashComponent` for actual a.
- Added raw PCF, raw Eastmoney 1-minute, raw/declared interest-source retention under `reports/511130_daily_actual_a/YYYYMMDD/raw/`.
- Added CSV, `summary.json`, and one-page PDF output, plus Desktop PDF copy to `/Users/happytang/Desktop/511130_每日实际a/`.
- Added optional 5-minute cross-check CSV/PDF using Eastmoney 5-minute K-line data; it is saved separately and marked as cross-check-only.
- Added fail-closed checks for `CreationRedemptionUnit=10000`, PCF `RecordNumber`, component-code lock, 240 shared one-minute timestamps, and missing per-bond interest.
- Limited retry behavior to temporary PCF/upstream-readiness failures; structural failures now write pending immediately.
- Added fixture regressions for the confirmed 2026-06-12 close values `+297.37/-0.27` and 2026-06-15 close values `+234.96/-254.61`.
- Added `reportlab` to local requirements and embedded a macOS Chinese font in generated PDFs so PNG/PDF rendering keeps Chinese text visible.
- Created Codex local cron automation `511130-a` / `511130 次日实际a日报`.

## 2026-06-16

- Deployed the Railway runtime reliability patch to production; latest verified deployment is `6ad1ba11-56b8-4a75-a52c-43252aa79673`.
- Updated Railway Feishu environment variables with the current webhook and signing secret, without storing raw secret values in repo docs.
- Verified production with read-only `smoke_check.py`: `/health` and `/api/data` returned 200, `process_ok=true`, `auto_loop=running`, notification config present, and strict accuracy guardrails intact.
- Verified real Feishu delivery through `POST /api/notify-test`: business response code `0`, message `success`, and `/health.diagnostics.notification=sent`.
- Prepared a runtime reliability patch for Railway: automatic Shanghai target date, Eastmoney system-curl fallback, slower 15-second auto polling, and startup notify flag.
- Added auto-date rollover for long-running dashboard processes so `target_date=auto` does not stay pinned to the startup date.
- Preserved explicit fixed dates (`--date` / fixed target-date mode) so manual replay or fixed-day runs are not overwritten by auto rollover.
- Added market-hours gating for auto-run and startup preload so closed-market nights and weekends do not look like crashes or trigger runtime-error alerts.
- Keeps auto calculation failures out of Feishu; runtime/data errors are surfaced through dashboard, `/health`, and logs.
- Added an auto-loop guard so an unexpected single-cycle exception records an error and continues the next cycle instead of silently killing the auto-run thread.
- Added `/health.auto_loop` heartbeat diagnostics so a live web process can distinguish a running auto thread from a stale, starting, disabled, or stuck one.
- Added `/health.process_ok`; `/health` now returns HTTP 503 only when the auto loop is stale, so Railway can restart a stuck background thread without restarting on quote/PCF unavailability.
- Added redacted `notification_setup` diagnostics to `/health` and `/api/data.config` so webhook kind and Feishu signing-secret presence can be checked without sending a message or exposing secrets.
- Added `accuracy_setup` diagnostics to `/health` and `/api/data.config` so formula version, PCF source, component lock, strict realtime sources, skew/stale limits, and missing-interest policy are visible before trusting an a-value.
- Added read-only `smoke_check.py` for post-deploy validation of `/health`, `/api/data`, auto-loop state, notification setup, and accuracy guardrails without sending Feishu messages.
- Kept `/health` lightweight during auto-date rollover by avoiding external PCF/interest preloading inside the healthcheck route.
- Decoupled dashboard Feishu test from market data via `POST /api/notify-test`.
- Added PCF-not-ready retry backoff and `/health` retry countdown.
- Added structured runtime diagnostics for PCF, quote, and notification layers.
- Decoupled notification delivery failures from data health: a fresh strict-realtime a remains available when Feishu delivery fails, while notification failure is surfaced separately.
- Tightened webhook success detection so Feishu must return `code`/`StatusCode` and WeCom must return `errcode`; HTTP 200 with missing business code is now treated as failure.
- Made notification/alert event file writes best-effort so log write failures do not break Feishu delivery or auto-run.
- Made a-value result, interest-cache, and alert-state writes fail-soft so storage issues do not break the calculation path.
- Added an in-process latest-result fallback so the dashboard can still show the current strict-realtime a when result-log writes fail.
- Made dashboard run-file reads fail-soft so malformed `a_values.jsonl`, `alerts.jsonl`, `notifications.jsonl`, or `runs/` paths do not break `/api/data`, `/health`, or `/api/dates`.
- Hardened dashboard rendering against malformed snapshot field types such as null timestamps or numeric component `code/name` values.
- Added API-level JSON error fallback so unexpected route exceptions do not surface to the browser as dropped connections.
- Made `state.json` loading fail-soft when Railway Volume state is corrupt or malformed.
- Added `/health` data-state fields and explicit `pcf_not_ready` dashboard status.
- Added focused tests for auto date resolution and rollover, lightweight healthcheck rollover, fixed-date behavior, market-hours gating and preload skipping, auto-loop exception recovery, heartbeat diagnostics, stale-loop healthcheck 503, redacted notification setup, accuracy guard diagnostics, read-only smoke checks, Eastmoney fallback, PCF-not-ready status, API error fallback, webhook business-code enforcement, Feishu signature timestamp handling, automatic error non-delivery, independent Feishu tests, notification/data-health isolation, malformed snapshot display fields, state/log read/write hardening, in-process latest-result fallback, state-file hardening, and preserving `target_date=auto` on config save.

## 2026-06-15

- Railway public dashboard deployed at `https://511130-live-monitor-production.up.railway.app`.
- Added Docker/Railway deployment config with `/health` and automatic `PORT` handling.
- Hardened a-value correctness gates: PCF structure, creation unit, interest source, stale snapshots, and main status priority.
- Added focused tests for the live monitor dashboard and formula guardrails.
- Added K-line style chart controls: date selection, range selection, 1-second line view, and 1-minute/15-minute a-value OHLC aggregation.
- Mounted Railway Volume `511130-live-monitor-volume` at `/data` and set `A_MONITOR_RUNS_DIR=/data/runs` for persistent chart history.
