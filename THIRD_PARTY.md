# 第三方内容与许可边界

根目录 MIT 许可适用于项目自有代码和文档，不替代第三方源码、网页、图片、文档或数据集的许可。各文件原有版权声明和子目录许可继续有效。

| 内容 | 来源与绑定 | 分发边界 |
| --- | --- | --- |
| WebArena-Verified | `pyproject.toml` 和 `uv.lock` 固定源码仓库与提交 | 通过依赖安装；保留上游许可和归属 |
| WebMainBench / WCXB | 对应 `evaluation/` 配置固定母集、评分器与哈希 | 按上游授权单独获取；代码许可不授予源网页权利 |
| 独立获取的网页、PDF、媒体和参考快照 | 任务的来源注释、`evaluation/source-inputs.json` 及各轨道外部证据绑定 | 快照保存在私有数据目录，按清单从获准使用的副本导入；公开可访问不等于允许重新分发 |
| 人工轨迹与任务定义 | 轨迹轨道的配置、来源说明和资格证据 | 保留原始来源及使用条件，区分定义、录像与测量输出 |
| 仓库编辑技能 | `.agents/skills/` 内各自的来源和声明 | 保留相应声明；不作为浏览器安装包的运行依赖 |
| 合成离线示例 | `harness/web_fetch/data/` | 项目自有测试输入，不包含第三方抓取内容 |
| 资源采集开销校准 | `harness/resource_calibration.py`，源自 [Lexbench 固定提交](https://github.com/lexmount/Lexbench-Headless-Browser/blob/fd8d64c7623d46cd165607f216a0e28967d03040/runner/resources.py)，来源文件哈希记录在模块中 | Apache-2.0；保留[完整许可](harness/licenses/Lexbench-Apache-2.0.txt)及归属，随源码和安装包分发 |

运行结果、登录态、供应商原始响应和内部审查材料不属于源码分发内容。公开证据包还需经过隐私检查和该轨道的发布门禁。只有来源链接或 SHA-256 仍不足以证明授权；缺少明确依据的素材不能纳入对外源码包。
