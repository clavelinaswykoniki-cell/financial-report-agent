# Next Session

先读：

1. `AGENTS.md`
2. `CODEX.md`
3. `docs/PROJECT.md`
4. `docs/TASKS.md`
5. `scripts/511130_live_monitor/CODEX.md`
6. `scripts/511130_live_monitor/docs/NEXT_SESSION.md`

## Current State

- 511130 看板布局调整已上线：顶部大号当前 a 统计块已移除，四联行情卡上移，历史曲线移动到四联卡下方。
- GitHub 分支：`codex/511130-a-monitor`；布局提交：`6b29e98`；Railway production 最新成功 deployment 以 `railway deployment list --json` 的同分支记录为准。
- 当前接续任务是 `scripts/511130_live_monitor` 的 511130 行情更新频率和看板状态确认。
- 3 秒更新任务已完成：`live_a_dashboard.py` 默认 `--interval=3`，前端按 `cfg.refreshSec * 1000` 轮询，Dockerfile 和 `railway.toml` 均用 `--interval 3`。
- 本地验证通过：`python3.12 -m py_compile ...` 无错误；`python3.12 -m unittest tests.test_511130_live_monitor` 通过 91 个测试。
- 线上只读 smoke 通过：生产 `https://511130-live-monitor-production.up.railway.app` 返回 `ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`。
- 线上 HTML/DOM 验证通过：`refreshSec3=true`、`quote_before_chart=true`、`has_primary_value=false`、`has_latest_a=false`、`chart_title=true`。
- 2026-06-24 16:31 CST 线上状态是 `market_closed`，提示 `06-25 09:25后恢复自动计算`；这是休市状态，不是自动线程空转。
- Railway 服务在线并挂载 `/data` volume；生产运行不依赖本地 Codex 在线。
- `/health.public_readonly=false`，公网仍允许手动计算/飞书测试等写接口；若要只读公开看板，设置 `A_MONITOR_PUBLIC_READONLY=1`，但会禁用这些按钮。
- 本轮未调用 `/api/notify-test`，未触发飞书真实发送，未读取或写入 webhook/secret。

## Highest Priority Next

- 下一个交易时段复查生产看板是否从 `market_closed` 恢复严格实时 a。
- 如果 `data_ok=false`，优先看 `/health.diagnostics.pcf`、`diagnostics.quote`、`diagnostics.notification`。
- 不要放宽 3 秒同步、30 秒新鲜度、缺利息 fail-closed 或东方财富严格实时源锁定。
- 决策待定：是否把生产切到 `A_MONITOR_PUBLIC_READONLY=1`，用安全换掉线上手动计算/飞书测试能力。

## Still Available

- `research_training_daily` MVP 仍已完成；如回到日报任务，读取 `docs/HANDOFF.md` 的 2026-06-23 条目。
- 不要自动交易或连接下单接口。
- 不要把 webhook/secret 写入仓库。
