# `lightmount-dark` Web Fetch 评测审计

审计提交：`228330684f3502fd573f98da08123e20090bdaae`（2026-09-01 读取）。

## 它实际在测什么

`moli-benchmark top-sites` 比较 Moli、Chromium、Lightpanda、Obscura 等浏览器
引擎或 CDP 路径。主要输出是渲染 HTML、标题、可见文本长度、前 500 字符、
进程耗时和 PSS/RSS。它不是托管 Web Fetch API 的端到端成本/正文清洗基准。

`webfetch-mix` 共 259 个 URL：前 100 个来自中外站点列表，后 159 个来自历史
WebFetch 失败记录。159 条按原始失败原因覆盖 403、浏览器抓取失败、模板无效、
429、401、其他 4xx、5xx 等。2026-07-11 横评先把五个目标都不可达的 77 条排除，
在共同 182 条上比较成功率。

关键代码和证据：

- `docs/webfetch-horizontal-benchmark-2026-07-11.md`
- `docs/webfetch-longtail-seed-list.md`
- `docs/render-quality-seed-list.md`
- `docs/legacy-encoding-websites-seed-list.md`
- `moli-benchmark/moli_benchmark/top_sites.py`
- `moli-benchmark/moli_benchmark/wild_web.py`
- `moli-benchmark/moli_benchmark/render_compare.py`
- `moli-benchmark/scripts/run-webfetch-mix-benchmark.sh`

## 值得直接借鉴

1. 失败类型不是一个 `failed`：应区分 timeout、network、app shell、JS challenge、
   CAPTCHA、forbidden、login wall、binary 和源站不可达。
2. 保存每 URL/目标的原始输出、stderr、摘要和资源采样，支持聚焦复现。
3. 使用共同计分集和逐 URL 交集，避免两轮网络可达集合不同却直接比较总成功率。
4. 将热门站点、历史失败长尾、渲染质量页、遗留编码页拆成不同 source/profile。
5. 具体内容页比主页更能暴露 hydration、模板和运行时 API 问题。本项目因此导入
   12 条 `render-quality` URL。

## 不能照搬的部分

1. 2026-07-11 报告明确说明“只判断是否取得可用内容，不比较 DOM/正文质量”。
   标题、最小文本长度和少量关键词不足以证明 Agent 得到正确完整正文。
2. 历史失败样本存在强烈选择偏差，适合回归和稳定性压力，不适合作为通用内容
   质量主榜；登录墙/WAF 也不应与公开正文抽取混成一个分数。
3. Moli/Lightpanda 常用并发 20，Chromium 并发 2；资源和吞吐数据不具备严格横向
   因果可比性，原报告也主动披露这一限制。
4. “五方均不可达就排除”适合浏览器引擎调查，但供应商服务横评还需 HIL 判断：
   页面下线、地域不可达、标准答案漂移和供应商失败是四类不同结论。
5. 全部调用时延会被 timeout 和快速错误页污染。主榜应只统计请求成功且内容通过
   的调用；失败耗时和套餐节流另做诊断。

## 本项目采用的修正版

- 100 条网页主集 = 9 条协议/能力核心网页题 + 79 条 WCXB 七页面类型跨站题 +
  12 条 `lightmount-dark` 渲染质量题；直接文档与媒体 URL 放到独立能力集。
- 每条包含任务、参考答案、必需事实组、反向样板噪声、完整供应商输出和 HIL。
- HIL 增加 `dataset_drift` 与 `invalid_case`，页面变化时从分母排除，而不是归罪供应商。
- 历史 159 条失败长尾保留为后续稳定性分榜，不进入公开正文质量主榜。
- 托管供应商只比较各自最接近“已知 URL → 可读正文”的接口；浏览器交互、搜索和
  HTML 诊断接口不混入。
