# API Key 接入说明

这个项目支持三种 LLM API key 接入方式。没有 key 时仍然可以用 `--llm off` 跑规则报告；有 key 时用 `--llm on` 或默认 `--llm auto` 启用 Agent 补充解读。

CLI 可以从 `.env` 读取 key；本地 dashboard 也可以在表单中临时输入 key。表单输入的 key 只在当前分析调用内存中使用，不写入全局环境变量，也不会写入 Markdown/JSON 报告。

## OpenAI

`.env`：

```bash
OPENAI_API_KEY="sk-..."
FIN_AGENT_MODEL="openai:gpt-4o-mini"
```

运行：

```bash
python3 -m pip install -e ".[llm]"
python3 -m financial_report_agent --ticker AAPL --llm on
```

## DeepSeek

`.env`：

```bash
DEEPSEEK_API_KEY="sk-..."
FIN_AGENT_MODEL="deepseek:deepseek-chat"
```

运行：

```bash
python3 -m pip install -e ".[llm]"
python3 -m financial_report_agent --ticker AAPL --llm on
```

## 自定义 OpenAI-Compatible API

适合硅基流动、OpenRouter、本地代理、公司内部网关等兼容 OpenAI Chat Completions 风格的服务。

`.env`：

```bash
FIN_AGENT_API_KEY="sk-..."
FIN_AGENT_BASE_URL="https://api.example.com/v1"
FIN_AGENT_MODEL="openai-compatible:your-model"
```

运行：

```bash
python3 -m pip install -e ".[llm]"
python3 -m financial_report_agent --ticker AAPL --llm on
```

## 安全边界

- 不要把 `.env` 提交到 GitHub。
- API key 只放本地 `.env` 或系统环境变量。
- 简历和 README 里写“支持 API key 接入”，不要公开真实 key。
- LLM 只做解释层，财务事实来自 SEC 和规则模块，避免模型直接编数。
