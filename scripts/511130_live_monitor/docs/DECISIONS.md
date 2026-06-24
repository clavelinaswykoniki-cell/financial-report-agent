# Decisions

## 2026-06-23: Signed Threshold Alerts

Decision: threshold monitoring should treat `300` and `500` as absolute levels and evaluate both positive and negative crossings.

Rationale: the user wants alerts at `±300` and `±500` to support possible reverse operations. A negative threshold crossing is materially different from a positive one and should not be suppressed by prior state in the opposite direction.

Implications:

- Store configured thresholds as positive levels such as `300` and `500`.
- Detect both `+level` and `-level`.
- Track threshold activation with signed keys so `+300` and `-300` have independent de-duplication state.
- Dashboard display should classify `abs(a) >= threshold` as alert-level while preserving the signed value in the UI/message.

## 2026-06-23: Merge Seed And Live Chart Points

Decision: for historical chart display, packaged seed points and live `RUNS_DIR` points for the same date should be merged by timestamp, with live points winning.

Rationale: packaged history keeps old open-day curves available after deploys, while live Volume data may add or correct points for the same date. Choosing only the first directory can hide newer live data and make the chart misleading.

Implications:

- Load seed points first, then overlay live points with the same timestamp.
- Sort and apply `max_points` after the merge, not per source.
- Keep this merge behavior limited to chart/history reads; current-a display and Feishu alerts still use strict latest realtime snapshots only.

## 2026-06-19: Exchange Holidays Pause Auto-Run

Decision: dashboard auto-run should treat configured exchange-closed dates as non-trading days, not as PCF/data failures.

Rationale: `20260619` is an official SSE Dragon Boat Festival closed day. Fetching same-day PCF on a closed day creates repeated false Feishu runtime alerts even though the monitor process and notification path are healthy.

Implications:

- Keep 2026 official closed dates in `config.json` under `auto_run_closed_dates`.
- On a closed date, auto-run clears data errors, does not preload PCF, does not fetch realtime quotes, and waits for the next trading session.
- This does not loosen strict realtime, PCF, interest, skew, stale, or component-structure checks on actual trading days.

## 2026-06-19: Feishu Sends Only Valid Alerts

Decision: Feishu should not receive automatic runtime/data-error messages. It should receive only valid threshold alerts after strict calculation succeeds, plus explicit manual webhook tests.

Rationale: the user needs a prewarning, not a stream of implementation errors. If the monitor cannot compute a trustworthy current a, sending an error to the group does not help the decision and creates alert fatigue.

Implications:

- Keep current a fail-closed when PCF, interest, quote freshness, quote sync, or structure checks fail.
- The code-level Feishu send path is limited to valid threshold alerts and explicit tests; dashboard auto errors, CLI precheck output, and top-level exceptions do not call notification delivery.
- Surface failures through dashboard, `/health`, logs, and smoke checks, not through the Feishu group robot.
- No-threshold successful calculations do not send a Feishu run-check message.
- Threshold-crossing calculations send `511130 a值预警` with retry; if all delivery attempts fail, the threshold state is rolled back so the next valid above-threshold run keeps trying.
- If the strict realtime quote path fails after PCF and interest context is ready, the dashboard may run a clearly labeled degraded candidate alert. It sends only when thresholds are crossed, uses a separate alert state, and does not replace the strict current-a display.
- `/health` and `/api/data.config` expose `alert_policy_setup`; post-deploy smoke checks must confirm threshold-only notifications, disabled runtime/no-alert notifications, degraded candidate alerts, and retry count.

## 2026-06-19: Lock Current A To Eastmoney Realtime

Decision: current strict realtime a and Feishu alerts use only `realtime_eastmoney`.

Rationale: mixing realtime sources can create subtle timestamp/field differences. A single source plus strict same-source synchronization is easier to audit and safer for alerting.

Implications:

- Eastmoney primary request may use the external `curl` transport fallback, but it remains the same Eastmoney snapshot endpoint.
- If Eastmoney realtime is unavailable, stale, date-mismatched, or not synchronized across the ETF and bonds within 3 seconds, current a fails closed.
- Sina realtime and minute/K-line sources are not accepted for strict current-a alerts. Any non-strict fallback can only produce a labeled candidate alert, not a formal strict-a value.

## 2026-06-19: Same-Day Verified Interest Cache

Decision: if per-bond accrued interest has already been verified for the same trading day from SSE or explicit manual override, the live monitor may reuse that same-day cached value when the interest endpoint later fails.

Rationale: this preserves the fail-closed stance against stale interest while avoiding a fragile single upstream interest query blocking valid threshold alerts all day.

Implications:

- Cross-date interest cache is still rejected for current a.
- Missing/default interest remains rejected.
- Cached same-day interest source is labeled separately in diagnostics.

## 2026-06-15: Fail Closed For Current a

Decision: the dashboard must hide current a instead of showing a questionable number when realtime data, PCF, or interest inputs fail validation.

Rationale: this is an internal monitor that may inform real-money decisions. A missing value with an explicit reason is safer than a fabricated or stale current value.

Implications:

- Three security quote timestamps must remain within the configured skew limit, currently 3 seconds.
- PCF component structure changes require manual confirmation.
- Historical cached interest is not accepted for current a.
- Railway chart history is useful for observation but not a durable audit record.

## 2026-06-15: Chart History Uses Raw Strict Realtime Points

Decision: keep storing 1-second strict realtime snapshots as the source of truth, and derive 1-minute/15-minute a-value OHLC series on read.

Rationale: the user needs K-line style review without creating a second calculation path. Aggregating from raw points keeps the chart auditable and makes later recalculation possible.

Implications:

- `/api/series` returns line points for `interval=1s` and OHLC rows for `interval=1m` or `interval=15m`.
- The chart may show historical or aggregated a values, but the top current a still comes only from `/api/data` after strict freshness checks.
- Long-term cross-deploy history on Railway requires a persistent Volume or external storage.
