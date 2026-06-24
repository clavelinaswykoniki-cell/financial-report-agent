# Project State

## 2026-06-24

- 本地完成 511130 团队看板布局调整：去掉顶部大号当前 a 统计块，四联行情卡上移到首屏，历史曲线移动到四联卡下方；公式、行情源、阈值和飞书逻辑未改。
- 接续检查 `scripts/511130_live_monitor` 的“加快行情更新频率”任务。
- 代码已确认：`live_a_dashboard.py` 默认 `--interval=3`，前端按 `cfg.refreshSec * 1000` 轮询，Dockerfile 和 `railway.toml` 启动命令均使用 `--interval 3`。
- 线上只读 smoke 通过：`ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`。
- 当前线上状态为休市 `market_closed`，因此 `data_ok=false` 属于预期数据状态，不是自动线程空转或服务崩溃。
- 本轮未触发飞书真实发送，未读取或写入 webhook/secret。

## 2026-06-23

- 新增 `research_training_daily` MVP。
- 已实现核心池配置、日报生成、新币提醒、飞书 webhook 发送、测试和 dry-run。
- 日报当前是训练框架 + 公开信号抓取，不接入券商行情、付费新闻、卖方一致预期或自动交易接口。
- 新币提醒按可信度分层：高=官方公告/API，中=聚合器，低=手动/社媒传闻。
- 已创建 Codex 自动化：`ai-3` 每天 08:30 生成投研训练日报；`automation-3` 每小时检查新币上市信号。
- 当前 shell 未配置 `RESEARCH_DAILY_FEISHU_WEBHOOK_URL` / `RESEARCH_DAILY_FEISHU_SECRET`，因此未执行真实飞书发送。
