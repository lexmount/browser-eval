# WebArena 固定轨迹回放：环境与复现

这是一套 RPA 式固定轨迹回放，不是 Agent browser-use。正式运行不调用 Agent、模型或
LLM judge；两个浏览器重放相同的 WebArena 公开人工轨迹，再由固定 evaluator 验收最终
回答、服务状态和 HAR。

## 代码与依赖

- `harness/environments/webarena/`：Docker 和 Rainbond 站点生命周期、初始化与机器可读回执；
  `deploy/webarena/` 保存部署配置和数据挂载契约。
- `harness/trajectory_replay/public_webarena/`：公开轨迹清单、离线修复记录、资格门禁、Chromium/Moli 镜像、动作
  重放、HAR、evaluator、一次失败重试、首次/最终成功统计、证据审计和独立报告发布。

WebArena-Verified 1.2.3 作为独立依赖提供任务和评分器；全部官方镜像只在
`evaluation/browser-use-public/webarena-environment-images.json` 中锁定。

为新实验解析官方镜像来源时，运行 `uv run --frozen python scripts/lock_webarena_images.py --output /absolute/path/new-images.json`。命令复用环境配置的站点定义，核对注册表摘要与清单字节，只创建新文件。历史实验继续使用其配置绑定的镜像锁，重新解析 `latest` 不构成历史镜像复现。
本仓库提供在新机器上重新执行协议所需的代码，但不会仅凭 Git 还原旧运行的逐字节证据：
原始截图、HAR、attempts、manifest 和 summary 按仓库规则保存在 Git 外。Git 中的
`evaluation/browser-use-public/current-report.json` 保存当前公开结果与外部证据哈希，
用于证明报告绑定，而不是替代一次新运行。

公开人工轨迹文件含 179 个候选任务；其中 30 个标为 `eligible`，其余 149 个为
`skipped`。正式运行器只执行 `eligible` 任务。不要把候选文件的总数当成本轮分母。

## 主机和架构

使用 Linux x86-64 Docker 主机即可，Intel 和 AMD 均可。当前正式 30 题只使用
`linux/amd64` 站点；Wikipedia 的 `linux/arm64` 任务在运行前按架构规则排除，不启用
跨架构模拟。浏览器测量 daemon 必须提供 cgroup v2 CPU quota；站点 daemon 可相同，
也可通过外部运行配置指定另一条 Unix socket。

## 从空机器复现

1. 检出并固定本仓库提交，执行 `make setup` 和 `make ci`。运行
   `uv run --frozen python scripts/verify_webarena_env_contract.py`，检查部署和评测
   配置使用同一份镜像锁以及一致的端口、凭据变量名。
2. 复制 `deploy/webarena/runtime.example.json` 到 Git 外，填写可访问的 `hostname`，
   将 `image_lock` 设置为本仓库镜像锁的绝对路径。使用
   `uv run --frozen python scripts/manage_webarena_environment.py verify --config <配置> --site reddit`
   校验环境，将 `verify` 换为 `start` 或 `reset` 启动或重建站点，通过 `--receipt <路径>` 保存回执。
   仅在需要 Map 的任务范围内准备地图数据；`setup --config <配置> --site map --data-dir <目录>`
   会先核对三份归档的 SHA-256，再运行官方初始化。当前 Rainbond 部署不包含 Map。
   账号只通过环境变量提供。
3. 在本仓库复制 `examples/trajectory_replay/public_webarena/webarena.config.example.json`，使用同一组端口、
   镜像 digest、网站地址与凭据变量名；运行环境 smoke，确认 CDP、登录、HAR 与站点 origin。
   示例默认共用本机 Docker daemon，不假定存在某个用户的 rootless socket。若使用两条
   daemon，须在本地配置中显式填写站点与浏览器 socket。
4. 用冻结清单执行配对正式运行：

```text
uv run --frozen python -m harness.trajectory_replay.public_webarena.cli \
  --suite evaluation/browser-use-public/webarena-human-suite.json \
  --webarena-config <runtime-config> --env-file .env \
  --browser-docker-host unix:///var/run/docker.sock \
  --engines chromium,moli --failure-retries 1 \
  --output <evidence>/formal/webarena-public-trajectories/run \
  --reports <evidence>/formal/webarena-public-trajectories/reports
```

5. 运行 `examples/trajectory_replay/public_webarena/verify_metrics.py` 和报告打包器。任何缺失回答、动作、终止
   原因、评分，或进入浏览器阶段后缺失截图、最终页面、HAR，都按失败处理。

正式运行前必须核对 WebArena-Verified 版本和任务涉及的站点镜像 digest。
这项检查不允许用 mutable tag、跨架构模拟或手工修改数据库替代。

执行器支持 Docker 容器重建和带回执的 Rainbond 独立槽位重建。每个并发执行者必须
独占一整个有状态槽位；只读 Wikipedia 可共享。Rainbond 使用方法见
[环境示例](../examples/trajectory_replay/public_webarena/README.md)。HTTP 并发测试只证明
网页可访问；正式运行仍须生成与本次重置、镜像、初态和就绪检查绑定的回执。
共享 Kiwix 的 amd64 部署与官方 arm64 镜像身份不同，不能直接沿用官方镜像的验证回执。
