# Changelog

## 2026-06-15

- Railway public dashboard deployed at `https://511130-live-monitor-production.up.railway.app`.
- Added Docker/Railway deployment config with `/health` and automatic `PORT` handling.
- Hardened a-value correctness gates: PCF structure, creation unit, interest source, stale snapshots, and main status priority.
- Added focused tests for the live monitor dashboard and formula guardrails.
- Added K-line style chart controls: date selection, range selection, 1-second line view, and 1-minute/15-minute a-value OHLC aggregation.
