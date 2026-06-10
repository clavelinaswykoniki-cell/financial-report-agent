# Apple Inc. (AAPL) 财报分析报告

- 生成时间: 2026-06-10T17:01:28Z
- CIK: `0000320193`
- 交易所: Nasdaq
- SIC: 3571 Electronic Computers
- 最近年度报告: 10-K 2025-09-27
- SEC 原始文件: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm

## 结论摘要

- 风险等级: **Medium**，风险分: **32 / 100**。
- 分析置信度: **High**。
- 该报告基于 SEC XBRL facts 与规则评分生成，不构成投资建议。

## 核心指标

| 指标 | 数值 |
| --- | ---: |
| Revenue | $416.16B |
| Revenue YoY | 6.43% |
| Gross margin | 46.91% |
| Operating margin | 31.97% |
| Net margin | 26.92% |
| Debt / assets | 79.48% |
| Current ratio | 0.89 |
| OCF / net income | 1.00 |
| Free cash flow | $98.77B |
| FCF margin | 23.73% |
| R&D intensity | 8.30% |

## 年度趋势

| Fiscal year | Revenue | Net income | Operating cash flow | Free cash flow |
| --- | ---: | ---: | ---: | ---: |
| 2022 | $394.33B | $99.80B | $122.15B | $111.44B |
| 2023 | $383.29B | $97.00B | $110.54B | $99.58B |
| 2024 | $391.04B | $93.74B | $118.25B | $108.81B |
| 2025 | $416.16B | $112.01B | $111.48B | $98.77B |

## 分析工作流

- **Data Step** [completed]: Resolved ticker, pulled SEC submissions and companyfacts JSON. 证据: CIK 0000320193 / 10-K。
- **Metric Step** [completed]: Calculated growth, margin, leverage, liquidity, cash-flow, and R&D-intensity metrics. 证据: Revenue tag: RevenueFromContractWithCustomerExcludingAssessedTax。
- **Risk Step** [completed]: Assigned Medium risk with High confidence. 证据: 2 triggered rule(s), score 32 / 100。
- **Quality Step** [completed]: Checked key XBRL coverage and trend comparability limits. 证据: 0 data warning(s)。
- **Report Step** [completed]: Rendered a Markdown report and machine-readable JSON payload. 证据: Fiscal period 2025。
- **LLM Analyst Agent** [skipped]: Rules-only mode remains fully runnable and reproducible. 证据: No LLM pass requested or no API key detected。

## 风险观察

- **资产负债率较高** (+20): 负债/资产为 79.48%。
- **短期偿债压力** (+12): 流动比率为 0.89。

## 数据质量

- 关键 XBRL 标签覆盖较完整。

## 方法说明

- 数据源: SEC EDGAR submissions 与 XBRL companyfacts API。
- 口径: 优先 US-GAAP 标准标签；缺失时使用同类 fallback 标签。
- 风险评分: 只做财务报表风险观察，不输出买卖建议或目标价。
- 局限: 行业差异、公司自定义 taxonomy、重述/修订文件、非美国公司 IFRS 标签会影响可比性。
