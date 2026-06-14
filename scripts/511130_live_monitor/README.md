# 511130 Live a-value Monitor

只读监控，不下单，不连接交易接口。

## 口径

`预估a = 511130实时报价 / 100 * 1,000,000 - [sum((成分券实时净价 + 当日逐券利息) * PCF数量 * 10) + EstimatedCashComponent]`

## 常用命令

```bash
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode precheck
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once
```

只测试飞书预警链路：

```bash
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode notify-test
```

带 webhook 提醒：

```bash
A_MONITOR_WEBHOOK_URL="https://..." A_MONITOR_WEBHOOK_KIND="feishu" \
reports/511130_a_20260612/.venv/bin/python scripts/511130_live_monitor/monitor_511130.py --mode once --notify
```

云端手动测试：

```bash
gh workflow run 511130-a-monitor.yml -f mode=notify-test
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
