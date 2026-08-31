# Chromium 与 Moli 浏览器工具调用评测

运行编号：`f69cce591ad9`

这是确定性的 BrowserTool 等价调用微基准，不调用 CowAgent、LiteLLM 或 Codex 模型。

| 内核 | 成功/尝试 | 成功率 | P50 ms | mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|
| chromium | 30/30 | 100.00% | 1,796.96 | 2,903.17 | 10,185.84 |
| moli | 30/30 | 100.00% | 2,243.39 | 5,504.71 | 34,371.62 |

```bash
BROWSER_USE_EVAL_REPEATS=3 ./examples/browser_use/run.sh
```
