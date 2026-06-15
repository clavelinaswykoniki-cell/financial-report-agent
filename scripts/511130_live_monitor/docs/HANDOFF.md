# Handoff

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
