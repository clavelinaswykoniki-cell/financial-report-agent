# Handoff

## 2026-06-24 - 511130 看板布局调整

状态：已提交、推送 GitHub，并部署 Railway production。

发布：

- 分支：`codex/511130-a-monitor`
- 代码提交：`6b29e98 feat: reorder 511130 dashboard layout`
- 生产地址：`https://511130-live-monitor-production.up.railway.app`

改动：

- 移除顶部左侧大号当前 a 统计块。
- 四联行情卡上移到标题栏下方，作为首屏主体。
- 历史曲线 / a-K线移动到四联卡下方。
- 页面层只改布局，不改 a 值公式、行情源、阈值、飞书发送或 fail-closed 规则。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`：91 tests OK。
- 本地 Chrome 截图：`tmp/511130-dashboard-reorder-local.png`，DOM 检查 `cards=4`、`hasPrimary=false`、`hasLatestA=false`、`chartBelowQuote=true`。
- 线上只读 smoke：`ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`、`data_status_code=market_closed`。
- 线上 HTML/DOM：`refreshSec3=true`、`quote_before_chart=true`、`has_primary_value=false`、`has_latest_a=false`、`chart_title=true`。
- Railway source 确认为 `codex/511130-a-monitor`，Dockerfile 和 `--auto-run --auto-run-notify --interval 3` 启动命令生效；最终 deployment ID 以 `railway deployment list --json` 中最新 `SUCCESS` 的同分支记录为准。

运维注意：

- Railway 服务在线，持久化 volume 挂载 `/data`，运行不依赖本地 Codex 在线。
- `/health.public_readonly=false`，因此公网仍保留 `手动算一次` / `发送飞书测试` 等写接口能力；若后续希望团队看板只读，应在 Railway 设置 `A_MONITOR_PUBLIC_READONLY=1`，但这会禁用这些按钮。
- 本轮没有调用 `/api/notify-test`，没有发送飞书消息，没有读取或写入 webhook/secret。
- headless Chrome/Edge 在本机生成线上截图时被系统杀掉；线上 DOM 和 smoke 已通过，本地截图仍在 `tmp/511130-dashboard-reorder-local.png`。

## 2026-06-24 - 511130 行情更新频率接续检查

状态：接续完成，未发现 511130 看板仍卡在旧的 15 秒频率。根目录交接文档此前停在 `research_training_daily`，已同步回 511130 当前状态。

确认事实：

- `scripts/511130_live_monitor/live_a_dashboard.py` 默认 `--interval=3`。
- 前端刷新使用 `cfg.refreshSec * 1000` 轮询 `/api/data`。
- `Dockerfile` 和 `railway.toml` 的 Railway 启动命令均为 `--auto-run --auto-run-notify --interval 3`。
- `scripts/511130_live_monitor/docs/NEXT_SESSION.md`、`TASKS.md`、`CHANGELOG.md` 已记录 2026-06-24 的 3 秒刷新、91 tests OK 和生产 smoke 结果。
- 生产地址：`https://511130-live-monitor-production.up.railway.app`。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/smoke_check.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`：91 tests OK。
- `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json`：`ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`。

当前线上状态：

- 2026-06-24 16:31 CST 查询时，线上 `/api/data.status.code=market_closed`，详情为 `盘外暂停，06-25 09:25后恢复自动计算`。
- 因为当前是休市后，`data_ok=false` 是预期数据状态，不代表进程空转或自动线程死亡。
- 本轮只读验证，没有触发 `/api/notify-test`，没有发送飞书消息，没有读取或写入 webhook/secret。

下一步：

- 下一个交易时段复查生产看板是否恢复严格实时 a。
- 若仍 `data_ok=false`，按 `diagnostics.pcf`、`diagnostics.quote`、`diagnostics.notification` 顺序排查。
- 不要为了显示数字放宽 3 秒同步、30 秒新鲜度、缺利息 fail-closed 或东方财富严格实时源锁定。

## 2026-06-23 - 科技/AI/加密投研训练日报与新币提醒

状态：实现完成并通过测试。

自动化：

- `ai-3`: 科技AI加密投研训练日报，每天 08:30 Asia/Shanghai。
- `automation-3`: 新币上市信号每小时提醒，每小时运行。

关键文件：

- `src/research_training_daily/`
- `research_universe.yaml`
- `tests/test_research_training_daily.py`
- `docs/PROJECT_STATE.md`
- `docs/NEXT_SESSION.md`

验证：

- `.venv/bin/python -m pytest tests/test_research_training_daily.py -q`
- `.venv/bin/python -m pytest -q`
- `PYTHONPATH=src python3 -m research_training_daily report --date 2026-06-23 --skip-listings --out-root tmp/research_training_daily_dryrun`
- `PYTHONPATH=src python3 -m research_training_daily listings --date 2026-06-23 --out-root tmp/research_training_daily_listing_dryrun2 --max-alerts 20 --manual-signal 'XYZ|Rumor Coin|https://example.com|KOL says exchange listing soon'`
- Codex app automation create: `ai-3` and `automation-3`

注意：

- `python` 命令在当前 shell 不存在，使用 `python3` 或 `.venv/bin/python`。
- 系统 Python 没有 pytest，测试使用 `.venv/bin/python -m pytest`。
- 飞书正式发送需要配置 `RESEARCH_DAILY_FEISHU_WEBHOOK_URL`，如机器人启用签名还要配置 `RESEARCH_DAILY_FEISHU_SECRET`。
- 本次检查显示当前 shell 中这两个环境变量都未配置，因此未做真实发送。
- 当前实现不接入实时股票行情或付费新闻，报告会明确数据缺口。
