# StaffDeck Docker 部署指南

本目录包含将 StaffDeck 作为独立容器部署到 Linux / Windows 服务器所需的所有 Docker 构建、编排文件及运维管理脚本。

---

## 目录结构说明

```
deploy/
├── Dockerfile              # 多阶段 Docker 镜像构建文件 (Node 20 前端构建 + Python 3.11 单端口运行)
├── Dockerfile.dockerignore # Docker 镜像构建忽略规则
├── .dockerignore           # Docker 忽略规则
├── docker-compose.yml      # Docker Compose 服务编排定义 (端口映射、持久化挂载、健康检查)
├── requirements.txt        # 提取的后端依赖清单 (用于 Docker 缓存层加速)
├── .env.example            # 环境变量配置模板
├── deploy.sh               # Linux / macOS 部署与运维生命周期脚本
├── deploy.ps1              # Windows PowerShell 部署与运维生命周期脚本
├── nginx.conf              # 生产环境 Nginx 反向代理配置参考 (支持 SSE 流式与 WebSocket)
└── data/                   # (自动生成) 业务持久化数据目录 (SQLite 数据库、上传附件、工作区等)
```

---

## 极速开始 (Linux / macOS)

### 1. 初始化配置
```bash
cd deploy
./deploy.sh init
```
脚本会自动在 `deploy/` 下创建 `.env` 文件。

### 2. 配置大模型接口
编辑 `deploy/.env`：
```bash
# 替换为生产环境的强随机安全密钥
APP_SECRET=your-production-secret-random-key-32chars

# 配置 OpenAI 或兼容接口 (如 DeepSeek, 通义千问, Moonshot 等)
DEMO_MODEL_BASE_URL=https://api.openai.com/v1
DEMO_MODEL_NAME=gpt-4o
DEMO_MODEL_API_KEY=sk-your-model-api-key
```

### 3. 一键启动
```bash
./deploy.sh up
```
该命令会自动完成：
1. 前端 React SPA 生产包构建
2. Python 后端运行环境组装
3. 后台启动容器并进行健康状态探测

### 4. 访问服务
- **网页控制台**：浏览器打开 `http://<服务器IP>:5173`
- **默认管理员账号**：`admin`
- **默认管理员密码**：`admin` *(登录后请务必在右上角修改密码)*

---

## 运维管理常用命令

| 操作 | Linux / macOS | Windows PowerShell | 说明 |
| :--- | :--- | :--- | :--- |
| **启动/更新** | `./deploy.sh up` | `.\deploy.ps1 up` | 构建并启动服务（后台运行） |
| **停止服务** | `./deploy.sh down` | `.\deploy.ps1 down` | 停止并移除容器 |
| **查看状态** | `./deploy.sh status` | `.\deploy.ps1 status` | 检查容器运行状态与健康探测 |
| **查看实时日志** | `./deploy.sh logs` | `.\deploy.ps1 logs` | 跟踪容器最新输出日志 |
| **重启服务** | `./deploy.sh restart` | `.\deploy.ps1 restart` | 重启应用容器 |
| **备份业务数据** | `./deploy.sh backup` | `.\deploy.ps1 backup` | 将 `data/` 目录打包归档 |

---

## 生产环境 Nginx 反向代理与 HTTPS 配置

若需使用域名访问并通过 80/443 暴露服务，请参考 `deploy/nginx.conf`。

关键配置项：
1. **禁用代理缓冲 (`proxy_buffering off;`)**：
   StaffDeck 与大模型的对话以及执行过程依赖 **SSE (Server-Sent Events) 流式推送**。若未关闭 Nginx 缓冲，会造成打字机输出卡顿或一次性吐出。
2. **启用 WebSocket 支持**：
   ```nginx
   proxy_http_version 1.1;
   proxy_set_header Upgrade $http_upgrade;
   proxy_set_header Connection "upgrade";
   ```
3. **增加超时限制**：
   复杂 SOP 执行可能需要较长时间，建议配置 `proxy_read_timeout 600s;`。

---

## 常见问题与说明

1. **数据保存在哪里？**
   所有数据保存在 `deploy/data/` 目录下（包括 SQLite 数据库 `skill_agent_loop.db`、上传文件、工作区等），即使重新构建镜像或更新代码，数据也不会丢失。
2. **为什么容器需要普通用户与 `cap_add: [SYS_ADMIN]`？**
   StaffDeck 内置了进程沙箱与隔离机制（Linux 下使用 `bubblewrap`）。容器内使用非 root 账户 `staffdeck`，并在 `docker-compose.yml` 中赋予 `SYS_ADMIN` 权限，以支持数字员工在隔离沙箱中执行 Linux 指令。
