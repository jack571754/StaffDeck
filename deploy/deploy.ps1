# ==============================================================================
# StaffDeck Docker 服务部署与生命周期管理脚本 (Windows PowerShell)
# ==============================================================================
param (
    [Parameter(Position = 0)]
    [ValidateSet("init", "up", "start", "down", "stop", "restart", "build", "logs", "status", "backup", "help")]
    [string]$Action = "help"
)

$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $DeployDir

# 检测 docker compose
$composeCmd = "docker compose"
try {
    & docker compose version | Out-Null
} catch {
    Write-Error "❌ 错误: 未检测到 Docker Compose。请确保已安装 Docker Desktop 并处于运行状态。"
    exit 1
}

function Ensure-Env {
    $envPath = Join-Path $DeployDir ".env"
    $examplePath = Join-Path $DeployDir ".env.example"
    if (-not (Test-Path $envPath)) {
        if (Test-Path $examplePath) {
            Write-Host "ℹ️ 未找到 .env，正在从 .env.example 初始化..." -ForegroundColor Cyan
            Copy-Item $examplePath $envPath
            Write-Host "⚠️ 已生成 .env 文件，请务必根据需要编辑其中的大模型配置与 APP_SECRET！" -ForegroundColor Yellow
        } else {
            Write-Error "❌ 错误: 未找到 .env 且不存在 .env.example。"
            exit 1
        }
    }
    $dataPath = Join-Path $DeployDir "data"
    if (-not (Test-Path $dataPath)) {
        New-Item -ItemType Directory -Path $dataPath | Out-Null
    }
}

function Show-Status {
    Write-Host "==> 容器运行状态:" -ForegroundColor Cyan
    & docker compose ps

    $port = "5173"
    $envFile = Join-Path $DeployDir ".env"
    if (Test-Path $envFile) {
        $lines = Get-Content $envFile
        foreach ($line in $lines) {
            if ($line -match "^STAFFDECK_PORT=(.*)$") {
                $port = $matches[1].Trim(' "')
                break
            }
        }
    }

    Write-Host "`n==> 探测服务接口 (http://127.0.0.1:$port/api/health):" -ForegroundColor Cyan
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/health" -TimeoutSec 3 -ErrorAction Stop
        Write-Host "✅ 服务健康状态正常！访问地址: http://127.0.0.1:$port" -ForegroundColor Green
    } catch {
        Write-Host "⏳ 服务可能仍在初始化中或暂未就绪，可执行 '.\deploy.ps1 logs' 查看实时日志。" -ForegroundColor Yellow
    }
}

switch ($Action) {
    "init" {
        Ensure-Env
        Write-Host "✅ 环境初始化完成。数据持久化目录位于: $DeployDir\data" -ForegroundColor Green
    }
    { $_ -in "up", "start" } {
        Ensure-Env
        Write-Host "==> 启动 StaffDeck 服务 (后台运行)..." -ForegroundColor Cyan
        & docker compose up -d --build
        Start-Sleep -Seconds 4
        Show-Status
    }
    { $_ -in "down", "stop" } {
        Write-Host "==> 停止并移除 StaffDeck 容器..." -ForegroundColor Cyan
        & docker compose down
        Write-Host "✅ 服务已停止。" -ForegroundColor Green
    }
    "restart" {
        Write-Host "==> 重启 StaffDeck 容器..." -ForegroundColor Cyan
        & docker compose restart
        Start-Sleep -Seconds 3
        Show-Status
    }
    "build" {
        Ensure-Env
        Write-Host "==> 开始构建 StaffDeck Docker 镜像..." -ForegroundColor Cyan
        & docker compose build
        Write-Host "✅ 镜像构建完成。" -ForegroundColor Green
    }
    "logs" {
        & docker compose logs -f --tail 100
    }
    "status" {
        Show-Status
    }
    "backup" {
        $timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
        $backupZip = Join-Path $DeployDir "backup_staffdeck_$timestamp.zip"
        $dataPath = Join-Path $DeployDir "data"
        if (Test-Path $dataPath) {
            Write-Host "==> 正在压缩备份业务数据目录 (data\) ..." -ForegroundColor Cyan
            Compress-Archive -Path $dataPath -DestinationPath $backupZip
            Write-Host "✅ 备份成功: $backupZip" -ForegroundColor Green
        } else {
            Write-Warning "未找到数据目录 $dataPath，跳过备份。"
        }
    }
    Default {
        Write-Host "StaffDeck Docker 部署管理脚本 (PowerShell)" -ForegroundColor Cyan
        Write-Host "`n用法: .\deploy.ps1 [命令]"
        Write-Host "`n可用命令:"
        Write-Host "  init      初始化配置文件与数据目录 (.env, data/)"
        Write-Host "  up        启动服务 (自动构建并后台运行)"
        Write-Host "  down      停止并销毁容器"
        Write-Host "  restart   重启服务容器"
        Write-Host "  build     仅重新构建 Docker 镜像"
        Write-Host "  logs      跟踪实时容器输出日志"
        Write-Host "  status    检查容器运行状态与健康接口"
        Write-Host "  backup    备份数据目录 (zip)"
        Write-Host "  help      查看帮助信息`n"
    }
}
