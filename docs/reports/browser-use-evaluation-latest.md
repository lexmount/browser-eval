# Chromium 与 Moli 浏览器工具调用评测

运行编号：`78e5e3a4ddfb`

这是确定性的 BrowserTool 等价调用微基准；成功判定和失败分类均由程序完成。

| Provider | 结果正确 | 结果与参考过程均正确 | P50 ms | 平均值 ms | P95 ms |
|---|---:|---:|---:|---:|---:|
| chromium | 30/30（100.00%） | 30/30（100.00%） | 1,720.68 | 1,871.10 | 4,287.50 |
| moli | 25/30（83.33%） | 25/30（83.33%） | 2,355.27 | 3,694.38 | 8,958.35 |

```bash
BROWSER_USE_EVAL_FAILURE_RETRIES=1 ./examples/browser_use/run.sh
```
