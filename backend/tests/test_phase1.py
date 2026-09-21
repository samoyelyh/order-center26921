# ============================================================================
# Phase 1 自动测试
#
# 覆盖（第二十四条）：
#   设计包   创建成功 / code 唯一
#   上传会话 第一次创建 / 相同 uploadSessionId 不重复创建
#   Asset    JPG / PNG / PSD / BLAKE3 / pHash / 相同文件复用
#   Material 新图建 MAT / 相同主素材复用 MAT / 同一 MAT 被两个设计包引用
#   Position 同设计包 position 冲突拦截
#   PSD      Revision1 / current_psd_revision_id 正确
#   文件     存储失败时不落脏数据
#   事务     material 入库失败整体回滚
#   Auth     API 返回 DTO（不是裸 SQLAlchemy）
# ============================================================================

from __future__ import annotations

from sqlalchemy import select, text

from app.db.models import (
    Asset,
    DesignPackageMaterial,
    Material,
    MaterialPsdRevision,
    PackageUpload,
    UploadSession,
)
from app.services.fingerprint import compute_blake3, compute_phash
from app.services.storage import get_storage
from tests.conftest import _jpg_bytes, _png_bytes


# ================================================================ 设计包


def test_create_design_package_returns_dto(client, db_session):
    response = client.post(
        "/api/design-packages",
        json={
            "name": "万圣节1",
            "designCode": "HB-2026-0916-A",
            "remark": "首批",
            "designerName": "设计美工-肖芸",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["id"].startswith("pkg-")
    assert body["code"].startswith("DP-")
    assert body["name"] == "万圣节1"
    # 设计编码是用户填的（与系统生成的 DP- 编码并存）
    assert body["designCode"] == "HB-2026-0916-A"
    assert body["remark"] == "首批"
    # 设计美工是设计包的长期属性（第五~七条）
    assert body["designerName"] == "设计美工-肖芸"
    assert body["designerId"] is None
    # DTO 聚合字段存在，但表里没有对应列
    assert body["mainMaterialCount"] == 0
    assert body["uploadCount"] == 0

    columns = {row[0] for row in db_session.execute(text("SHOW COLUMNS FROM design_packages"))}
    for forbidden in (
        "main_material_count",
        "variant_count",
        "current_batch_no",
        "operator_id",
        "operator_name",
    ):
        assert forbidden not in columns, f"design_packages 不应有缓存字段 {forbidden}"


def test_design_package_code_unique_and_logged(client, db_session):
    # 同一个设计可以有多个设计包（新一版），所以设计编码允许重复；
    # 系统设计包编码 code 仍然必须互不相同。
    first = client.post(
        "/api/design-packages",
        json={"name": "万圣节1", "designCode": "HB-2026-0916-A", "designerName": "美工A"},
    ).json()
    second = client.post(
        "/api/design-packages",
        json={"name": "万圣节1", "designCode": "HB-2026-0916-A", "designerName": "美工A"},
    ).json()
    assert first["id"] != second["id"]
    assert first["code"] != second["code"], "同名设计包也必须得到不同 code"
    assert first["designCode"] == second["designCode"] == "HB-2026-0916-A"

    codes = [row[0] for row in db_session.execute(text("SELECT code FROM design_packages"))]
    assert len(codes) == len(set(codes))

    logs = client.get("/api/activity-logs", params={"design_package_id": first["id"]}).json()
    assert any(log["action"] == "CREATE_PACKAGE" for log in logs)


def test_archived_package_rejects_upload(client, db_session, create_package):
    pkg = create_package("归档包")
    db_session.execute(
        text("UPDATE design_packages SET archived_at = NOW() WHERE id = :id"), {"id": pkg["id"]}
    )
    db_session.commit()

    response = client.post(
        f"/api/design-packages/{pkg['id']}/uploads",
        json={"originalPackageName": "x.zip"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "DESIGN_PACKAGE_ARCHIVED"


# ================================================================ 上传会话 / 幂等


def test_create_upload_creates_upload_and_session(client, db_session, create_package):
    pkg = create_package()
    response = client.post(
        f"/api/design-packages/{pkg['id']}/uploads",
        json={
            "originalPackageName": "万圣节夜景球迷款-20260916.zip",
            "fileSize": 1024,
            "uploadSessionId": "sess-abc-001",
            "uploaderName": "肖芸",
            "operatorId": "zhang",
            "operatorName": "张三",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["packageUpload"]["uploadSessionId"] == "sess-abc-001"
    assert body["session"]["stage"] == "PARSING"
    assert body["session"]["receivedFiles"] == []
    assert body["session"]["operatorName"] == "张三"

    assert db_session.execute(select(PackageUpload)).scalars().all()
    assert db_session.execute(select(UploadSession)).scalars().all()


def test_same_upload_session_id_is_idempotent(client, db_session, create_package):
    pkg = create_package()
    payload = {
        "originalPackageName": "万圣节夜景球迷款-20260916.zip",
        "uploadSessionId": "sess-idem-001",
        "uploaderName": "肖芸",
    }
    first = client.post(f"/api/design-packages/{pkg['id']}/uploads", json=payload).json()
    second = client.post(f"/api/design-packages/{pkg['id']}/uploads", json=payload).json()

    assert first["created"] is True
    assert second["created"] is False, "相同 uploadSessionId 必须命中幂等，不能重复创建"
    assert first["packageUpload"]["id"] == second["packageUpload"]["id"]
    assert first["session"]["id"] == second["session"]["id"]

    upload_count = len(db_session.execute(select(PackageUpload)).scalars().all())
    session_count = len(db_session.execute(select(UploadSession)).scalars().all())
    assert upload_count == 1
    assert session_count == 1


def test_idempotency_header_is_supported(client, create_package):
    pkg = create_package()
    body = {"originalPackageName": "a.zip"}
    first = client.post(
        f"/api/design-packages/{pkg['id']}/uploads",
        json=body,
        headers={"Idempotency-Key": "header-key-001"},
    ).json()
    second = client.post(
        f"/api/design-packages/{pkg['id']}/uploads",
        json=body,
        headers={"Idempotency-Key": "header-key-001"},
    ).json()
    assert first["created"] is True
    assert second["created"] is False
    assert first["packageUpload"]["id"] == second["packageUpload"]["id"]


def test_upload_session_query_and_patch(client, create_upload, create_package):
    pkg = create_package()
    created = create_upload(pkg["id"])
    session_id = created["session"]["id"]

    got = client.get(f"/api/upload-sessions/{session_id}")
    assert got.status_code == 200
    assert got.json()["packageUploadId"] == created["packageUpload"]["id"]

    patched = client.patch(
        f"/api/upload-sessions/{session_id}",
        json={"operatorId": "li", "operatorName": "李四", "stage": "PARSING"},
    )
    assert patched.status_code == 200
    assert patched.json()["operatorName"] == "李四"

    missing = client.get("/api/upload-sessions/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["code"] == "UPLOAD_SESSION_NOT_FOUND"


# ================================================================ Asset / 指纹


def test_upload_png_asset_with_blake3_and_phash(client, create_package, create_upload, upload_file):
    pkg = create_package()
    created = create_upload(pkg["id"])
    content = _png_bytes((200, 30, 30), tag="a")

    body = upload_file(created["packageUpload"]["id"], content, "1.png", "image/png")

    assert body["reused"] is False
    assert body["kind"] == "IMAGE"
    assert body["blake3"] == compute_blake3(content)
    assert len(body["blake3"]) == 64
    assert body["phash"] is not None and len(body["phash"]) == 16
    assert body["phashVersion"] == 1
    assert body["width"] == 64 and body["height"] == 64
    assert body["mimeType"] == "image/png"
    assert body["storageKey"].startswith("assets/")
    assert body["uri"]

    # pHash 与参考实现一致（8x8 DCT）
    assert body["phash"] == compute_phash(content).hex()


def test_upload_jpg_asset(client, create_package, create_upload, upload_file):
    pkg = create_package()
    created = create_upload(pkg["id"])
    content = _jpg_bytes((20, 120, 200))

    body = upload_file(created["packageUpload"]["id"], content, "1-1.jpg", "image/jpeg")
    assert body["kind"] == "IMAGE"
    assert body["mimeType"] == "image/jpeg"
    assert body["width"] == 64
    assert body["phash"] is not None


def test_upload_psd_asset_has_no_phash(client, db_session, create_package, create_upload, upload_file):
    pkg = create_package()
    created = create_upload(pkg["id"])
    # 真实 PSD 二进制这里不解码：验证「不可解码的设计文件」路径
    # （blake3 / phash 必须同时为 null，满足 ck_assets_fingerprint_pair）
    content = b"8BPS" + b"\xa5" * 512

    body = upload_file(created["packageUpload"]["id"], content, "1.psd", "image/vnd.adobe.photoshop")
    assert body["kind"] == "DESIGN"
    assert body["mimeType"] == "image/vnd.adobe.photoshop"
    assert body["phash"] is None
    assert body["blake3"] is None
    assert body["phashVersion"] is None

    asset = db_session.get(Asset, body["assetId"])
    assert asset is not None and asset.phash is None and asset.blake3 is None


def test_identical_file_reuses_asset(client, db_session, create_package, create_upload, upload_file):
    pkg = create_package()
    created = create_upload(pkg["id"])
    content = _png_bytes((10, 200, 10), tag="same")
    upload_id = created["packageUpload"]["id"]

    first = upload_file(upload_id, content, "1.png", "image/png")
    second = upload_file(upload_id, content, "1-copy.png", "image/png")

    assert first["reused"] is False
    assert second["reused"] is True
    assert second["assetId"] == first["assetId"], "BLAKE3 相同必须复用已有 Asset"
    assert second["blake3"] == first["blake3"]

    assets = db_session.execute(select(Asset)).scalars().all()
    assert len(assets) == 1, "完全相同文件不应产生第二条 Asset"


def test_different_phash_for_different_image(client, create_package, create_upload, upload_file):
    pkg = create_package()
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    a = upload_file(upload_id, _png_bytes((250, 250, 250), tag="light"), "a.png", "image/png")
    b = upload_file(upload_id, _png_bytes((5, 5, 5), tag="dark"), "b.png", "image/png")

    assert a["blake3"] != b["blake3"]
    assert a["phash"] != b["phash"], "视觉差异大的两张图不应得到相同 pHash"


def test_unsupported_file_type_rejected(client, create_package, create_upload):
    pkg = create_package()
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    response = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "UNSUPPORTED_FILE_TYPE"


def test_empty_file_rejected(client, create_package, create_upload):
    pkg = create_package()
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    response = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "EMPTY_FILE"


def test_storage_failure_leaves_no_asset_row(
    client, db_session, monkeypatch, create_package, create_upload, upload_file
):
    """MinIO / 本地存储写入失败时，不产生 Asset 脏数据，并返回可读错误。"""
    from app.services.storage import StorageError

    pkg = create_package()
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    def _boom(self, storage_key, data, content_type):  # noqa: ANN001
        raise StorageError("模拟 MinIO 写入失败")

    monkeypatch.setattr(type(get_storage()), "put", _boom, raising=True)

    response = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": ("boom.png", _png_bytes((1, 2, 3)), "image/png")},
    )
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "STORAGE_WRITE_FAILED"
    assert "MinIO" in body["message"] or "存储" in body["message"]

    assert db_session.execute(select(Asset)).scalars().all() == []

    # 失败动作必须留下维护记录
    logs = client.get("/api/activity-logs", params={"design_package_id": pkg["id"]}).json()
    assert any(log["action"] == "UPLOAD_FAILED" for log in logs)


def test_asset_lookup_and_not_found(client, create_package, create_upload, upload_file):
    pkg = create_package()
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    body = upload_file(upload_id, _png_bytes((60, 60, 60)), "x.png", "image/png")

    got = client.get(f"/api/assets/{body['assetId']}")
    assert got.status_code == 200
    assert got.json()["blake3"] == body["blake3"]

    missing = client.get("/api/assets/nope")
    assert missing.status_code == 404
    assert missing.json()["code"] == "ASSET_NOT_FOUND"


# ================================================================ Material / 位置


def _prepare(package_name: str, mains: list[tuple[str, bytes, str, bytes | None]]):
    """辅助：建包 → 建上传 → 传主素材预览(+可选 PSD) → 返回 (pkg, upload, assets)"""
    return package_name, mains


def test_submit_materials_creates_mat_and_position(
    client, db_session, create_package, create_upload, upload_file
):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    asset = upload_file(upload_id, _png_bytes((100, 20, 20)), "1.png", "image/png")
    psd = upload_file(upload_id, b"8BPS" + b"\x00" * 256, "1.psd", "image/vnd.adobe.photoshop", kind="PSD")

    response = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "actor": "肖芸",
            "materials": [
                {
                    "position": 1,
                    "previewAssetId": asset["assetId"],
                    "psdAssetId": psd["assetId"],
                    "sourceFileName": "1.png",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["createdMaterialCount"] == 1
    assert body["reusedMaterialCount"] == 0
    assert len(body["positions"]) == 1

    position = body["positions"][0]
    assert position["position"] == 1
    assert position["material"]["materialCode"] == "MAT-000001"
    assert position["previewUri"]
    assert position["psdUri"]

    material = db_session.get(Material, "MAT-000001")
    assert material is not None
    assert material.preview_asset_id == asset["assetId"]
    assert material.current_psd_revision_id is not None

    revision = db_session.get(MaterialPsdRevision, material.current_psd_revision_id)
    assert revision is not None
    assert revision.revision_no == 1
    assert revision.asset_id == psd["assetId"]
    assert revision.material_id == material.id


def test_material_codes_increment_sequentially(
    client, create_package, create_upload, upload_file
):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    items = []
    for position in range(1, 4):
        asset = upload_file(
            upload_id, _png_bytes((10 * position, 40, 90), tag=f"p{position}"), f"{position}.png", "image/png"
        )
        items.append({"position": position, "previewAssetId": asset["assetId"], "sourceFileName": f"{position}.png"})

    body = client.post(f"/api/uploads/{upload_id}/materials", json={"materials": items}).json()
    codes = [p["material"]["materialCode"] for p in sorted(body["positions"], key=lambda p: p["position"])]
    assert codes == ["MAT-000001", "MAT-000002", "MAT-000003"]


def test_same_material_reused_on_second_upload(client, db_session, create_package, create_upload, upload_file):
    """相同主素材再次上传（新上传会话）→ 复用已有 MAT，不新建。"""
    pkg = create_package("万圣节1")
    content = _png_bytes((33, 66, 99), tag="reuse")

    first_upload = create_upload(pkg["id"])["packageUpload"]["id"]
    asset1 = upload_file(first_upload, content, "1.png", "image/png")
    client.post(
        f"/api/uploads/{first_upload}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset1["assetId"], "sourceFileName": "1.png"}]},
    )

    # 第二次上传：新会话、新位置、同一份文件内容
    second = create_upload(pkg["id"], uploadSessionId="sess-second", originalPackageName="b.zip")
    second_upload = second["packageUpload"]["id"]
    asset2 = upload_file(second_upload, content, "1.png", "image/png")
    assert asset2["reused"] is True
    assert asset2["assetId"] == asset1["assetId"]

    body = client.post(
        f"/api/uploads/{second_upload}/materials",
        json={"materials": [{"position": 2, "previewAssetId": asset2["assetId"], "sourceFileName": "1.png"}]},
    ).json()

    assert body["reusedMaterialCount"] == 1
    assert body["createdMaterialCount"] == 0
    assert body["positions"][0]["material"]["materialCode"] == "MAT-000001"
    assert body["positions"][0]["material"]["reused"] is True

    materials = db_session.execute(select(Material)).scalars().all()
    assert len(materials) == 1, "相同主素材不应产生第二个 MAT"

    positions = db_session.execute(
        select(DesignPackageMaterial).order_by(DesignPackageMaterial.position)
    ).scalars().all()
    assert [p.position for p in positions] == [1, 2]
    assert {p.material_id for p in positions} == {"MAT-000001"}


def test_same_material_linked_by_two_design_packages(
    client, db_session, create_package, create_upload, upload_file
):
    """核心规则（第十三条）：Material 只有一份，DesignPackageMaterial 有两条。"""
    content = _png_bytes((222, 111, 44), tag="shared")

    # 设计包 A：位置 1
    pkg_a = create_package("设计包A")
    upload_a = create_upload(pkg_a["id"])["packageUpload"]["id"]
    asset_a = upload_file(upload_a, content, "1.png", "image/png")
    result_a = client.post(
        f"/api/uploads/{upload_a}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset_a["assetId"], "sourceFileName": "1.png"}]},
    ).json()
    code_a = result_a["positions"][0]["material"]["materialCode"]

    # 设计包 B：位置 7，同一份文件
    pkg_b = create_package("设计包B")
    upload_b = create_upload(pkg_b["id"])["packageUpload"]["id"]
    asset_b = upload_file(upload_b, content, "7.png", "image/png")
    result_b = client.post(
        f"/api/uploads/{upload_b}/materials",
        json={"materials": [{"position": 7, "previewAssetId": asset_b["assetId"], "sourceFileName": "7.png"}]},
    ).json()
    code_b = result_b["positions"][0]["material"]["materialCode"]

    assert code_a == code_b == "MAT-000001"
    assert result_b["reusedMaterialCount"] == 1

    materials = db_session.execute(select(Material)).scalars().all()
    assert len(materials) == 1, "同一 MAT 必须只有一份主数据"

    positions = db_session.execute(select(DesignPackageMaterial)).scalars().all()
    assert len(positions) == 2
    package_ids = {p.design_package_id for p in positions}
    assert package_ids == {pkg_a["id"], pkg_b["id"]}

    # 详情接口给出跨包引用计数
    detail = client.get("/api/materials/MAT-000001").json()
    assert detail["designCount"] == 2


def test_position_resubmit_same_asset_is_idempotent(client, db_session, create_package, create_upload, upload_file):
    """同一个位置重传同一张主素材 = 复用（新一版整包重传的正常路径）。

    第二次上传（V2）常常会把主素材一起重传，BLAKE3 去重后还是同一个 Asset，
    这时不能报「位置已存在」，否则用户永远生不出 V2。
    """
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((70, 70, 200)), "1.png", "image/png")

    payload = {"materials": [{"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"}]}
    first = client.post(f"/api/uploads/{upload_id}/materials", json=payload)
    assert first.status_code == 200

    second = client.post(f"/api/uploads/{upload_id}/materials", json=payload)
    assert second.status_code == 200
    body = second.json()
    assert body["createdMaterialCount"] == 0
    assert body["reusedMaterialCount"] == 1
    assert body["positions"][0]["material"]["materialCode"] == "MAT-000001"

    positions = db_session.execute(select(DesignPackageMaterial)).scalars().all()
    assert len(positions) == 1, "重传同一张主素材不能产生第二个位置"


def test_position_conflict_when_main_image_replaced(client, db_session, create_package, create_upload, upload_file):
    """同一个设计包位置换成另一张主图 → 409（位置与 MAT 身份不允许被顶替）。"""
    pkg = create_package("万圣节2")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((70, 70, 200)), "1.png", "image/png")
    first = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"}]},
    )
    assert first.status_code == 200

    # 第二次上传（同包新一版）：位置 1 换成另一张图
    second_upload = create_upload(pkg["id"], uploadSessionId="sess-v2")["packageUpload"]["id"]
    other = upload_file(second_upload, _png_bytes((11, 200, 90)), "1.png", "image/png")
    assert other["assetId"] != asset["assetId"]

    conflict = client.post(
        f"/api/uploads/{second_upload}/materials",
        json={"materials": [{"position": 1, "previewAssetId": other["assetId"], "sourceFileName": "1.png"}]},
    )
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["code"] == "POSITION_CONFLICT"
    assert "位置 1" in body["message"] or "position" in body["message"].lower()

    positions = db_session.execute(select(DesignPackageMaterial)).scalars().all()
    assert len(positions) == 1


def test_position_conflict_within_same_request(client, create_package, create_upload, upload_file):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((9, 9, 9)), "1.png", "image/png")

    response = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "materials": [
                {"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"},
                {"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"},
            ]
        },
    )
    assert response.status_code == 409
    assert response.json()["code"] == "POSITION_CONFLICT"


def test_missing_asset_rejected(client, create_package, create_upload):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    response = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"materials": [{"position": 1, "previewAssetId": "asset-nope", "sourceFileName": "1.png"}]},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "ASSET_NOT_FOUND"


def test_material_integrity_error_rolls_back_everything(
    client, db_session, create_package, create_upload, upload_file, monkeypatch
):
    """入库事务性：任一位置失败 → 本次请求全部回滚（不留半个 MAT / 位置）。"""
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    asset1 = upload_file(upload_id, _png_bytes((11, 22, 33)), "1.png", "image/png")
    asset2 = upload_file(upload_id, _png_bytes((44, 55, 66)), "2.png", "image/png")

    import app.api.materials as materials_module

    original = materials_module.write_log
    calls = {"n": 0}

    def flaky_write_log(*args, **kwargs):  # noqa: ANN002, ANN003
        calls["n"] += 1
        # 在第二个位置的日志写入时抛错，模拟事务中途失败
        if calls["n"] == 2:
            raise RuntimeError("模拟入库中途失败")
        return original(*args, **kwargs)

    monkeypatch.setattr(materials_module, "write_log", flaky_write_log)

    response = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "materials": [
                {"position": 1, "previewAssetId": asset1["assetId"], "sourceFileName": "1.png"},
                {"position": 2, "previewAssetId": asset2["assetId"], "sourceFileName": "2.png"},
            ]
        },
    )
    assert response.status_code == 500
    assert response.json()["code"] == "MATERIAL_CREATE_FAILED"

    db_session.expire_all()
    assert db_session.execute(select(Material)).scalars().all() == []
    assert db_session.execute(select(DesignPackageMaterial)).scalars().all() == []
    # 序列发号也回滚（否则 MAT 编号会跳号）
    current = db_session.execute(
        text("SELECT current_value FROM code_sequences WHERE name = 'material'")
    ).scalar_one()
    assert current == 0


# ================================================================ PSD


def test_psd_revision_and_current_pointer(
    client, db_session, create_package, create_upload, upload_file
):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]

    preview = upload_file(upload_id, _png_bytes((120, 130, 140)), "3.png", "image/png")
    psd = upload_file(upload_id, b"8BPS" + b"\x00" * 128, "3.psd", "image/vnd.adobe.photoshop", kind="PSD")

    body = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "materials": [
                {
                    "position": 3,
                    "previewAssetId": preview["assetId"],
                    "psdAssetId": psd["assetId"],
                    "sourceFileName": "3.png",
                }
            ]
        },
    ).json()

    material = db_session.get(Material, "MAT-000001")
    assert material is not None

    revisions = db_session.execute(
        select(MaterialPsdRevision).where(MaterialPsdRevision.material_id == material.id)
    ).scalars().all()
    assert len(revisions) == 1
    assert revisions[0].revision_no == 1
    assert revisions[0].asset_id == psd["assetId"]
    assert material.current_psd_revision_id == revisions[0].id

    # DTO 也带上 PSD
    dto = body["positions"][0]["material"]
    assert dto["currentPsdRevisionId"] == revisions[0].id
    assert dto["psdAsset"]["assetId"] == psd["assetId"]
    assert len(dto["psdRevisions"]) == 1


def test_material_without_psd_has_null_pointer(client, db_session, create_package, create_upload, upload_file):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((200, 200, 10)), "1.png", "image/png")

    client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"}]},
    )
    material = db_session.get(Material, "MAT-000001")
    assert material is not None
    assert material.current_psd_revision_id is None
    assert db_session.execute(select(MaterialPsdRevision)).scalars().all() == []


# ================================================================ 整包视图 DTO


def test_package_overview_dto_shape(client, create_package, create_upload, upload_file):
    pkg = create_package("万圣节夜景球迷款")

    # 首次上传前
    empty = client.get(f"/api/design-packages/{pkg['id']}/overview").json()
    assert empty["phase"] == "PHASE_2"
    assert empty["upload"] is None
    assert empty["positions"] == []
    assert empty["mainMaterialCount"] == 0

    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((150, 100, 50)), "1.png", "image/png")
    client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"}]},
    )

    overview = client.get(f"/api/design-packages/{pkg['id']}/overview").json()
    assert overview["designPackage"]["name"] == "万圣节夜景球迷款"
    assert overview["upload"]["originalPackageName"] == "万圣节夜景球迷款-20260916.zip"
    assert overview["mainMaterialCount"] == 1
    assert overview["variantCount"] == 0
    assert len(overview["positions"]) == 1
    assert len(overview["materials"]) == 1
    assert overview["assets"], "整包视图必须包含展开的 Asset"

    # 上传文件按角色分组（前端据此显示「主素材 / PSD / 副图」张数）
    assert overview["uploadedFiles"]["mainCount"] == 1
    assert overview["uploadedFiles"]["variantCount"] == 0
    assert overview["uploadedFiles"]["main"][0]["fileRole"] == "MAIN_PREVIEW"
    assert overview["uploadedFiles"]["main"][0]["previewUrl"].startswith("/api/assets/")
    assert overview["uploadedFiles"]["main"][0]["originalFilename"] == "1.png"

    # 副图已入库但还没匹配确认 → 文案不谎报「副素材 0」，也不阻塞**上传**
    assert overview["countCheck"]["mainCount"] == 1
    assert overview["countCheck"]["variantUploadCount"] == 0
    assert overview["countCheck"]["variantCount"] == 0
    assert all("副素材 0" not in m for m in overview["countCheck"]["messages"])
    # 主素材 1 / 副图 0 → 数量不一致：只阻止「确认并生成版本」，不影响上传
    assert overview["countCheck"]["blocked"] is True
    assert "本次没有上传副图" in " ".join(overview["countCheck"]["messages"])
    build = client.post(f"/api/design-packages/{pkg['id']}/batches", json={"actor": "小柯"})
    assert build.status_code == 409, build.text
    assert build.json()["code"] == "MATERIAL_COUNT_MISMATCH"

    # 还没配对 / 建版：Phase 2 字段保持空，前端据此显示「待配对」
    assert overview["phase"] == "PHASE_2"
    assert overview["currentBatch"] is None
    assert overview["batches"] == []
    assert overview["variants"] == []
    assert overview["pairings"] == []
    # 还没跑配对，但已经指明「配对属于哪一次上传」
    assert overview["pairingUploadId"] == upload_id
    # 数量不一致本身就是阻断异常
    assert [a["code"] for a in overview["blockingAnomalies"]] == ["MATERIAL_COUNT_MISMATCH"]

    assert overview["operators"] == [{"operatorId": "zhang", "operatorName": "张三"}]

    # 维护记录
    actions = {log["action"] for log in overview["logs"]}
    assert {"CREATE_PACKAGE", "UPLOAD_PACKAGE", "CREATE_ASSET", "CREATE_MATERIAL", "CREATE_POSITION"} <= actions


def test_activity_log_query_by_target(client, create_package, create_upload, upload_file):
    pkg = create_package("万圣节1")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    asset = upload_file(upload_id, _png_bytes((5, 250, 5)), "1.png", "image/png")
    client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"materials": [{"position": 1, "previewAssetId": asset["assetId"], "sourceFileName": "1.png"}]},
    )

    by_material = client.get(
        "/api/activity-logs", params={"target_type": "MATERIAL", "target_id": "MAT-000001"}
    ).json()
    assert any(log["action"] == "CREATE_MATERIAL" for log in by_material)

    by_asset = client.get(
        "/api/activity-logs", params={"target_type": "ASSET", "target_id": asset["assetId"]}
    ).json()
    assert any(log["action"] == "CREATE_ASSET" for log in by_asset)


def test_health_endpoint(client):
    body = client.get("/api/health").json()
    # 阶段标识必须与 overview.phase 一致：当前已进入 Phase 2
    assert body["phase"] == "PHASE_2"
    assert body["database"]["ok"] is True
    assert body["storage"]["ok"] is True
    assert client.get("/api/design-packages/nope-not-exist/overview").status_code == 404


def test_unknown_design_package_returns_typed_error(client):
    response = client.get("/api/design-packages/pkg-nope/overview")
    assert response.status_code == 404
    assert response.json()["code"] == "DESIGN_PACKAGE_NOT_FOUND"
    assert response.json()["message"] == "设计包不存在"
