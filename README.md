# BudMon

Python + PostgreSQL + Ant Design + 原生 SwiftUI 的多用户网站与 SSL 证书监测系统。

为了苹果上架补了个网站  [https://budmon.budwk.com](https://budmon.budwk.com)，苹果商城搜索 BudMon 即可下载。

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

## Ubuntu 原生部署（systemd）

适用于 Ubuntu 26.04 LTS / Python 3.14.4，也支持 Python 3.12–3.13。打包和部署分开执行。

**1. 本地打包**（需要 Node.js 20+、npm、rsync、tar）：

```bash
bash scripts/package-native.sh
# 可选：指定输出目录
bash scripts/package-native.sh -o ./dist
```

固定输出 `dist/budmon.tar.gz`，压缩包顶层目录为 `budmon/`，文件名和目录名均不带版本号。打包脚本在临时目录安装前端依赖并构建，压缩包包含 `website/dist` 官网静态文件、管理后台前端产物、后端源码及依赖清单、服务器部署脚本和配置模板，不包含本地虚拟环境、数据库或密钥。Python 依赖在服务器安装，以适配服务器的 Python 版本和系统架构。

**2. 上传压缩包**（将用户名和地址替换为实际服务器）：

```bash
scp dist/budmon.tar.gz user@server:~/
```

**3. 在服务器解压并部署**：

```bash
sudo mkdir -p /data
sudo tar -xzf budmon.tar.gz -C /data
cd /data/budmon
sudo bash scripts/deploy-native.sh
```

应用安装目录固定为 `/data/budmon`。也可先解压到其他临时目录，脚本会将后端和前端产物安装到该目录。服务器需已安装 Nginx（主配置需加载 `/etc/nginx/conf.d/*.conf`）。部署脚本复用现有 Nginx，通过 apt 安装 Python venv、Certbot 及 `python3-certbot-nginx` 等运行依赖，使用 `python3 -m venv ~/.budmon-venv` 创建独立运行环境，安装 Python 依赖，注册 `budmon.service`，并通过 `systemctl enable` 配置 BudMon 和 Nginx 随系统启动。服务器无需 Node.js/npm，无需构建前端，但需能够访问 apt 和 Python 包源。

官网地址为 [https://budmon.budwk.com](https://budmon.budwk.com)；管理后台为 [https://budmon.budwk.com/admin/](https://budmon.budwk.com/admin/)，首次访问管理后台初始化管理员；iOS 请求固定到 `https://budmon.budwk.com/api`，登录/注册只需账号密码。部署前将域名的 A/AAAA 记录指向服务器（如设置了 AAAA，也必须正确可达），并放通 TCP 80、443。脚本使用 Certbot 申请证书、配置 HTTP 跳转 HTTPS，并启用 `certbot.timer` 自动续期；首次运行时按提示填写账户信息并确认服务条款。证书申请失败会明确报错，修正 DNS/网络后重新执行部署脚本即可。证书保存在 `/etc/letsencrypt`，更新部署时复用有效证书。参见 [Certbot 官方参数说明](https://eff-certbot.readthedocs.io/en/stable/man/certbot.html)。已自行安装系统依赖时可使用 `sudo bash scripts/deploy-native.sh --skip-apt`。服务器要求 Python 3.12–3.14 和正在运行的 systemd。指定 Python 时执行：

```bash
sudo env PYTHON_BIN=/usr/bin/python3.14 bash scripts/deploy-native.sh
```

| 用途 | 路径 |
| --- | --- |
| 后端代码 | `/data/budmon/backend` |
| Python 虚拟环境 | `~/.budmon-venv` |
| 自动生成的 JWT 密钥与本地状态 | `/var/lib/budmon` |
| 环境配置 | `/data/budmon/.env` |
| 官网静态文件 | `/data/budmon/website/dist` |
| 管理后台静态文件 | `/data/budmon/frontend/dist` |
| Nginx 配置 | `/etc/nginx/conf.d/budmon.budwk.com.conf` |

`~` 指执行部署的用户家目录（通常直接以 root 执行时使用 `/root`，虚拟环境保存在 `~/.budmon-venv`）。

后端监听 `127.0.0.1:9977`，由 Nginx 的 `budmon.budwk.com` HTTPS 入口代理。服务固定使用一个 worker，避免重复启动监测调度器。Python 3.14 使用单独的 FastAPI/Pydantic 版本标记，旧版 Python 保留原有版本。

常用管理命令：

```bash
sudo systemctl status budmon nginx
sudo systemctl restart budmon
sudo systemctl stop budmon
sudo journalctl -u budmon -f
curl -f https://budmon.budwk.com/api/health
sudo systemctl status certbot.timer
```

修改 `/data/budmon/.env` 后重启 `budmon`。数据库默认通过 `BUDMON_DATABASE_URL` 配置（默认 `postgresql://budmon:budmon@127.0.0.1:5432/budmon`，用户与库名均为 `budmon`）。APNs 使用服务器上的真实密钥路径，例如 `/data/budmon/AuthKey.p8`，并将密钥权限设为 `0640`；不要使用 Docker 容器内路径。修改端口、域名或 HTTPS 配置后运行 `sudo nginx -t && sudo systemctl reload nginx`。

更新代码后在本地重新打包，上传到服务器并解压到独立目录，再运行包内的部署脚本即可升级，期间服务会短暂停止。脚本保留已有环境配置与数据，更新 Nginx 配置和 systemd 单元；迁移旧版 Nginx 配置时会备份旧文件并重新配置证书；自定义服务参数请使用 `sudo systemctl edit budmon`。升级前应停止服务并备份整个 `/var/lib/budmon` 和 `/data/budmon/.env`。若 Python 主/次版本发生变化，需在停止服务后移走旧 `.budmon-venv`，再运行脚本重新创建。安装失败时查看输出、修复后重新运行；脚本不自动回滚。原 Docker 数据不会自动迁移。

### 大规模监测调优

后台按主键分批读取监测目标，服务检测和 APNs 推送使用独立的有界线程池；证书默认每 6 小时检测一次，历史记录和已完成推送按批清理。PostgreSQL 默认启用 2–24 个连接的连接池。可在 `.env` 中调整：

- `BUDMON_MONITOR_WORKERS`、`BUDMON_MONITOR_BATCH_SIZE`：检测并发数与每批目标数。
- `BUDMON_CERT_CHECK_INTERVAL_SECONDS`：证书检测间隔。
- `BUDMON_PUSH_WORKERS`、`BUDMON_PUSH_BATCH_SIZE`、`BUDMON_PUSH_INTERVAL_SECONDS`：APNs 并发、批量和调度间隔。
- `BUDMON_DB_POOL_MIN`、`BUDMON_DB_POOL_MAX`：PostgreSQL 连接池上下限。
- `BUDMON_RETENTION_INTERVAL_MINUTES`、`BUDMON_RETENTION_BATCH_SIZE`：历史清理频率和批量。

并发值应根据服务器 CPU、出口带宽和 PostgreSQL `max_connections` 调整，数据库连接池上限应高于检测并发并为 API 请求预留连接。仍须保持单 Uvicorn worker，避免重复启动进程内调度器；需要多实例或数万级目标时，应将检测和推送迁移到带租约/幂等控制的独立任务队列。

## iOS 内购与管理记录

内购产品 `budmon_number`：每份永久增加 1 个监测名额，可重复购买，累计不设业务上限。App 显示 StoreKit 返回的本地化价格；请在 App Store Connect 创建**消耗型**产品，将中国大陆价格设为 **¥1.00**，完成销售地区、税务/银行资料和审核。代码不能代替 App Store Connect 的产品发布与定价。

在 `/data/budmon/.env` 添加：

```dotenv
APPLE_APP_ID=填写AppStoreConnect中的数字AppleID
APPLE_BUNDLE_ID=com.budwk.app.budmon
APPLE_ALLOW_SANDBOX=0
```

未配置有效 `APPLE_APP_ID` 时内购入口会提示未启用。测试、TestFlight 和 App Review 需要 `APPLE_ALLOW_SANDBOX=1`；Sandbox 测试订单也会给所绑定账号增加名额并明确标记测试环境，建议使用专用测试账号。配置后执行 `sudo systemctl restart budmon`。

App Store Connect 的生产与沙盒 **App Store Server Notifications V2** URL 均填写 `https://budmon.budwk.com/api/iap/notifications`，用于退款/撤销及退款撤销同步。必须配置通知并完成沙盒验证后再上线销售。

管理后台新增“购买与登录记录”，也可在“用户与配额”中查看单个用户的购买、注册和登录记录。已有账号保留原注册时间，历史登录时间无法补录；新登录会记录时间。购买在 Apple 验签后幂等入账，重复交易不重复发放，退款扣回相应名额；已有监测目标保留，额度不足时不能新增。名额绑定 BudMon 账号，删除账号不会转移给同名新账号。

完整配置、验证步骤及实现限制见 [docs/IOS.md](docs/IOS.md)。

## 发布到 Docker Hub

先登录 Docker Hub：

```bash
docker login
```

在项目根目录构建并推送后端、前端镜像：

```bash
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.3
```

脚本未传 `-v` 时读取仓库根目录 `VERSION`（当前 `1.0.3`）。默认构建 `linux/amd64` 与 `linux/arm64`，并推送：

```text
wizzer/budmon-backend:1.0.3
wizzer/budmon-backend:latest
wizzer/budmon-frontend:1.0.3
wizzer/budmon-frontend:latest
```

只构建单架构：

```bash
scripts/publish-dockerhub.sh -u wizzer -v 1.0.3 -p linux/amd64
```

不推送 `latest`：

```bash
scripts/publish-dockerhub.sh -u wizzer -v 1.0.3 -n
```

可通过环境变量自定义仓库名：

```bash
BACKEND_IMAGE=budmon-api FRONTEND_IMAGE=budmon-web scripts/publish-dockerhub.sh -u wizzer -v 1.0.3
```

## 生产环境配置文件 

docker-compose.yml

```yaml
services:
  backend:
    image: wizzer/budmon-backend:1.0.3
    container_name: budmon-backend
    environment:
      BUDMON_DATA_DIR: /data
      BUDMON_SECRET_KEY: change-this-secret
      APNS_KEY_ID: ""
      APNS_TEAM_ID: ""
      APNS_BUNDLE_ID: "com.budwk.app.budmon"
      # 指向新的独立目录
      APNS_KEY_PATH: "/data/AuthKey.p8"
    volumes:
      - budmon-data:/data
    restart: unless-stopped

  frontend:
    image: wizzer/budmon-frontend:1.0.3
    container_name: budmon-frontend
    depends_on:
      - backend
    ports:
      - "9977:80"
    restart: unless-stopped

volumes:
  budmon-data:
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
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.3
# 显式指定同一代理：
bash scripts/publish-dockerhub.sh -u wizzer -v 1.0.3 -x http://127.0.0.1:7890
```
