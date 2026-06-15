# Decisions

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
