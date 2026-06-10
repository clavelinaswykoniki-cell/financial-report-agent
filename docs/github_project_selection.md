# 项目定位与开源参考边界

筛选目标：做一个能写进金融或 AI Agent 岗位简历、能本地跑通、能讲清楚业务价值和技术边界的小项目。

## 结论

不要把项目包装成“AI 炒股”或“自动交易”。更稳的方向是 **Financial Report Agent**：

- 输入 ticker，抓 SEC EDGAR 官方公开数据。
- 解析 XBRL companyfacts，计算财务指标。
- 用规则 Agent 做风险观察，不输出买卖建议。
- 可选 LangChain tool-calling Agent 只做中文补充解读。
- 输出 Markdown/JSON，便于展示、测试和后续做 Web UI。

这个方向比交易决策 Agent 更适合简历：合规边界清楚、可解释性强、能体现财务数据处理和 Agent 工程能力。

## 可以参考但不要照搬的开源方向

- 多 Agent 金融分析项目：参考角色分工、工具调用、报告生成链路。
- 金融数据平台项目：参考数据接入、指标口径、缓存和 CLI 设计。
- RAG/Agent 项目：参考工具调用、引用来源、结果审查和 fallback。

这些参考只用于架构灵感；当前项目的核心卖点应该是“官方数据 + 可复现指标 + 可审计风险观察”，不是复刻大仓库。

## 可写进简历的定位

项目名建议：`Financial Report Agent | SEC 财报分析与风险观察 Agent`

一句话定位：基于 SEC EDGAR 官方 API 构建本地财报分析 Agent，实现 ticker 到 CIK 映射、XBRL 指标抽取、规则化风险评分和可选 LLM 中文解读，输出可审计 Markdown/JSON 报告。
