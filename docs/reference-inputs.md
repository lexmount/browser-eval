# 冻结参考输入

历史 Web Fetch 的源页、文档与独立渲染快照保存在 Git 外部。
`evaluation/source-inputs.json` 将完整文件清单、逐文件 SHA-256 和冻结数据集
SHA-256 绑定在一起。数据集及历史证书保留原始字节。

默认位置为：

```text
~/.browser-eval/reference-inputs/<清单 id>/sources/
```

`BROWSER_EVAL_DATA_HOME` 可以替换数据根目录。清单指定了外置树时，运行器、
参考预检和审计只读取该树；不会退回仓库中的同名目录，也不会自动请求网站。
自定义数据集没有外置清单时，仍使用它旁边的 `sources/` 和 `list-references/`。

## 导入和校验

从已有且获准使用的副本导入。`--source` 指向包含 `sources/` 的目录：

```sh
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json \
  --source /absolute/path/approved-evaluation-copy
uv run --frozen python scripts/import_reference_inputs.py \
  --manifest evaluation/source-inputs.json --check
```

导入只复制清单中的文件，排除未声明的日志和其他材料。目标目录必须是新目录；
校验不修改已有文件。缺失文件、哈希不符、越界路径和符号链接都会被拒绝。
完整校验通过后才保留新输入目录，中断不留下半成品。

源副本的获取和使用条件由原来源决定。这些命令不下载第三方数据，不改变其
许可，也不授予公开分发权。不要将私有输入目录或源副本上传到 GitHub。
项目代码许可不覆盖数据集内的第三方参考正文。

## 测试和修订

常规 `make ci` 可在没有历史快照的干净检出中运行。依赖这些快照的重建检查
会说明缺少外置输入；合成场景的执行、评分、输入校验和报告测试仍运行。
验证历史参考时使用严格模式：

```sh
make test-reference-inputs
# 等价的 Python 测试入口
uv run --frozen --all-extras python -m pytest tests -q --require-reference-inputs
```

严格模式先校验完整清单，缺少输入直接失败。常规模式下，存在但损坏的输入
也会失败，不会被当作缺失数据跳过。

已绑定输入不可原地修改。参考更新命令只接受独立候选数据集和源目录；完成
独立采集、适用的审查和回归验证后，新的清单及实验配置声明新版本。保留
原输入版本、测量证据和证书。历史收据中的仓库相对路径是原始来源标识，
不再表示源文件应随代码检出。

Git 历史仍包含早期提交的源材料。从当前树移除文件不等于清理历史；
对外发布的提交历史也必须遵守同样的分发边界。
