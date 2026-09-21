# 启动素材中心后端（含图片搜索 Milvus + ResNet-50 所需环境变量）
# 用法：在 backend 目录执行  powershell -ExecutionPolicy Bypass -File start-backend.ps1
$ErrorActionPreference = "Stop"

$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_PORT = "3307"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = ""
$env:MYSQL_DATABASE = "material_center"
$env:STORAGE_BACKEND = "local"
$env:LOCAL_STORAGE_ROOT = "./.storage"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

# 图片搜索：Milvus Lite + ResNet-50
#   向量检索后端：milvus（默认，生产）/ numpy（内存索引，无额外依赖）
$env:IMAGE_SEARCH_VECTOR_DB = "milvus"
# Milvus 数据文件务必用英文路径（faiss 无法写中文目录）
$env:IMAGE_SEARCH_MILVUS_PATH = "$env:LOCALAPPDATA\mc-milvus\mc.db"
# ResNet-50 权重：backend/models/resnet50-0676ba61.pth（首次需联网下载一次）
$env:IMAGE_SEARCH_MODEL_WEIGHTS = "models/resnet50-0676ba61.pth"

Write-Host "启动素材中心后端（Milvus + ResNet-50）→ http://127.0.0.1:8000"
Write-Host "（Ctrl+C 停止；若进程意外退出，重新执行本脚本即可）"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
