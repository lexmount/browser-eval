# GitHub 工作流

`ci.yml` 在 main 的推送和 PR 上运行。它使用临时 GitHub 托管运行器，安装 Python 3.11、uv 0.7.8 和 Node.js 22，再执行 `make setup-test` 与 `make ci`。本地贡献者使用相同入口。

`claude.yml` 是可选的中文代码审查。维护者设置仓库变量 `ENABLE_CLAUDE_REVIEW=true` 和密钥 `CLAUDE_CODE_OAUTH_TOKEN` 后才会启动；普通 CI 不需要这些配置。审查同样使用临时 GitHub 托管运行器，不依赖个人主机或本机 Claude/Bun。

启用后，非 Draft PR 的新建、更新、转为待审查和重新打开事件触发自动审查。issue、PR 评论及 review 中的 `@claude` 使用交互步骤，接收评论中的请求。规则集中在 `CLAUDE_REVIEW_STANDARDS`，并引用本仓库的 AGENTS.md 和相关 skill。同一 PR 的新提交会取消旧审查。
