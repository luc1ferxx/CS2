# 在 Mac 开发，发布 Windows 软件

更新日期：2026-09-13。首发用户平台已确定为 **Windows**，后续日常开发转到 **macOS**。Mac 不运行 CS2 或生成真实视频。桌面版核心解析采用联网服务还是离线本地模式，仍待确定；此开发环境同时适用于两条路线的现有功能维护。

## 开发与发布分工

| 环境 | 工作 |
| --- | --- |
| Mac | 界面、桌面入口代码、真实 `.dem` 解析、规则建议、地图、任务可靠性、接口、测试和发布脚本。Docker 内运行现有后端依赖。 |
| GitHub Actions 的 Windows runner（待配置） | 在 Windows 环境安装依赖、测试并构建 Windows 安装包；签名凭据通过发布 secrets 注入。 |
| 现有 Windows 电脑 | 保留已配置的 CS2/CSDM 渲染环境，按需验证真实视频；检查 Windows 文件选择、路径、权限和进程行为。 |
| 没有开发环境的 Windows 测试电脑 | 从安装包验证首次启动、导入、复盘、关闭重开、更新和卸载的数据行为。联网版还需验证离线/服务故障提示。 |

Electron 是当前优先评估的桌面方案，尚未接入。其打包默认面向运行构建的系统；使用 Windows CI 可让开发工作留在 Mac。参考 [Electron Forge 构建流程](https://www.electronforge.io/core-concepts/build-lifecycle) 和 [GitHub runner 选择](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job)。CI 构建通过不能替代 Windows 真机安装验收。

## Mac 首次启动

1. 安装 Git 和适合 Intel/Apple Silicon 的 [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/)，启动 Docker 并等待引擎就绪。此要求针对开发者；最终用户安装软件时不需要 Docker。
2. 在 Mac 克隆包含本次开发入口的仓库版本；若已有仓库，在处理好本地改动后同步对应提交：

   ```bash
   git clone https://github.com/luc1ferxx/CS2.git
   cd CS2
   bash scripts/dev.sh check
   bash scripts/dev.sh
   ```

3. 打开 <http://localhost:3000/dashboard>，导入复制到 Mac 的 `.dem`，继续以 xelex 复盘。首次拉取镜像和安装依赖需要联网，耗时取决于下载速度。

脚本固定使用 `cs2-dev` Compose 项目、仓库 `.env.example` 与 `docker-compose.dev.yml`，不读取根目录 `.env` 或 Windows 的 ignored `.local/` 配置。开发覆盖文件固定连接本项目的 Postgres/Redis、使用 development 身份和本地 artifact 存储，并将 API/前端只绑定到 `127.0.0.1`。需要 Docker Compose 2.24.4 或更新版本，以支持真正替换端口列表的 `!override`；参考 [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/)。不要给这套开发环境注入生产凭据。

只运行前端、API、解析 worker、Postgres 和 Redis。`RENDER_WORKER_MODE=fallback` 让未连接视频设备的请求显示不可用；不会启动游戏或录制工具。Mac 与 Windows 各自的 `localhost` 和 Docker 数据卷独立，此入口不会连接当前 Windows 的数据库或视频。

## 日常修改与验证

前端 `app/components/lib/public/types` 及后端 `app` 从源码只读挂载到容器，前端和 API 可以检测变更。解析 worker 不自动重载；修改解析/规则代码、依赖或构建配置后，再运行 `bash scripts/dev.sh`，应用新镜像与代码。若 Mac 文件监听没有刷新，也可运行该命令。重建保留命名数据卷。

```bash
bash scripts/dev.sh logs
bash scripts/dev.sh stop
```

`stop` 保留开发数据。再次运行默认命令即可启动；不要用带 `-v` 的删除命令作为普通重启步骤。若 3000/8000 端口已被其他项目占用，先停止对应旧服务。

无需在 Mac 宿主机安装 Python 或 Node 就能启动此环境。编辑后端时可在已有镜像内运行当前源码的完整测试：

```bash
docker compose --project-name cs2-dev --env-file .env.example \
  -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps \
  -v "$PWD:/workspace:ro" -w /workspace \
  -e PYTHONPATH=/workspace:/workspace/backend:/workspace/backend/tests \
  -e PYTHONPYCACHEPREFIX=/tmp/cs2-pycache \
  api python -m unittest discover -s backend/tests
```

需要直接使用 `scripts/verify.sh` 时，在 Mac 新建 Python 3.12 虚拟环境，安装 `backend/requirements.txt`；安装与前端 Dockerfile 一致的 Node 主版本，在 `frontend` 执行 `npm ci`。不要复制 Windows 的 `.venv` 或 `node_modules`。验证范围及 UI 人工清单见 [仓库指南](../AGENTS.md)。

## 数据与任务交接

- Git 同步代码、配置模板和文档；原四个 `.dem`、已生成视频、Docker 数据卷、本机 `.env` 和 `.local/` 工具不会上传 GitHub。
- 最快继续开发的方式是将需要的 `.dem` 单独复制到 Mac 后重新导入。新解析记录会有新 ID；原来的 Windows 比赛链接不会在新的本地库中出现。
- 保存的视频、既有比赛名称/归档/任务记录需要额外迁移数据库和 artifact 数据，或者配置到同一个后端后访问；仅复制 `.dem` 不能恢复这些记录。本次未进行数据迁移或跨电脑联网配置。
- 默认复盘玩家仍为 xelex。浏览器中的个人选择和播放位置也可能需要重新设置。
- 同步前检查 `git status --short`，记录当前提交；推送后在 Mac 获取该提交。一次只在一台电脑改同一批文件，减少冲突。

## 下一阶段验收

先选择联网或离线解析方式，再实现桌面入口与相应后端连接/生命周期，随后配置 Windows 安装包构建、签名和更新，在干净 Windows 设备完成安装→导入→解析→复盘→重开验收。真实视频是独立增强，不作为 Mac 开发环境的依赖。完整交付要求见 [桌面版发行计划](desktop_distribution_v1.md)。

本次只交付开发启动入口与迁移说明；尚未产出 Windows 安装包，也未配置发布 CI。已在当前 Windows 开发环境通过 Compose 配置合并检查（项目、开发模式、存储、端口和健康检查）、Bash 语法检查，以及使用模拟 Docker/curl 的命令分派和依赖失败检查；没有为此启动实际应用容器。Mac 原生启动与 Apple Silicon 实机解析仍需要到 Mac 执行验收。
