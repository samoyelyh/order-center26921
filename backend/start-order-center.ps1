# 启动 order-center 后端（独立订单中心）
# 用法：在 backend 目录执行  powershell -ExecutionPolicy Bypass -File start-order-center.ps1
$ErrorActionPreference = "Stop"

$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "3307"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = ""
$env:MYSQL_DATABASE = "order_center"
$env:STORAGE_BACKEND = "local"
$env:LOCAL_STORAGE_ROOT = "./.storage"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# 图片匹配：轻量或 ResNet-50（按部署环境提供权重）
$env:IMAGE_SEARCH_VECTOR_DB = "numpy"
$env:IMAGE_SEARCH_MODEL_WEIGHTS = "models/resnet50-0676ba61.pth"

# 素材平台契约：生产/预发布必须提供；Fake 只允许开发/测试。
$env:APP_ENV = "development"
$env:MATERIAL_PLATFORM_ALLOW_FAKE = "1"
$env:MATERIAL_PLATFORM_BASE_URL = "http://127.0.0.1:8000"

Write-Host "启动 order-center 后端 → http://127.0.0.1:8010"
Write-Host "（素材平台: $env:MATERIAL_PLATFORM_BASE_URL；Ctrl+C 停止）"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
