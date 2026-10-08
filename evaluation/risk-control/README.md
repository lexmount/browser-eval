# 曾观察到访问限制的 100 个网址评测

本评测回答一个问题：Moli 1.1.11 在不点击、不输入、不滚动、不翻页、不登录、
也不使用 OCR 的前提下，能否从曾观察到登录、验证、反自动化或访问限制的指定网址，
取得首次加载时已经存在的目标内容，而不是验证页、空返回或正文片段。

当前范围是 100 个网址案例、74 个独立网站。外置的 `dataset.json` 是可执行任务定义，
由本目录 `source-inputs.json` 绑定；`dataset-catalog.json` 保留任务目录和定义哈希。
完整参考按[参考输入说明](../../docs/reference-inputs.md)导入，不随源码分发。
每例要求首次加载范围内声明的全部稳定文字，包括已登记的导航、按钮和侧栏文字；
任一稳定文字缺失都不能通过。79 例另有完整正文分区；21 例按 27 个独立数量组检查名称、
真实链接、路径、去重及同条目字段。人类规则、机器契约、资格审核与替换依据分别由
`first-30-rule-review.md`、`remaining-70-rule-review.md`、`body-contracts.json`、
`list-contracts.json` 和 `qualification/` 保存。资格信息只说明 cohort 的历史入选依据，
不参与结果评分；原始资格调查留在 Git 外。`run-profile.json` 绑定全部当前权威输入、
provider 配置、执行参数和二进制身份。

## 确定性构建与零调用验证

```sh
uv run --frozen python scripts/build_risk_control_dataset.py --output /tmp/risk-control-dataset.json
uv run --frozen python scripts/run_certified_benchmark.py risk-control --env-file .env
```

第一条命令从 `BROWSER_EVAL_DATA_HOME`（默认 `~/.browser-eval`）下的哈希绑定独立证据
重建数据集；输出必须与清单绑定的外置 `dataset.json` 字节一致。构建必须显式指定
新的输出路径，不能覆盖已冻结的输入。第二条命令只校验 profile、
数据集、Moli 1.1.11 版本和二进制 SHA-256，不发起 provider 调用。

编译器位于 `harness.web_fetch.risk_control_dataset`。在安装包或其他工作目录中调用时，
用 `--definition-dir` 显式指定本目录的规则与资格清单副本；冻结规则的哈希检查保持不变。

## 正式执行

```sh
uv run --frozen python scripts/run_certified_benchmark.py risk-control \
  --env-file .env --execute \
  --output "$BROWSER_EVAL_DATA_HOME/evidence/risk-control/<new-run-id>"
```

输出目录必须是新的，并位于数据目录下。正式入口固定为
`scripts/run_certified_benchmark.py risk-control`；直接对执行脚本使用 `--execute` 会被拒绝。
当前 profile 的新正式运行恰有 100 个逻辑结果，且不自动重试，并为每次调用在 Git 外保留完整、
哈希绑定的 debug stderr。保留的历史证据另有 3 个 DNS 失败后的用户授权补调，因此保留 103 次
物理调用；每例固定采用最后一次响应，全部调用耗时均计入，不能择优。运行前后 Moli 身份必须
一致；100 份声明的参考文本也会在调用
前后逐一核验，缺失、内容变化或范围不完整都会中止认证。生成报告前还会校验
`certification.json` 对 profile、dataset、results、manifest、execution、参考证据核验回执
和完整日志的绑定。

本目录不包含 Moli 实测输出、provider 原始工件、debug 日志或凭据。重新生成报告继续使用
仓库已有的构建入口；必须提供完整且已认证的证据。历史结果缺少调用前后的参考认证，
不能通过补写回执变成正式结果。

“全页”指首次加载范围内已声明的稳定文字、正文与列表规则，不是声称已证明网页每一个
未登记字符或交互后的内容均被取得。导航等文字只要已登记，就与正文文字一样是必需项。

## 当前契约与历史结果

当前契约包含 1,600 项必需稳定文字。
`research/risk-control-full-page-rule-revision.json` 绑定前序契约与全页规则修订，
保留全部已登记外围文字，并不改写历史修订收据或供应商输出。
历史报告的 31/100 属于先前输入和证据版本，不能直接当作当前契约的成绩。

当前适配器通过同次 `fetch --dump json` 的 `final_url`、状态和最终 HTML 取得页面身份，
再在同一截止预算内转换为 Markdown。旧 debug 日志不能按 GET 完成顺序证明重定向归属；
证据不足时页面身份为未知。调用路径改变时，正式成绩和耗时须来自覆盖完整受影响范围的
新执行，不能复用旧路径的日志或耗时。
