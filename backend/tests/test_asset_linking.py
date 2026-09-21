# ============================================================================
# Asset ↔ 上传行为的多对多归属（package_upload_assets）
#
# 规则（本文件是这条规则的可执行文档）：
#   - Asset = 物理文件身份（BLAKE3 去重，可被多次上传复用）
#   - PackageUploadAsset = 「这次上传以什么角色交付了这个文件」，只追加、不修改
#   - 命中 BLAKE3 复用已有 Asset 时，只新增关联行，**绝不破坏旧上传的归属**
#   - 文件角色（MAIN_PREVIEW / PSD / VARIANT / OTHER）只表示文件用途，
#     不是主副素材匹配结果（匹配属 Phase 2）
#
# 本文件不使用 create_upload / upload_file fixture，全部显式构造请求，
# 避免 fixture 的默认参数掩盖真实入参。
# ============================================================================

from __future__ import annotations

from sqlalchemy import text

from tests.conftest import _png_bytes


def _create_package(client, name: str) -> str:
    response = client.post(
        "/api/design-packages",
        json={
            "name": name,
            "designCode": "DS-TEST-001",
            "designerName": "设计美工-肖芸",
            "createdBy": "实际上传人-小柯",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _create_upload(client, package_id: str, session_key: str, original_name: str) -> str:
    response = client.post(
        f"/api/design-packages/{package_id}/uploads",
        json={
            "originalPackageName": original_name,
            "uploadSessionId": session_key,
            "uploaderName": "实际上传人-小柯",
            "operatorId": "zhang",
            "operatorName": "张三",
            "uploadType": "INITIAL",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True, f"期望新建上传记录：{body}"
    return body["packageUpload"]["id"]


def _upload(
    client,
    upload_id: str,
    content: bytes,
    filename: str,
    kind: str = "MAIN",
    file_role: str | None = None,
) -> dict:
    data = {"kind": kind}
    if file_role:
        data["fileRole"] = file_role
    response = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": (filename, content, "image/png")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _links(db_session, upload_id: str) -> list[tuple[str, str]]:
    """
    读取某次上传的关联行 (asset_id, file_role)。

    注意：必须 commit() 结束这次读事务。
    MySQL 默认 REPEATABLE READ，长事务会一直返回第一次 SELECT 时的快照，
    导致断言看到过期数据（本次调试踩过的坑，不是业务问题）。
    """
    try:
        rows = db_session.execute(
            text(
                "SELECT asset_id, file_role FROM package_upload_assets "
                "WHERE package_upload_id = :upload_id ORDER BY file_role, original_filename"
            ),
            {"upload_id": upload_id},
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
    finally:
        db_session.commit()


def _asset_upload_ids(db_session, asset_id: str) -> list[str]:
    """一个 Asset 被哪些上传行为引用（多对多，必须是列表）。"""
    try:
        rows = db_session.execute(
            text(
                "SELECT package_upload_id FROM package_upload_assets "
                "WHERE asset_id = :asset_id ORDER BY created_at"
            ),
            {"asset_id": asset_id},
        ).fetchall()
        return [row[0] for row in rows]
    finally:
        db_session.commit()


# ---------------------------------------------------------------- 1. N:M 基础


def test_new_asset_records_one_relation_for_current_upload(client, db_session):
    """新文件：Asset 落库 + 一条关联行（角色来自 fileRole 或文件名）。"""
    pkg_id = _create_package(client, "资产归属包")
    upload_id = _create_upload(client, pkg_id, "own-sess-1", "own.zip")
    body = _upload(client, upload_id, _png_bytes((5, 5, 5)), "x.png", file_role="MAIN_PREVIEW")

    assert body["reused"] is False
    assert body["fileRole"] == "MAIN_PREVIEW"
    assert body["linkId"], "上传响应必须带上本次归属关联行 id"

    assert _links(db_session, upload_id) == [(body["assetId"], "MAIN_PREVIEW")]
    assert _asset_upload_ids(db_session, body["assetId"]) == [upload_id]

    row = db_session.execute(
        text("SELECT mime_type, size_bytes FROM assets WHERE id = :id"),
        {"id": body["assetId"]},
    ).one()
    assert row[0] == "image/png"
    assert row[1] > 0
    db_session.commit()


def test_asset_no_longer_has_upload_ownership_column(db_session):
    """第5/6条：assets 表不再持有单归属字段，归属完全由关联表回答。"""
    columns = {row[0] for row in db_session.execute(text("SHOW COLUMNS FROM assets"))}
    db_session.commit()
    assert "upload_session_id" not in columns, (
        "Asset 不能再用单字段表示归属：一个文件可以被多次上传复用"
    )


# ---------------------------------------------------------------- 2. BLAKE3 复用


def test_blake3_reuse_creates_second_relation_without_breaking_first(client, db_session):
    """
    完全相同文件被第二次上传：
      - 复用同一个 Asset（不新建）
      - 新增第二条关联行
      - 第一次上传的归属关系保持不变 ← 这是「副素材 0」问题的根因修复
    """
    pkg_id = _create_package(client, "复用过账包")
    content = _png_bytes((77, 88, 99), tag="link-test")

    upload_1 = _create_upload(client, pkg_id, "link-sess-1", "a.zip")
    asset_1 = _upload(client, upload_1, content, "1.png", file_role="MAIN_PREVIEW")
    assert asset_1["reused"] is False

    upload_2 = _create_upload(client, pkg_id, "link-sess-2", "b.zip")
    asset_2 = _upload(client, upload_2, content, "1.png", file_role="VARIANT")

    assert asset_2["reused"] is True, asset_2
    assert asset_2["assetId"] == asset_1["assetId"], "相同内容必须复用同一 Asset"

    # 同一个 Asset 现在被两次上传引用（多对多成立）
    assert _asset_upload_ids(db_session, asset_1["assetId"]) == [upload_1, upload_2]

    # 旧上传的归属没有被改写（角色依然是当初的 MAIN_PREVIEW）
    assert _links(db_session, upload_1) == [(asset_1["assetId"], "MAIN_PREVIEW")]
    # 新上传记的是它自己的角色
    assert _links(db_session, upload_2) == [(asset_2["assetId"], "VARIANT")]

    # 没有为重复文件新建第二条 Asset
    count = db_session.execute(text("SELECT COUNT(*) FROM assets")).scalar_one()
    assert count == 1
    db_session.commit()

    # 维护记录里能看到复用
    logs = client.get(
        "/api/activity-logs", params={"target_type": "ASSET", "target_id": asset_2["assetId"]}
    ).json()
    assert "REUSE_ASSET" in {log["action"] for log in logs}


def test_duplicate_pair_key_in_same_role_is_rejected(client, db_session):
    """
    同一次上传里，同一角色下同一个 pairKey 只能有一个文件（重复就是异常）。

    新配对规则的核心约束：UNIQUE(package_upload_id, file_role, pair_key)。
    """
    pkg_id = _create_package(client, "同上传去重包")
    upload_id = _create_upload(client, pkg_id, "dup-sess-1", "dup.zip")
    content = _png_bytes((1, 2, 3), tag="dup")

    first = _upload(client, upload_id, content, "1.png", file_role="MAIN_PREVIEW")
    assert first["pairKey"] == "1"

    duplicate = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": ("1.png", content, "image/png")},
        data={"kind": "MAIN", "fileRole": "MAIN_PREVIEW"},
    )
    assert duplicate.status_code == 422, duplicate.text
    assert duplicate.json()["code"] == "DUPLICATE_PAIR_KEY"

    assert len(_links(db_session, upload_id)) == 1


def test_same_asset_can_be_main_and_variant_in_one_upload(client, db_session):
    """
    同一份字节、同一个名字既当主图又当副图时，必须留下两条不同角色的关联行。

    如果唯一键只按 (upload, asset)，第二次上传只能**改写**第一行的 file_role，
    主图的 MAIN_PREVIEW 归属会被静默抹掉 —— 主素材直接失去文件。
    """
    pkg_id = _create_package(client, "同文件双角色包")
    upload_id = _create_upload(client, pkg_id, "dual-sess-1", "dual.zip")
    content = _png_bytes((9, 8, 7), tag="dual")

    main = _upload(client, upload_id, content, "1.png", file_role="MAIN_PREVIEW")
    variant = _upload(client, upload_id, content, "1.png", kind="VARIANT", file_role="VARIANT")
    assert variant["reused"] is True, "相同字节应复用同一个 Asset"
    assert variant["assetId"] == main["assetId"]

    roles = sorted(role for _asset_id, role in _links(db_session, upload_id))
    assert roles == ["MAIN_PREVIEW", "VARIANT"]

    rows = client.get(f"/api/uploads/{upload_id}/assets").json()
    by_role = {row["fileRole"]: row["pairKey"] for row in rows}
    assert by_role == {"MAIN_PREVIEW": "1", "VARIANT": "1"}


# ---------------------------------------------------------------- 3. 角色过滤接口


def test_list_upload_assets_filter_by_variant_role(client, db_session):
    """GET /api/uploads/{id}/assets?role=VARIANT 只返回副图（验收要求：2 张）。"""
    pkg_id = _create_package(client, "副图过滤包")
    upload_id = _create_upload(client, pkg_id, "role-sess-1", "role.zip")

    main = _upload(client, upload_id, _png_bytes((10, 0, 0)), "1.png", file_role="MAIN_PREVIEW")
    psd = _upload(client, upload_id, b"8BPS" + b"\x11" * 128, "1.psd", kind="PSD", file_role="PSD")
    variant_a = _upload(
        client, upload_id, _png_bytes((0, 10, 0)), "1-1.png", kind="VARIANT", file_role="VARIANT"
    )
    variant_b = _upload(
        client, upload_id, _png_bytes((0, 0, 10)), "1-2.png", kind="VARIANT", file_role="VARIANT"
    )

    variants = client.get(f"/api/uploads/{upload_id}/assets", params={"role": "VARIANT"}).json()
    assert [item["originalFilename"] for item in variants] == ["1-1.png", "1-2.png"]
    assert {item["assetId"] for item in variants} == {variant_a["assetId"], variant_b["assetId"]}
    assert all(item["fileRole"] == "VARIANT" for item in variants)
    assert all(item["previewUrl"] == f"/api/assets/{item['assetId']}/content" for item in variants)

    all_files = client.get(f"/api/uploads/{upload_id}/assets").json()
    assert len(all_files) == 4
    assert {item["assetId"] for item in all_files} == {
        main["assetId"],
        psd["assetId"],
        variant_a["assetId"],
        variant_b["assetId"],
    }

    # 未知角色要明确报错，而不是静默返回全部
    bad = client.get(f"/api/uploads/{upload_id}/assets", params={"role": "MAIN"})
    assert bad.status_code == 422, bad.text


def test_role_filter_on_missing_upload_returns_404(client):
    response = client.get("/api/uploads/upload-not-exist/assets", params={"role": "VARIANT"})
    assert response.status_code == 404, response.text


# ---------------------------------------------------------------- 4. 刷新后仍然可见


def test_overview_keeps_variants_after_refresh(client, db_session):
    """
    验收要求：副图刷新后依然可见（整包视图 uploadedFiles 必须有副图）。
    这正是「副素材 0」问题的回归测试。
    """
    pkg_id = _create_package(client, "刷新保留副图包")
    upload_id = _create_upload(client, pkg_id, "refresh-sess-1", "refresh.zip")

    asset = _upload(client, upload_id, _png_bytes((120, 30, 60)), "1.png", file_role="MAIN_PREVIEW")
    psd = _upload(client, upload_id, b"8BPS" + b"\x22" * 128, "1.psd", kind="PSD", file_role="PSD")
    _upload(client, upload_id, _png_bytes((60, 120, 30)), "1-1.png", kind="VARIANT", file_role="VARIANT")
    _upload(client, upload_id, _png_bytes((30, 60, 120)), "1-2.png", kind="VARIANT", file_role="VARIANT")

    submitted = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "materials": [
                {
                    "position": 1,
                    "previewAssetId": asset["assetId"],
                    "psdAssetId": psd["assetId"],
                    "sourceFileName": "1.png",
                }
            ]
        },
    )
    assert submitted.status_code == 200, submitted.text

    # 第一次拉取（上传后立即刷新）
    first = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert first["uploadedFiles"]["mainCount"] == 1
    assert first["uploadedFiles"]["psdCount"] == 1
    assert first["uploadedFiles"]["variantCount"] == 2
    assert sorted(item["originalFilename"] for item in first["uploadedFiles"]["variants"]) == [
        "1-1.png",
        "1-2.png",
    ]
    assert first["latestUploadedFiles"]["variantCount"] == 2

    # 再次拉取（模拟浏览器刷新页面）结果必须一致
    second = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert second["uploadedFiles"] == first["uploadedFiles"]
    assert sorted(a["originalFilename"] for a in second["assets"]) == [
        "1-1.png",
        "1-2.png",
        "1.png",
        "1.psd",
    ]

    # 文案里不能再出现「副素材 0」这种让用户以为文件丢了的话
    for message in second["countCheck"]["messages"]:
        assert "副素材 0" not in message
        assert "缺少" not in message
    # 副图张数来自上传关联（variantUploadCount）；variantCount 是已生成版本的副素材数
    assert second["countCheck"]["variantUploadCount"] == 2
    assert second["countCheck"]["variantCount"] == 0
    db_session.commit()


def test_overview_uploaded_files_dedupes_reused_asset(client, db_session):
    """同一文件在两次上传里复用时，整包 uploadedFiles 不应重复计数。"""
    pkg_id = _create_package(client, "整包去重包")
    content = _png_bytes((200, 200, 10), tag="dedupe")

    upload_1 = _create_upload(client, pkg_id, "dd-sess-1", "dd1.zip")
    _upload(client, upload_1, content, "1.png", file_role="MAIN_PREVIEW")
    upload_2 = _create_upload(client, pkg_id, "dd-sess-2", "dd2.zip")
    _upload(client, upload_2, content, "1.png", file_role="MAIN_PREVIEW")

    overview = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert overview["uploadedFiles"]["mainCount"] == 1, overview["uploadedFiles"]
    assert len(overview["uploadAssetLinks"]) == 2, "关联行本身保留两条（历史不被抹掉）"
    db_session.commit()


# ---------------------------------------------------------------- 5. 美工 / 归属运营


def test_uploader_and_operator_are_free_text_and_persist(client, db_session):
    """
    第7条：
      - 美工（uploaderName）允许自由文本 → package_uploads.uploader_name
      - 归属运营允许自由文本、且**不要求**有真实 userId → operator_id 可为空
      - 刷新后两者都必须还在
    """
    pkg_id = _create_package(client, "自由填写包")
    response = client.post(
        f"/api/design-packages/{pkg_id}/uploads",
        json={
            "originalPackageName": "free.zip",
            "uploadSessionId": "free-sess-1",
            "uploaderName": "美工-小林",
            # 故意不传 operatorId：运营是新同事，系统里还没有 userId
            "operatorName": "运营-王姐",
            "uploadType": "INITIAL",
        },
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()["packageUpload"]["id"]

    overview = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert overview["upload"]["uploaderName"] == "美工-小林"
    assert overview["session"]["operatorName"] == "运营-王姐"
    assert overview["session"]["operatorId"] is None, "没有真实 userId 时必须允许为空"
    assert overview["operatorName"] == "运营-王姐"
    assert overview["operators"] == [{"operatorId": "", "operatorName": "运营-王姐"}]

    # 上传后再改一次（页面上美工 / 归属运营都是可编辑输入框）
    patched = client.patch(
        f"/api/uploads/{upload_id}",
        json={"uploaderName": "美工-阿强", "operatorName": "运营-李哥"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["uploaderName"] == "美工-阿强"

    refreshed = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert refreshed["upload"]["uploaderName"] == "美工-阿强"
    assert refreshed["session"]["operatorName"] == "运营-李哥"
    assert refreshed["session"]["operatorId"] is None
    db_session.commit()


def test_operator_with_known_user_id_keeps_id(client, db_session):
    """能对上系统用户时，operator_id 与 operator_name 一起保存。"""
    pkg_id = _create_package(client, "已知运营包")
    upload_id = _create_upload(client, pkg_id, "known-sess-1", "known.zip")
    patched = client.patch(
        f"/api/uploads/{upload_id}",
        json={"operatorId": "wang", "operatorName": "王敏"},
    )
    assert patched.status_code == 200, patched.text

    overview = client.get(f"/api/design-packages/{pkg_id}/overview").json()
    assert overview["session"]["operatorId"] == "wang"
    assert overview["session"]["operatorName"] == "王敏"
    assert overview["operators"] == [{"operatorId": "wang", "operatorName": "王敏"}]
    db_session.commit()


# ---------------------------------------------------------------- 6. Asset 内容代理


def test_asset_content_proxy_returns_bytes(client, db_session):
    """第7条：浏览器通过后端代理取图，而不是直接访问 storage_key。"""
    pkg_id = _create_package(client, "内容代理包")
    upload_id = _create_upload(client, pkg_id, "content-sess-1", "content.zip")
    content = _png_bytes((9, 200, 9), tag="proxy")
    body = _upload(client, upload_id, content, "1.png", file_role="MAIN_PREVIEW")

    # DTO 暴露的一定是代理地址，绝不能是 storage_key
    assert body["uri"] == f"/api/assets/{body['assetId']}/content"
    assert body["storageKey"] not in body["uri"]

    response = client.get(f"/api/assets/{body['assetId']}/content")
    assert response.status_code == 200, response.text
    assert response.content == content
    assert response.headers["content-type"].startswith("image/png")

    missing = client.get("/api/assets/asset-not-exist/content")
    assert missing.status_code == 404, missing.text
    db_session.commit()


# ---------------------------------------------------------------- 7. Phase 1 边界


def test_variant_asset_stored_but_no_variant_entity(client, db_session):
    """上传本身只落 Asset + 关联行；副素材实体只在「确认整包生成版本」时才创建。"""
    pkg_id = _create_package(client, "副素材仅存Asset")
    upload_id = _create_upload(client, pkg_id, "var-sess-1", "var.zip")
    asset = _upload(
        client, upload_id, _png_bytes((30, 30, 30)), "1-1.png", kind="VARIANT", file_role="VARIANT"
    )
    assert asset["kind"] == "IMAGE"
    assert asset["fileRole"] == "VARIANT"

    # Phase 2 已经建表，但单纯上传不应该产生任何副素材行
    tables = {row[0] for row in db_session.execute(text("SHOW TABLES"))}
    assert "material_variants" in tables
    db_session.rollback()
    assert db_session.execute(text("SELECT COUNT(*) FROM material_variants")).scalar_one() == 0
    assert db_session.execute(text("SELECT COUNT(*) FROM derivative_batches")).scalar_one() == 0
    db_session.commit()

    stored = db_session.execute(
        text("SELECT original_filename FROM assets WHERE id = :id"), {"id": asset["assetId"]}
    ).scalar_one()
    assert stored == "1-1.png"
    db_session.commit()
    db_session.commit()


def test_filename_inference_used_when_role_not_sent(client, db_session):
    """前端未显式传 fileRole 时，后端按文件名兜底分类。"""
    pkg_id = _create_package(client, "文件名兜底包")
    upload_id = _create_upload(client, pkg_id, "infer-sess-1", "infer.zip")

    main = _upload(client, upload_id, _png_bytes((1, 1, 200)), "3.png")
    variant = _upload(client, upload_id, _png_bytes((1, 200, 1)), "3-1.png", kind="VARIANT")
    psd = _upload(client, upload_id, b"8BPS" + b"\x33" * 64, "3.psd", kind="PSD")

    assert main["fileRole"] == "MAIN_PREVIEW"
    assert variant["fileRole"] == "VARIANT"
    assert psd["fileRole"] == "PSD"

    rows = client.get(f"/api/uploads/{upload_id}/assets").json()
    by_name = {row["originalFilename"]: row for row in rows}
    assert by_name["3.png"]["positionHint"] == 3
    assert by_name["3-1.png"]["positionHint"] == 3
    db_session.commit()
