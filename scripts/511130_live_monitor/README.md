# 511130 Live a-value Monitor

只读监控，不下单，不连接交易接口。

## 口径

`预估a = 511130实时报价 / 100 * 1,000,000 - [sum((成分券实时净价 + 当日逐券利息) * PCF数量 * 10) + EstimatedCashComponent]`

## 常用命令

```bash
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode precheck
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once
```

带 webhook 提醒：

```bash
A_MONITOR_WEBHOOK_URL="https://..." A_MONITOR_WEBHOOK_KIND="feishu" \
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once --notify
```

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

- `--auto-run`：每 `--interval` 秒自动算一次并写入 `runs/<日期>/a_values.csv`（默认 1 秒）
- `--open`：自动打开浏览器
- `--date YYYYMMDD`：指定交易日（默认读 `config.json` 里的 `target_date`）
- `--interval 秒数`：刷新和自动算间隔（不小于 1 秒）

示例（开盘实时刷新）：
```bash
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open --interval 1   # 一秒
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open --interval 15  # 15秒
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --auto-run --open --interval 60  # 一分钟
```

不加 `--auto-run` 时是只读页面，页面里可手动点“手动算一次”触发一次计算。

团队同一局域网访问：

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --auto-run-notify --interval 1
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

部署入口在仓库根目录：

- `Dockerfile`
- `railway.toml`

Railway 启动命令：

```bash
python -u scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --interval 1
```

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
- `/api/data` 的 `status.label` 必须是 `正常 / 接近300 / 已超过300` 才代表当前 a 通过实时校验。
- `quote_skew_seconds` 必须小于等于 `3`。
- 页面公式里的 a 必须能按 `511130价格 / 100 * 1,000,000 - 成分券价值 - EstimatedCashComponent` 独立复算一致。

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

说明：

- 这是内部观察看板，不下单。
- 如果行情不同步、过旧、PCF结构变化或利息异常，页面会拒绝展示当前 a。
- Railway Volume 用于跨重启/重部署保留曲线历史；如果以后删除 Volume 或改挂载路径，历史保留会受影响。
