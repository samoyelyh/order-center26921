# ============================================================================
# order-center pytest 基线配置
#
# 重要：必须在 import app.* 之前设置环境变量（app.core.config import 时读取）。
# 订单测试不依赖真实素材库：material platform 用 Fake 实现（见 fixture）。
# ============================================================================

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ---- 测试专用环境（独立数据库 + 本地存储）----
TEST_DB = os.getenv("TEST_MYSQL_DATABASE", "material_center_test")
_TMP_STORAGE = Path(tempfile.mkdtemp(prefix="oc-test-storage-"))

os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3307")
os.environ.setdefault("MYSQL_USER", "root")
os.environ.setdefault("MYSQL_PASSWORD", "")
os.environ["MYSQL_DATABASE"] = TEST_DB
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_ROOT"] = str(_TMP_STORAGE)
os.environ["IMAGE_SEARCH_VECTOR_DB"] = "numpy"
os.environ["IMAGE_SEARCH_MODEL_WEIGHTS"] = str(BACKEND_ROOT / "models" / "resnet50-0676ba61.pth")
# order-center 测试用 Fake material platform（不连真实素材库）
os.environ["MATERIAL_PLATFORM_BASE_URL"] = ""
os.environ["PYTHONIOENCODING"] = "utf-8"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

# ---------------------------------------------------------------- schema 管理


def _run_alembic(*args: str) -> None:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(scope="session", autouse=True)
def _reset_database():
    import pymysql

    conn = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
            cur.execute(
                f"CREATE DATABASE {TEST_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
            )
    finally:
        conn.close()
    _run_alembic("upgrade", "head")
    yield
    shutil.rmtree(_TMP_STORAGE, ignore_errors=True)


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function", autouse=True)
def clean_tables():
    """每个测试前清空订单表。"""
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in [
            "order_match_actions",
            "asin_variant_bindings",
            "material_url_bindings",
            "order_buyer_assets",
            "order_import_batch_items",
            "order_items",
            "order_import_batches",
            "order_assets",
        ]:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ================================================================ 工具


def make_order_zip(payloads: list[dict]) -> bytes:
    """把订单 JSON 打包成领星风格 ZIP（Files/order.json 或嵌套 zip）。"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for i, payload in enumerate(payloads):
            archive.writestr(f"Files/order-{i}.json", __import__("json").dumps(payload, ensure_ascii=False))
    return out.getvalue()
