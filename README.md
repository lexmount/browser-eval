# Web Fetch 评测

140 个任务 × 11 种方法：网页正文 100 个，文档、图片、视频、真实网站各 10 个。

## 唯一当前入口

```text
evaluation/                 # 当前任务、1540 条结果、原始输出和来源证据
browser_harness_eval/       # 接口适配、评分、当前报告生成器
scripts/build_report.py     # 唯一正式报告构建入口
reports/webfetch/           # 唯一当前报告，可重复生成
research/webfetch-adopters.json  # 开源采用检索证据
```

```bash
python3 scripts/build_report.py
python3 scripts/check_report.py reports/webfetch
python3 scripts/check_report_links.py reports/webfetch
python3 -m unittest discover -s tests
node tests/test_report_ui.mjs
```

布局校验另执行 `python3 scripts/check_report_revision.py`，需要 BeautifulSoup4。
构建不调用供应商、不修改当前测量成绩。输出校验失败会中止；重建会替换整个已标记的生成目录，避免旧页面残留。

## 重新实测

先按 `configs/providers.example.json` 和 `configs/providers.resources.json` 配置环境变量，并通过 `python3 -m browser_harness_eval validate --env-file /path/to/.env --providers <方法名>` 校验凭据。原生实现需要对应仓库和 Python/Node 依赖；开源运行环境可通过 `scripts/bootstrap_oss_baselines.sh` 准备。

```bash
python3 scripts/run_benchmark.py --env-file /path/to/.env --output runs/webfetch
```

使用具备当前全部解析依赖的 Python 环境执行。此命令会真实调用 11 种方法，可能消耗供应商配额。不同方法并发，同一方法先完成网页组，再测其他组；正文与资源配置分开。已有输出目录会被拒绝覆盖。新结果核验后才能替换 `evaluation/`，不自动替换已发布成绩。

独立源页检查：`python3 scripts/audit_sources.py`，需要 requests、BeautifulSoup4 和文档解析依赖。HTTP 失败只触发进一步检查，不能据此自动删除任务。真实网站的动态列表按字段结构评分，不耦合当前标题、排序或价格。

## 发布

```bash
python3 scripts/package_artifact_hub.py
python3 scripts/publish_artifact_hub.py --update --expected-version <当前服务端版本标识>
```

打包输出 `publish/webfetch-sites.zip`，将完整证据去重压缩并按需加载。使用既有 Artifact Hub 站点和分享地址；服务端版本标识仅用于并发更新保护，不是报告代码版本。发布令牌和管理回执不得提交或放入站点。

## 内容规范

只维护当前实现，不建带版本号的报告脚本、兼容跳转或旧报告依赖链。正式报告只包含任务、参考范围、评测方法、结果、接口说明与证据；协助操作和工作日志只在会话中沟通。知乎、大众点评已有快照继续保留，访问受限不等于网站无效。

更多约束见 [AGENTS.md](AGENTS.md)、[架构](docs/architecture.md)、[指标](docs/metrics.md) 和 [接口契约](docs/provider-contract.md)。
