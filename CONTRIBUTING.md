# 贡献指南

先运行 README 中的离线示例，再阅读对应轨道的运行说明。问题报告请附任务 ID、适配器类型、版本、操作系统、可复现命令和已脱敏的错误信息；不要附凭据或登录态。

扩展现有执行、评分、聚合或报告模块。新增浏览器、版本和任务规模应使用配置或注册表，不复制运行器或报告生成器。`scripts/` 是命令入口，可复用逻辑放在 `harness/`。

行为变化须有场景测试，覆盖应通过和应拒绝的输入。评分修正还须提供共享评分入口的回归控制与修订来源，不直接改写结果。历史实测证据与证书不能因代码重构而重新标定。

提交前运行 `make ci`。开发测试使用 CI 中的 Node.js 22，需提供 `node:module.stripTypeScriptTypes`；基础 Python 离线示例不需要 Node.js。涉及报告发布时，另外执行该轨道完整发布检查，包括浏览器中成功与失败详情、证据弹窗、返回导航，以及桌面和窄屏、明暗主题。真实服务测试所需环境见各轨道文档。

`make ci` 包含 `make package-check`：先构建源码包，再从源码包构建 wheel，在仓库外分别验证基础安装、`webmainbench-eval` 和 `oss-eval` 扩展安装，并逐文件核对当前模块和声明的资源。检查包含 TypeScript 工具桥接、合成证据的 TEDS 修订，以及 LangChain 正文提取和 Crawl4AI 文件下载的本地 HTTP 场景，不调用真实评测服务。缺失、过期或额外的安装文件都会被拒绝。单独修改打包配置时，可先运行 `make package-check`。

常规测试不下载历史源页。重建历史参考的检查需要按 [参考输入说明](docs/reference-inputs.md) 安装外置输入，再运行 `make test-reference-inputs`。严格模式下，缺少、篡改或不完整的输入会在测试开始前报错；常规模式只跳过依赖缺失历史输入的检查，已有输入损坏仍会失败。

不要提交 `.env`、Token、Cookie、供应商输出、运行目录、生成报告或内部审核记录。第三方内容须保留来源、版本和许可说明。补充任务定义不等于取得源页面的再分发权。

首次公开源码时，从审查后的当前提交导出源码包，在新仓库中初始化公开历史；不要推送仍含外置源材料的开发历史。删除当前文件不会从旧提交中移除它们。导出使用 Git 自身的归档功能：

```sh
git archive --format=tar.gz --output=/tmp/browser-eval-source.tar.gz HEAD
```

源码包不包含 `.git`、未跟踪文件和本机安装目录。解包后按 README 安装依赖并运行 `make ci`，使用空的 `BROWSER_EVAL_DATA_HOME` 验证普通贡献者的使用场景。历史认证器按清单保留为只读、哈希绑定的文本材料，不依赖旧 Git 提交，也不是可执行入口。源材料的分发边界仍以 [第三方内容说明](THIRD_PARTY.md) 和 [参考输入说明](docs/reference-inputs.md) 为准。

可选的 Claude 审查需要维护者设置仓库变量 `ENABLE_CLAUDE_REVIEW=true` 和 `CLAUDE_CODE_OAUTH_TOKEN`。普通 CI 不依赖这些配置；该审查使用临时 GitHub 托管运行器，不要求个人主机路径。
