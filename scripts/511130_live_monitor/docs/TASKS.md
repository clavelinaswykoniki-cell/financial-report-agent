# Tasks

## Done

- 2026-06-15: 部署 Railway 公网看板，URL 为 `https://511130-live-monitor-production.up.railway.app`。
- 2026-06-15: 增加 Railway `Dockerfile`、`railway.toml`、`/health` 和 `$PORT` 自动读取。
- 2026-06-15: 增加公式/PCF/利息/过期快照/状态优先级 focused tests。
- 2026-06-15: 强化 fail-closed 规则：PCF结构变化、CreationRedemptionUnit变化、缓存利息、过旧/不同步行情都不展示当前 a。
- 2026-06-15: 升级 `live_a_dashboard.py` 为手机/电脑团队看板。
- 2026-06-15: `/api/data` 返回状态分类、计算拆解、成分券明细、配置元数据。
- 2026-06-15: 增加 `/api/dates` 和 `/api/series`，支持近1分钟/5分钟/15分钟/1小时/今天范围切换。
- 2026-06-15: 曲线支持 1秒折线、1分钟 a值OHLC、15分钟 a值OHLC，并保留当前 a 与历史图表分离。
- 2026-06-15: 页面展示 a 值、300 距离、行情时间差、计算时间、飞书最近状态、阈值线、红色超阈值区间、手工利息标签。
- 2026-06-15: 增加 `/api/config`，支持保存目标日期、阈值和逐券利息覆盖。
- 2026-06-15: README 补充局域网团队访问命令。
- 2026-06-15: 挂载 Railway Volume `511130-live-monitor-volume` 到 `/data`，并设置 `A_MONITOR_RUNS_DIR=/data/runs`。

## Backlog

- 若需要更强审计能力，可在 Railway Volume 之外再接数据库或对象存储。
- 若未来 PCF 换券，先人工确认新券结构，再改 `expected_component_codes`。
