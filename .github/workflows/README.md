# Claude 中文审查

`claude.yml` 采用 lexmount/lex-voice 的仓库级机制，审查规则集中在 `CLAUDE_REVIEW_STANDARDS`，并引用本仓库的 AGENTS.md 和相关 skill。

- 非 Draft PR 的 `opened`、`synchronize`、`ready_for_review`、`reopened` 自动审查，不限制目标分支。
- issue、PR 评论及 review 中的 `@claude` 使用交互步骤，不设置固定 prompt，让 action 接收用户的具体请求。评论类触发需要工作流先合并到默认分支。
- 自动审查步骤提供固定任务，要求中文，只报告影响合并判断的问题，不限制符合该标准的发现数量，并给出优先级、文件行号、问题与建议。
- 使用现有 self-hosted runner、`CLAUDE_CODE_OAUTH_TOKEN` 和 runner 上的 Claude/Bun，无备用凭据逻辑。
- 同一 PR 新提交取消旧的仓库级审查；并发组与组织级工作流分开。

仓库工作流不替代组织的 `Auto Code Review - Claude` 规则。目标为 main 的 PR 可能同时运行两套审查；如需仅保留仓库级版本，应在组织规则中单独排除此仓库，不能通过互相取消运行来规避必需检查。
