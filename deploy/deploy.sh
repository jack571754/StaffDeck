#!/usr/bin/env bash
# ==============================================================================
# StaffDeck Docker 服务部署与生命周期管理脚本 (Linux / macOS)
# ==============================================================================
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEPLOY_DIR"

# 自动检测 docker compose 命令
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    echo "❌ 错误: 未检测到 Docker Compose。请先安装 Docker 和 Docker Compose。" >&2
    exit 1
fi

check_env() {
    if [[ ! -f "$DEPLOY_DIR/.env" ]]; then
        if [[ -f "$DEPLOY_DIR/.env.example" ]]; then
            echo "ℹ️  未找到 .env，正在从 .env.example 初始化..."
            cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
            echo "⚠️  已生成 $DEPLOY_DIR/.env 文件，请务必根据需要编辑其中的模型配置与 APP_SECRET！"
        else
            echo "❌ 错误: 未找到 .env 且不存在 .env.example。" >&2
            exit 1
        fi
    fi
    mkdir -p "$DEPLOY_DIR/data"
}

action_init() {
    echo "==> 初始化部署环境..."
    check_env
    echo "✅ 环境就绪。数据目录位于: $DEPLOY_DIR/data"
}

action_build() {
    check_env
    echo "==> 开始构建 StaffDeck Docker 镜像 (前后端多阶段构建)..."
    $COMPOSE_CMD build
    echo "✅ 镜像构建完成。"
}

action_up() {
    check_env
    echo "==> 启动 StaffDeck 服务 (后台模式)..."
    $COMPOSE_CMD up -d --build
    echo "==> 等待服务启动并进行健康检查..."
    sleep 5
    action_status
}

action_down() {
    echo "==> 停止并移除 StaffDeck 容器..."
    $COMPOSE_CMD down
    echo "✅ 服务已停止。"
}

action_restart() {
    echo "==> 重启 StaffDeck 容器..."
    $COMPOSE_CMD restart
    sleep 3
    action_status
}

action_logs() {
    $COMPOSE_CMD logs -f --tail=100
}

action_status() {
    echo "==> 容器运行状态:"
    $COMPOSE_CMD ps
    echo ""
    PORT="5173"
    if [[ -f "$DEPLOY_DIR/.env" ]]; then
        CONF_PORT=$(grep -E '^STAFFDECK_PORT=' "$DEPLOY_DIR/.env" | cut -d'=' -f2 | tr -d ' "\r' || true)
        if [[ -n "$CONF_PORT" ]]; then
            PORT="$CONF_PORT"
        fi
    fi
    echo "==> 探测服务接口 (http://127.0.0.1:${PORT}/api/health):"
    if curl -s -f "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
        echo "✅ 服务健康状态正常！访问地址: http://127.0.0.1:${PORT}"
    else
        echo "⏳ 服务可能仍在初始化中或暂未就绪，可执行 './deploy.sh logs' 查看实时日志。"
    fi
}

action_backup() {
    BACKUP_FILE="$DEPLOY_DIR/backup_staffdeck_$(date +%Y%m%d_%H%M%S).tar.gz"
    echo "==> 正在备份业务数据目录 (data/) ..."
    if [[ -d "$DEPLOY_DIR/data" ]]; then
        tar -czf "$BACKUP_FILE" -C "$DEPLOY_DIR" data
        echo "✅ 备份成功: $BACKUP_FILE"
    else
        echo "⚠️  未发现数据目录 $DEPLOY_DIR/data，跳过备份。"
    fi
}

show_help() {
    echo "StaffDeck Docker 部署管理脚本"
    echo ""
    echo "用法: ./deploy.sh [命令]"
    echo ""
    echo "可用命令:"
    echo "  init      初始化配置文件与数据目录 (.env, data/)"
    echo "  up        启动服务 (自动构建并后台运行)"
    echo "  down      停止并销毁容器"
    echo "  restart   重启服务容器"
    echo "  build     仅重新构建 Docker 镜像"
    echo "  logs      跟踪实时容器输出日志"
    echo "  status    检查容器运行状态与健康接口"
    echo "  backup    备份 SQLite 数据库及所有业务数据 (tar.gz)"
    echo "  help      查看帮助信息"
    echo ""
}

CMD="${1:-help}"
case "$CMD" in
    init)
        action_init
        ;;
    up|start)
        action_up
        ;;
    down|stop)
        action_down
        ;;
    restart)
        action_restart
        ;;
    build)
        action_build
        ;;
    logs)
        action_logs
        ;;
    status)
        action_status
        ;;
    backup)
        action_backup
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "未知命令: $CMD" >&2
        show_help
        exit 1
        ;;
esac
