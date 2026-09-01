# Web Fetch / 正文抽取公开基准取舍

## 直接采用：WCXB

[WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) 提供
2,008 个 HTML 快照与人工复核标准答案，覆盖 article、forum、product、collection、
listing、documentation、service 七种结构。标准答案包含完整 `main_content`、必须
保留的 `with[]` 和应排除的 `without[]`，并提供词级 precision/recall/F1。数据许可为
CC-BY-4.0。

本项目固定到提交 `c039d5ee9f5a3a984a0e167e63aacd04e76e78a9`，按七类型各取
4 条开发集样本，共 28 条。区别是 WCXB 原任务在固定 HTML 上做离线正文抽取，本项目
调用 live URL。因此 HIL 必须先判断页面是否漂移；固定快照的答案不能无条件当成当前
网页事实。

## 采用原则：LiveWeb-IE

[LiveWeb-IE](https://arxiv.org/abs/2603.13773) 专门讨论固定 HTML 与 live 网站的差距，
采用内容稳定的网站、自然语言查询、多属性难度分层与人工验证。论文报告离线 wrapper
迁移到当前网页后平均 F1 下降超过 15%。本项目采用它的“稳定事实 + live 漂移单判”原则，
但不引入图片、链接、视觉定位和 XPath 生成任务，因为这些属于结构化/Agent 抽取而非
通用 Web Fetch 正文契约。

## 只作离线参考：Readability、Dragnet、CleanEval

- Mozilla Readability 有大型真实页面回归 fixture，适合验证标题、byline、正文和样板
  去除；它不执行默认脚本，也不覆盖云 API 限流、认证和供应商网络。
- Dragnet/其训练数据用人工正文做 token-level accuracy、precision、recall、F1，适合
  借鉴词级指标；数据和页面年代较老，重点仍是文章。
- CleanEval 是经典网页清洗集，但规模、年代和页面类型不足以单独代表现代 Agent Web Fetch。

## 不纳入正文主榜

- SWDE、WebSRC 等结构化抽取集测试字段/XPath/问答，不等同于返回通用可读正文。
- WebArena、Mind2Web、BrowserGym 等测试交互式 Web Agent，不是单次 URL fetch。
- Common Crawl/FineWeb 测语料抓取与清洗，不提供本轮逐 live URL、逐供应商接口的公平输入。
- 反爬、登录、验证码和历史 4xx/5xx 长尾有价值，但应独立评“可达性/解锁稳定性”。
