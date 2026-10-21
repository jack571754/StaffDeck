# StaffDeck 生产环境部署与运维手册 (Linux / Docker)

本手册基于实际生产部署经验（包含 CentOS 7 / Ubuntu / Debian 环境的落地验证）编写，详细说明 StaffDeck 单端口全栈容器化架构、环境前置要求、安装与数据迁移流程、核心环境变量、常见避坑问题及日常运维管理。

---

## 目录

1. [架构与组件概览](#一架构与组件概览)
2. [环境前置要求](#二环境前置要求)
3. [部署目录规范](#三部署目录规范)
4. [核心环境变量说明](#四核心环境变量说明)
5. [完整部署实施流程](#五完整部署实施流程)
6. [业务数据迁移与轻量化处理（重要）](#六业务数据迁移与轻量化处理重要)
7. [关键踩坑点与排错指南](#七关键踩坑点与排错指南)
8. [服务验证清单](#八服务验证清单)
9. [日常运维与备份管理](#九日常运维与备份管理)
10. [Nginx 生产反向代理配置](#十nginx-生产反向代理配置)

---

## 一、架构与组件概览

StaffDeck 采用**单端口全栈自包含架构**（Single-Port Runtime）：

```
                          ┌──────────────────────────────────────────────┐
                          │            Docker 容器 (staffdeck)            │
                          │                                              │
  浏览器 / 客户端          │  FastAPI (Uvicorn 0.0.0.0:5173)              │
  ─────────────────────►  │  ├── 静态资源服务 (React 18 + Vite SPA)       │
  http://<IP>:5173        │  ├── RESTful API (/api/*)                    │
                          │  ├── 数据查询中心 (/api/enterprise/data-query)│
                          │  ├── 定时任务调度器 (APScheduler 进程)        │
                          │  └── 飞书长连接子进程 (Lark WebSocket Worker) │
                          │                                              │
                          │  持久化数据卷挂载 (/app/data)                 │
                          │  └── SQLite 数据库 (skill_agent_loop.db)     │
                          └───────────────┬──────────────────────────────┘
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
         外部数据源 (MySQL 等)                             飞书开放平台网关
         <mysql-host>:3306                                wss://msg-frontier.feishu.cn
```

- **单端口服务**：前端 React SPA 经 Vite 生产编译后由 Python FastAPI 直接代理托管，前后端统一在 `5173` 端口运行，无需额外部署独立 Nginx 容器。
- **存储方案**：元数据、员工画像、记忆、查询模板、定时任务统一保存在 SQLite 数据库中，通过 Docker 目录卷挂载持久化。
- **权限与沙箱**：容器内使用普通用户运行，挂载 `SYS_ADMIN` Capabilities 以便 Linux Bubblewrap (`bwrap`) 运行执行沙箱。

---

## 二、环境前置要求

### 1. 硬件规格建议
- **CPU**：4 核及以上
- **内存**：8GB 及以上（由于包含复杂报表查询、大模型多轮上下文推理，推荐 8GB+）
- **磁盘**：50GB 及以上可用空间（存放 Docker 镜像、数据卷与日志）

### 2. 操作系统与软件依赖
- **操作系统**：CentOS 7 / RHEL 8+ / Ubuntu 20.04+ / Debian 11+
- **Docker 引擎**：Docker CE >= 20.10.x（需支持多阶段构建）
- **Docker Compose**：Docker Compose v2 >= 2.20.x（支持 `docker compose` 命令）

### 3. 网络与防火墙规划
- **服务监听端口**：`5173/TCP`（如开启防火墙需执行 `firewall-cmd --add-port=5173/tcp --permanent && firewall-cmd --reload`）
- **出网访问**：
  - 大模型 API（如火山引擎 Ark、OpenAI 等）
  - 飞书开放平台网关（`open.feishu.cn`，以及 WebSocket `msg-frontier.feishu.cn`）
- **内网互通**：需能够访问企业内部的数据源（如业务 MySQL/PostgreSQL 数据库）。

---

## 三、部署目录规范

在生产服务器上，推荐统一规划在 `/opt/staffdeck` 目录下：

```bash
/opt/staffdeck/
├── deploy/
│   ├── Dockerfile              # 多阶段镜像构建定义
│   ├── docker-compose.yml      # 容器编排定义
│   ├── requirements.txt        # Python 依赖清单 (固化核心依赖版本)
│   ├── .env                    # 生产环境配置文件 (严禁纳入 Git)
│   ├── .env.example            # 配置参考模板
│   ├── deploy.sh               # 自动化运维脚本
│   └── data/                   # 宿主机持久化数据挂载目录
│       ├── skill_agent_loop.db # 核心业务 SQLite 数据库
│       ├── uploads/            # 附件上传目录
│       └── workspaces/         # 临时执行空间
├── backend/                    # 后端源代码
└── frontend-enterprise/        # 前端源代码
```

---

## 四、核心环境变量说明

部署配置文件位于 `deploy/.env`，关键配置项及说明如下：

```ini
# ==============================================================================
# 基础服务配置
# ==============================================================================
STAFFDECK_PORT=5173
STAFFDECK_ROLE=all
TOOL_BASE_URL=http://localhost:5173
CORS_ORIGINS=*

# ==============================================================================
# 安全与加密密钥（非常关键！）
# 1. 数据库中敏感凭据（如数据源密码通过 AES-256-GCM，飞书密钥通过 Fernet）均依赖此 Key。
# 2. 如果是从既有环境迁移 SQLite 数据库，此处的 APP_SECRET 必须与历史环境完全一致，
#    否则容器启动时解密既有渠道和数据源凭据会抛出 InvalidToken / InvalidSignature 错误！
# ==============================================================================
APP_SECRET=change-me-in-development
CHANNEL_SECRET=

# ==============================================================================
# 大模型接口配置 (OpenAI 协议兼容接口)
# ==============================================================================
DEMO_MODEL_BASE_URL=https://ark.cn-beijing.volces.com/api/coding
DEMO_MODEL_NAME=ark-code-latest
DEMO_MODEL_API_KEY=your-api-key-here

# ==============================================================================
# 飞书机器人通知 Webhook (可选)
# ==============================================================================
FEISHU_SALES_REPORT_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
FEISHU_ALERT_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
GENERAL_SKILL_ENV_PASSTHROUGH="FEISHU_SALES_REPORT_WEBHOOK,FEISHU_ALERT_WEBHOOK"

# ==============================================================================
# 扩展执行环境与依赖
# ==============================================================================
GENERAL_SKILL_RUNTIME_PACKAGES="requests,httpx,pymysql"
GENERAL_SKILL_RUNTIME_AUTO_INSTALL="true"
```

---

## 五、完整部署实施流程

### 步骤 1：上传代码至服务器
将 StaffDeck 项目根目录上传至目标服务器的 `/opt/staffdeck`：
```bash
# 示例：通过 rsync 或 git clone
mkdir -p /opt/staffdeck
cd /opt/staffdeck
# 上传/解压项目源码，包含 backend, frontend-enterprise, deploy 目录
```

### 步骤 2：创建持久化目录与赋权
```bash
cd /opt/staffdeck/deploy
mkdir -p data
# 容器内运行用户为 UID 1000 (staffdeck)，为确保读写正常，放行数据目录权限
chmod 777 data
```

### 步骤 3：准备环境配置文件
```bash
cp .env.example .env
vim .env
# 填入大模型 API Key、APP_SECRET 及相关 Webhook
```

### 步骤 4：构建镜像并启动容器
```bash
cd /opt/staffdeck/deploy
docker compose up -d --build
```
> **构建过程说明**：
> 1. 第一阶段：使用 `node:20-bookworm-slim` 执行 `npm ci` 与 `npm run build`，编译输出生产静态文件。
> 2. 第二阶段：使用 `python:3.11-slim-bookworm`，安装 Python 依赖（锁定 `sqlmodel==0.0.42`），将前端打包物拷贝至 `/app/frontend-dist`。
> 3. 首次构建耗时约 2-3 分钟，构建完成后自动启动。

### 步骤 5：验证运行状态
```bash
# 检查容器状态（应为 Up 且 healthy）
docker ps

# 检查日志
docker logs -f staffdeck
```

---

## 六、业务数据迁移与轻量化处理（重要）

### 1. 为什么要做数据轻量化清理？
StaffDeck 在日常运行调度、数字员工思考与调试期间，会向 `agent_events` 表写入大量瞬态中间调试日志。在长时间运行后，SQLite 数据库文件可能膨胀至数 GB（例如运行数周后 `agent_events` 可达 39 万条，文件超 1.3 GB）。

在向新服务器迁移数据库时，**强烈建议执行清理压缩**，仅保留所有核心实体（用户、租户、数字员工、长期记忆、演化提案、数据源、查询模板、定时任务、最近消息），剔除瞬态事件。

### 2. 标准清理压缩脚本
在迁移前，在原开发机执行以下轻量化脚本：

```python
# clean_database_for_migration.py
import sqlite3
import os

SRC_DB = "backend/skill_agent_loop.db"
DST_DB = "backend/skill_agent_loop_clean.db"

# 1. 备份原库结构与数据
if os.path.exists(DST_DB):
    os.remove(DST_DB)

src_conn = sqlite3.connect(SRC_DB)
dst_conn = sqlite3.connect(DST_DB)

with dst_conn:
    src_conn.backup(dst_conn)
dst_conn.close()
src_conn.close()

# 2. 清理瞬态事件
conn = sqlite3.connect(DST_DB)
c = conn.cursor()
print("Cleaning ephemeral event tables...")
c.execute("DELETE FROM agent_events;")
c.execute("DELETE FROM a2a_task_events;")
c.execute("DELETE FROM team_task_events;")
# 仅保留最近 200 条调度运行历史
c.execute("DELETE FROM scheduled_task_runs WHERE id NOT IN (SELECT id FROM scheduled_task_runs ORDER BY created_at DESC LIMIT 200);")
c.execute("VACUUM;")
conn.commit()
conn.close()

print(f"Original size: {os.path.getsize(SRC_DB)/1024/1024:.2f} MB")
print(f"Cleaned size: {os.path.getsize(DST_DB)/1024/1024:.2f} MB")
```
> **效果**：数据库文件可从 **1.37 GB 直接瘦身至 27 MB**，迁移上传数秒完成，且所有业务配置、数字员工与定时任务 100% 完整保留。

### 3. 上传数据库并生效
```bash
# 上传至服务器持久化目录
scp backend/skill_agent_loop_clean.db root@<deploy-host>:/opt/staffdeck/deploy/data/skill_agent_loop.db

# 在服务器上调整文件读写权限
chmod 666 /opt/staffdeck/deploy/data/skill_agent_loop.db
```

---

## 七、关键踩坑点与排错指南

在 Linux 生产化部署过程中，以下 5 个技术细节至关重要：

### 坑 1：SQLModel >= 0.0.48 引发的时区异常
- **错误特征**：启动时日志抛出 `ValueError: Datetime values must have timezone information`，导致应用崩溃循环重启。
- **根本原因**：SQLModel 0.0.48+ 版本在数据校验中对 Datetime 字段强制要求带时区信息，而系统内部 `utc_now()` 采用 `replace(tzinfo=None)`。
- **解法**：在 `deploy/requirements.txt` 中**显式锁定 `sqlmodel==0.0.42`**，禁止安装最新版本。

### 坑 2：凭据解密失败 (`InvalidToken` / `InvalidSignature`)
- **错误特征**：日志出现 `Process staffdeck-feishu-chan_xxx: cryptography.fernet.InvalidToken` 或 MySQL 密码解密失败。
- **根本原因**：数据源密码（AES-256-GCM）与渠道密钥（Fernet）是基于 `.env` 中的 `APP_SECRET` 派生密钥进行加密存库的。更换新环境若使用了新随机 `APP_SECRET`，旧数据将无法解密。
- **解法**：确保新部署环境 `deploy/.env` 中的 `APP_SECRET` 与旧环境数据库加密时所用密钥一致。

### 坑 3：修改 `.env` 后 `docker compose restart` 不生效
- **常见误区**：运维人员修改了 `deploy/.env`，执行 `docker compose restart staffdeck`，发现配置未更新。
- **根本原因**：Docker Compose 机制中，`restart` 仅对现有容器重新发送启动信号，**不会重新读取 `.env` 注入环境变量**。
- **解法**：修改 `.env` 后，必须执行：
  ```bash
  cd /opt/staffdeck/deploy
  docker compose up -d
  ```
  此时 Compose 会显示 `Container staffdeck Recreate`，环境变量才会正式重新装载。

### 坑 4：CentOS 7 宿主机 SQLite 驱动过旧问题
- **错误特征**：在 CentOS 宿主机直接运行 Python 操作数据库时报错 `sqlite3.DatabaseError: malformed database schema ... near "WHERE": syntax error`。
- **根本原因**：CentOS 7 自带的 SQLite 仅为 3.7.17，不支持带 `WHERE` 的局部唯一索引（Partial Index）。
- **解法**：**切勿在 CentOS 宿主机直接用系统 Python 读取数据库**。应始终进入容器或使用 `docker exec` 调用容器内自带的 Python 3.11 与现代 SQLite 驱动：
  ```bash
  docker exec -it staffdeck python
  ```

### 坑 5：MySQL 跨主机访问权限与反向 DNS 解析
- **错误特征**：容器连接内部 MySQL（如 `<mysql-host>:3306`）抛出 `Access denied for user 'root'@'<server-hostname>'`。
- **根本原因**：MySQL 服务端开启了反向 DNS 查询，将来源 IP `<deploy-host>` 解析成了主机名 `<server-hostname>`，且 MySQL 用户仅授权了 IP 或未匹配该主机名。
- **解法**：
  1. MySQL 服务端添加主机名授权：`GRANT ALL PRIVILEGES ON *.* TO 'root'@'<server-hostname>' IDENTIFIED BY 'xxx';`
  2. 或在 MySQL 服务端 `my.cnf` 中配置 `skip-name-resolve` 关闭反向解析。

---

## 八、服务验证清单

部署完成后，请按照以下清单逐一执行验证：

| 验证项 | 验证命令 / 操作 | 期望结果 |
| :--- | :--- | :--- |
| **容器健康状态** | `docker ps` | 状态显示为 `Up ... (healthy)` |
| **HTTP 健康接口** | `curl -s http://127.0.0.1:5173/api/health` | 返回 `{"status":"ok","app":"StaffDeck"}` |
| **Web 前端加载** | 浏览器打开 `http://<服务器IP>:5173` | 正常呈现 StaffDeck 登录/主控制台界面 |
| **飞书长连接** | `docker logs staffdeck \| grep Lark` | 包含 `connected to wss://msg-frontier.feishu.cn/ws/v2` |
| **数字员工状态** | 登录后台查看“数字员工”列表 | 实时销售汇报员、店铺改价监控员处于活跃状态 |
| **数据源测试** | 进入“数据查询 -> 数据源管理”点击测试连接 | 提示“连接成功” |
| **查询模板执行** | 进入“数据查询 -> 查询模板”执行试跑 | 成功返回真实业务数据结果表格 |
| **定时任务** | 进入“定时任务”列表 | 任务处于“运行中”，下次触发时间显示正确 |

---

## 九、日常运维与备份管理

### 1. 常用运维指令
```bash
cd /opt/staffdeck/deploy

# 1. 实时跟踪应用日志
docker compose logs -f

# 2. 仅看最近 50 条日志
docker compose logs --tail 50

# 3. 查看资源占用 (CPU/内存)
docker stats staffdeck

# 4. 进入容器内交互式终端
docker exec -it staffdeck bash

# 5. 平滑重启
docker compose restart
```

### 2. 数据库定期备份
建议将以下脚本加入服务器 `crontab`（每日凌晨执行备份）：

```bash
#!/bin/bash
# /opt/staffdeck/scripts/backup_db.sh
BACKUP_DIR="/opt/staffdeck/backups"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# 借助 SQLite 在线备份功能安全导出
docker exec staffdeck python -c "
import sqlite3
src = sqlite3.connect('/app/data/skill_agent_loop.db')
dst = sqlite3.connect('/app/data/backup_temp.db')
src.backup(dst)
dst.close()
src.close()
"

mv /opt/staffdeck/deploy/data/backup_temp.db "$BACKUP_DIR/skill_agent_loop_$DATE.db"
gzip "$BACKUP_DIR/skill_agent_loop_$DATE.db"

# 仅保留最近 30 天的备份
find "$BACKUP_DIR" -name "*.db.gz" -mtime +30 -delete
```

### 3. 代码更新升级流程
当后续代码仓库有新版本发布时：
```bash
cd /opt/staffdeck

# 1. 拉取最新代码
git pull origin main

# 2. 重新构建并平滑拉起（保持 data 卷不动）
cd deploy
docker compose up -d --build

# 3. 检查升级后健康状态
docker compose ps
docker compose logs --tail 30
```

---

## 十、Nginx 生产反向代理配置

若需要通过域名或标准 80/443 端口对外提供服务，请在宿主机或网关 Nginx 中添加如下配置：

```nginx
server {
    listen 80;
    server_name staffdeck.yourdomain.com;

    # 1. 关键：关闭缓冲以支持大模型 SSE 流式输出（避免打字机卡顿）
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;

    # 2. 长时间任务执行超时防中断
    proxy_connect_timeout 300s;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    # 3. 支持 WebSocket（用于飞书/前端实时通道）
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    location / {
        proxy_pass http://127.0.0.1:5173;
    }
}
```
