# Next Session

先读：

1. `scripts/511130_live_monitor/CODEX.md`
2. `scripts/511130_live_monitor/README.md`
3. `scripts/511130_live_monitor/live_a_dashboard.py`
4. `scripts/511130_live_monitor/monitor_511130.py`

当前最高优先级：

- 公网看板：

```text
https://511130-live-monitor-production.up.railway.app
```

- 如用户要本地现场使用，运行：

```bash
cd /Users/happytang/Documents/工作
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --auto-run-notify --interval 1
```

- 同一局域网设备访问终端打印的 `Team LAN URL`。
- 曲线已支持 K-line 式切换：
  - 范围：近1分钟、近5分钟、近15分钟、近1小时、今天。
  - 周期：1秒折线、1分钟 a值OHLC、15分钟 a值OHLC。
  - 接口：`/api/dates`、`/api/series?date=YYYYMMDD&range=today&interval=1m`。

已知约束：

- 只读监控，不下单。
- 三只证券行情时间差必须 `<=3` 秒。
- 缺逐券利息不展示伪实时 a。
- 飞书发送失败要按业务响应码暴露，不只看 HTTP 200。
- 当前 PCF 结构锁定为 `019776/019837`；换券要先人工确认再改配置。
- Railway 文件系统不是长期审计存储。
- 要让过去日期跨 Railway 重启/重部署长期保留，需要挂 Railway Volume 并设置 `A_MONITOR_RUNS_DIR=/data/runs`。
