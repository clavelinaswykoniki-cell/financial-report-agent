# Codex 带做路线

## 你要先懂什么

- Python 基础：函数、字典、列表、异常、文件读写、命令行参数。
- 财务基础：收入、净利润、毛利率、经营现金流、自由现金流、资产负债率、流动比率。
- 数据接口：HTTP JSON、缓存、API User-Agent、错误处理。
- Agent 基础：工具调用不是魔法，先把可靠数据和规则算出来，再通过 API key 让 LLM 做解释。
- GitHub 基础：README、测试、示例输出、许可证和引用来源。

## Codex 怎么做这个项目

1. 让 Codex 先明确边界：不做实盘交易，不给买卖建议，只做财报事实分析和风险观察。
2. 让 Codex 查官方来源：SEC EDGAR API、XBRL companyfacts、LangChain tool-calling。
3. 先实现确定性主链路：`ticker -> CIK -> submissions/companyfacts -> metrics -> risk -> report`。
4. 给每一段加测试：XBRL 去重、指标计算、风险规则、报告输出。
5. 再接 LLM：把财报快照和风险结果封装成 tools，支持 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或自定义 OpenAI-compatible API key，要求模型只基于工具事实输出。
6. 跑真实 ticker：例如 `AAPL`、`MSFT`，检查报告是否有明显口径错误。
7. 写 README 和简历 bullet：强调数据源、指标、风险控制、可解释性和局限。

## 你后续可以加的改造点

- 加 peer comparison：同业公司毛利率、净利率、负债率对比。
- 加行业规则：银行、保险、软件、制造业使用不同风险指标。
- 加 Web UI：上传 ticker 列表，展示风险分和报告链接。
- 加 RAG：把 10-K MD&A 文本切块检索，再让 LLM 引用原文回答。
- 加评测：构造样例公司，验证风险规则是否触发预期。

## 面试讲法

不要说“我复制了一个 AI hedge fund”。说：

> 我参考了开源金融 Agent 项目的多角色分析思路，但把交易决策改造成更可审计的财报分析 Agent。系统先用 SEC 官方数据做确定性指标计算和风险评分，再让可选 LLM Agent 基于工具结果生成中文解释，避免模型直接编财务事实。
