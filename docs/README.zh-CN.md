<div align="center">

<img src="assets/overview.png" alt="Browser Eval — 相同任务，清晰比较。" width="100%">

[English](../README.md) · **简体中文**

[![CI](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](../pyproject.toml)

[快速开始](#快速开始) · [评测流程](#评测流程) · [报告](#报告) · [版本](#版本) · [相关仓库](#相关仓库) · [参与贡献](../CONTRIBUTING.md)

</div>

Browser Eval 支持 Web Fetch、固定轨迹重放和自主 Browser Use 评测，并提供 Web Search 预览实现。通过共用的适配器、保存的证据和可查阅的报告，比较任务结果、延迟与资源用量。

## 评测流程

主分支包含下列三种浏览器评测流程。Web Search 已在表中链接的开发分支实现，当前主分支的软件包尚未包含它。

| 流程 | 测什么 | 使用指南 |
| --- | --- | --- |
| Web Fetch | 获取任务要求的网页正文、记录、资源或文件 | [本地示例](../examples/web_fetch/README.md) · [完整指南](../harness/web_fetch/README.md) |
| Web Search（预览） | 比较搜索结果、相关性、来源检索、延迟与计费 | [实现与接入 PR #58](https://github.com/lexmount/browser-eval/pull/58) · [搜索评测指南](https://github.com/lexmount/browser-eval/blob/codex/report-release-checks/harness/web_search/README.md) |
| 轨迹重放 | 比较不同浏览器能否完成同一组录制动作 | [自建任务集](../examples/trajectory_replay/README.md) · [WebArena 重放](../examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | 让 Agent 使用不同浏览器完成任务 | [CowAgent + WebArena](../examples/browser_use/README.md) |

Web Fetch 支持本地浏览器、在线服务和提取工具。可用方法见[适配器说明](../docs/provider-contract.md)，安装要求见[依赖说明](../docs/dependencies.md)。轨迹重放和 Browser Use 各有自己的运行入口与环境。

题目和评分规则来自选定的评测集：[LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch)、[WebMainBench](https://github.com/opendatalab/WebMainBench)、[WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) 或 [WebArena-Verified](https://github.com/ServiceNow/webarena-verified)。搜索题目和评分由 [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) 维护。接入方式见[评测集指南](../docs/benchmarks.md)。

## 快速开始

安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，运行离线示例：

```sh
git clone https://github.com/lexmount/browser-eval.git
cd browser-eval
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

打开终端打印的报告路径。示例包含一个预期通过和一个预期失败，无需浏览器、Docker 或 API 密钥。支持 Python 3.11–3.14，仓库开发使用 3.11。

接下来可以[用 HTTP、Moli 或 Chromium 读取本地文章](../examples/web_fetch/README.md)。可选适配器、服务凭据和在线评测命令见上面的各流程指南。

## 报告

报告包含结果对比、逐题结果、完整响应和失败证据。运行结果默认保存在 `~/.browser-eval/runs/`，也可用 `--output /absolute/path/new-run` 指定新目录。

从保存的证据重建报告，无需重新调用浏览器或服务：

```sh
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

默认生成英文；运行或重建时加 `--language zh` 可选中文。重建时不传该选项，会沿用已保存的语言。

新报告记录 Browser Eval 构建版本和选定的评测版本，并保留代码与输入哈希。结果比较规则见[版本说明](../docs/benchmarks.md#benchmark-editions)，历史数据见[参考输入](../docs/reference-inputs.md)。

在线分享时，按[托管指南](../docs/report-hosting.md)将 `report/` 目录发布到 [Artifact Site](https://github.com/lexmount/artifact-site)，并设置访问权限。正式评测发布还需通过对应流程的检查。

## 版本

当前软件包版本是 `0.2.0`。软件标签采用 `v主版本.次版本.补丁版本`，评测集和托管报告各有独立版本。报告记录这三种身份，因此重建报告不会改换题目版本或保存的调用。

兼容规则、发布检查和首个软件发行版的范围见[发布指南](../docs/releases.md)。Web Search 在接入主分支前保持预览状态。稳定版承诺的是文档中的命令、输入约定和历史证据兼容性；浏览器与在线网站的逐题表现仍由评测结果说明。

## 相关仓库

| 仓库 | 用途 |
| --- | --- |
| [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) | Web Fetch 题目、验收评分和冻结输入绑定 |
| [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) | 搜索问题、来源参考和检索评分 |
| [Moli](https://github.com/lexmount/moli) | 与其他参与者按相同任务规则评测的浏览器 |
| [WebFetch CLI](https://github.com/lexmount/webfetch-cli) | Lexmount WebFetch 服务的命令行客户端 |
| [Artifact Site](https://github.com/lexmount/artifact-site) | 报告托管、不可变版本和访问权限 |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | 协议、自动化驱动和 Web 平台评测 |
| [WebMainBench 分支](https://github.com/lexmount/WebMainBench/tree/lex-main) · [上游](https://github.com/opendatalab/WebMainBench) | 冻结 HTML 提取任务与有记录的评分修正 |
| [WebArena-Verified 分支](https://github.com/lexmount/webarena-verified/tree/lex-main) · [上游](https://github.com/ServiceNow/webarena-verified) | Browser Use 和 WebArena 轨迹重放的题目与评测器 |
| [WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | 独立的内容提取评测集 |
| [CowAgent](https://github.com/zhayujie/CowAgent)、[OpenClaw](https://github.com/openclaw/openclaw)、[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Agent 运行和 Web Fetch 工具基线 |

Playwright、提取库和配套运行环境的来源与固定版本见[依赖说明](../docs/dependencies.md)，引用要求和数据权利见[第三方说明](../THIRD_PARTY.md)。引用 Browser Eval 时，也请引用实际使用的评测集与上游数据。

## 参与贡献

代码归属、测试和 PR 规范见 [CONTRIBUTING](../CONTRIBUTING.md)。反馈问题请[提交可复现的示例](https://github.com/lexmount/browser-eval/issues)。

```sh
make setup-test
make ci
```

[代码结构](../docs/architecture.md) · [安全](../SECURITY.md) · [第三方内容](../THIRD_PARTY.md) · [Apache 2.0](../LICENSE)

## 引用 Browser Eval

使用以下引用或 [CITATION.cff](../CITATION.cff)，并注明实际使用的提交。

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  version = {0.2.0},
  url = {https://github.com/lexmount/browser-eval}
}
```
