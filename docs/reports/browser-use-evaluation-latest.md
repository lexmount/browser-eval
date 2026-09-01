# Chromium 与 Moli 浏览器工具调用评测

运行编号：`897427526ea5`

这是确定性的 BrowserTool 等价调用微基准，不调用 CowAgent、LiteLLM 或 Codex 模型。

| 内核 | 首次通过 | 重试恢复 | 最终通过 | 成功率 | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| chromium | 15/15 | 0 | 15/15 | 100.00% | 448.19 | 930.47 |
| moli | 11/15 | 0 | 11/15 | 73.33% | 138.07 | 599.40 |

```bash
BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh
```
