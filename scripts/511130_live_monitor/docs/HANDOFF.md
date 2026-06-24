# Handoff

## 2026-06-24 Dashboard Layout Reorder

当前状态：

- 已按用户确认的效果图调整 511130 团队看板首屏。
- 已提交、推送并部署 Railway production：`97db306a-2342-4555-a6f5-20747807eec3`，分支 `codex/511130-a-monitor`，提交 `6b29e98`。
- 删除原顶部左侧大统计块：`当前 a 值`、`511130 价格`、`距离 300`、`行情时间差`、`最新计算时间`、`飞书最近状态`、`点位数` 和盘外/休市提示不再单独占据左上大面板。
- 四联行情卡现在紧跟标题栏显示：`511130`、`019776`、`019837`、`套利值A`。
- 历史曲线 / a-K线移动到四联卡下方，标题改为 `历史曲线 / a-K线`。
- `套利值A` 卡仍展示当前 a、阈值、状态和严格实时说明；页面层没有改公式、行情源、阈值、飞书发送或 fail-closed 规则。
- 本轮没有发飞书测试消息、没有做交易或下单动作。

修改文件：

- `scripts/511130_live_monitor/live_a_dashboard.py`
- `tests/test_511130_live_monitor.py`
- `scripts/511130_live_monitor/docs/CHANGELOG.md`
- `scripts/511130_live_monitor/docs/HANDOFF.md`
- `scripts/511130_live_monitor/docs/TASKS.md`
- `scripts/511130_live_monitor/docs/NEXT_SESSION.md`

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`，91 tests OK。
- 本地只读预览 `127.0.0.1:8811` Chrome 截图通过：`cards=4`、`hasPrimary=false`、`hasLatestA=false`、`quoteTop=94`、`chartTop=639`、`chartBelowQuote=true`。
- 本地截图：`tmp/511130-dashboard-reorder-local.png`。
- 线上只读 smoke：`ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`、`data_status_code=market_closed`。
- 线上 HTML/DOM：`refreshSec3=true`、`quote_before_chart=true`、`has_primary_value=false`、`has_latest_a=false`、`chart_title=true`。
- Railway deployment list 确认 `97db306a-2342-4555-a6f5-20747807eec3` 是 `codex/511130-a-monitor` / `6b29e98` 的 `SUCCESS`，Dockerfile 和 `--interval 3` 启动命令生效。

运维注意：

- Railway production 在线并挂载 `/data` volume；自动循环在 Railway 进程内运行，不依赖本地 Codex 在线。
- `/health.public_readonly=false`，公网仍保留手动计算、配置保存和飞书测试写接口；若要公开只读看板，设置 `A_MONITOR_PUBLIC_READONLY=1`，但会禁用这些按钮。
- headless Chrome/Edge 线上截图在本机被系统杀掉，未生成生产截图；线上 DOM 和 smoke 已通过。

## 2026-06-24 Quote Cards, Four-Card Strip, Five-Level Order Book, 3s Refresh

当前状态：

- 已按用户要求把 dashboard 往“行情 + 五档买卖 + 每只证券分时曲线”方向改造。
- `/api/data.quote_cards` 现在返回 `511130`、`019776`、`019837` 的行情卡片数据：最新价、涨跌、开盘/昨收、成交量/成交额、五档买卖、本地分时点。
- 页面行情区已改为四张卡横向连在一起：前三张是 `511130`、`019776`、`019837`，第四张是 `套利值A`；卡片之间 `gap=0px`，窄屏用横向滚动保持四联结构，不再拆成竖向列表。
- 五档盘口来自新浪 `hq.sinajs.cn` 展示快照，只用于页面观察；不参与 a 值公式、严格实时校验、飞书预警或阈值去重。
- 当前 a 和飞书预警仍锁定东方财富 `realtime_eastmoney`，并保留 3 秒行情同步、30 秒新鲜度、缺利息 fail-closed 等规则。
- 每只证券小分时线以东方财富 1 分钟分时为底图，失败时用新浪 1 分钟兜底，并叠加本地已保存的严格实时计算点；1 分钟底图有缓存，不按 3 秒高频轮询。
- 默认 dashboard / Docker / Railway 自动刷新间隔已改为 3 秒。上游行情时间戳不更新时，3 秒刷新只会展示重复快照。
- 本地 Chrome 验收通过：`cardCount=4`，`quoteCards=3`，`hasA=true`，`orderRows=30`，`sparkLines=3`，`gridColumns=312px 312px 312px 312px`，`adjacentGap=0`。
- 已按用户授权推送 GitHub 并部署 Railway production：`7ab73f6a-ce74-4ec2-ac7f-d9c7311a13e9`。
- 线上只读 smoke 通过：`ok=true`、`issues=[]`、`process_ok=true`、`auto_loop=running`、`notification_configured=true`、`allowed_price_sources=['realtime_eastmoney']`、`max_skew_seconds=3`、`max_stale_seconds=30`。
- 线上 `/api/data` 返回 3 张行情卡和 30 行五档盘口；线上 Chrome DOM 验收通过：`cardCount=4`、`hasA=true`、`sparkLines=3`、`adjacentGap=0`。
- 本轮未发飞书测试消息、未做任何交易或下单动作。

修改文件：

- `scripts/511130_live_monitor/monitor_511130.py`
- `scripts/511130_live_monitor/live_a_dashboard.py`
- `tests/test_511130_live_monitor.py`
- `Dockerfile`
- `railway.toml`
- `scripts/511130_live_monitor/README.md`
- `scripts/511130_live_monitor/CODEX.md`
- `scripts/511130_live_monitor/docs/CHANGELOG.md`
- `scripts/511130_live_monitor/docs/HANDOFF.md`
- `scripts/511130_live_monitor/docs/TASKS.md`
- `scripts/511130_live_monitor/docs/NEXT_SESSION.md`
- `scripts/511130_live_monitor/docs/DECISIONS.md`
- `scripts/511130_live_monitor/docs/artifacts/511130-four-card-strip-chrome-20260624.png`
- `scripts/511130_live_monitor/history_seed/runs/`
- `scripts/511130_live_monitor/daily_actual_a_report.py`
- `scripts/511130_live_monitor/smoke_check.py`
- `scripts/511130_live_monitor/seed_history_curves.py`

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/smoke_check.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`，91 tests OK。
- 本地 `127.0.0.1:8799` Chrome 验收横向四联卡，截图保存到 `scripts/511130_live_monitor/docs/artifacts/511130-four-card-strip-chrome-20260624.png`。
- `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json`，线上只读 smoke OK。
- 线上 Chrome DOM 验收：`cardCount=4`、`quoteCards=3`、`hasA=true`、`orderRows=30`、`sparkLines=3`、`adjacentGap=0`。

下一步：

- 下一个交易时段确认严格实时 a 是否恢复展示；如果仍为 `market_closed` 或 `data_ok=false`，先看 `/health.diagnostics.pcf/quote/notification`，不要放宽 3 秒同步、30 秒新鲜度或缺利息不兜底规则。
- 不要用 `/api/notify-test`，除非用户明确要发真实飞书测试。

## 2026-06-23 511130/511090 A Curve And Debug Audit

当前状态：

- 已生成 511130 上周开市日 `20260615` 至 `20260618` 的 1 分钟 `estimated_a` / `actual_a` 曲线汇总；`20260619` 为上交所休市日，未纳入。
- 511130 汇总 CSV：`reports/511130_daily_actual_a/summary_511130_1m_estimated_actual_a_20260615_20260618.csv`。
- 511130 概览 SVG：`reports/511130_daily_actual_a/511130_1m_estimated_actual_a_20260615_20260618.svg`。
- 已生成 511090 从 `20260608` 至 `20260618` 的日收盘 `estimated_a` / `actual_a` 曲线；这是日收盘曲线，不是分钟级曲线，因为本次没有稳定的一手公开源可复原这段时间成分券历史分钟价。
- 511090 CSV：`reports/511090_a_20260608_20260618/511090_daily_close_estimated_actual_a_20260608_20260618.csv`。
- 511090 summary：`reports/511090_a_20260608_20260618/summary.json`。
- 511090 概览 SVG：`reports/511090_a_20260608_20260618/511090_daily_close_estimated_actual_a_20260608_20260618.svg`。
- 511090 口径：鹏扬基金官网 PCF / Baostock 5 分钟 15:00 ETF 收盘 / 上交所债券日净价与应计利息；公式与 511130 相同，`actual_a` 使用下一交易日 PCF `PreCashComponent` 回填。
- 监控预警已改为按绝对值触发 `±300`、`±500`；阈值状态使用有符号 key，避免 `+300` 与 `-300` 互相压制。
- Dashboard 当前状态和阈值距离已按 `abs(a)` 判断，负向跌破 `-300/-500` 会显示告警级别。
- Debug 代码检查员发现历史 seed 与 live Volume 同日数据没有真正合并；已修复为先读 seed、再用 live 同 timestamp 覆盖，最后统一排序和截断。
- Debug A 准确性检查员独立复算了 511090 `20260611/20260612` 和 511130 `20260617`，与生成结果一致。
- 本轮没有发飞书、没有部署 Railway、没有做交易或下单动作。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor.Test511130MonitorHardening.test_live_points_merge_with_history_seed_for_same_date`
- `python3.12 -m unittest tests.test_511130_live_monitor`，88 tests OK。
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`

下一步：

- 若要让线上页面也按新的 `±300/±500` 和 seed/live 合并逻辑运行，需要单独授权部署 Railway。
- 511090 当前只有日收盘口径；若必须要分钟级曲线，需要找到可审计的历史分钟成分券价格源，否则不要硬造。

## 2026-06-19 Historical Open-Day Curve Replay

当前状态：

- 已实现“今天实时 + 历史开市日回看”的同一曲线区体验。
- `/api/data`、当前 a、飞书阈值预警仍沿用严格实时口径；历史回放不参与当前 a、飞书告警、阈值去重或交易判断。
- `/api/series` 的图表读取口径放宽，可读 `daily_report_1m`、`historical_eastmoney_1m`、`realtime_sina_snapshot` 和 `realtime_eastmoney`。
- 新增内置历史种子目录 `history_seed/runs/`，包含 `20260612`、`20260615`、`20260616`、`20260617` 四天，每天 240 个 1 分钟 `estimated_a` 点。
- `20260618` 本机只有 PCF/raw 线索，没有完整曲线文件，本次未补，避免伪造。
- Railway Volume 文件上传尝试因当前机器未注册 Railway SSH key 失败；改用镜像内置历史种子兜底，后续开市日仍从 `/data/runs/YYYYMMDD` 自动积累。
- 前端曲线控件默认 `全天`；当前日无点但有历史日时，默认选最近历史开市日；图表说明区分 `今天实时` 与 `历史回放`。

修改文件：

- `scripts/511130_live_monitor/live_a_dashboard.py`
- `scripts/511130_live_monitor/seed_history_curves.py`
- `scripts/511130_live_monitor/history_seed/runs/**`
- `scripts/511130_live_monitor/docs/CHANGELOG.md`
- `scripts/511130_live_monitor/docs/TASKS.md`
- `scripts/511130_live_monitor/docs/HANDOFF.md`
- `scripts/511130_live_monitor/docs/NEXT_SESSION.md`

验证：

- `python3 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/seed_history_curves.py`
- `python3 scripts/511130_live_monitor/seed_history_curves.py --output-root scripts/511130_live_monitor/history_seed/runs`，四天共 960 点。
- 本地 dashboard `127.0.0.1:8809` 验证：`/api/dates` 返回 `20260612/20260615/20260616/20260617`；`/api/series?date=20260617&range=day&interval=1m` 返回 240 点；`/api/series?date=20260612&range=day&interval=1s` 返回 240 点。

下一步：

- 部署 Railway 后确认线上 `/api/dates` 返回四个历史日期，`/api/data` 当前状态仍是休市/实时状态，页面 HTML 包含 `全天` 和历史回放逻辑。
- 若用户要求补 `20260618`，需要单独生成或找到完整 1 分钟曲线文件；不能只用 PCF raw 硬造曲线。

## 2026-06-19 Railway Alert Policy Fix Deployed

当前状态：

- 用户收到飞书“511130监控运行异常”：`日期: 20260619`、`连续失败: 25`、`PCF未更新或不可读: 20260619; HTTP 200`。
- 已联网核验上交所 2026 年休市安排：`20260619` 至 `20260621` 为端午节休市，`20260622` 起照常开市。
- 已按用户授权部署 Railway production，部署 ID：`d97fbc14-846c-4d80-8dfc-bd9f93b369ab`，服务 Online。
- 线上 `/health` 已验证：`last_run_message=休市暂停，06-22 09:25后恢复自动计算`、`last_error=""`、`auto_error_count=0`、`process_ok=true`、`auto_loop.code=running`。
- 线上严格实时源已验证：`intraday_source=eastmoney_realtime_snapshot_only`，`allowed_price_sources=['realtime_eastmoney']`。
- 线上预警策略已验证：`threshold_only_notifications=true`、`runtime_error_notifications=false`、`no_alert_run_check_notifications=false`、`degraded_alert_enabled=true`、`notification_attempts=3`。
- 已修复并部署：自动运行错误不发飞书；缺利息、PCF未就绪、行情不同步等错误只在页面、`/health` 和日志中诊断。CLI `precheck --notify` 和主程序异常捕获也不再发送飞书错误。
- 已修复并部署：成功计算但没有触发阈值时也不发“运行检查”消息；飞书只发真正触发阈值的 `511130 a值预警` 和显式 `/api/notify-test`。
- 已修复并部署：严格实时行情失败但 PCF + 逐券利息上下文已就绪时，会尝试一次“降级行情候选预警”；只有穿阈值才发飞书，标题为 `511130 a值候选预警（降级行情）`，正文明确说明不等同严格实时 a。
- 已修复并部署：正式严格预警与降级候选预警的阈值去重状态分开，避免两条链路切换导致重复预警；降级候选不会提升为 dashboard 当前严格 a，低于阈值不会发送飞书。
- 已修复并部署：自动循环进入交易时段后先准备并缓存 PCF + 逐券利息上下文；上下文未准备好时不会进入实时价计算或预警。
- 已修复并部署：同一交易日已验证过的逐券利息可作为同日缓存兜底，避免上交所取息短暂失败阻断有效阈值预警；跨日期缓存仍拒绝。
- 本地候选版本已用 dashboard + `smoke_check.py` 做端到端只读验证：`http://127.0.0.1:8797/health` 显示 `last_run_message=休市暂停，06-22 09:25后恢复自动计算`、`intraday_source=eastmoney_realtime_snapshot_only`、`allowed_price_sources=['realtime_eastmoney']`、`alert_policy_setup.runtime_error_notifications=false`、`degraded_alert_enabled=true`、`notification_attempts=3`；本地 smoke 返回 `ok=true`。
- 用户后续贴出的 `20260616` 连续失败 `缺少逐券应计利息` 报警，属于旧固定日期/取息失败状态下的重复机器人消息；本地日报留痕已显示 20260616 官方利息曾可取到：`019776=0.273`、`019837=0.319`，所以根因不是公式变化，而是线上当时缺利息后通知层重复发送。
- 本次部署后未发送飞书测试消息，未改 webhook/secret，未做交易或下单动作。

修改文件：

- `scripts/511130_live_monitor/live_a_dashboard.py`
- `scripts/511130_live_monitor/monitor_511130.py`
- `scripts/511130_live_monitor/smoke_check.py`
- `scripts/511130_live_monitor/config.json`
- `tests/test_511130_live_monitor.py`
- `scripts/511130_live_monitor/README.md`
- `scripts/511130_live_monitor/docs/CHANGELOG.md`
- `scripts/511130_live_monitor/docs/TASKS.md`
- `scripts/511130_live_monitor/docs/DECISIONS.md`
- `scripts/511130_live_monitor/docs/HANDOFF.md`
- `scripts/511130_live_monitor/docs/NEXT_SESSION.md`

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/smoke_check.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`，85 tests OK。
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`
- 本地 dashboard 候选版本 smoke：启动 `live_a_dashboard.py --host 127.0.0.1 --port 8797 --auto-run --interval 15` 后运行 `python3.12 scripts/511130_live_monitor/smoke_check.py http://127.0.0.1:8797 --json`，返回 `ok=true`。
- `railway up --detach -y --message "Deploy 511130 alert-policy and degraded candidate alert fix"` 创建部署 `d97fbc14-846c-4d80-8dfc-bd9f93b369ab`。
- `railway deployment list --json` 轮询到 `d97fbc14-846c-4d80-8dfc-bd9f93b369ab` 状态 `SUCCESS`。
- `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json` 返回 `ok=true`、`issues=[]`。
- `curl`/Python 只读 `/health` 返回 `last_error=""`、`auto_error_count=0`、`休市暂停，06-22 09:25后恢复自动计算`。
- `railway logs --http --status ">=400" --lines 50` 无输出；`railway logs --deployment --lines 80` 只看到启动、`/health` 和 `/api/data` 200。
- `git diff --check -- scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/config.json scripts/511130_live_monitor/README.md scripts/511130_live_monitor/docs/HANDOFF.md scripts/511130_live_monitor/docs/TASKS.md scripts/511130_live_monitor/docs/CHANGELOG.md tests/test_511130_live_monitor.py`

下一步：

- 下一个交易时段复查严格实时 a 是否恢复；若 Eastmoney strict 失败但 PCF/利息上下文已就绪，观察是否按候选预警策略执行。
- 如果用户要求处理 20260616 固定日回放，可用本地 raw 留痕复核 `reports/511130_daily_actual_a/20260616/raw/interest_sources_20260616.json`；实时路径只能用同交易日已验证缓存，不能用跨日期旧利息硬凑。

## 2026-06-17 Next-Day Actual A Report Automation Prepared

当前状态：

- 新增 `scripts/511130_live_monitor/daily_actual_a_report.py`。
- 目标：每个交易日下一交易日 PCF 发布后，生成前一交易日 1 分钟共同时间戳的预估 a / 实际 a 曲线。
- 默认日期映射：运行日 PCF `PreTradingDay` -> 目标日；周一运行时自然映射上周五，节假日后同理。
- 预估 a 使用目标日 PCF `EstimatedCashComponent`。
- 实际 a 使用运行日 PCF `PreCashComponent` 回填目标日。
- 行情源为东方财富 `trends2/get?ndays=5` 1 分钟；主口径只接受 `09:31-11:30` 和 `13:01-15:00` 共 240 个共同时间戳。
- 5 分钟交叉核验默认用新浪 scale-5 K 线；只接受 `09:35-11:30` 和 `13:05-15:00` 共 48 个共同时间戳，输出单独 CSV/PDF，不替代 1 分钟正式主口径。
- 利息源优先 `config.json` 的逐券手工覆盖；没有覆盖时调用上交所净价全价接口，并在 PDF/CSV/summary 里标注来源。
- 输出目录：`reports/511130_daily_actual_a/YYYYMMDD/`，包含 `summary.json`、`raw/`、CSV、PDF；PDF 同步复制到 `/Users/happytang/Desktop/511130_每日实际a/`。
- 失败路径：PCF 未发布、`PreTradingDay` 不匹配、成分券变化、`CreationRedemptionUnit` 变化、利息缺失或 1 分钟数据不足时，只写 `reports/511130_daily_actual_a/pending/*.md`，不生成正式 PDF/CSV 结论；若只有 5 分钟交叉核验失败，1 分钟正式报告仍生成，`summary.json.cross_check_5m` 标注 skipped。

验证：

- `python3.12 -m pip install -r scripts/511130_live_monitor/requirements.txt`
- `python3.12 -m py_compile scripts/511130_live_monitor/daily_actual_a_report.py scripts/511130_live_monitor/monitor_511130.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`，67 tests OK。
- Fixture 回归：
  - 2026-06-12 收盘预估 a `+297.37`，实际 a `-0.27`。
  - 2026-06-15 收盘预估 a `+234.96`，实际 a `-254.61`。
- 5 分钟 fixture 回归：2026-06-15 48 个共同点下收盘仍为预估 a `+234.96`，实际 a `-254.61`。
- 用 fixture 生成 PDF 后，`pdftoppm -png -r 150` 渲染 1 页 PNG；标题、现金项、利息来源、收盘 a、均值、区间、图例均可读。

手工运行：

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/daily_actual_a_report.py --no-retry
```

回放指定日：

```bash
python3.12 scripts/511130_live_monitor/daily_actual_a_report.py \
  --run-date 20260616 \
  --target-date 20260615 \
  --no-desktop-copy \
  --no-retry
```

自动化：

- 已创建 Codex 本机 cron 自动化：`511130-a` / `511130 次日实际a日报`。
- 状态：`ACTIVE`；执行环境：`local`；工作目录：`/Users/happytang/Documents/工作`。
- 运行任务调用 `python3.12 scripts/511130_live_monitor/daily_actual_a_report.py --retry-until 10:00 --retry-interval-seconds 300`，并汇报 `summary.json.cross_check_5m` 的 5 分钟交叉核验状态和文件路径。

下一步：

- 首个真实运行日后检查 `summary.json` 和 PDF；如果生成 pending，按 pending 里的 PCF/利息/分钟数据错误处理，不要手工改出正式数值。

## 2026-06-16 Railway Deploy And Feishu Verified

当前状态：

- Railway production 已部署并在线，公网地址仍是 `https://511130-live-monitor-production.up.railway.app`。
- 最新验证部署 ID：`6ad1ba11-56b8-4a75-a52c-43252aa79673`。
- 已把当前飞书 webhook 和签名密钥配置到 Railway env；仓库文档不记录原始 webhook 或 secret。
- 真实飞书测试已成功：`POST /api/notify-test` 返回 `ok=true`，飞书业务响应码 `0`，message `success`，通知耗时约 `647ms`。
- 最终部署后未重复发送飞书消息；复验 `/health` 仍显示 `process_ok=true`、`data_ok=false`、`auto_loop.code=running`、`diagnostics.notification.code=sent`。
- `data_ok=false` 是盘中午间休市期间没有当前严格实时 a；自动循环显示“盘外暂停，06-16 12:55后恢复自动计算”，不是进程崩溃。
- `smoke_check.py` 线上只读检查通过：`/health`、`/api/data` 均 200，飞书配置脱敏可见，准确性护栏可见。
- Railway `logs --http --status ">=400"` 未返回 4xx/5xx。

验证：

- `railway status`
- `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json`
- `curl -fsS -X POST https://511130-live-monitor-production.up.railway.app/api/notify-test`
- `curl -fsS https://511130-live-monitor-production.up.railway.app/health`
- `railway logs --http --status ">=400" --lines 50`
- `railway logs --deployment --lines 100`

下一步：

- 下一个交易时段复查严格实时 a 是否恢复。若仍无当前 a，先看 `diagnostics.pcf/quote`，不要放宽 3 秒同步、30 秒新鲜度或缺利息不兜底规则。
- 若页面要给更大范围的人看，考虑设置 `A_MONITOR_PUBLIC_READONLY=1` 或增加访问控制。

## 2026-06-16 Runtime Reliability Patch Prepared (Superseded)

当前状态：

- 历史记录：本节记录部署前的本地修复准备状态；最终上线状态见上方 `Railway Deploy And Feishu Verified`。
- `config.json` 改为 `target_date=auto`，运行时按上海当天日期解析；dashboard 长跑进程跨日后会自动滚动到新日期，避免长期卡在 `20260615`。
- 显式 `--date YYYYMMDD` 或固定环境变量日期会保持 `target_date_mode=fixed`，不会被 auto 跨日滚动覆盖。
- Eastmoney 实时快照增加系统 `curl` 兜底；兜底只提高取数成功率，不放宽日期、3秒同步、30秒新鲜度校验。
- 自动计算增加交易时段门禁，默认只在工作日 `09:25-11:35`、`12:55-15:10` 运行；盘外会显示“盘外暂停”，不做启动预加载，不把闭市/周末当异常飞书提醒。
- 自动计算失败时不发飞书；PCF、利息、行情或校验错误只写入页面、`/health` 和日志。
- 自动计算线程外层增加守护：单轮非预期异常会写入 `last_error`、显示“自动线程异常，下一轮继续”，并继续下一轮，避免线程静默退出。
- `/health` 增加 `auto_loop` 心跳诊断，能区分自动线程运行中、未打点、疑似卡住或未启用。
- `/health` 增加 `process_ok`，并且只在 `auto_loop.code=stale` 时返回 HTTP 503，便于 Railway 重启卡住的后台自动线程；行情失败、PCF未就绪、盘外等待仍保持 HTTP 200。
- `/health` 和 `/api/data.config` 增加脱敏 `notification_setup`，只暴露 webhook/kind/Feishu secret 是否配置、来源和 env 名，不暴露 URL 或 secret 原文。
- `/health` 和 `/api/data.config` 增加 `accuracy_setup`，显式展示公式版本、PCF来源、成分券锁定、严格实时源、3秒同步、30秒新鲜度和缺利息不兜底策略。
- 新增 `scripts/511130_live_monitor/smoke_check.py`，部署后只读检查 `/health` 和 `/api/data`，不触发飞书消息。
- 飞书/企业微信通知成功判定必须有业务响应码；飞书缺少 `code`/`StatusCode`、企业微信缺少 `errcode` 会判定失败，避免 HTTP 200 空响应误报“已发送”。
- Railway/Docker 启动命令改为 `--auto-run --auto-run-notify --interval 15`，并在 Docker 镜像里安装 `curl`。
- `/health` 增加 `target_date_mode`、`process_ok`、`data_ok`、`notification_configured`、`auto_error_count`、`auto_loop`，区分服务存活、数据可用和自动线程是否仍有心跳。
- `/health.data_ok` 只反映当前严格实时 a 是否可用；飞书发送/测试失败会留在 `diagnostics.notification`，不再污染数据健康。
- `/health` 跨日只做轻量日期滚动，不触发 PCF/利息外部预加载，避免 Railway 健康检查被上游数据源拖慢或卡住。
- `/api/data` 对 PCF 未更新显示 `status.code=pcf_not_ready`、`label=清单未就绪`。
- 页面“发送飞书测试”改为 `POST /api/notify-test`，只验证 webhook/签名/飞书业务响应，不再依赖当前 a 计算。
- 删除 `build_html` 后旧版不可达 HTML/JS，避免继续残留 `/api/recalc?notify=1` 的误导路径。
- PCF 未就绪时自动模式会设置 `pcf_retry_remaining_seconds` 倒计时，默认 300 秒后重试，避免每 15 秒重复请求未发布 PCF。
- `/health` 和 `/api/data` 增加 `diagnostics` 三层诊断：`pcf`、`quote`、`notification`，例如 `PCF未就绪 / 等待PCF / 飞书未配置`。
- 通知事件和预警事件写盘改为 best-effort，避免 Volume/文件写入异常把已成功发送的飞书误判为失败或拖垮自动线程。
- 自动计算已产出有效 a 但飞书发送失败时，dashboard 会保留该 a 并继续展示；通知失败会通过最近通知记录和 diagnostics 暴露，下一轮阈值提醒仍会重试。
- `a_values.jsonl`、利息缓存和预警状态写盘也改为 fail-soft；写盘失败时页面/API 可用进程内最新严格实时结果展示当前 a，预警状态写盘失败时保留进程内状态，避免同一进程内重复触发已激活阈值。
- `a_values.jsonl`、`alerts.jsonl`、`notifications.jsonl` 和 `runs/` 日期索引读取也改为 fail-soft；文件损坏、变成目录或读取异常时会打印 warning 并忽略，不让读 API 直接 500。
- 历史点和成分券展示字段做类型清洗，坏 `timestamp`、数字型组件 `code/name` 等异常展示字段不会让图表排序、公式快照或组件表格崩掉。
- API 路由增加结构化 JSON 错误兜底；非预期展示层异常会返回 `ok=false` JSON 并打印 warning，避免前端直接拿到断开的连接。
- `state.json` 读取改为 fail-soft：JSON 损坏、`dates` / `interest_cache` 类型异常或日期条目异常时回退/清洗，不再让预警去重和利息缓存路径把服务拉崩。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`，57 tests OK；后续签名时间戳补丁后为 59 tests OK。
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`
- `python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json` 当前按预期失败：线上仍是旧版本，缺少 `accuracy_setup`、`auto_loop`、`data_ok`、`diagnostics`、`notification_setup`、`process_ok`、`target_date_mode` 等新字段。
- `git diff --check -- Dockerfile railway.toml scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py scripts/511130_live_monitor/config.json scripts/511130_live_monitor/README.md scripts/511130_live_monitor/CODEX.md scripts/511130_live_monitor/docs tests/test_511130_live_monitor.py`
- 本地 `127.0.0.1:8799` 以空 webhook 环境变量启动自动模式，`/health` 返回 HTTP 200、`date=20260616`、`target_date_mode=auto`、`process_ok=true`、`data_ok=false`、`auto_loop.code=running`、`last_run_message=盘外暂停，06-16 09:25后恢复自动计算`。
- 本地 `/health` 和 `/api/data.config` 返回脱敏 `notification_setup`，空 webhook 下显示 `webhook_source=missing`、`kind=feishu`、`feishu_secret_configured=false`，未泄露 URL 或 secret。
- 本地 `/health` 和 `/api/data.config` 返回 `accuracy_setup`，显示 `formula_version=estimated_a_v1`、`expected_component_codes=019776/019837`、`strict_realtime_required=true`、`max_skew_seconds=3`、`max_stale_seconds=30`、`missing_interest_fallback_allowed=false`。
- 本地 `/api/data` 返回 200，盘外状态为 `等待数据`，页面/API 不崩，自动线程没有把闭市当作 PCF/行情异常。
- 本地 `POST /api/notify-test` 在空 webhook 下返回“未配置 A_MONITOR_WEBHOOK_URL”，没有触碰 PCF/行情。
- 本地 `/health` 和 `/api/data` 均返回 `diagnostics.summary=PCF未阻塞 / 等待行情 / 飞书未配置`。
- 当时只读 `railway status` 确认线上服务 Online、Volume `/data` 正常；该状态已被上方最终部署记录覆盖。

注意：

- 该部署前阻塞已经解除；用户已授权部署 Railway 并测试飞书，最终验证见上方。
- 本机 Docker daemon 未启动，`docker build -t 511130-live-monitor:local-test .` 未能连接 `/var/run/docker.sock`，所以镜像构建尚未在本机验证。
- 若 20260616 PCF 仍未发布，系统会正常显示/提醒“清单未就绪”，不会伪造 a。
- 飞书真实发送已经在线上验证成功；代码层也有 mock 单测覆盖错误不通知、阈值预警发送和独立飞书测试。

## 2026-06-16 Railway Live Diagnosis

当前状态：

- Railway 服务仍为 Online，公网地址 `https://511130-live-monitor-production.up.railway.app` 可访问。
- 当前线上部署仍是 `ee84aa7e-e94f-4531-a737-cdd670b3911e`，Volume `511130-live-monitor-volume` 仍挂载在 `/data`。
- `/health` 返回 `ok=true`，但 `last_run_message=自动计算失败`。
- `/api/data` 返回 HTTP 200，但 `points=[]`、`chart_current=false`、状态为 `行情过旧`。
- 近 2 小时 HTTP 日志没有 4xx/5xx；近 30 分钟访问 `/api/data`、`/api/series`、`/health` 都是 200。

诊断：

- 这不是 Railway 进程崩溃；是数据层 fail-closed。
- 线上错误链路为：`xtquant` 未启用，Sina 三只证券快照不同步，Eastmoney 从 Railway 侧返回 `HTTP Error 502: Bad Gateway`。
- 本机直接 `curl` Eastmoney 同一接口可返回 200，但 Python `curl_cffi` 路径会被连接断开；说明 Eastmoney 访问稳定性和请求栈兼容性仍是风险点。
- 当前配置仍固定 `target_date=20260615`；北京时间 2026-06-16 凌晨检查 `20260616` PCF 仍未更新或不可读是正常数据未就绪，但开盘前后若仍固定 15 日，会阻止 16 日实时计算。
- Railway 变量检查只确认键名和脱敏状态：`A_MONITOR_RUNS_DIR=/data/runs`，飞书 webhook 和 secret 已设置；未打印或记录原始密钥。

建议下一步：

- 不要放宽严格实时校验，不要用分钟线硬凑当前 a。
- 修复方向一：让 Railway dashboard 默认使用上海日期或显式自动交易日，而不是长期固定 `config.json` 的 `target_date`。
- 修复方向二：为 Eastmoney 实时接口增加受控 fallback，或接入更稳定的正式行情源；fallback 仍必须保留 `<=3` 秒同步和 `<=30` 秒新鲜度校验。
- 修复方向三：如果公网只给团队查看，考虑设置 `A_MONITOR_PUBLIC_READONLY=1` 或加访问控制，避免公开 URL 被人改目标日期/利息/阈值。

## 2026-06-15 Railway Volume For Persistent History

当前状态：

- Railway Volume 已创建并挂载：
  - ID：`ed4287d1-7b34-47d5-aa52-536d5a259fc1`
  - Name：`511130-live-monitor-volume`
  - Mount path：`/data`
  - Size：5GB
- Railway 变量已设置：
  - `A_MONITOR_RUNS_DIR=/data/runs`
  - `RAILWAY_DOCKERFILE_PATH=Dockerfile`
- 当前成功部署：`ee84aa7e-e94f-4531-a737-cdd670b3911e`

验证：

- `railway status` 显示服务 Online，Volume 为 `511130-live-monitor-volume · /data · 0.1 GB / 4.9 GB`。
- `railway volume list --json` 显示 Volume `status=Ready`。
- Railway 变量检查确认 `A_MONITOR_RUNS_DIR` 等于 `/data/runs`。
- 线上 `/health` 返回 `ok=true`。
- 线上 `/api/dates` 可访问；当前为空是因为挂 Volume 后尚无成功严格实时点写入。

注意：

- 挂 Volume 后，后续有效实时点会写到 `/data/runs/YYYYMMDD/a_values.jsonl`。
- 当前盘后严格实时行情源失败，系统按 fail-closed 规则不写伪实时 a。
- Railway GitHub source 构建曾未读到分支里的 Dockerfile，本次最终用干净 worktree 的 `railway up` 部署成功。

## 2026-06-17 Daily Actual-A 5m Source Alignment

当前状态：

- `daily_actual_a_report.py` 的 5分钟交叉核验已改为东方财富 5分钟 K 线：`eastmoney_kline_get_5m`。
- 1分钟正式报告仍使用东方财富 `trends2/get` 1分钟数据。
- 5分钟文件仍是 cross-check-only，不替代 1分钟正式主口径。
- 20260616 重跑后 `summary.json.cross_check_5m.status=ok`，48 个共同 5分钟点位，15:00 收盘 ETF quote=`106.164`，预估 a=`125.74000`，实际 a=`-254.92000`，与 1分钟收盘一致。
- 旧的 Sina 5分钟源不再用于日报交叉核验。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/daily_actual_a_report.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`
- `python3.12 scripts/511130_live_monitor/daily_actual_a_report.py --retry-until 10:00 --retry-interval-seconds 300`

下一步：

- 后续日报若 1分钟和 5分钟仍有收盘差异，优先检查 Eastmoney 两个接口的 15:00 close 定义，而不是跨供应商差异。

## 2026-06-15 K-line Style Chart Controls

当前状态：

- 新增 `/api/dates`，返回已有 `runs/YYYYMMDD` 日期。
- 新增 `/api/series`，支持 `range=1m/5m/15m/1h/today` 和 `interval=1s/1m/15m`。
- `interval=1s` 返回原始严格实时 a 点；`interval=1m/15m` 返回 a 值 OHLC 聚合。
- 前端曲线卡片增加范围、周期、日期选择。左侧第一屏当前 a 仍只来自 `/api/data`。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`
- 本地 `127.0.0.1:8799` 验证 `/api/data`、`/api/dates`、`/api/series?range=1m&interval=1s`、`/api/series?range=today&interval=1m`、`/api/series?range=today&interval=15m`。
- 提取首页内嵌 JS 后执行 `node --check` 通过。

注意：

- 本机内置 Browser 插件不可用，Chrome headless 截图命令挂起后已清理；本轮以 API、HTML 和 JS 检查替代视觉截图。
- Railway 如果不挂 Volume，历史仍只是运行缓存；跨重启/重部署长期回看需要 `A_MONITOR_RUNS_DIR=/data/runs`。

## 2026-06-15 Team Dashboard Upgrade

当前状态：

- `live_a_dashboard.py` 已升级为响应式团队看板。
- 核心公式仍由 `monitor_511130.py` 负责，页面只展示 API 返回的拆解值。
- 严格实时和 3 秒行情同步规则未放松。
- 无效配置保存会返回具体错误；有效保存会写回 `config.json` 并在自动计算模式下尝试重建上下文。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py`
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`
- 临时服务 `127.0.0.1:8797` 验证了 `/api/data`、首页 HTML、无效 `/api/config`。
- Headless Chrome 验证了桌面 1440px 和手机 390px 视口，无横向溢出，无控制台错误；手机首屏可见 a、状态、债券净价/利息和曲线顶部。

注意：

- 不要在文档中复制 `config.json` 里的 webhook 或任何密钥。
- 浏览器验证没有点击“发送飞书测试”，避免外部消息副作用。

## 2026-06-15 Railway Deployment And Accuracy Hardening

当前状态：

- Railway 公网看板已上线：`https://511130-live-monitor-production.up.railway.app`
- 最新成功部署：`87e7e5a7-4bd2-4f61-93c2-d527f4b8b0c3`
- Railway `sleepApplication=false`，单副本，`/health` 健康检查。
- `railway.toml` 启动命令不再传 `--port $PORT`，由代码自动读取 `PORT`。
- Railway 已设置 `A_MONITOR_FEISHU_SECRET`，未在文档中记录具体值。

关键准确性改动：

- `CreationRedemptionUnit` 必须是 `10000`，否则拒绝计算。
- PCF `RecordNumber` 必须等于成分券数量，且当前必须是 `019776/019837` 两券结构。
- 历史缓存利息不再允许作为当前 a 计算输入。
- 主状态只反映行情/a/利息/过旧，不再被飞书失败遮蔽。
- 过期快照不展示当前 a，曲线标记为历史。

验证：

- `python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py tests/test_511130_live_monitor.py`
- `python3.12 -m unittest tests.test_511130_live_monitor`
- `python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest`
- 线上 `/health` 返回 `ok=true`。
- 线上 `/api/data` 返回 `status=正常`、`quote_skew_seconds=1`、`component_count=2`、`chart_current=true`。
- 线上独立复算 a 与 API `formula.estimated_a` 一致。
- Headless Chrome 检查桌面 1440px 和手机 390px，页面可见、无横向溢出、无控制台错误。

注意：

- Railway 上 `runs/` 是运行缓存，不是长期审计数据源。
- 这是内部观察看板，不下单。
