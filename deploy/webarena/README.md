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

## Rainbond 共享 Map

Map 独立于四个可写站点的槽位部署，单实例运行；更新时先停止 Map，再启动，
避免滚动更新的两个进程并发打开同一 PostgreSQL 数据目录。地图查询共享固定数据，
但 PostgreSQL、网站数据库和瓦片缓存需要可写目录，不能整卷挂成只读。

此入口使用 `--query-only`，保留完整查询数据库、瓦片和三种路线数据，省去
Nominatim 的 `flatnode.file` 导入／增量更新缓存。评测使用固定快照，不运行地图
数据更新。该文件仍包含在完整归档的 SHA-256 校验中，但不写入运行卷；原始
归档用于完整恢复。此配置不支持增量导入，切换数据模式必须使用新卷。
依据：[Nominatim 导入与冻结说明](https://nominatim.org/release-docs/4.5/admin/Import/#dropping-data-required-for-dynamic-updates)。

使用镜像锁中的 Map digest，单独持久化 `/map-data` 和
`/var/lib/postgresql/14/main`。不要直接覆盖镜像的整个 `/data`，否则会遮住其初始
样式目录。挂载以下配置文件，并将镜像 CMD 设置为 `/bin/bash /opt/map-bootstrap.sh`：

| 仓库文件 | 容器路径 |
|---|---|
| `deploy/webarena-rainbond/map-bootstrap.sh` | `/opt/map-bootstrap.sh` |
| `harness/environments/webarena/map_setup.py` | `/opt/map_setup.py` |
| `deploy/webarena/site-delivery-contract.json` | `/opt/map-contract.json` |

启动入口将实际瓦片缓存 `/var/cache/renderd/tiles` 链接到 `/map-data/tiles`，
避免容器重建后重新渲染。渲染并发为 2，首次缺失瓦片等待上限为 120 秒；
使用容器资源限额约束负载，避免共享节点的其他服务负载导致瓦片被拒绝。
就绪探针检查 `/tile/0/0/0.png`，确认地图渲染链路可用后再接入流量。
入口还会同步预编译 JavaScript 的 gzip 副本，避免镜像中的过期压缩资源让远端
浏览器错误访问 `localhost`；服务请求保持与网站同源。

三个原始归档约 174.5 GiB。初始化逐包下载、校验 SHA-256、稀疏解包并删除归档，
从最大的 Nominatim 包开始；空间预算应覆盖当前归档、解包后数据和至少 64 GiB
节点空闲保护线。下载和解包持续检查空闲空间，服务运行时也会检查；低于保护线
会停止 Map，保留数据。卷声明容量不能替代节点磁盘检查或实际存储配额。
下载最多 16 个连接，总速率上限 64 MiB/s，部分文件使用 aria2 状态继续下载。
只有校验和完整解包都完成后才写入完成回执；重启不依赖原下载服务器。
中断恢复先顺序读取现有文件，再比较各块 SHA-256，只重写不同内容；不按文件
大小推定内容正确，也避免机械盘上交替读取归档与数据库造成大量寻道。

默认从契约中的官方 S3 地址获取。复用已有原始归档时，可设置
`MAP_ARCHIVE_BASE_URL` 指向受控传输源；该源需支持 HEAD 和 HTTP Range。
若源提供同名 `.tar.zst` 文件，设置 `MAP_ARCHIVE_COMPRESSION=zstd`。
压缩传输采用流式恢复，同时校验原始 tar 的每个字节（包括尾部填充），不再落盘
一份完整 tar。数据全部通过原契约的长度与 SHA-256 校验前不会启动服务；
失败留下的部分恢复数据不记为完成，下次恢复会覆盖这些文件。
压缩副本和传输服务应在交付验证后回收，原始数据保留。

验收应覆盖首页、地名检索、反向地理编码、三种交通方式的路线及实际瓦片图片，
并在重启后重复检查。对外只开放 8080 网站端口；8877 控制接口不对外发布。

```sh
uv run --frozen python scripts/verify_webarena_map.py \
  --url <地图入口> --output <Git外的验收回执.json>
```

配置文件修改后先停止 Map，再重新部署，并核对容器内的实际文件哈希；普通重启
可能继续使用旧构建的配置快照。数据初始化期间不应配置依赖网站就绪的存活探针；准备完成后
再启用 HTTP 就绪探针，避免下载进程被反复重启。
