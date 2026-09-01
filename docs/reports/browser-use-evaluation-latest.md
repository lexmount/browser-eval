# Chromium 与 Moli 浏览器工具调用评测

运行编号：`a20e9b5a1878`

这是确定性的 BrowserTool 等价调用微基准，不调用 CowAgent、LiteLLM 或 Codex 模型。

| 内核 | 成功/尝试 | 成功率 | P50 ms | mean ms | P95 ms |
|---|---:|---:|---:|---:|---:|
| chromium | 30/30 | 100.00% | 2,250.49 | 2,802.55 | 7,220.47 |
| moli | 28/30 | 93.33% | 2,859.18 | 7,072.71 | 35,865.54 |

```bash
BROWSER_USE_EVAL_REPEATS=3 ./examples/browser_use/run.sh
```
