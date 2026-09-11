# WebArena 固定轨迹回放：双仓库复现契约

这是一套 RPA 式固定轨迹回放，不是 Agent browser-use。正式运行不调用 Agent、模型或
LLM judge；两个浏览器重放相同的 WebArena 公开人工轨迹，再由固定 evaluator 验收最终
回答、服务状态和 HAR。

## 两个仓库各自负责什么

- `lexmount/webarena-env#1`：固定 WebArena-Verified 1.2.3、站点镜像 digest、原生架构、
  端口、启动/重置和机器可读回执。
- 本仓库 PR：固定公开轨迹清单、离线修复记录、资格门禁、Chromium/Moli 镜像、动作
  重放、HAR、evaluator、一次失败重试、首次/最终成功统计、证据审计和独立报告发布。

两者共同足以在新机器上重新执行同一协议，但不会仅凭 Git 还原旧运行的逐字节证据：
原始截图、HAR、attempts、manifest 和 summary 按仓库规则保存在 Git 外。Git 中的
`evaluation/browser-use-public/current-report.json` 保存当前公开结果与外部证据哈希，
用于证明报告绑定，而不是替代一次新运行。

## 主机和架构

使用 Linux x86-64 Docker 主机即可，Intel 和 AMD 均可。当前正式 30 题只使用
`linux/amd64` 站点；Wikipedia 的 `linux/arm64` 任务在运行前按架构规则排除，不启用
跨架构模拟。浏览器测量 daemon 必须提供 cgroup v2 CPU quota；站点 daemon 可相同，
也可通过外部运行配置指定另一条 Unix socket。

## 从空机器复现

1. 检出并固定两个 PR 的合并提交；两个仓库都执行 `uv sync --frozen` 和各自的检查。
   再在本仓库运行
   `uv run --frozen python scripts/verify_webarena_env_contract.py --environment-repo <webarena-env>`，
   确认上游版本、六个站点、镜像 digest 和架构完全一致。
2. 在 `webarena-env` 复制 `config/runtime.example.json`，填写浏览器容器可访问的主机名，
   运行 `webarena-env verify/start` 并保留回执。账号只通过环境变量提供。
3. 在本仓库复制 `examples/browser_use/webarena.config.example.json`，使用同一组端口、
   镜像 digest、主机名与凭据变量名；运行环境 smoke，确认 CDP、登录、HAR 与站点 origin。
4. 用冻结清单执行配对正式运行：

```text
uv run --frozen python -m harness.browser_use.cli \
  --suite evaluation/browser-use-public/webarena-human-suite.json \
  --webarena-config <runtime-config> --env-file .env \
  --browser-docker-host unix:///var/run/docker.sock \
  --engines chromium,moli --failure-retries 1 \
  --output <evidence>/formal/webarena-public-trajectories/run \
  --reports <evidence>/formal/webarena-public-trajectories/reports
```

5. 运行 `examples/browser_use/verify_metrics.py` 和报告打包器。任何缺失回答、动作、终止
   原因、评分，或进入浏览器阶段后缺失截图、最终页面、HAR，都按失败处理。

正式运行前必须核对两个仓库的 WebArena-Verified 版本和六个站点镜像 digest 完全一致。
这项检查不允许用 mutable tag、跨架构模拟或手工修改数据库替代。
