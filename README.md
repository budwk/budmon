# BudMon

Python3 + SQLite + Ant Design 网站运行状态监控小系统。

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
docker compose up -d --build
```

启动后访问：

```text
http://localhost:8080
```

首次进入会显示初始化安装页面。

SQLite 数据库会保存在 Docker volume `budmon-data` 中。生产环境建议修改 `docker-compose.yml` 里的 `BUDMON_SECRET_KEY`。

如果本机执行 `docker compose` 提示无法连接 Docker API，请先启动 Docker Desktop 或 Docker daemon。

## 发布到 Docker Hub

先登录 Docker Hub：

```bash
docker login
```

构建并推送后端、前端镜像：

```bash
scripts/publish-dockerhub.sh -u <你的DockerHub用户名或组织名> -v 1.0.0
```

默认会推送：

```text
<namespace>/budmon-backend:1.0.0
<namespace>/budmon-backend:latest
<namespace>/budmon-frontend:1.0.0
<namespace>/budmon-frontend:latest
```

只构建单架构：

```bash
scripts/publish-dockerhub.sh -u <namespace> -v 1.0.0 -p linux/amd64
```

不推送 `latest`：

```bash
scripts/publish-dockerhub.sh -u <namespace> -v 1.0.0 -n
```

可通过环境变量自定义仓库名：

```bash
BACKEND_IMAGE=budmon-api FRONTEND_IMAGE=budmon-web scripts/publish-dockerhub.sh -u <namespace> -v 1.0.0
```

## 本地开发验证

后端：

```bash
python3 -m venv .venv
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
