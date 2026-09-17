# WebArena 环境

Docker 和 Rainbond 环境由 `harness/environments/webarena/` 维护。任务和评分器来自
固定版本的 `webarena-verified` 依赖。镜像身份只有一份：
`evaluation/browser-use-public/webarena-environment-images.json`。

`runtime.example.json` 提供本地 Docker 的主机和端口模板；`site-delivery-contract.json`
描述镜像内端口、初始化方式与只读 Map 数据挂载。Map 为可选环境，契约的存在不会触发下载或部署。
有状态站点从镜像内的初始数据库重建，不挂载跨槽位共享的可写数据库目录。
GitLab 在 Rainbond 中需要独立的 2 GiB `/dev/shm` 内存挂载。

复制运行配置到 Git 外并填写 `hostname`，将 `image_lock` 改为镜像锁绝对路径：

```sh
uv run --frozen python scripts/manage_webarena_environment.py verify --config <配置> --site reddit
uv run --frozen python scripts/manage_webarena_environment.py start --config <配置> --site reddit --receipt <回执>
uv run --frozen python scripts/manage_webarena_environment.py reset --config <配置> --site reddit --receipt <回执>
uv run --frozen python scripts/verify_webarena_env_contract.py
```

Rainbond 的独立槽位、租约、并发访问检查和资源回收见
[运行示例](../../examples/trajectory_replay/public_webarena/README.md)。
运行配置、地址、凭据和回执保存在 Git 外；代码和配置模板可公开提交。

Docker 管理代码和 Map 校验契约源自
[`lexmount/webarena-env`](https://github.com/lexmount/webarena-env/tree/daecdf8b46e71ccc70d7cc2fdaf92a83af899624)，
保留其 [MIT 许可证](LICENSE)。
