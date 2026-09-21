# ============================================================================
# 并发场景测试（第二十一条）
#
#   MAT 编码并发     → 不允许重复
#   同会话重复提交   → 幂等
#   同 position 并发 → 只能成功一个，另一个明确报错
# ============================================================================

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from app.db.models import DesignPackageMaterial, Material
from app.db.session import SessionLocal
from app.services.repository import allocate_material_codes


def test_allocate_material_codes_is_concurrency_safe(db_session):
    """多线程各自事务取号，不允许出现重复编码。"""
    workers = 8
    per_worker = 5

    def worker(_: int) -> list[str]:
        session = SessionLocal()
        try:
            codes = allocate_material_codes(session, per_worker)
            session.commit()
            return codes
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        batches = list(pool.map(worker, range(workers)))

    all_codes = [code for batch in batches for code in batch]
    assert len(all_codes) == workers * per_worker
    assert len(set(all_codes)) == len(all_codes), "并发取号产生了重复 MAT 编码"

    # 序列游标应当正好推进到总数
    current = db_session.execute(
        text("SELECT current_value FROM code_sequences WHERE name = 'material'")
    ).scalar_one()
    assert current == workers * per_worker


def test_concurrent_upload_session_creation_is_idempotent(client, db_session, create_package):
    """并发对同一 uploadSessionId 创建上传记录：只应产生一条。"""
    pkg = create_package()
    payload = {
        "originalPackageName": "race.zip",
        "uploadSessionId": "sess-race-001",
        "uploaderName": "肖芸",
    }

    def create(_: int):
        return client.post(f"/api/design-packages/{pkg['id']}/uploads", json=payload)

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(create, range(6)))

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    ids = {r.json()["packageUpload"]["id"] for r in responses}
    assert len(ids) == 1, f"同一 uploadSessionId 产生了多个上传记录：{ids}"

    rows = db_session.execute(
        text("SELECT COUNT(*) FROM package_uploads WHERE upload_session_id = 'sess-race-001'")
    ).scalar_one()
    assert rows == 1


def test_concurrent_position_submit_allows_only_one(
    client, db_session, create_package, create_upload, upload_file
):
    """并发提交同一 position：只能成功一次，其余明确 POSITION_CONFLICT。"""
    pkg = create_package("并发位置包")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    first_asset = upload_file(upload_id, _png(1), "1.png", "image/png")
    second_asset = upload_file(upload_id, _png(2), "2.png", "image/png")

    def submit(asset_id: str):
        return client.post(
            f"/api/uploads/{upload_id}/materials",
            json={"materials": [{"position": 1, "previewAssetId": asset_id, "sourceFileName": "1.png"}]},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, [first_asset["assetId"], second_asset["assetId"]]))

    codes = [r.status_code for r in responses]
    assert codes.count(200) == 1, f"同一 position 应只成功一次，实际：{codes}"
    assert codes.count(409) == 1, f"另一个应为 409 冲突，实际：{codes}"

    conflict = next(r for r in responses if r.status_code == 409)
    assert conflict.json()["code"] == "POSITION_CONFLICT"

    db_session.expire_all()
    positions = db_session.execute(select(DesignPackageMaterial)).scalars().all()
    assert len(positions) == 1
    materials = db_session.execute(select(Material)).scalars().all()
    assert len(materials) == 1


def _png(seed: int) -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (32, 32), (seed * 20 % 256, 40, 200))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
