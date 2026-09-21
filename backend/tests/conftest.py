# ============================================================================
# pytest 基线配置
#
# 重要：必须在 import app.* 之前设置环境变量，
# 因为 app.core.config 在 import 时就会读取环境。
# ============================================================================

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# ---- 测试专用环境（独立数据库 + 本地存储，不碰 MinIO / 开发库）----
TEST_DB = os.getenv("TEST_MYSQL_DATABASE", "material_center_test")
_TMP_STORAGE = Path(tempfile.mkdtemp(prefix="mc-test-storage-"))

os.environ.setdefault("MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("MYSQL_PORT", "3307")
os.environ.setdefault("MYSQL_USER", "root")
os.environ.setdefault("MYSQL_PASSWORD", "")
os.environ["MYSQL_DATABASE"] = TEST_DB
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_ROOT"] = str(_TMP_STORAGE)
# 图片搜索：测试用 numpy 内存索引（快、稳，不依赖 milvus-lite 二进制与网络）；
# 模型权重指向 backend/models 下的本地文件（不联网下载）
os.environ["IMAGE_SEARCH_VECTOR_DB"] = "numpy"
os.environ["IMAGE_SEARCH_MODEL_WEIGHTS"] = str(BACKEND_ROOT / "models" / "resnet50-0676ba61.pth")
os.environ["PYTHONIOENCODING"] = "utf-8"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
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
        raise RuntimeError(
            f"alembic {' '.join(args)} 失败:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    """整轮测试前重建测试库并 upgrade head；结束后清理。"""
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


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------- 数据清理


@pytest.fixture(autouse=True)
def clean_tables():
    """
    每个测试前清空业务表 + 重置 MAT 序列。

    使用 DELETE 而非 TRUNCATE，避免外键顺序问题；禁用外键检查以简化。
    """
    with engine.begin() as conn:

        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in [
            "activity_logs",
            "order_buyer_assets",
            "order_import_batch_items",
            "order_items",
            "order_import_batches",
            "material_pairings",
            "variant_revisions",
            "material_variants",
            "derivative_batches",
            "design_package_materials",
            "material_psd_revisions",
            "upload_sessions",
            "package_uploads",
            "package_upload_assets",
            "materials",
            "assets",
            "design_packages",
            "asset_image_embeddings",
            "order_import_batches",
            "order_items",
            "order_import_batch_items",
            "order_buyer_assets",
            "material_url_bindings",
            "asin_variant_bindings",
            "variant_effect_images",
        ]:
            conn.execute(text(f"DELETE FROM {table}"))
        conn.execute(text("UPDATE code_sequences SET current_value = 0 WHERE name = 'material'"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    yield


# ---------------------------------------------------------------- 测试文件


def _png_bytes(color: tuple[int, int, int], size: int = 64, tag: str = "") -> bytes:
    """生成可解码的 PNG（tag 只影响像素细节，用于制造不同 pHash）。"""
    img = Image.new("RGB", (size, size), color)
    if tag:
        pixels = img.load()
        for x in range(0, size, 4):
            for y in range(0, size, 4):
                pixels[x, y] = ((color[0] + x * 3) % 256, (color[1] + y * 5) % 256, 200)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _striped_png(index: int, size: int = 128) -> bytes:
    """
    与 _patterned_png 主图**视觉完全不同**的图片（密集细条纹）。
    用来证明主副素材配对只按同名 pairKey，跟图片内容毫无关系。

    条纹密度与相位由 index 决定，再在角落写入 index 的二进制条码，
    保证任何两个不同 index 的字节都不同（不然「V2 内容有变化」的用例会失去意义）。
    """
    img = Image.new("RGB", (size, size), (255, 255, 255))
    pixels = img.load()
    step = 2 + (index % 4)
    for x in range(size):
        for y in range(size):
            if (x + y) % step == 0:
                pixels[x, y] = (10, 10, 10)
    for bit in range(12):
        if (index >> bit) & 1:
            pixels[2 + bit * 3, 2] = (0, 0, 0)
            pixels[2 + bit * 3, 3] = (0, 0, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _patterned_png(index: int, *, variant: bool = False, size: int = 128) -> bytes:
    """
    生成「低频特征可区分」的测试图片。

    为什么不能只用纯色：纯色图的 8×8 DCT 交流系数几乎全为 0，pHash 与颜色无关，
    任意两张纯色图的 pHash 会完全相同，匹配测试就变成「全 100%」的假通过。
    这里在 8×8 网格的不同格子里画黑块，让每个 index 有独立的低频签名；
    variant=True 时只改动角落极小区域，字节不同（BLAKE3 不同）但 pHash 仍与主图接近。
    """
    img = Image.new("RGB", (size, size), (245, 245, 245))
    pixels = img.load()
    cell = size // 8
    slot = (index - 1) % 64
    cx, cy = slot % 8, slot // 8
    for x in range(cx * cell, (cx + 1) * cell):
        for y in range(cy * cell, (cy + 1) * cell):
            pixels[x, y] = (24, 24, 24)
    if variant:
        # 副图：同图案 + 角落小改动（字节不同 → BLAKE3 不同；低频不变 → pHash 接近）
        pixels[0, 0] = (255, 0, 0)
        pixels[1, 0] = (0, 255, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpg_bytes(color: tuple[int, int, int], size: int = 64) -> bytes:
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _psd_bytes(size: int = 1024) -> bytes:
    """最小可用的伪 PSD 字节（Phase 1 不解码 PSD，只校验 mime / 存储 / hash 约束）。"""
    header = b"8BPS\x00\x01" + b"\x00" * 6 + (2).to_bytes(2, "big")
    return header + b"\x00" * max(0, size - len(header))


@pytest.fixture()
def png_bytes():
    return _png_bytes


@pytest.fixture()
def jpg_bytes():
    return _jpg_bytes


@pytest.fixture()
def psd_bytes():
    return _psd_bytes


# ---------------------------------------------------------------- 便捷操作


@pytest.fixture()
def create_package(client):
    def _create(
        name: str = "万圣节1",
        remark: str | None = None,
        designer_name: str = "设计美工-肖芸",
        designer_id: str | None = None,
        created_by: str = "实际上传人-小柯",
        design_code: str = "DS-20260916-001",
    ) -> dict:
        response = client.post(
            "/api/design-packages",
            json={
                "name": name,
                "designCode": design_code,
                "remark": remark,
                "designerName": designer_name,
                "designerId": designer_id,
                "createdBy": created_by,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _create


@pytest.fixture()
def create_upload(client):
    def _create(package_id: str, **overrides) -> dict:
        payload = {
            "originalPackageName": "万圣节夜景球迷款-20260916.zip",
            # 实际上传人 / 归属运营都只给姓名：没有 userId 也必须能提交（第七、三十八条）
            "uploaderName": "实际上传人-小柯",
            "operatorId": "zhang",
            "operatorName": "张三",
            "uploadType": "INITIAL",
        }
        payload.update(overrides)
        response = client.post(f"/api/design-packages/{package_id}/uploads", json=payload)
        assert response.status_code == 200, response.text
        return response.json()

    return _create


@pytest.fixture()
def upload_file(client):
    def _upload(
        upload_id: str,
        content: bytes,
        filename: str,
        content_type: str,
        kind: str = "MAIN",
        file_role: str | None = None,
    ) -> dict:
        files = {"file": (filename, content, content_type)}
        data = {"kind": kind}
        if file_role:
            data["fileRole"] = file_role
        response = client.post(
            f"/api/uploads/{upload_id}/files",
            files=files,
            data=data,
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _upload


@pytest.fixture()
def phase2_package(client, create_package, create_upload, upload_file):
    """
    建一个「已上传 + 已提交位置 + 已按同名 pairKey 配对」的设计包。

    **命名即配对**：主图 1.png 与副图 1.png 同名 → pair_key = "1"。
    图片内容刻意做成视觉上完全不同（主图是大色块、副图是密集细条纹），
    用来证明配对与图片内容无关。

    返回 dict：pkg / upload_id / mains / variants / position_count / pairing
    """

    def _build(
        main_count: int = 4,
        variant_count: int | None = None,
        *,
        package_name: str = "Phase2包",
        variant_contents: dict[int, bytes] | None = None,
        variant_names: dict[int, str] | None = None,
        design_code: str = "DS-20260916-001",
        tags: list[str] | None = None,
        responsible_name: str = "肖芸",
    ):
        variant_count = main_count if variant_count is None else variant_count
        pkg = create_package(package_name)
        # 把标签/负责人/设计编码补到这个包上（真实创建路径在 create_package 里，
        # 这里用 PATCH 保持 fixture 的调用形态不变）
        if tags is not None or responsible_name != "肖芸":
            client.patch(
                f"/api/design-packages/{pkg['id']}",
                json={"tags": tags or [], "responsibleName": responsible_name, "designCode": design_code},
            )
            pkg = client.get(f"/api/design-packages/{pkg['id']}").json()
        upload = create_upload(pkg["id"])
        upload_id = upload["packageUpload"]["id"]

        mains = []
        for index in range(1, main_count + 1):
            asset = upload_file(
                upload_id,
                _patterned_png(index),
                f"{index}.png",
                "image/png",
                kind="MAIN",
                file_role="MAIN_PREVIEW",
            )
            mains.append(asset)

        variants = []
        for index in range(1, variant_count + 1):
            content = (variant_contents or {}).get(index) or _striped_png(index)
            name = (variant_names or {}).get(index, f"{index}.png")
            asset = upload_file(
                upload_id, content, name, "image/png", kind="VARIANT", file_role="VARIANT"
            )
            variants.append(asset)

        submit = client.post(
            f"/api/uploads/{upload_id}/materials",
            json={
                "actor": "实际上传人-小柯",
                "materials": [
                    {
                        "position": index + 1,
                        "previewAssetId": asset["assetId"],
                        "sourceFileName": asset["originalFilename"],
                    }
                    for index, asset in enumerate(mains)
                ],
                "linkAssetIds": [a["assetId"] for a in mains + variants],
            },
        )
        assert submit.status_code == 200, submit.text

        pair = client.post(f"/api/uploads/{upload_id}/pair")
        assert pair.status_code == 200, pair.text

        return {
            "pkg": pkg,
            "upload_id": upload_id,
            "mains": mains,
            "variants": variants,
            "position_count": main_count,
            "pairing": pair.json(),
        }

    return _build
