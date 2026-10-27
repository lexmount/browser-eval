<div align="center">

<img src="assets/overview.png" alt="Browser Eval — 相同任务，清晰比较。" width="100%">

[English](../README.md) · **简体中文**

[![CI](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml/badge.svg)](https://github.com/lexmount/browser-eval/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/code-Apache--2.0-51735b)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-356a91)](../pyproject.toml)

[特性](#特性) · [快速开始](#快速开始) · [评测流程](#评测流程) · [报告](#报告) · [版本](#版本) · [相关仓库](#相关仓库) · [参与贡献](../CONTRIBUTING.md)

</div>

运行 benchmark 对比，生成带逐题证据的报告，后续可重建或分享。

## 特性

| 特性 | 能做什么 |
| --- | --- |
| 从评测到报告 | 配置任务和评测对象后，一条命令完成评测并生成 HTML 报告。 |
| 接入多种 benchmark | 使用已接入的 benchmark，也可提供自己的任务，按所选 benchmark 的规则评分。[接入指南](benchmarks.md) |
| 扩展评测对象 | 比较本地浏览器、在线 API、内容提取工具和 Web Agent，通过已有[适配接口](provider-contract.md)接入新的参与方。 |
| 查看逐题证据 | 从汇总指标查看对应的逐题响应、操作轨迹和失败日志。 |
| 复用已有结果 | 从保存的输出重建报告、重算评分，无需重复调用参与方。 |
| 比较质量、性能和成本 | 同时查看任务通过情况、延迟、资源使用和已记录的计费数据。 |
| 追溯实验配置 | 保留任务输入、benchmark 和软件版本、评分代码及执行配置。 |
| 分享报告并控制权限 | 通过 Artifact Site 发布报告，并设置谁能访问。[创建与分享报告](#报告) |

## 评测流程

按任务选择评测流程，各指南提供任务约定、环境配置和可运行示例。

| 流程 | 测什么 | 对应评测集与任务集 | 使用指南 |
| --- | --- | --- | --- |
| Web Fetch | 获取任务要求的网页正文、记录、资源或文件 | [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch)、[WebMainBench](https://github.com/opendatalab/WebMainBench)、[WCXB](https://github.com/Murrough-Foley/web-content-extraction-benchmark) | [本地示例](../examples/web_fetch/README.md) · [完整指南](../harness/web_fetch/README.md) |
| Web Search | 比较搜索结果、相关性、来源检索、延迟与计费 | [LexBench Web Search](https://github.com/lexmount/lexbench-websearch)：场景问题，以及用于检索评测的 SimpleQA Verified 改编任务 | [本地示例](../examples/web_search/README.md) · [完整指南](../harness/web_search/README.md) |
| 轨迹重放 | 比较不同浏览器能否完成同一组录制动作 | [仓库自建固定动作任务集](../examples/trajectory_replay/README.md)，改编自 [Lexbench-Headless-Browser 场景设计](https://github.com/lexmount/Lexbench-Headless-Browser/tree/3f1580a20922fc3f339b5fc279d9bbdb06d5d194/tasks/scenarios)；[经过资格验证的 WebArena 人工录制轨迹](../evaluation/browser-use-public/webarena-human-suite.json)，由 [WebArena-Verified 评测器](https://github.com/lexmount/webarena-verified/tree/lex-main)验收 | [自建任务集](../examples/trajectory_replay/README.md) · [WebArena 重放](../examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | 让 Agent 使用不同浏览器完成任务 | [WebArena-Verified](https://github.com/ServiceNow/webarena-verified)，使用[固定版本的评测器分支](https://github.com/lexmount/webarena-verified/tree/lex-main)和[明确选定的任务](../evaluation/browser-use/webarena-verified-pilot.json) | [CowAgent + WebArena](../examples/browser_use/README.md) |

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

创建和分享报告，请打开 Artifact Site（[中国大陆](https://yours.lexmount.cn/artifact-site) · [全球](https://yours.lexmount.com/artifact-site)），安装网站提供的 skill，按引导创建、发布报告，并设置分享权限。

报告包含结果对比、逐题结果、完整响应和失败证据。运行结果默认保存在 `~/.browser-eval/runs/`，也可用 `--output /absolute/path/new-run` 指定新目录。

从保存的证据重建报告，无需重新调用浏览器或服务：

```sh
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

默认生成英文；运行或重建时加 `--language zh` 可选中文。重建时不传该选项，会沿用已保存的语言。

新报告记录 Browser Eval 构建版本和选定的评测版本，并保留代码与输入哈希。结果比较规则见[版本说明](../docs/benchmarks.md#benchmark-editions)，历史数据见[参考输入](../docs/reference-inputs.md)。

本地构建与正式评测发布要求见[报告指南](report-hosting.md)。

## 版本

已发布版本、变更和依赖见[发布记录](https://github.com/lexmount/browser-eval/releases)。升版规则与报告版本绑定见[版本说明](releases.md)。

## 相关仓库

| 仓库 | 用途 |
| --- | --- |
| [LexBench Web Fetch](https://github.com/lexmount/lexbench-webfetch) | Web Fetch 题目、验收评分和冻结输入绑定 |
| [LexBench Web Search](https://github.com/lexmount/lexbench-websearch) | 搜索问题、来源参考和检索评分 |
| [Moli](https://github.com/lexmount/moli) | 与其他参与者按相同任务规则评测的浏览器 |
| [WebFetch CLI](https://github.com/lexmount/webfetch-cli) | Lexmount WebFetch 服务的命令行客户端 |
| Artifact Site（[中国大陆](https://yours.lexmount.cn/artifact-site) · [全球](https://yours.lexmount.com/artifact-site)） | 报告创建、分享和权限控制 |
| [Lexbench-Headless-Browser](https://github.com/lexmount/Lexbench-Headless-Browser) | 自建轨迹的场景设计来源；另有独立的协议、自动化驱动和 Web 平台评测 |
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

项目引用：[CITATION.cff](../CITATION.cff)。

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  url = {https://github.com/lexmount/browser-eval}
}
```
