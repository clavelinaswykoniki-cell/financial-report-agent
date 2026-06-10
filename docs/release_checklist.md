# GitHub 发布前检查清单

## 项目质量

- README 能在 60 秒内讲清楚项目做什么、怎么跑、为什么适合金融/AI Agent 岗位。
- 示例报告放在 `examples/`，运行时输出放在被忽略的 `reports/`。
- `docs/api_key_setup.md` 说明 OpenAI、DeepSeek 和 OpenAI-compatible 三种 key 接入方式。
- `SECURITY.md` 说明 key 保护、发布前 secret scan 和金融安全边界。

## 本地验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/financial-report-agent --help
.venv/bin/financial-report-agent --ticker AAPL --llm off --output-dir reports --cache-dir .cache/sec
.venv/bin/financial-report-agent --ticker AAPL --watchlist MSFT,NVDA --llm off --output-dir reports --cache-dir .cache/sec
.venv/bin/financial-report-dashboard --help
OPENAI_API_KEY=test-key .venv/bin/financial-report-agent --ticker AAPL --llm auto --llm-timeout 5 --env-file /tmp/does-not-exist
```

用真实 key 发布前再跑一次：

```bash
python3 -m financial_report_agent --ticker AAPL --llm on
```

成功报告应出现 `LangChain Agent 补充解读`；失败日志不应回显真实 key。

## Secret Scan

```bash
rg -n "sk-[A-Za-z0-9_-]{20,}|(OPENAI|DEEPSEEK|FIN_AGENT)_API_KEY=[\"']?sk-[A-Za-z0-9_-]{20,}" .
```

只允许命中文档占位符，不允许出现真实 key。

## 发布边界

- 不提交 `.env`、`.cache/`、`.venv/`、`reports/`。
- 默认公开仓库名建议：`financial-report-agent`。
- 项目描述建议：`SEC filing analysis agent with rule-based financial risk scoring and optional LLM API key integration.`
