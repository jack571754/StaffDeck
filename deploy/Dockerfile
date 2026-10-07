# ==========================================
# 阶段 1: 前端静态资源构建 (Node.js 20)
# ==========================================
FROM node:20-bookworm-slim AS frontend-builder

WORKDIR /build/frontend-enterprise

# 先复制依赖定义，利用 Docker 缓存层
COPY frontend-enterprise/package*.json ./
RUN npm ci --no-audit --no-fund

# 复制前端完整源码并构建生产 bundle
COPY frontend-enterprise/ ./
RUN npm run build

# ==========================================
# 阶段 2: Python 后端与一体化运行环境 (Python 3.11)
# ==========================================
FROM python:3.11-slim-bookworm AS runner

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ULTRARAG_DATA_DIR=/app/data \
    DATABASE_URL=sqlite:////app/data/skill_agent_loop.db \
    APP_PORT=5173

# 安装基础运行系统依赖与沙箱工具 (bubblewrap)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    bubblewrap \
    bash \
    procps \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 业务运行用户 (UID 1000)
RUN useradd -m -u 1000 -s /bin/bash staffdeck

WORKDIR /app

# 利用独立 requirements.txt 预先安装 Python 依赖库，最大化利用 Docker 缓存
COPY deploy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

# 复制后端源码并以可编辑模式注册
COPY backend /app/backend
RUN pip install --no-cache-dir --no-deps -e /app/backend

# 复制阶段 1 前端构建产物 (dist)
COPY --from=frontend-builder /build/frontend-enterprise/dist /app/frontend-enterprise/dist

# 创建持久化数据目录并授予 staffdeck 用户权限
RUN mkdir -p /app/data && chown -R staffdeck:staffdeck /app

USER staffdeck
WORKDIR /app/backend

EXPOSE 5173

# 启动单端口整合服务 (支持 API + 前端 SPA 静态托管)
CMD ["python", "-m", "uvicorn", "single_port_app:app", "--host", "0.0.0.0", "--port", "5173"]
