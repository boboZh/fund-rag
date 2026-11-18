# 容器构建与镜像发布

## 当前范围

本次只补齐镜像构建、健康检查和 GHCR 发布。`cd.yml` 已替换旧部署逻辑：发布镜像后不会 SSH、SCP、重载 PM2 或切换线上入口。现有服务器服务继续运行。
尚未包含生产 Compose、服务器部署脚本、自动回滚或数据搬迁，先不要删除旧服务和目录。

## 本地构建

在当前仓库根目录执行：

```bash
docker build -t fund-rag:local .
```

构建上下文排除了环境文件、密钥、本地依赖和运行数据。健康检查使用镜像内置工具，不依赖额外 curl。

## 发布到 GHCR

1. 先提交并推送本仓库改动。工作流需要存在于远程仓库中；手动入口通常需先合入默认分支。
2. 在 Actions 手动运行 Publish container image，或推送尚未使用的 `v*.*.*` tag。
3. 工作流测试通过后，使用自动提供的 GITHUB_TOKEN 推送 `ghcr.io/<小写owner>/<小写repo>`。
4. tag 发布生成对应版本标签，所有发布生成 `sha-<完整commit>` 标签；不生成 latest。
5. Actions 摘要记录镜像 digest，后续 Compose 使用完整 `image@sha256:...` 固定版本。
6. 私有镜像在服务器使用具有 read:packages 权限的 classic Token 登录 GHCR，然后拉取摘要中的镜像。

默认发布 `linux/amd64`，上线前用 `uname -m` 核对服务器架构；ARM 服务器必须先调整工作流平台设置。
首次推送默认私有；仓库/组织策略须允许工作流写入 Packages。未提交、未推送前不会产生镜像。

## 服务注意事项

- Python 3.11，直接从 requirements.txt 安装锁定依赖，不复制本地虚拟环境。
- Uvicorn 监听 `0.0.0.0:9001`，默认 2 workers；后续按内存和负载调整。
- `--env-file`/Compose `env_file` 注入现有数据库、Qdrant、Embedding、Rerank 和 LLM 配置。
- 必须配置 `DEEPSEEK_API_KEY`：现有 LLM 模块在导入时创建客户端。测试的占位 Key 仅存在 conftest.py 中，不会复制进运行镜像。
- MySQL 和 Qdrant 地址必须从容器可达；保留现有 Qdrant 容器与命名卷，不新建空集合替代业务数据。
- `/healthz` 是进程存活检查，不调用模型或数据库，不能代替检索和入库验收。
- pytest.ini 通过 importlib 模式解决 api/、services/ 下同名测试模块冲突。LLM 测试使用模拟流，不消耗真实模型额度。

## 上线验收

先备份并验证业务数据和配置，再准备 Compose 网络、环境变量及持久化挂载。
镜像的 HEALTHCHECK 只是存活检查；依赖连接和完整业务流程通过后才能切换入口。
应用更新期间保留 MySQL、Redis、Qdrant 原部署及数据卷。
生产环境的普通 Compose 重建不是零停机发布；保留旧镜像、配置和可恢复的数据备份。
