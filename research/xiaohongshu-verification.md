# 小红书：三条内容核验

这份清单仅用于本次会话协作，不包含在正式报告中。

Tavily 原始输出包含下列三个名称及带访问参数的详情地址。评测端曾错误地移除查询参数，导致核验使用的简化地址显示“当前笔记暂时无法浏览，请打开小红书 App 扫码查看”。这不是 Tavily 的抓取失败。

2026-09-02 10:27 UTC，保留原始查询参数后，三条详情页均成功显示实际标题，与输出一致。依据已保存在 evaluation/list-references/xiaohongshu-details.json，由名称与链接身份匹配程序重算；无大模型裁判。此前人工反馈准确反映了简化地址的限制，无需再次操作。

1. [工资到账了，咬碎牙挣来的！](https://www.xiaohongshu.com/explore/6a51a3bd0000000016024807)
2. [他还是个孩子这句话居然能从我嘴里说出来](https://www.xiaohongshu.com/explore/6a6975bc000000000a039df2)
3. [男朋友送的包包是正品吗](https://www.xiaohongshu.com/explore/6a42ffe2000000000702a887)

小红书任务保留，该次 Tavily 输出的前三条均获确认，任务通过。已有知乎、大众点评、JustWatch 快照已使用，无需重新提供。
