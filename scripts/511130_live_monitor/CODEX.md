# 511130 Live Monitor Codex Notes

本目录是 `511130` a 值只读监控和团队看板子项目。

## Start Here

每次开发先读：

- `README.md`
- `config.json`
- `monitor_511130.py`
- `live_a_dashboard.py`
- `docs/NEXT_SESSION.md`

## Rules

- 只做读取、计算、展示和预警，不自动下单，不连接交易接口。
- 核心公式在 `monitor_511130.py`，页面层不得另起一套不一致公式。
- 严格实时模式下，三只证券行情时间差必须满足 `realtime_max_skew_seconds`，当前配置为 3 秒。
- 缺逐券利息时必须暴露错误，不用默认值伪造 a 值。
- 不要把 webhook、签名密钥、账号信息写入文档或提交说明。

## Verification

常用检查：

```bash
python3.12 -m py_compile scripts/511130_live_monitor/live_a_dashboard.py scripts/511130_live_monitor/monitor_511130.py
python3.12 scripts/511130_live_monitor/monitor_511130.py --mode selftest
```

本地看板：

```bash
python3.12 scripts/511130_live_monitor/live_a_dashboard.py --host 0.0.0.0 --auto-run --auto-run-notify --interval 1
```

Railway 公网看板：

```text
https://511130-live-monitor-production.up.railway.app
```

Railway 入口：

- 根目录 `Dockerfile`
- 根目录 `railway.toml`
- `/health`

Railway 当前用途是内部观察，不是交易基础设施。准确性策略是 fail closed：数据不同步、过旧、缺利息、PCF结构变化、利息异常时不展示当前 a。
