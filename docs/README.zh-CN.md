# Browser Eval

[English](../README.md) | **简体中文**

[Moli 浏览器](https://github.com/lexmount/moli)配套的可复现评测工具，也支持其他浏览器、网页抓取服务和 Agent 工具。比较使用明确的任务、统一验收规则和可查看的原始证据。

## 可以评测什么

| 轨道 | 输入与评测内容 | 输出 | 说明 |
| --- | --- | --- | --- |
| Web Fetch | 实时 URL 或冻结 HTML；正文覆盖、结构化记录、资源发现、文件交付 | 任务结果、内容指标、延迟、原始响应、HTML 报告；配置容器采集时还包括资源消耗 | [Web Fetch](../harness/web_fetch/README.md) |
| Browser Trajectory Replay（轨迹重放） | 固定的 BrowserTool 操作轨迹；比较任务完成情况和资源消耗 | 操作响应、评估器结果、耗时、资源测量和报告 | [本地任务集](../examples/trajectory_replay/README.md) · [WebArena 人工轨迹](../examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | 同一 Agent、模型和工具契约下自主完成任务；使用官方 WebArena-Verified 评估器 | 任务成绩、模型用量、操作轨迹、日志和报告 | [Agent 运行说明](../examples/browser_use/README.md) |

各轨道分别管理任务、分母和发布目标，通过率不能合成一个“浏览器总分”。HTTP 抓取只是基线，不执行浏览器自动化；固定轨迹重放不评测自主规划能力。

### 浏览器与评测方法

| 轨道 | 已实现的参与方 | 准备条件 |
| --- | --- | --- |
| Web Fetch：本地浏览器 | Moli、Chromium、Lightpanda、Obscura | 安装所选浏览器；Chromium 还需要 Playwright 和 Node.js 转换依赖 |
| Web Fetch：服务 | Lexmount、Firecrawl、Tavily Extract、Exa Contents、Cloudflare Markdown/Content；通过 Cloudflare 调用托管 Kitesurf | 供应商账户；凭据写入仓库 `.env` |
| Web Fetch：Agent 工具与提取库 | CowAgent、OpenClaw、DeepSeek Harness、LangChain WebBaseLoader、Crawl4AI | 按适配器要求准备上游源码、运行环境或 `oss-eval` 依赖 |
| 轨迹重放 / Browser Use | 当前浏览器注册表中的 Moli、Chromium | Docker/Linux 环境；Browser Use 还需要 CowAgent 和模型账户 |
| 对照方法 | 普通 HTTP、离线 fixture | 基础 Python 安装 |

实现了适配器不代表它支持全部任务类型和 benchmark，运行前要核对输出契约和依赖。见[依赖来源与固定版本](../docs/dependencies.md)、[完整方法配置](../configs/providers.example.json)、[原生浏览器路径](../harness/web_fetch/HEADLESS.md)和[适配器契约](../docs/provider-contract.md)。完整配置保留历史版本固定值；新建本地实验可使用下面的入门配置。

### Benchmark 与任务集

| 任务集 | 评测内容 | 可用条件与入口 |
| --- | --- | --- |
| 离线 fixture / 本地文章 | 检查运行、任务选择、评分和证据，不提供性能排名 | 随仓库提供，命令见下文 |
| 实时 Web Fetch 任务集 | 普通网页、动态内容、文档、图片/视频、热门网站、访问限制 | [轨道说明](../harness/web_fetch/README.md)；完整历史参考需要获准使用的[外部输入](../docs/reference-inputs.md) |
| WebMainBench / WCXB | 独立绑定的冻结 HTML 与参考内容上的提取质量 | [冻结 HTML 说明](../harness/web_fetch/WEBMAINBENCH.md)；按原数据许可获取输入，并使用对应认证流程 |
| 项目维护的轨迹任务集 | 在指定公开网页上执行相同固定操作 | [轨迹运行说明](../examples/trajectory_replay/README.md)；需要 Docker 和网站访问 |
| WebArena 人工轨迹重放 | 自建网站上的已通过资格检查的固定轨迹与官方任务评估 | [复现说明](../docs/webarena-fixed-trajectory-reproduction.md)；需要外部数据和较多部署资源 |
| WebArena-Verified Browser Use | CowAgent 自主完成任务，由官方评估器判定 | [Browser Use 说明](../examples/browser_use/README.md)；任务范围和环境分别声明 |

通用 CLI 通过数据集文件选择 Web Fetch 任务，不接受 benchmark 名称作为快捷选项。轨迹重放和自主任务使用各自入口，不能作为 `browser-harness-eval run` 的选项。

## 先运行离线示例

克隆仓库后，在仓库根目录执行命令。安装 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.11 或更新版本。基础 CLI 在 Python 3.11 至 3.14 上验证；`.python-version` 为仓库开发和冻结实验选择 3.11。选择 3.12 时，在 `uv sync --frozen` 和后续 `uv run --frozen` 命令中都加上 `--python 3.12`。可选依赖和历史方法另有运行环境约束，完整开发与复现环境仍固定为 3.11。

```bash
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

首次安装需要联网。示例不访问网站，不使用 Docker 或 API Key，包含一个预设成功和一个预设失败。打开终端打印的报告绝对路径即可查看。加上 `--fail-on-failure` 后，该示例返回退出码 2。

## 用真实适配器抓取本地页面

示例提供一篇仓库自有文章，检查完整正文，无需外部数据或账户。在单独的终端启动页面服务，并保持运行：

```bash
uv run --frozen python -m http.server 8765 --bind 127.0.0.1 \
  --directory examples/web_fetch/site
```

在另一个终端进入仓库根目录，验证并运行 HTTP 基线：

```bash
uv run --frozen browser-harness-eval validate \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http
uv run --frozen browser-harness-eval run \
  --dataset examples/web_fetch/dataset.json \
  --provider-config configs/providers.quickstart.json --providers http --delay 0
```

完整文章应通过。`http` 解析响应文本，不执行 JavaScript。运行 Moli 时，从 [Moli 发行版](https://github.com/lexmount/moli/releases)安装，将 `moli` 加入 PATH，或将 `MOLI_BINARY` 设为绝对路径；然后把两条命令中的 `--providers http` 改为 `--providers moli`。可用 `--providers http,moli,chromium` 比较多个已安装方法；[入门配置](../configs/providers.quickstart.json)也提供 Lightpanda 和 Obscura。程序只初始化选中的方法。这篇静态文章用于检查接入，不用于比较动态网页能力。

Chromium 需要额外安装：

```bash
uv sync --frozen --extra headless-eval
uv run --frozen playwright install chromium
npm ci
```

仓库验证使用 Node.js 22。`CHROMIUM_BINARY` 可指定浏览器路径；未指定时，适配器可使用 Playwright 安装的 Chromium。其他路径变量为 `LIGHTPANDA_BINARY`、`OBSCURA_BINARY`。入门配置记录实际运行版本，不强制历史固定值；正式比较必须在调用前声明并核对版本。本地 HTTP 访问是示例的设计要求，服务仅绑定回环地址。

### 查看与重建输出

每次运行打印报告绝对路径，报告位于本次运行的证据目录内。默认输出在 `~/.browser-eval/runs/`，可用 `BROWSER_EVAL_DATA_HOME` 改数据根目录，或用 `--output /absolute/path/new-run` 指定新目录。已有证据目录会被拒绝。

| 文件或目录 | 内容 |
| --- | --- |
| `report/index.html` | 汇总、任务列表、结果与证据页面入口 |
| `results.jsonl`、`summary.json` | 可供程序读取的逐次评分和汇总 |
| `manifest.json`、`dataset.json` | 运行配置、参与方身份和所选任务 |
| `artifacts/` | 保存的供应商输出及相关证据 |

从已有证据重建 HTML，不重新调用方法：

```bash
uv run --frozen browser-harness-eval report /absolute/path/to/run
```

报告构建默认中文。支持英文的轨道构建器通过显式选项生成英文；未生成时，英文入口显示说明并提供返回链接。仓库文档以英文为主，另提供中文 README，冻结证据保留原文。

## 选择自己的任务和比较范围

复制本地数据集和方法配置，声明独立参考、验收规则和各任务 URL，并显式传入两个文件：

```bash
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers method-a,method-b \
  --case-ids case-a,case-b --task-kind body \
  --output /absolute/path/new-evidence
```

`--providers` 使用配置中的键名，不是任意浏览器可执行文件名。`--case-ids` 与 `--task-kind` 取交集；其他任务类型为 `structure`、`resource`、`file`。报告只包含本次所选任务和方法。解释结果或新增方法前，请阅读[指标](../docs/metrics.md)和[适配器契约](../docs/provider-contract.md)。

普通 CLI 运行生成诊断证据。正式 Web Fetch 批次通过 `scripts/run_certified_benchmark.py` 和对应配置冻结并复核参考；部分完成或未认证的运行不能变成正式 benchmark 发布。模型和远程接口可能产生费用。历史发布配置是复现依据，不是自己的新实验可直接使用的发布目标。

按需安装额外依赖：`headless-eval` 用于本地 Chromium、PDF 和容器采集；`webmainbench-eval` 用于 WebMainBench 评分；`browser-use-eval` 用于 WebArena 和 Agent；`oss-eval` 用于开源提取库。`browser-use-eval` 需要 Git，安装固定提交的 WebArena-Verified fork，不能用同版本的注册表包替代。浏览器、Docker、网站部署和账户分别准备。

## 分享报告

本地 HTML 报告无需托管账户，也不会自动上传。[Artifact Site](https://artifact-site.lexmount.cn)是可选托管服务：在网站登录或注册，授权 CLI，把选定报告目录发布到自己的账户。命令、访问权限和正式认证报告的发布流程见[托管说明](../docs/report-hosting.md)。项目维护的报告各自绑定站点、主机和预期版本，新报告不能复用那些目标。

## 贡献与复现

欢迎为浏览器适配器、任务、回归测试、评分修正、文档和 README 翻译提交 PR。修改应解决共同问题，并对所有浏览器使用相同验收和证据要求。请阅读[贡献指南](../CONTRIBUTING.md)，或提交带有复现示例的 [issue](https://github.com/lexmount/browser-eval/issues)。Moli 的安装与使用见 [Moli 仓库](https://github.com/lexmount/moli)。

```bash
make setup-test
make ci
```

完整检查使用固定 Python 环境，覆盖 Node.js、原生协议和独立安装；基础兼容性检查也在 Python 3.12、3.13、3.14 上运行公开 CLI。真实容器和账户实验需要各自声明的环境。

Git 保存可复用代码、精简定义、验收规则和证据绑定。完整历史输入保存在外部，测试不会自动下载；按[参考输入说明](../docs/reference-inputs.md)导入获准使用的副本。`make test-reference-inputs` 对缺失或损坏的必需输入直接失败。供应商输出、执行清单、报告正文、截图和发布包不提交 Git。

项目自有代码采用 [MIT 许可](../LICENSE)。第三方数据权利和技能来源见[第三方说明](../THIRD_PARTY.md)，凭据和不可信内容处理见[安全说明](../SECURITY.md)。

## 引用 Browser Eval

在公开作品中使用本仓库的工具、任务定义或评测方法时，请引用仓库，并记录实际使用的 tag 或提交。GitHub 的引用入口读取 [CITATION.cff](../CITATION.cff)。首个版本可这样引用：

```bibtex
@software{browser_eval_2026,
  author = {{Browser Eval contributors}},
  title = {Browser Eval},
  year = {2026},
  version = {0.1.0},
  url = {https://github.com/lexmount/browser-eval}
}
```

使用上游 benchmark 的数据、评估器或方法时，还应分别引用原项目；引用本仓库不能代替上游的署名要求。
