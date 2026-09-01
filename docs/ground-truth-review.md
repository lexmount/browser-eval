# 标准答案与人工复核协议

Dataset: `public-webfetch-v3.0-hil`

Review date: 2026-09-01

Annotator: Codex

标准答案在供应商评分之前冻结。事实来自源网页，不从任何参评供应商输出反推。
每个案例包含完整题目、参考答案、事实组、错误页标记、源页面证据和人工检查清单。

首轮核验方式：

- 普通 HTML 与重定向：核对源页面文本和最终目标正文；
- JavaScript 案例：核对执行脚本后的 DOM；
- PDF 案例：核对 15 页 arXiv PDF 的标题、作者、Transformer 与 Scaled Dot-Product Attention；
- 产品文档：核对已发布的 CowAgent 与 Lexmount 文档；
- GitHub：核对默认分支 README。

The annotation deliberately excludes dynamic values such as stars, article
revision dates and latency. Facts are stable semantic units, not an exhaustive
copy of each page. A content pass requires all registered facts, a minimum
length and no registered error/challenge marker.

这是首轮单人标注，不声称绝对正确。对外营销或采购使用前，第二位复核者必须在
`report/review/index.html` 进入每条调用页，并排阅读题目、参考答案和供应商完整输出，
独立给出正确性、完整性、可读性和最终结论。任何标准答案修改都必须升级数据集版本并
重跑全部方法。

## 人工复核 JSON

每条调用页可将复核记录保存到浏览器 localStorage，也可导出 JSON。字段如下：

```json
{
  "attempt_id": "provider__case__r001",
  "reviewer": "reviewer-id",
  "decision": "pass | fail | uncertain",
  "correctness": "complete | partial | wrong",
  "completeness": "complete | partial | empty",
  "readability": "good | noisy | unusable",
  "notes": "引用具体命中、缺失或误判内容",
  "saved_at": "ISO-8601"
}
```

正式发布时应把复核 JSON 纳入审计材料；localStorage 只是离线填写便利，不是集中存储。

## v2.1 correction log

The first strict score exposed three annotation false negatives. They were
checked against source pages before final scoring: Example Domain now displays
`Learn more`; Books to Scrape visibly truncates the first title to
`A Light in the ...`; and GitHub may include a local loading-error template even
when the complete README is present. v2.1 adds the two source-visible
alternatives and removes that GitHub template string as a global hard failure.
Raw supplier artifacts were not modified; the final metrics were recomputed
from their hashes.
