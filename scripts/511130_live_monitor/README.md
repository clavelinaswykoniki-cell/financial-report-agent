# 511130 Live a-value Monitor

只读监控，不下单，不连接交易接口。

## 口径

`预估a = 511130实时报价 / 100 * 1,000,000 - [sum((成分券实时净价 + 当日逐券利息) * PCF数量 * 10) + EstimatedCashComponent]`

## 常用命令

```bash
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode precheck
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once
```

带 webhook 预警：

```bash
A_MONITOR_WEBHOOK_URL="https://..." A_MONITOR_WEBHOOK_KIND="feishu" \
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once --notify
```

`--notify` 只在严格校验通过且触发阈值时发送 `511130 a值预警`；PCF 未就绪、利息缺失、行情失败或未触发阈值都不发飞书。有效预警默认会尝试发送 3 次，每次仍必须拿到飞书业务成功码。

`A_MONITOR_WEBHOOK_KIND` 可填；不填默认 `feishu`：

- `feishu`：飞书群机器人
- `wecom`：企业微信群机器人
- `generic`：通用 JSON webhook

如果飞书自定义机器人开启了“签名校验”，再设置：

```bash
A_MONITOR_FEISHU_SECRET="飞书机器人安全设置里的签名密钥"
```

## 改阈值

编辑 `config.json` 的 `thresholds`，例如：

```json
"thresholds": [200, 400, 600]
```

下一轮监控会自动按新阈值判断，不影响公式。

云端更推荐在 GitHub Variables 里设置：

- `A_MONITOR_THRESHOLDS=300,500,800`

以后只改这个变量即可，不需要改代码。

## 手工覆盖交易软件利息

如果周一交易软件显示的利息与上交所接口不同，在 `config.json` 填：

```json
"interest_overrides": {
  "20260615": {
    "019776": "0.267",
    "019837": "0.305"
  }
}
```

覆盖只对对应日期和对应券生效。

## 次日实际 a 日报

新增 `daily_actual_a_report.py`：用于在下一交易日 PCF 发布后，生成前一交易日的 1 分钟共同时间戳预估 a / 实际 a 曲线。

安装本机依赖：

```bash
python3.12 -m pip install -r scripts/511130_live_monitor/requirements.txt
```

手工运行：

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/daily_actual_a_report.py --no-retry
```

指定运行日和目标日回放：

```bash
python3.12 scripts/511130_live_monitor/daily_actual_a_report.py \
  --run-date 20260616 \
  --target-date 20260615 \
  --no-desktop-copy \
  --no-retry
```

自动日期规则：

- 默认先读运行日 PCF。
- 用运行日 PCF 的 `PreTradingDay` 作为目标交易日。
- 用目标日 PCF 的 `EstimatedCashComponent` 算预估 a。
- 用运行日 PCF 的 `PreCashComponent` 回填目标日实际 a。

正式输出：

```text
reports/511130_daily_actual_a/YYYYMMDD/summary.json
reports/511130_daily_actual_a/YYYYMMDD/raw/
reports/511130_daily_actual_a/YYYYMMDD/511130_YYYYMMDD_1m_estimated_actual_a.csv
reports/511130_daily_actual_a/YYYYMMDD/511130_YYYYMMDD_1m_estimated_actual_a.pdf
reports/511130_daily_actual_a/YYYYMMDD/511130_YYYYMMDD_5m_estimated_actual_a_cross_check.csv
reports/511130_daily_actual_a/YYYYMMDD/511130_YYYYMMDD_5m_estimated_actual_a_cross_check.pdf
/Users/happytang/Desktop/511130_每日实际a/511130_YYYYMMDD_1m_estimated_actual_a.pdf
```

准确性边界：

- 只读数据，不下单，不连接交易接口。
- 1 分钟数据只用东方财富 `trends2/get?ndays=5` 的共同时间戳；正式报告不使用 5 分钟数据冒充。
- 主口径排除 `09:30`，要求 `09:31-11:30` 和 `13:01-15:00` 共 240 个共同分钟点。
- 5 分钟文件是交叉核验，不替代 1 分钟正式报告；默认用新浪 5 分钟 K 线，要求 `09:35-11:30` 和 `13:05-15:00` 共 48 个共同点。
- `units = PCF Quantity * 10`。
- 若 PCF 未发布、`PreTradingDay` 不匹配、成分券不等于 `expected_component_codes`、`CreationRedemptionUnit` 不是 `10000`、逐券利息缺失或 1 分钟数据不足，脚本只写 `pending/*.md`，不生成正式 PDF/CSV 结论。
- 若只有 5 分钟交叉核验失败，1 分钟正式报告仍生成，并在 `summary.json.cross_check_5m` 写明失败原因。
- 逐券利息优先用 `config.json` 手工覆盖；没有覆盖时用上交所净价全价接口，并在 `summary.json`、CSV 和 PDF 里标注来源。

## 云端提醒

已提供 GitHub Actions 工作流：`.github/workflows/511130-a-monitor.yml`。

在 GitHub 仓库设置 Secrets：

- `A_MONITOR_WEBHOOK_URL`：飞书/企业微信/其他 webhook 地址
- `A_MONITOR_WEBHOOK_KIND`：`feishu`、`wecom` 或 `generic`；飞书可不填
- `A_MONITOR_FEISHU_SECRET`：如果飞书机器人启用签名校验则填写；没启用就不填

云端计划：

- 2026-06-14 20:00（北京时间）：检查周一 PCF 是否更新
- 2026-06-15 08:00（北京时间）：再次检查 PCF
- 2026-06-15 盘中每 5 分钟：计算预估 a，并按阈值提醒

GitHub Actions 的定时粒度通常是 5 分钟，不是毫秒级行情系统；适合实验监控，不适合作为自动交易基础设施。

## 本地实时曲线看板（网站）

新增 `live_a_dashboard.py`：本地开网页看 511130 a 值实时变化。

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open
```

- `--auto-run`：每 `--interval` 秒自动算一次并写入 `runs/<日期>/a_values.csv`（默认 3 秒）
- `--open`：自动打开浏览器
- `--date YYYYMMDD`：指定交易日（默认读 `config.json`；`target_date=auto` 时按上海当天日期；显式 `--date` 会固定该日，不会被 auto 跨日滚动覆盖）
- `--interval 秒数`：刷新和自动算间隔（不小于 1 秒）

示例（开盘实时刷新）：
```bash
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open --interval 3   # 3秒，接近手机App刷新
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open --interval 15  # 15秒，较轻量
```

页面刷新可以比上游行情变动更快；如果行情源时间戳没有更新，3 秒刷新只会看到重复快照，不会伪造新行情。

不加 `--auto-run` 时是只读页面，页面里可手动点“手动算一次”触发一次计算。

团队同一局域网访问：

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --auto-run-notify --interval 3
```

同一网络下，手机或电脑访问终端打印的 `Team LAN URL`，形式类似：

```text
http://你的电脑IP:8787
```

如果不在同一网络，用公网转发工具转发 `8787` 端口即可，不需要改程序。

## Railway 公网看板

当前 Railway 地址：

```text
https://511130-live-monitor-production.up.railway.app
```

最新生产验收：

- 部署 ID：`6ad1ba11-56b8-4a75-a52c-43252aa79673`
- `smoke_check.py` 线上只读检查通过。
- 飞书 webhook 和签名密钥通过 Railway env 配置；`POST /api/notify-test` 已返回飞书业务码 `0`、`success`。
- 原始 webhook 和签名密钥不要写入 README、docs 或提交说明。

部署入口在仓库根目录：

- `Dockerfile`
- `railway.toml`

Railway 启动命令：

```bash
python -u scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --auto-run-notify --interval 3
```

稳定性策略：

- `config.json` 默认 `target_date=auto`，服务按上海当天日期运行；长跑进程跨日后会自动滚动到新日期并重建/清空预加载上下文。仍可用 `--date YYYYMMDD` 或 `A_MONITOR_TARGET_DATE` 固定某日；固定日期不会被 auto 跨日逻辑覆盖。
- 当前 a 和飞书预警锁定东方财富实时快照 `realtime_eastmoney`；新浪实时/分钟线不能触发当前 a 预警。
- 页面行情卡片展示三只证券的最新行情、五档买卖和本地分时小曲线；五档盘口来自新浪展示快照，只用于观察，不参与 a 值公式或飞书预警。
- Eastmoney 实时快照主请求失败时，会尝试系统 `curl` 兜底；兜底成功后仍必须通过日期、3 秒同步和 30 秒新鲜度校验。
- 严格实时行情失败但 PCF 和逐券利息上下文已准备好时，会尝试降级行情候选预警；候选消息只有穿阈值才发，标题为 `511130 a值候选预警（降级行情）`，正文明确说明不等同严格实时 a，且不进入当前严格 a 展示口径。
- 自动计算默认只在交易日 `09:25-11:35`、`12:55-15:10` 运行；周末和 `config.json` 的 `auto_run_closed_dates` 官方休市日会暂停预加载和自动取 PCF/行情，避免把闭市/休市误判为崩溃或反复发异常提醒。
- 自动计算失败时不发送飞书；错误只保留在页面、`/health` 和日志里。飞书只发送通过严格校验后的阈值预警，避免把取数/输入错误当成预警刷群。
- 自动线程外层有兜底守护，非预期循环异常会记录为“自动线程异常，下一轮继续”；线程不会因为单轮异常直接退出。
- `/health.auto_loop` 会暴露自动线程心跳，能区分自动线程运行中、未打点、疑似卡住或未启用。
- `/health.process_ok` 表示进程/自动线程层是否健康；只有 `auto_loop.code=stale` 时 `/health` 才返回 HTTP 503 给 Railway 重启，行情失败或 PCF 未就绪仍返回 200。
- 自动计算已算出严格实时 a 但飞书发送失败时，当前 a 仍会保留并展示；飞书失败只进入 `diagnostics.notification` 和最近通知记录，不污染 `/health.data_ok`。
- `/health` 保持轻量确定：跨日时只更新目标日期和内存状态，不触发 PCF/利息等外部预加载，避免 Railway 健康检查被上游数据源拖慢。
- 页面“发送飞书测试”走 `/api/notify-test`，只验证 webhook/签名/业务响应，不依赖 PCF 或行情。
- 飞书/企业微信通知必须返回业务响应码；飞书缺少 `code`/`StatusCode`、企业微信缺少 `errcode` 时会判定失败，避免只因 HTTP 200 就误报“已发送”。
- 有效阈值预警默认按 `notification_attempts=3` 重试；如果全部失败，会回滚阈值激活状态，下一轮严格实时 a 仍在阈值上方时继续尝试发送。
- 正式严格预警和降级候选预警使用独立阈值状态，避免两条链路切换造成重复推送。
- `/health` 和 `/api/data.config` 会返回脱敏 `notification_setup`，只暴露 webhook/kind/secret 是否配置、来源和 env 名，不暴露 URL 或签名密钥。
- `/health` 和 `/api/data.config` 会返回 `accuracy_setup`，用于确认公式版本、PCF来源、成分券锁定、严格实时源锁定为东方财富、3秒同步、30秒新鲜度和缺利息不兜底策略。
- `/health` 和 `/api/data.config` 会返回 `alert_policy_setup`，用于确认飞书只发阈值预警、不发运行错误/无阈值检查、降级候选预警已启用、有效预警至少重试 3 次。
- PCF 未发布时自动模式会等待 `pcf_not_ready_retry_seconds` 后再重试，避免每个刷新周期重复打同一个未就绪清单。
- 当逐券利息已在同一交易日从上交所或手工覆盖成功验证过，后续同日取息接口短暂失败时可复用同日缓存继续计算；跨日期缓存仍拒绝用于当前 a。
- `/health` 和 `/api/data` 返回 `diagnostics`，拆成 `pcf`、`quote`、`notification` 三层，便于判断是清单、行情还是飞书问题。
- 飞书发送结果写入 `notifications.jsonl` 是 best-effort；写盘失败会打印 warning，但不把已成功发送的飞书误判为失败。
- `a_values.jsonl`、利息缓存和预警状态写盘失败时会打印 warning 并尽量继续计算/提醒；当前 a 会用进程内最新严格实时结果兜底展示，预警状态也会保留进程内缓存，降低同一进程内重复提醒风险。
- 读取 `a_values.jsonl`、`alerts.jsonl`、`notifications.jsonl` 和 `runs/` 日期索引时也是 fail-soft；Volume 上单个文件损坏或类型异常会打印 warning 并忽略该文件，不让 `/api/data`、`/health` 或 `/api/dates` 直接 500。
- 历史点和成分券展示字段会先做类型清洗；坏 `timestamp`、数字型 `code/name` 等异常展示字段不会让图表排序、公式快照或组件表格崩掉。
- API 路由有统一兜底；非预期展示层异常会返回结构化 JSON 错误并打印 warning，避免前端直接拿到断开的连接。
- `state.json` 损坏或内部结构异常时会回退到空状态并打印 warning，避免 Volume 上的状态文件问题拖垮自动计算。

服务会自动读取 Railway 注入的 `PORT`，健康检查地址是：

```text
/health
```

Railway 已挂持久化 Volume：

- Volume：`511130-live-monitor-volume`
- Mount path：`/data`
- 写入路径：`A_MONITOR_RUNS_DIR=/data/runs`

这意味着 `runs/YYYYMMDD/a_values.jsonl` 会写到 Volume；重启或重新部署后，已有历史曲线仍应保留。

线上校验重点：

- `/health` 返回 `ok: true`。
- `/health` 的 `process_ok` 表示进程/自动线程健康，`data_ok` 才表示当前严格实时 a 可用；`pcf_retry_remaining_seconds` 表示 PCF 未就绪后的下次重试倒计时。
- `/health.auto_loop.code=running` 表示后台自动计算循环仍有心跳；`stale` 才需要怀疑自动线程卡住。
- `/health.notification_setup` 可用于确认 Railway 是否读到了 `A_MONITOR_WEBHOOK_URL`、`A_MONITOR_WEBHOOK_KIND`、`A_MONITOR_FEISHU_SECRET`，但不会返回密钥内容。
- `/health.accuracy_setup` 可用于确认当前是否仍是 `estimated_a_v1`、`019776/019837` 两券结构、东方财富严格实时行情、`<=3` 秒同步、`<=30` 秒新鲜度、缺利息不兜底。
- `/health.alert_policy_setup` 可用于确认错误不发飞书、无阈值不发飞书、降级候选预警启用和通知重试次数。
- `/health` 和 `/api/data` 的 `diagnostics.summary` 会给出三层摘要，例如 `PCF未就绪 / 等待PCF / 飞书未配置`。
- `/api/data` 的 `status.label` 必须是 `正常 / 接近300 / 已超过300` 才代表当前 a 通过实时校验。
- `quote_skew_seconds` 必须小于等于 `3`。
- 页面公式里的 a 必须能按 `511130价格 / 100 * 1,000,000 - 成分券价值 - EstimatedCashComponent` 独立复算一致。
- `POST /api/notify-test` 只测试飞书链路；如果 webhook 未配置，应返回未配置错误，而不是 PCF/行情错误。

只读 smoke 检查：

```bash
python3.12 scripts/511130_live_monitor/smoke_check.py https://511130-live-monitor-production.up.railway.app --json
```

这个命令只读 `/health` 和 `/api/data`，不会触发飞书消息。它会检查线上是否已是新版本、自动线程是否健康、飞书配置诊断是否可见、准确性护栏是否仍是严格实时。

## 曲线回看和 a-K线

看板曲线支持两个维度：

- 范围：近1分钟、近5分钟、近15分钟、近1小时、今天。
- 周期：1秒、1分钟、15分钟。

接口：

```text
GET /api/dates
GET /api/series?date=20260615&range=15m&interval=1s
GET /api/series?date=20260615&range=today&interval=1m
GET /api/series?date=20260615&range=today&interval=15m
```

规则：

- 后端保存的仍是 1 秒原始严格实时快照。
- 1分钟和15分钟视图由原始 a 点聚合为 OHLC：open/high/low/close。
- 这是 `a值K线`，不是 511130 价格K线。
- 当前 a 的展示仍走 `/api/data` 的严格实时校验；切到历史日期只改变图表，不把历史值冒充为当前 a。

行情卡片：

- `/api/data.quote_cards` 返回 `511130`、`019776`、`019837` 的价格、涨跌、成交、五档盘口和本地分时序列。
- 页面将三只证券行情卡和 `套利值A` 状态卡做成四联横排；桌面端四张卡相连展示，窄屏保持横向滚动，不把四张卡拆成竖向列表。
- 卡片价格优先使用严格实时计算快照；五档盘口只使用新浪展示快照，并在页面标注来源和时间。
- 每只证券的小分时线以东方财富 1 分钟分时为底图，失败时用新浪 1 分钟兜底，并叠加本地已保存的严格实时计算点；1 分钟底图有缓存，不按 3 秒高频打满。

说明：

- 这是内部观察看板，不下单。
- 如果行情不同步、过旧、PCF结构变化或利息异常，页面会拒绝展示当前 a。
- Railway Volume 用于跨重启/重部署保留曲线历史；如果以后删除 Volume 或改挂载路径，历史保留会受影响。
