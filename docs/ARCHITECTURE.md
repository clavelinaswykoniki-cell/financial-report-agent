# Architecture

## Python Packages

- `src/financial_report_agent`: SEC EDGAR/XBRL 财报分析。
- `src/crypto_risk_agent`: OKX 公开行情 + 本地仓位配置的只读风控。
- `src/research_training_daily`: 投研训练日报、新币提醒、飞书卡片。
- `scripts/511130_live_monitor`: 511130 a 值只读监控、团队看板、Railway 部署和次日实际 a 日报。

## 511130 Live Monitor

- `live_a_dashboard.py`: 本地/线上 Web 看板，默认 3 秒自动计算和浏览器刷新。
- `monitor_511130.py`: PCF、逐券利息、东方财富严格实时行情和 a 值公式主入口。
- `daily_actual_a_report.py`: 下一交易日实际 a 报告，正式主口径使用东方财富 1 分钟共同时间戳。
- `smoke_check.py`: 只读验证 `/health` 和 `/api/data`，不触发飞书消息。
- `Dockerfile` / `railway.toml`: Railway 入口，启动命令使用 `--auto-run --auto-run-notify --interval 3`。
- 运行边界：只读展示和预警，不连接下单接口；飞书成功以业务响应码为准。

## Research Training Daily

- `research_universe.yaml`: 全球科技股票池和 crypto 池。
- `research_training_daily.universe`: 受控 YAML 解析、增删、状态维护。
- `research_training_daily.selection`: 训练排序和深读任务生成。
- `research_training_daily.listings`: Binance/OKX/Bybit 官方公告、CoinGecko/CoinMarketCap/CoinMarketCal/CryptoRank/ICO Drops 聚合器抓取、低可信手动信号。
- `research_training_daily.report`: Markdown/JSON/Feishu card 输出。
- `research_training_daily.feishu`: 飞书 webhook 签名和业务码校验。
- CLI: `research-training-daily` 或 `python -m research_training_daily`。

## Outputs

- `reports/research_training_daily/YYYY-MM-DD/brief.md`
- `reports/research_training_daily/YYYY-MM-DD/brief.json`
- `reports/research_training_daily/YYYY-MM-DD/feishu-card.json`
- `reports/research_training_daily/YYYY-MM-DD/source-log.json`
- `reports/research_training_daily/YYYY-MM-DD/listing-alerts.jsonl`
- `reports/research_training_daily/listing-alerts-seen.jsonl`
