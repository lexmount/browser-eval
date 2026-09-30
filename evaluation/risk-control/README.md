# 曾观察到访问限制的 100 个网址评测

本评测回答一个问题：Moli 1.1.11 在不点击、不输入、不滚动、不翻页、不登录、
也不使用 OCR 的前提下，能否从曾观察到登录、验证、反自动化或访问限制的指定网址，
取得首次加载时已经存在的目标内容，而不是验证页、空返回或正文片段。

当前范围是 100 个网址案例、74 个独立网站。`dataset.json` 是可执行任务定义：
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
重建数据集；输出必须与仓库中的 `dataset.json` 字节一致。第二条命令只校验 profile、
数据集、Moli 1.1.11 版本和二进制 SHA-256，不发起 provider 调用。

## 正式执行

```sh
uv run --frozen python scripts/run_certified_benchmark.py risk-control \
  --env-file .env --execute \
  --output "$BROWSER_EVAL_DATA_HOME/evidence/risk-control/<new-run-id>"
```

输出目录必须是新的，并位于数据目录下。正式入口固定为
`scripts/run_certified_benchmark.py risk-control`；直接对执行脚本使用 `--execute` 会被拒绝。
当前 profile 的新正式运行恰有 100 个逻辑结果，且不自动重试，并为每次调用在 Git 外保留完整、
哈希绑定的 debug stderr。当前发布证据另有 3 个 DNS 失败后的用户授权补调，因此保留 103 次
物理调用；每例固定采用最后一次响应，全部调用耗时均计入，不能择优。运行前后 Moli 身份必须
一致；100 份声明的参考文本也会在调用
前后逐一核验，缺失、内容变化或范围不完整都会中止认证。生成报告前还会校验
`certification.json` 对 profile、dataset、results、manifest、execution、参考证据核验回执
和完整日志的绑定。

本目录不包含 Moli 实测输出、provider 原始工件、debug 日志或凭据。当前报告使用的外部证据
相对位置、数据/结果/修正回执哈希、报告目录、语言和原发布站点都由 `current-report.json`
绑定；`make risk-control-report` 可在新会话中直接重建。PR 中的评分修正只复用既有 103 次
物理调用，离线重评分没有新增 provider 调用。

## 重建与原站点更新

```sh
make risk-control-report
make risk-control-package ARCHIVE=/tmp/risk-control-report.zip
make risk-control-publish ARCHIVE=/tmp/risk-control-report.zip \
  RECEIPT=/tmp/risk-control-publication.json
```

重建和打包不调用 Moli。默认只生成中文；显式要求英文时使用
`make risk-control-report ENGLISH=1`。发布只更新 `current-report.json` 绑定的原站点，
校验预期远端版本，下载完整发布包核对后才更新仓库中的版本绑定；不创建第二个站点。
发布凭据按目标主机从 Artifact Site 授权文件读取，不读取评测用的 `.env`。

新会话可直接使用这些入口，但换机器时必须恢复 profile 绑定的 Git 外证据和独立参考文件；
只有 Git 仓库不能重建实测报告。历史结果没有正式运行入口新增的调用前后认证，不能补造。
当前历史修正版通过独立的严格校验：核对评分修正链、100 个逻辑结果、103 次物理调用、
完整日志、最后一次响应、累计耗时及100份参考文件的当前哈希。这个当前文件校验不等于
证明历史调用前后文件未变化；新的正式实测仍必须走认证入口。

“全页”指首次加载范围内已声明的稳定文字、正文与列表规则，不是声称已证明网页每一个
未登记字符或交互后的内容均被取得。导航等文字只要已登记，就与正文文字一样是必需项。
