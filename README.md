# BudMon

Python + SQLite + Ant Design + 原生 SwiftUI 的多用户网站与 SSL 证书监测系统。

**iOS 完整工程、APNs 推送、多用户升级和会员配额部署指南：[docs/IOS.md](docs/IOS.md)。**

直接打开 `ios/BudMon.xcodeproj` 运行客户端。支持注册登录、监测目标管理、运行状态与历史、告警收件箱和 APNs 推送；后台已加入用户隔离、权限控制与套餐配额，预留付费事件结构。

## 功能

- 初始化安装管理员账号
- 后台管理员登录、重置密码
- 配置监控目标：网站名称、网站地址、是否启用
- 配置监控策略：检测间隔、失败重试间隔、短信/邮箱通知方式、独立的短信与邮箱通知目标
- 配置阿里云/腾讯云短信渠道及可维护短信模板
- 定时访问目标网址，失败后间隔 N 秒重试，连续 2 次失败后发送告警
- HTTPS 网站证书过期监测，证书剩余天数小于等于阈值时发送告警
- 监测记录一键清空

默认短信模板：

```text
服务通知：温馨提醒：您的 ${name} 服务已断连，请及时关注。
证书过期：温馨提醒：您的 ${name} 证书剩余 ${day} 天过期，请及时关注。
```

默认模板 Code 均为 `SMS_507940075`。服务通知变量为 `name`，证书过期变量为 `name`、`day`。

## Docker Compose 启动

```bash
docker compose -f docker-compose.local.yml up -d --build
```

启动后访问：

```text
http://localhost:9977
```

首次进入会显示初始化安装页面。

SQLite 数据库会保存在 Docker volume `budmon-data` 中。JWT 密钥默认自动生成并保存在数据卷，也可通过环境变量 `BUDMON_SECRET_KEY` 显式设置。

如果本机执行 `docker compose` 提示无法连接 Docker API，请先启动 Docker Desktop 或 Docker daemon。

## 发布到 Docker Hub

先登录 Docker Hub：

```bash
docker login
```

在项目根目录构建并推送后端、前端镜像（线上旧版本为 `1.0.1`，本次发布 `1.0.2`）：

```bash
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.2
```

脚本未传 `-v` 时读取仓库根目录 `VERSION`（当前 `1.0.2`）。默认构建 `linux/amd64` 与 `linux/arm64`，并推送：

```text
wizzer/budmon-backend:1.0.2
wizzer/budmon-backend:latest
wizzer/budmon-frontend:1.0.2
wizzer/budmon-frontend:latest
```

只构建单架构：

```bash
scripts/publish-dockerhub.sh -u wizzer -v 1.0.2 -p linux/amd64
```

不推送 `latest`：

```bash
scripts/publish-dockerhub.sh -u wizzer -v 1.0.2 -n
```

可通过环境变量自定义仓库名：

```bash
BACKEND_IMAGE=budmon-api FRONTEND_IMAGE=budmon-web scripts/publish-dockerhub.sh -u wizzer -v 1.0.2
```

## 线上从 1.0.1 升级至 1.0.2

镜像推送成功后，在服务器原有 Compose 项目目录更新 `docker-compose.yml`，两个镜像分别固定为 `wizzer/budmon-backend:1.0.2` 与 `wizzer/budmon-frontend:1.0.2`。保留原来的项目名、数据卷、端口及密钥配置。

本次包含多用户数据库迁移，升级前备份数据库。以下命令先拉取新镜像，再短暂停止后端，将旧数据库目录复制出来：

```bash
docker compose pull
mkdir -p backups
backup_dir="backups/budmon-1.0.1-$(date +%Y%m%d-%H%M%S)"
docker compose stop backend
docker compose cp backend:/data "$backup_dir"
# 确认上一条备份成功后再继续；若备份失败，先执行 docker compose start backend 恢复旧服务。
docker compose up -d
docker compose ps
docker compose logs --tail=100 backend
```

不要执行 `docker compose down -v`，避免删除持久化数据。旧账号与监测目标会迁移保留，升级后需要重新登录。若需回滚至 `1.0.1`，应同时恢复升级前备份的数据与旧镜像，不要仅切换镜像版本。

若线上启用了 APNs 密钥挂载，上述 Compose 命令均加上 `-f docker-compose.yml -f compose.apns.yml`，确保更新时保留密钥挂载。

## 本地开发验证

后端：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
BUDMON_DATA_DIR=/tmp/budmon-dev .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

前端：

```bash
cd frontend
npm install
npm run build
npm run dev
```

## 说明

- 监测逻辑：按配置间隔访问目标网址；如果访问失败，等待配置的重试间隔后再访问一次；连续 2 个检测周期失败时触发服务断连告警。
- 证书逻辑：仅对 `https://` 目标读取 TLS 证书到期时间；剩余天数小于等于配置阈值时触发证书过期告警，同一目标每天最多发送一次证书告警。
- 短信渠道：支持阿里云短信和腾讯云短信。阿里云模板参数按 JSON 对象发送；腾讯云模板参数按模板维护的 `params` 顺序发送。
- 邮箱通知：支持 SMTP SSL。短信通知目标和邮箱通知目标独立配置，手机号与邮箱地址不要混填。

## Docker Desktop 构建代理

发布脚本默认使用本机 HTTP 代理 `http://127.0.0.1:7890`：

```bash
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.2
# 显式指定同一代理：
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.2 -x http://127.0.0.1:7890
```

脚本让宿主机 CLI 使用该地址，容器内自动改用 `http://host.docker.internal:7890`，同时设置独立 BuildKit builder 的代理（基础镜像元数据、拉取与推送）及构建参数（pip/npm）。不会删除已有 builder，代理参数改变时使用不同 builder。

请启动本机代理；如果容器访问仍被拒绝，检查代理软件允许局域网连接/监听地址，并仅允许可信网络访问该端口。Docker Desktop 通过 `host.docker.internal` 访问宿主机，不能把容器内的 `127.0.0.1` 当作 Mac 地址。参考 [Docker Desktop 网络文档](https://docs.docker.com/desktop/features/networking/networking-how-tos/) 与 [BuildKit 容器驱动配置](https://docs.docker.com/build/builders/drivers/docker-container/)。

新 builder 第一次启动需由 Docker Engine 拉取 `moby/buildkit`，这一步不受 builder 内的环境变量控制；若此处失败，还需将 Docker Desktop 自身的 HTTP/HTTPS 代理配置为 `http://127.0.0.1:7890` 并应用设置。代理认证访问 Docker Hub 的网络与容器依赖安装是不同阶段。

其他构建机可用 `-x http://构建容器可访问的代理地址:端口` 覆盖；原生 Linux 不保证存在 `host.docker.internal`，应使用容器可访问的宿主机 IP。`-d` 或 `BUDMON_BUILD_PROXY=''` 仅关闭脚本显式代理设置，Docker 全局已有的代理仍可能生效。
