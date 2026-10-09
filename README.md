# Browser Eval

Moli 浏览器配套的可复现评测工具，也支持其他浏览器和网页服务。任务、验收规则、运行配置与原始证据分别保存；浏览器适配器负责执行，共享评分器负责判定。

| 轨道 | 要回答的问题 | 运行与复现 |
| --- | --- | --- |
| Web Fetch | 给定 URL 或冻结页面，能否交付所需正文、结构化内容或文件？ | [Web Fetch](harness/web_fetch/README.md) · [冻结 HTML 对比](harness/web_fetch/HEADLESS.md) |
| Browser Trajectory Replay（轨迹重放） | 同一组预先固定的操作，在不同浏览器上是否完成任务，耗费多少资源？ | [本地示例](examples/trajectory_replay/README.md) · [WebArena 人工轨迹](examples/trajectory_replay/public_webarena/README.md) |
| Browser Use | 同一 Agent、模型与工具契约下，不同浏览器能否自主完成任务？ | [运行说明](examples/browser_use/README.md) |

三个轨道分别管理任务、分母和发布目标。它们的通过率不能合并为一个“浏览器总分”。正式实验须使用对应轨道的固定配置；下面的离线示例只检查工具是否可用。

## 先跑离线示例

需要 Python 3.11 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。首次安装依赖需要联网；示例运行不访问网站、不使用 Docker 或 API Key。

```bash
uv sync --frozen
uv run --frozen browser-harness-eval validate
uv run --frozen browser-harness-eval run --delay 0
```

示例包含一个预设成功和一个预设失败。终端会打印报告的绝对路径；在浏览器中打开即可查看任务、结果、参考内容、实际输出和错误。默认输出位于 `~/.browser-eval/runs/`，可用 `BROWSER_EVAL_DATA_HOME` 更改数据根目录。加 `--fail-on-failure` 时，这个示例会返回退出码 2，便于验证调用方的失败处理。

`run` 和 `validate` 默认使用随安装包提供的合成数据。使用自己的任务和方法时，显式指定两个配置：

```bash
uv run --frozen browser-harness-eval validate \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers my-browser
uv run --frozen browser-harness-eval run \
  --dataset /absolute/path/dataset.json \
  --provider-config /absolute/path/providers.json --providers my-browser \
  --output /absolute/path/new-evidence
```

普通 `run` 产生诊断证据。正式 Web Fetch 调用必须通过 `scripts/run_certified_benchmark.py` 冻结并复核参考；不完整批次不能用于发布。模型、远程接口和真实网站运行可能产生费用，运行要求见各轨道说明。

用 `--task-kind file` 可检查文件交付；也可选择 `body`、`structure` 或 `resource`。`--case-ids case-a,case-b` 选择指定任务，与任务类型同时使用时取交集。报告只统计本次选择的任务和方法，并标明诊断用途。

## 安装所需能力

基础安装只包含离线示例和基础 Web Fetch 所需依赖。按实验安装额外依赖：

```bash
uv sync --frozen --extra headless-eval       # 本地浏览器、PDF
uv sync --frozen --extra webmainbench-eval   # 官方 WebMainBench 评分
uv sync --frozen --extra browser-use-eval    # WebArena 与 Agent
uv sync --frozen --extra oss-eval            # 开源正文提取库
```

浏览器二进制、Docker、网站部署、模型和供应商账户独立准备，不随 Python 安装包分发。历史实验固定的二进制、Python 补丁版本和源码提交只用于复现对应实验，不应直接当作新实验配置。

`browser-use-eval` 通过 Git 安装固定提交的 WebArena-Verified fork，需要 Git 和网络连接。源码锁文件和安装包使用同一来源；同版本的注册表包不能替代该评估器。

## 任务、方法与证据

- 新增方法：实现现有 Provider 接口并注册类型，保留统一结果结构与评分规则。见 [适配器契约](docs/provider-contract.md)。
- 新增任务：声明输入、独立参考、验收规则和覆盖计划，为正确答案和应拒绝答案分别提供测试。见 [指标](docs/metrics.md)。
- 查看与构建报告：使用显式证据目录，不重新调用供应商；报告构建验证哈希、调用覆盖及所需审查。见 [架构](docs/architecture.md)。
- 发布配置：当前 Web Fetch 由 [配置文件](evaluation/current-report.json) 绑定；完整 WebMainBench、WCXB、Browser Use 和轨迹重放各有独立配置。发布站点和预期版本以这些文件为准。

生成报告默认只有中文。英文报告需要显式启用；未生成时，英文入口显示说明并提供返回链接。

## 开发与贡献

```bash
make setup-test   # 完整测试依赖和 Node 依赖
make ci           # 全量 Python 测试、界面脚本和离线示例
```

CI 还构建安装包，并在仓库目录外验证运行。真实容器和在线端点测试需相应环境，不能用离线测试替代实验复现。详细要求见 [贡献指南](CONTRIBUTING.md) 和 [安全说明](SECURITY.md)。

历史 Web Fetch、热门网站、动态内容和访问限制任务集的完整参考输入单独保存在私有数据目录，由各自的 `source-inputs.json` 绑定；对应的 `dataset-catalog.json` 保留案例和定义哈希。完整输入不随源码或安装包分发，也不会在测试时自动下载。已有获准使用的副本时，按 [参考输入说明](docs/reference-inputs.md) 导入；`make test-reference-inputs` 会校验全部清单并运行测试，缺少任一套输入直接失败。

仓库保存可复用代码、精简任务定义、验收合同与证据绑定。供应商响应、运行清单、报告正文、截图及发布包保存在外部数据目录，不提交 Git。第三方源页和数据集的授权独立于代码许可，见 [第三方内容说明](THIRD_PARTY.md)。

项目自有代码采用 [MIT 许可](LICENSE)。各子目录已有的第三方声明继续有效。
