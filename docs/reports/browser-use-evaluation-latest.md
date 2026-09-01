# Chromium 与 Moli 浏览器工具调用评测

运行编号：`abf08971d1d2`

这是确定性的 BrowserTool 等价调用微基准；成功判定和失败分类均由程序完成。

| Provider | 结果正确 | 结果与参考过程均正确 | P50 ms | 平均值 ms | P95 ms |
|---|---:|---:|---:|---:|---:|
| chromium | 30/30（100.00%） | 30/30（100.00%） | 1,576.77 | 1,732.93 | 3,329.30 |
| moli | 27/30（90.00%） | 27/30（90.00%） | 1,985.00 | 2,578.54 | 8,353.12 |

```bash
BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh
```
