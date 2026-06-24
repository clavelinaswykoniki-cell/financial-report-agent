# Tasks

## Done

- 2026-06-24: 本地完成 511130 看板布局调整：移除顶部大号当前 a 统计块，四联行情卡上移，历史曲线放到四联卡下方；本轮未部署 Railway。
- 2026-06-24: 接续并验证 `scripts/511130_live_monitor` 行情更新频率任务；确认本地代码、Dockerfile、`railway.toml`、模块文档均为 3 秒自动刷新/轮询。
- 2026-06-24: 运行 511130 本地验证：`python3.12 -m py_compile ...` 无错误，`python3.12 -m unittest tests.test_511130_live_monitor` 通过 91 个测试。
- 2026-06-24: 运行线上只读 smoke：生产 `https://511130-live-monitor-production.up.railway.app` 返回 `ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`；当前 `market_closed` 是休市状态，不是服务空转故障。
- 新增 `research_universe.yaml` 全球科技股/crypto 初始池。
- 新增 `research-training-daily report/listings/pool` CLI。
- 新增日报 Markdown/JSON/Feishu card 输出。
- 新增新币提醒抓取、去重和可信度标签。
- 新增测试 `tests/test_research_training_daily.py`。
- 创建自动化 `ai-3` 和 `automation-3`。

## Next

- 若用户要求上线本次布局调整，提交、推送 `codex/511130-a-monitor` 并部署 Railway，然后跑只读 smoke。
- 511130 下一个交易时段复查生产看板是否从 `market_closed` 恢复为严格实时 a；若 `data_ok=false`，先看 `/health.diagnostics.pcf/quote/notification`，不要放宽 3 秒同步、30 秒新鲜度或缺利息 fail-closed 规则。
- 配置专用飞书 webhook 环境变量后，运行测试卡片。
- 根据实际日报质量调整股票池和评分权重。
- 若需要更真实的股票涨跌线索，接入稳定行情/新闻/财报事件源。
