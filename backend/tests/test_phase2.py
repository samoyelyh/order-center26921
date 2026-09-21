# ============================================================================
# Phase 2 测试：人员字段 / 同名 pairKey 配对 / 人工修改 / 建版（V1、V2）
#
# 核心规则（本文件是可执行文档）：
#   1. 主副素材关系**只由同一次上传内的同名 pairKey 决定**，与图片内容无关
#   2. pairKey 只用于当前设计包配对，永久主素材身份仍是 MAT-xxxxxx
#   3. package_upload_assets: UNIQUE(package_upload_id, file_role, pair_key)
#   4. 缺副图 / 多余副图 / 重复 pairKey / 无法解析 pairKey → 异常
#   5. 一个副图只能属于一个主素材；允许人工修改配对
#   6. 没有任何地方依赖相似度判断主副素材关系
#   7. Batch 整套统一生成：V1 → 1-1/2-1/3-1，V2 → 1-2/2-2/3-2
#   8. 若新版本副图与已有副素材当前 Revision 是同一个文件 → 复用原副素材（不新建实体）
# ============================================================================

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    DerivativeBatch,
    DesignPackageMaterial,
    MaterialPairing,
    MaterialVariant,
    VariantRevision,
)
from app.services.files import pair_key_of
from tests.conftest import _patterned_png, _striped_png


def _overview(client, pkg_id: str) -> dict:
    response = client.get(f"/api/design-packages/{pkg_id}/overview")
    assert response.status_code == 200, response.text
    return response.json()


def _pairings(client, upload_id: str) -> list[dict]:
    response = client.get(f"/api/uploads/{upload_id}/pairings")
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_all(client, upload_id: str) -> dict:
    response = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert response.status_code == 200, response.text
    return response.json()


# ================================================================ 一、pairKey 解析


def test_pair_key_is_filename_stem_lowercased():
    """pairKey = 文件名去扩展名（小写），目录前缀不影响。"""
    assert pair_key_of("main/1.jpg") == "1"
    assert pair_key_of("psd/1.psd") == "1"
    assert pair_key_of("variant/1.jpg") == "1"
    assert pair_key_of("1.JPG") == "1"
    assert pair_key_of("  2 .png") == "2"
    assert pair_key_of("weird name-final.png") == "weird name-final"
    # 解析不出来 → 空串（配对阶段报「无法解析 pairKey」）
    assert pair_key_of("") == ""
    assert pair_key_of(".jpg") == ""


def test_upload_records_pair_key(client, create_package, create_upload, upload_file):
    """上传时把同名键写进 package_upload_assets；三种角色同名 → 同一个 pairKey。"""
    pkg = create_package("pairKey记录包")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    upload_file(
        upload_id, _patterned_png(1), "main/1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW"
    )
    upload_file(
        upload_id, _striped_png(1), "variant/1.png", "image/png", kind="VARIANT", file_role="VARIANT"
    )
    upload_file(
        upload_id,
        b"8BPS" + b"\x00" * 128,
        "psd/1.psd",
        "image/vnd.adobe.photoshop",
        kind="PSD",
        file_role="PSD",
    )

    rows = client.get(f"/api/uploads/{upload_id}/assets").json()
    assert {row["fileRole"] for row in rows} == {"MAIN_PREVIEW", "VARIANT", "PSD"}
    assert {row["pairKey"] for row in rows} == {"1"}


def test_duplicate_pair_key_in_same_role_is_rejected(
    client, create_package, create_upload, upload_file
):
    """同一上传同一角色出现重复 pairKey → 明确报错，不能静默。"""
    pkg = create_package("重复pairKey包")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    upload_file(
        upload_id, _patterned_png(1), "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW"
    )

    clash = client.post(
        f"/api/uploads/{upload_id}/files",
        files={"file": ("1.png", _patterned_png(2), "image/png")},
        data={"kind": "MAIN", "fileRole": "MAIN_PREVIEW"},
    )
    assert clash.status_code == 422, clash.text
    assert clash.json()["code"] == "DUPLICATE_PAIR_KEY"
    assert "1" in clash.json()["message"]


# ================================================================ 二、同名配对


def test_same_name_pairing_is_one_to_one(client, phase2_package):
    """3 主素材 + 3 副素材同名 → 直接 1↔1、2↔2、3↔3。"""
    ctx = phase2_package(3)
    body = ctx["pairing"]
    assert body["mainCount"] == 3
    assert body["variantUploadCount"] == 3
    assert body["pairedCount"] == 3
    assert body["unpairedCount"] == 0
    assert body["pairKeys"] == ["1", "2", "3"]

    pairs = {dto["position"]: (dto["pairKey"], dto["variantFileName"]) for dto in body["pairings"]}
    assert pairs == {1: ("1", "1.png"), 2: ("2", "2.png"), 3: ("3", "3.png")}
    assert all(dto["status"] == "PAIRED" for dto in body["pairings"])
    assert all(dto["source"] == "NAME" for dto in body["pairings"])


def test_pairing_follows_names_even_when_content_would_say_otherwise(
    client, phase2_package, db_session
):
    """
    图片内容**故意错位**时，配对仍然只按同名 → 证明生产链路完全不看图片。

    构造：副图 1.png 的内容其实是主图 3 的图案、副图 2.png 是主图 1 的、副图 3.png 是主图 2 的。
    如果按 pHash 相似度匹配，结果会是 1↔2、2↔3、3↔1；
    实际必须仍然按同名给出 1↔1、2↔2、3↔3。
    """
    shifted = {1: _patterned_png(3), 2: _patterned_png(1), 3: _patterned_png(2)}
    ctx = phase2_package(3, variant_contents=shifted)
    paired = {dto["position"]: dto["variantFileName"] for dto in ctx["pairing"]["pairings"]}
    assert paired == {1: "1.png", 2: "2.png", 3: "3.png"}

    # 反事实：如果按「pHash 最接近」来配，位置1 会选中副图2 —— 系统没有这么做
    rows = db_session.execute(
        text(
            "SELECT dpm.position, a.phash, p.variant_asset_id, v.phash "
            "FROM design_package_materials dpm "
            "JOIN assets a ON a.id = dpm.source_asset_id "
            "LEFT JOIN material_pairings p ON p.design_package_material_id = dpm.id "
            "LEFT JOIN assets v ON v.id = p.variant_asset_id "
            "WHERE dpm.design_package_id = :pkg ORDER BY dpm.position"
        ),
        {"pkg": ctx["pkg"]["id"]},
    ).fetchall()
    variants = db_session.execute(
        text(
            "SELECT pua.pair_key, a.phash FROM package_upload_assets pua "
            "JOIN assets a ON a.id = pua.asset_id "
            "WHERE pua.package_upload_id = :u AND pua.file_role = 'VARIANT' ORDER BY pua.pair_key"
        ),
        {"u": ctx["upload_id"]},
    ).fetchall()
    db_session.commit()

    variant_hashes = {key: phash for key, phash in variants}
    for position, main_phash, _paired_asset, _paired_phash in rows:
        distances = {
            key: sum(bin(x ^ y).count("1") for x, y in zip(main_phash, phash, strict=True))
            for key, phash in variant_hashes.items()
        }
        content_choice = min(distances, key=distances.get)
        # 内容最像的那张是"错位"的，但配对用的是同名的那张
        assert content_choice != str(position), (
            f"位置{position} 内容最像的副图恰好也是同名的，用例构造失败：{distances}"
        )
        assert distances[str(position)] > distances[content_choice]


def test_missing_variant_is_blocking_anomaly(client, phase2_package):
    """缺副图 → MISSING_VARIANT 阻断异常。"""
    ctx = phase2_package(3, 2)  # 3 个主素材只有 2 张副图
    body = ctx["pairing"]
    assert body["pairedCount"] == 2
    assert body["unpairedCount"] == 1
    codes = {a["code"] for a in body["anomalies"]}
    assert "MISSING_VARIANT" in codes
    assert "MATERIAL_COUNT_MISMATCH" in codes
    missing = next(dto for dto in body["pairings"] if dto["status"] == "UNPAIRED")
    assert missing["position"] == 3
    assert missing["pairKey"] == "3"


def test_extra_variant_is_warning_not_blocking(client, phase2_package):
    """多余副图 → EXTRA_VARIANT 告警（不阻断，但不参与建版）。"""
    ctx = phase2_package(2, 3)
    body = ctx["pairing"]
    extras = [a for a in body["anomalies"] if a["code"] == "EXTRA_VARIANT"]
    assert extras, body["anomalies"]
    assert all(a["blocking"] is False for a in extras)
    assert all(dto["status"] == "PAIRED" for dto in body["pairings"])


def test_unresolved_pair_key_is_blocking(client, create_package, create_upload, upload_file):
    """主图文件名解析不出 pairKey → UNRESOLVED_PAIR_KEY 阻断异常，可人工改。"""
    pkg = create_package("无法解析pairKey包")
    upload_id = create_upload(pkg["id"])["packageUpload"]["id"]
    upload_file(
        upload_id, _patterned_png(1), "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW"
    )
    main_asset = client.get(f"/api/uploads/{upload_id}/assets").json()[0]

    # 直接造一条 pair_key 为空的主图关联（模拟历史脏数据）
    from app.db.session import SessionLocal

    db = SessionLocal()
    db.execute(
        text("UPDATE package_upload_assets SET pair_key = '' WHERE asset_id = :a"),
        {"a": main_asset["assetId"]},
    )
    db.commit()
    db.close()

    client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "materials": [
                {"position": 1, "previewAssetId": main_asset["assetId"], "sourceFileName": "1.png"}
            ],
            "linkAssetIds": [main_asset["assetId"]],
        },
    )
    body = client.post(f"/api/uploads/{upload_id}/pair").json()
    assert {a["code"] for a in body["anomalies"]} >= {"UNRESOLVED_PAIR_KEY"}
    assert body["pairings"][0]["status"] == "UNPAIRED"


def test_duplicate_pair_key_across_positions_is_blocking(client, phase2_package, db_session):
    """两个位置解析到同一个 pairKey → DUPLICATE_PAIR_KEY 阻断（副图不能给两个主素材）。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    db_session.execute(
        text(
            "UPDATE design_package_materials SET source_asset_id = ("
            "  SELECT source_asset_id FROM ("
            "    SELECT source_asset_id FROM design_package_materials "
            "    WHERE design_package_id = :p AND position = 1"
            "  ) t"
            ") WHERE design_package_id = :p AND position = 2"
        ),
        {"p": pkg_id},
    )
    db_session.commit()

    body = client.post(f"/api/uploads/{ctx['upload_id']}/pair").json()
    codes = {a["code"] for a in body["anomalies"]}
    assert "DUPLICATE_PAIR_KEY" in codes, body["anomalies"]
    statuses = {dto["position"]: dto["status"] for dto in body["pairings"]}
    assert statuses[1] == "PAIRED"
    assert statuses[2] == "UNPAIRED"


# ================================================================ 三、人工修改


def test_manual_reassign_pairing(client, phase2_package):
    """人工改配对有效，标记 source=MANUAL，原位置退回未配对。"""
    ctx = phase2_package(3)
    upload_id = ctx["upload_id"]
    pairings = _pairings(client, upload_id)
    target, other = pairings[0], pairings[1]

    response = client.patch(
        f"/api/uploads/{upload_id}/pairings/{target['id']}",
        json={"variantAssetId": other["variantAssetId"], "actor": "小柯", "confirmReassign": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["variantAssetId"] == other["variantAssetId"]
    assert response.json()["source"] == "MANUAL"

    after = {dto["position"]: dto for dto in _pairings(client, upload_id)}
    assert after[1]["variantAssetId"] == other["variantAssetId"]
    assert after[2]["status"] == "UNPAIRED"
    assert after[2]["variantAssetId"] is None
    owners = [
        dto["position"] for dto in after.values() if dto["variantAssetId"] == other["variantAssetId"]
    ]
    assert owners == [1]


def test_manual_reassign_requires_confirmation(client, phase2_package):
    """目标副图已配对别的位置 → 409 要求确认重新分配。"""
    ctx = phase2_package(3)
    upload_id = ctx["upload_id"]
    pairings = _pairings(client, upload_id)

    conflict = client.patch(
        f"/api/uploads/{upload_id}/pairings/{pairings[0]['id']}",
        json={"variantAssetId": pairings[1]["variantAssetId"], "actor": "小柯"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "VARIANT_ALREADY_PAIRED"
    assert conflict.json()["detail"]["occupiedByPosition"] == 2


def test_rerun_pairing_does_not_overwrite_manual_decision(client, phase2_package):
    """人工修改后重跑同名配对：人工结论不被自动配对覆盖。"""
    ctx = phase2_package(3)
    upload_id = ctx["upload_id"]
    pairings = _pairings(client, upload_id)
    client.patch(
        f"/api/uploads/{upload_id}/pairings/{pairings[0]['id']}",
        json={
            "variantAssetId": pairings[1]["variantAssetId"],
            "actor": "小柯",
            "confirmReassign": True,
        },
    )
    rerun = client.post(f"/api/uploads/{upload_id}/pair").json()
    first = next(dto for dto in rerun["pairings"] if dto["position"] == 1)
    assert first["variantAssetId"] == pairings[1]["variantAssetId"]
    assert first["source"] == "MANUAL"


def test_manual_pairing_must_use_this_upload_variant(client, phase2_package):
    """只能选本次上传的副图。"""
    ctx = phase2_package(3)
    response = client.patch(
        f"/api/uploads/{ctx['upload_id']}/pairings/{_pairings(client, ctx['upload_id'])[0]['id']}",
        json={"variantAssetId": "asset-not-exists", "actor": "小柯"},
    )
    assert response.status_code == 422
    assert "本次上传" in response.json()["message"]


# ================================================================ 四、确认


def test_confirm_requires_no_blocking_issues(client, phase2_package):
    """缺副图时不允许确认。"""
    ctx = phase2_package(3, 2)
    response = client.post(f"/api/uploads/{ctx['upload_id']}/pairings/confirm")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PAIRING_INCOMPLETE"


def test_confirm_all_then_generate_v1(client, phase2_package, db_session):
    """确认整包 → 生成 V1 → 1-1 / 2-1 / 3-1。"""
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    upload_id = ctx["upload_id"]

    confirmed = _confirm_all(client, upload_id)
    assert confirmed["confirmedCount"] == 3
    assert all(dto["status"] == "CONFIRMED" for dto in confirmed["pairings"])

    batch = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "设计美工-阿May", "uploadId": upload_id},
    )
    assert batch.status_code == 200, batch.text
    body = batch.json()
    assert body["versionNo"] == 1 and body["code"] == "V1"
    assert [v["displayCode"] for v in body["variants"]] == ["1-1", "2-1", "3-1"]
    assert body["createdVariantCount"] == 3
    assert body["reusedVariantCount"] == 0
    assert all(v["currentRevisionNo"] == 1 for v in body["variants"])
    assert all(v["currentRevisionId"] for v in body["variants"])

    db_session.rollback()
    assert len(db_session.execute(select(DerivativeBatch)).scalars().all()) == 1
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 3
    assert len(db_session.execute(select(VariantRevision)).scalars().all()) == 3
    rows = db_session.execute(select(MaterialPairing)).scalars().all()
    assert all(row.variant_id for row in rows), "配对记录必须回填真实副素材 id"
    db_session.commit()


def test_batch_requires_confirmed_pairings(client, phase2_package):
    """没确认就建版 → 拒绝。"""
    ctx = phase2_package(3)
    response = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches", json={"actor": "小柯"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "PAIRING_INCOMPLETE"


def test_batch_blocked_when_count_mismatch(client, phase2_package):
    """副图比主素材多（数量不一致）→ 禁止建版（MATERIAL_COUNT_MISMATCH）。"""
    ctx = phase2_package(2, 3)
    _confirm_all(client, ctx["upload_id"])
    response = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches", json={"actor": "小柯"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "MATERIAL_COUNT_MISMATCH"


def test_confirm_blocked_when_variant_missing(client, phase2_package):
    """缺副图时既不能确认、也不能建版。"""
    ctx = phase2_package(3, 2)
    confirm = client.post(f"/api/uploads/{ctx['upload_id']}/pairings/confirm")
    assert confirm.status_code == 409, confirm.text
    assert confirm.json()["code"] == "PAIRING_INCOMPLETE"
    batch = client.post(f"/api/design-packages/{ctx['pkg']['id']}/batches", json={"actor": "小柯"})
    assert batch.status_code == 409
    assert batch.json()["code"] in {"MATERIAL_COUNT_MISMATCH", "PAIRING_INCOMPLETE"}


# ================================================================ 五、V2 与复用


def test_second_upload_with_changed_images_creates_v2(
    client, phase2_package, create_upload, upload_file
):
    """第二次上传，副图内容**有变化** → V2 → 1-2 / 2-2 / 3-2（新建副素材）。"""
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    v1 = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    ).json()
    assert v1["code"] == "V1"

    second = create_upload(pkg_id, originalPackageName="第二次.zip", uploadType="NEW_BATCH")
    upload2 = second["packageUpload"]["id"]
    for index in range(1, 4):
        upload_file(
            upload2,
            _striped_png(index + 10),
            f"{index}.png",
            "image/png",
            kind="VARIANT",
            file_role="VARIANT",
        )
    pair2 = client.post(f"/api/uploads/{upload2}/pair").json()
    assert pair2["pairedCount"] == 3, pair2
    assert [dto["pairKey"] for dto in pair2["pairings"]] == ["1", "2", "3"]
    _confirm_all(client, upload2)

    v2 = client.post(
        f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯", "uploadId": upload2}
    )
    assert v2.status_code == 200, v2.text
    body = v2.json()
    assert body["code"] == "V2" and body["versionNo"] == 2
    assert [v["displayCode"] for v in body["variants"]] == ["1-2", "2-2", "3-2"]
    assert body["createdVariantCount"] == 3
    assert body["reusedVariantCount"] == 0

    batches = client.get(f"/api/design-packages/{pkg_id}/batches").json()
    assert [b["code"] for b in batches] == ["V1", "V2"]


def test_second_upload_reuploading_whole_package_creates_v2(
    client, phase2_package, create_upload, upload_file, db_session
):
    """
    浏览器真实路径：第二次上传把**主素材 + 副图整包重传**到同一个设计包。

    主素材内容没变（BLAKE3 去重 → 同一个 Asset），所以「位置已存在」不能报冲突，
    必须复用原位置与 MAT；副图换了内容 → V2 → 1-2 / 2-2 / 3-2。
    """
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    v1 = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    ).json()
    assert v1["code"] == "V1"

    second = create_upload(pkg_id, originalPackageName="整包重传.zip", uploadType="NEW_BATCH")
    upload2 = second["packageUpload"]["id"]

    # 主素材重传（内容完全相同）
    mains2 = [
        upload_file(
            upload2,
            _patterned_png(index),
            f"{index}.png",
            "image/png",
            kind="MAIN",
            file_role="MAIN_PREVIEW",
        )
        for index in range(1, 4)
    ]
    assert all(item["reused"] is True for item in mains2), mains2
    assert {item["assetId"] for item in mains2} == {m["assetId"] for m in ctx["mains"]}

    submit2 = client.post(
        f"/api/uploads/{upload2}/materials",
        json={
            "actor": "小柯",
            "materials": [
                {
                    "position": index + 1,
                    "previewAssetId": asset["assetId"],
                    "sourceFileName": asset["originalFilename"],
                }
                for index, asset in enumerate(mains2)
            ],
            "linkAssetIds": [a["assetId"] for a in mains2],
        },
    )
    assert submit2.status_code == 200, submit2.text
    assert submit2.json()["createdMaterialCount"] == 0
    assert submit2.json()["reusedMaterialCount"] == 3

    positions = db_session.execute(
        select(DesignPackageMaterial).where(DesignPackageMaterial.design_package_id == pkg_id)
    ).scalars().all()
    assert len(positions) == 3, "整包重传不能新建重复位置"
    assert {p.material_id for p in positions} == {v["materialId"] for v in v1["variants"]}, (
        "整包重传必须复用 V1 的 MAT，不能重新发号"
    )

    # 副图换内容，按同名 pairKey 配对
    for index in range(1, 4):
        upload_file(
            upload2,
            _striped_png(index + 10),
            f"{index}.png",
            "image/png",
            kind="VARIANT",
            file_role="VARIANT",
        )
    pair2 = client.post(f"/api/uploads/{upload2}/pair").json()
    assert pair2["pairedCount"] == 3, pair2
    _confirm_all(client, upload2)

    v2 = client.post(
        f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯", "uploadId": upload2}
    )
    assert v2.status_code == 200, v2.text
    body = v2.json()
    assert body["code"] == "V2"
    assert [v["displayCode"] for v in body["variants"]] == ["1-2", "2-2", "3-2"]
    assert body["createdVariantCount"] == 3


def test_second_upload_with_identical_images_reuses_existing_variant(
    client, phase2_package, create_upload, upload_file, db_session
):
    """
    第二次上传的副图与 V1 **完全一样** → 复用原来的副素材，不新建实体。

    V2 仍然生成，但它的每个位置通过配对继续引用 V1 的 MaterialVariant / Asset。
    """
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    v1 = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    ).json()
    v1_variant_ids = [v["id"] for v in v1["variants"]]

    second = create_upload(pkg_id, originalPackageName="完全相同.zip", uploadType="NEW_BATCH")
    upload2 = second["packageUpload"]["id"]
    uploaded = [
        upload_file(
            upload2,
            _striped_png(index),
            f"{index}.png",
            "image/png",
            kind="VARIANT",
            file_role="VARIANT",
        )
        for index in range(1, 4)
    ]
    # 复用了 V1 的 Asset，没有新建文件实体
    assert all(item["reused"] is True for item in uploaded), uploaded
    assert {item["assetId"] for item in uploaded} == {v["assetId"] for v in v1["variants"]}

    pair2 = client.post(f"/api/uploads/{upload2}/pair").json()
    assert pair2["pairedCount"] == 3
    _confirm_all(client, upload2)

    v2 = client.post(
        f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯", "uploadId": upload2}
    )
    assert v2.status_code == 200, v2.text
    body = v2.json()
    assert body["code"] == "V2"
    # 关键：没有新建副素材，三个位置全部复用 V1 的副素材
    assert body["createdVariantCount"] == 0
    assert body["reusedVariantCount"] == 3
    assert [v["id"] for v in body["variants"]] == v1_variant_ids
    assert all(v["reusedByBatchCode"] == "V2" for v in body["variants"])
    assert all(v["currentRevisionNo"] == 1 for v in body["variants"])
    assert body["reusedNotes"], body["reusedNotes"]

    db_session.rollback()
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 3, (
        "内容完全相同时不应新建副素材实体"
    )
    assert len(db_session.execute(select(VariantRevision)).scalars().all()) == 3, (
        "内容完全相同时不应新建 Revision"
    )
    db_session.commit()

    batches = {b["code"]: b for b in client.get(f"/api/design-packages/{pkg_id}/batches").json()}
    assert len(batches["V2"]["variants"]) == 3
    assert {v["id"] for v in batches["V2"]["variants"]} == set(v1_variant_ids)


def test_mixed_second_upload_reuses_only_unchanged(
    client, phase2_package, create_upload, upload_file, db_session
):
    """第二次上传里部分图片没变、部分变了 → 只复用没变的。"""
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    v1 = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    ).json()
    v1_by_position = {v["position"]: v for v in v1["variants"]}

    second = create_upload(pkg_id, originalPackageName="混合.zip", uploadType="NEW_BATCH")
    upload2 = second["packageUpload"]["id"]
    for index, content in [(1, _striped_png(1)), (2, _striped_png(2)), (3, _striped_png(99))]:
        upload_file(
            upload2, content, f"{index}.png", "image/png", kind="VARIANT", file_role="VARIANT"
        )
    client.post(f"/api/uploads/{upload2}/pair")
    _confirm_all(client, upload2)

    v2 = client.post(
        f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯", "uploadId": upload2}
    ).json()
    assert v2["code"] == "V2"
    assert v2["createdVariantCount"] == 1
    assert v2["reusedVariantCount"] == 2
    by_position = {v["position"]: v for v in v2["variants"]}
    assert by_position[1]["id"] == v1_by_position[1]["id"]
    assert by_position[2]["id"] == v1_by_position[2]["id"]
    assert by_position[3]["id"] != v1_by_position[3]["id"]
    assert by_position[3]["displayCode"] == "3-2"

    db_session.rollback()
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 4  # 3 + 1 新的
    db_session.commit()


def test_version_number_comes_from_batch_not_per_variant(client, phase2_package):
    """版本号只能从 Batch 获取：同一次确认只产生一个版本。"""
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    first = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯"}).json()
    assert first["code"] == "V1"
    assert {v["batchCode"] for v in first["variants"]} == {"V1"}
    assert {v["batchId"] for v in first["variants"]} == {first["id"]}


def test_batch_rolls_back_completely_on_failure(client, phase2_package, db_session, monkeypatch):
    """建版任一步失败 → 整套回滚（Batch / Variant / Revision 都不留）。"""
    import app.services.batch_service as batch_service

    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])

    real_new_id = batch_service.new_id
    calls = {"n": 0}

    def flaky_new_id(prefix: str) -> str:
        calls["n"] += 1
        if calls["n"] >= 4:
            raise RuntimeError("模拟建版中途失败")
        return real_new_id(prefix)

    monkeypatch.setattr(batch_service, "new_id", flaky_new_id)
    with pytest.raises(RuntimeError):
        client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯"})
    monkeypatch.undo()

    db_session.rollback()
    assert db_session.execute(select(DerivativeBatch)).scalars().all() == []
    assert db_session.execute(select(MaterialVariant)).scalars().all() == []
    assert db_session.execute(select(VariantRevision)).scalars().all() == []
    rows = db_session.execute(select(MaterialPairing)).scalars().all()
    assert len(rows) == 3 and all(row.status == "CONFIRMED" for row in rows)
    assert all(row.variant_id is None for row in rows)
    db_session.commit()

    ok = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["code"] == "V1"


def test_same_position_cannot_repeat_in_same_batch(client, phase2_package, db_session):
    """UNIQUE(batch_id, design_package_material_id) 真实生效。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    batch = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯"}).json()

    variant = (
        db_session.execute(
            select(MaterialVariant).where(MaterialVariant.batch_id == batch["id"])
        )
        .scalars()
        .first()
    )
    assert variant is not None
    from app.services.repository import new_id

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO material_variants "
                "(id, material_id, design_package_material_id, batch_id, display_code, deleted, created_at, updated_at) "
                "VALUES (:id, :material_id, :dpm_id, :batch_id, :code, 0, NOW(6), NOW(6))"
            ),
            {
                "id": new_id("variant"),
                "material_id": variant.material_id,
                "dpm_id": variant.design_package_material_id,
                "batch_id": batch["id"],
                "code": "1-1-dup",
            },
        )
    db_session.rollback()


# ================================================================ 六、overview / 刷新


def test_overview_exposes_pairing_state_after_refresh(client, phase2_package):
    """刷新后「已上传 / 已配对 / 已确认 / 已生成版本」都还在。"""
    ctx = phase2_package(3)
    pkg_id = ctx["pkg"]["id"]
    upload_id = ctx["upload_id"]

    before = _overview(client, pkg_id)
    assert before["phase"] == "PHASE_2"
    assert before["pairingUploadId"] == upload_id
    assert len(before["pairings"]) == 3
    assert {dto["pairKey"] for dto in before["pairings"]} == {"1", "2", "3"}
    assert before["pairingSummary"]["PAIRED"] == 3
    assert before["uploadedFiles"]["mainCount"] == 3
    assert before["uploadedFiles"]["variantCount"] == 3
    assert before["countCheck"]["variantUploadCount"] == 3
    assert before["currentBatch"] is None
    # 不再有相似度 / 匹配结果字段
    assert "matches" not in before
    assert "matchSummary" not in before

    _confirm_all(client, upload_id)
    client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "小柯"})

    after = _overview(client, pkg_id)
    assert after["pairingSummary"]["CONFIRMED"] == 3
    assert after["currentBatch"]["code"] == "V1"
    assert [v["displayCode"] for v in after["variants"]] == ["1-1", "2-1", "3-1"]
    assert after["blockingAnomalies"] == []
    assert after["designPackage"]["designerName"] == "设计美工-肖芸"
    assert after["upload"]["uploaderName"] == "实际上传人-小柯"
    assert after["operatorName"] == "张三"

    again = _overview(client, pkg_id)
    assert again["variants"] == after["variants"]
    assert again["pairings"] == after["pairings"]


def test_overview_has_no_similarity_fields_at_all(client, phase2_package):
    """确认生产链路的 DTO 里没有任何相似度/候选字段。"""
    ctx = phase2_package(2)
    body = _overview(client, ctx["pkg"]["id"])
    blob = str(body)
    for forbidden in (
        "similarity",
        "Similarity",
        "phashDistance",
        "candidates",
        "AUTO_HIGH",
        "AUTO_LOW",
    ):
        assert forbidden not in blob, f"overview 里仍残留 {forbidden}"


def test_no_similarity_code_left_in_production_modules():
    """源码级确认：生产模块里不再有 Hungarian / 相似度匹配代码。"""
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    forbidden = (
        "hungarian",
        "linear_sum_assignment",
        "phash_similarity",
        "hamming_distance",
        "matched_similarity",
        "material_match_results",
        "match_service",
    )
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.name}:{token}")
    assert offenders == [], f"仍在生产代码里发现相似度匹配痕迹：{offenders}"


# ================================================================ 七、人员字段（回归）


def test_designer_name_only_without_id_is_accepted(client):
    response = client.post(
        "/api/design-packages",
        json={
            "name": "只有设计美工姓名",
            "designCode": "DS-ONLY-NAME",
            "designerName": "设计美工-阿May",
            "designerId": None,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["designerName"] == "设计美工-阿May"
    assert response.json()["designerId"] is None


def test_missing_designer_name_returns_explicit_error(client):
    response = client.post(
        "/api/design-packages",
        json={"name": "没有设计美工", "designCode": "DS-NO-DESIGNER"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "设计美工" in response.json()["message"]


def test_missing_design_code_returns_explicit_error(client):
    """设计编码必填：素材的「关联设计」靠它聚合，不能空着上传。"""
    response = client.post(
        "/api/design-packages",
        json={"name": "没有设计编码", "designerName": "设计美工-阿May"},
    )
    assert response.status_code == 422, response.text


def test_blank_design_code_is_rejected(client):
    response = client.post(
        "/api/design-packages",
        json={"name": "空设计编码", "designCode": "   ", "designerName": "设计美工-阿May"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "设计编码" in response.json()["message"]


def test_design_code_can_be_corrected_and_is_logged(client, create_package):
    """设计编码允许事后修正（美工改编号），并留下维护记录。"""
    pkg = create_package("可改设计编码", design_code="DS-OLD")
    assert pkg["designCode"] == "DS-OLD"

    patched = client.patch(
        f"/api/design-packages/{pkg['id']}", json={"designCode": "DS-NEW"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["designCode"] == "DS-NEW"

    blank = client.patch(f"/api/design-packages/{pkg['id']}", json={"designCode": "  "})
    assert blank.status_code == 422, blank.text

    logs = client.get("/api/activity-logs", params={"design_package_id": pkg["id"]}).json()
    assert any(log["action"] == "UPDATE_DESIGN_CODE" for log in logs), logs


def test_design_code_is_exposed_in_overview(client, create_package):
    pkg = create_package("整包视图设计编码", design_code="HB-2026-0917-Z")
    overview = client.get(f"/api/design-packages/{pkg['id']}/overview").json()
    assert overview["designPackage"]["designCode"] == "HB-2026-0917-Z"


def test_uploader_and_operator_name_only_are_accepted(client, create_package):
    pkg = create_package("只有姓名的人员字段")
    response = client.post(
        f"/api/design-packages/{pkg['id']}/uploads",
        json={
            "originalPackageName": "names-only.zip",
            "uploaderName": "实际上传人-小柯",
            "operatorName": "归属运营-王姐",
            "uploadType": "INITIAL",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["packageUpload"]["uploaderId"] is None
    assert response.json()["session"]["operatorId"] is None


# ================================================================ 八、设计包删除（软删除 / 归档）
#    与「素材中心只显示已生成版本的包」


def test_draft_without_batch_is_hidden_from_with_batch_list(client, phase2_package):
    """只上传、还没确认整包生成版本的设计包 = 草稿：素材中心拉列表时不应该看到它。"""
    draft = client.post(
        "/api/design-packages",
        json={"name": "还没生成版本的草稿", "designCode": "DS-DRAFT", "designerName": "设计美工-阿May"},
    ).json()

    all_packages = client.get("/api/design-packages").json()
    assert draft["id"] in [p["id"] for p in all_packages], "草稿仍应存在于上传页可见的全量列表"

    with_batch = client.get("/api/design-packages", params={"withBatch": "true"}).json()
    assert draft["id"] not in [p["id"] for p in with_batch], "草稿不能出现在素材中心列表里"

    # 生成版本之后才出现
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    )
    with_batch_after = client.get("/api/design-packages", params={"withBatch": "true"}).json()
    ids = [p["id"] for p in with_batch_after]
    assert ctx["pkg"]["id"] in ids
    assert draft["id"] not in ids


def test_delete_design_package_is_soft_and_hides_it(client, phase2_package, db_session):
    """删除 = 软删除（归档）：列表与整包视图都不再返回，但数据与维护记录都还在。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    )

    deleted = client.delete(f"/api/design-packages/{pkg_id}", params={"actor": "运营-王姐"})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["archivedAt"] is not None

    assert pkg_id not in [p["id"] for p in client.get("/api/design-packages").json()]
    assert pkg_id not in [
        p["id"] for p in client.get("/api/design-packages", params={"withBatch": "true"}).json()
    ]
    archived_list = client.get("/api/design-packages", params={"includeArchived": "true"}).json()
    assert pkg_id in [p["id"] for p in archived_list]

    overview = client.get(f"/api/design-packages/{pkg_id}/overview")
    assert overview.status_code == 409
    assert overview.json()["code"] == "DESIGN_PACKAGE_ARCHIVED"

    # 数据没有物理删除：位置 / 版本 / 主素材都还在
    assert client.get(f"/api/materials").json()  # materials 接口仍可用
    rows = db_session.execute(
        select(DesignPackageMaterial).where(DesignPackageMaterial.design_package_id == pkg_id)
    ).scalars().all()
    assert len(rows) == 2, "软删除不能删掉位置数据"
    assert db_session.execute(
        select(DerivativeBatch).where(DerivativeBatch.design_package_id == pkg_id)
    ).scalars().all(), "软删除不能删掉版本数据"

    logs = client.get("/api/activity-logs", params={"design_package_id": pkg_id}).json()
    assert any(log["action"] == "ARCHIVE_PACKAGE" for log in logs), logs

    # 删除后不能再改配对 / 生成版本
    pairing = client.post(f"/api/uploads/{ctx['upload_id']}/pair")
    assert pairing.status_code == 409 and pairing.json()["code"] == "DESIGN_PACKAGE_ARCHIVED"
    batch = client.post(
        f"/api/design-packages/{pkg_id}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    )
    assert batch.status_code == 409 and batch.json()["code"] == "DESIGN_PACKAGE_ARCHIVED"
    upload = client.post(
        f"/api/design-packages/{pkg_id}/uploads",
        json={"originalPackageName": "again.zip", "uploaderName": "小柯", "uploadType": "NEW_BATCH"},
    )
    assert upload.status_code == 409 and upload.json()["code"] == "DESIGN_PACKAGE_ARCHIVED"


def test_restore_design_package(client, phase2_package):
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    client.delete(f"/api/design-packages/{pkg_id}")

    restored = client.post(f"/api/design-packages/{pkg_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["archivedAt"] is None
    assert pkg_id in [p["id"] for p in client.get("/api/design-packages").json()]
    assert client.get(f"/api/design-packages/{pkg_id}/overview").status_code == 200


def test_delete_is_idempotent_and_unknown_package_404(client, create_package):
    pkg = create_package("删两次")
    first = client.delete(f"/api/design-packages/{pkg['id']}")
    second = client.delete(f"/api/design-packages/{pkg['id']}")
    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["archivedAt"] == first.json()["archivedAt"], "重复删除不应刷新归档时间"

    missing = client.delete("/api/design-packages/pkg-not-exist")
    assert missing.status_code == 404


# ================================================================ 九、标签 / 负责人 / 流转记录
#    （创建时继承，之后独立修改；所有变更写 ActivityLog）


def _confirm_and_batch(client, ctx):
    _confirm_all(client, ctx["upload_id"])
    return client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    ).json()


def test_tags_inherit_on_create_then_independent(client, phase2_package):
    """设计包标签 → 建 MAT 时继承 → 建版时副素材再继承；之后三者独立。"""
    pkg = client.post(
        "/api/design-packages",
        json={
            "name": "标签继承包",
            "designCode": "DS-TAGS",
            "designerName": "设计美工-阿May",
            "responsibleName": "肖芸",
            "tags": ["万圣节", "NHL"],
        },
    ).json()
    assert pkg["tags"] == ["万圣节", "NHL"]
    assert pkg["responsibleName"] == "肖芸"

    # 用 fixture 的 tags 参数建包：设计包带上标签后再建 MAT，继承才生效
    ctx = phase2_package(2, package_name="标签继承包素材", design_code="DS-TAGS", tags=["万圣节", "NHL"])
    _confirm_and_batch(client, ctx)

    materials = client.get("/api/materials").json()
    mat = next(m for m in materials if m["materialCode"] == "MAT-000001")
    assert mat["tags"] == ["万圣节", "NHL"], "主素材应继承设计包标签"

    detail = client.get("/api/design-packages/" + ctx["pkg"]["id"] + "/overview").json()
    variant = detail["variants"][0]
    assert variant["tags"] == ["万圣节", "NHL"], "副素材建版时应继承主素材标签"

    # 修改主素材标签 → 设计包与副素材不受影响（创建时复制，不是动态绑定）
    client.patch(f"/api/materials/{mat['materialCode']}/tags", json={"tags": ["夜跑"], "actor": "小柯"})
    mat2 = client.get(f"/api/materials/{mat['materialCode']}").json()
    assert mat2["tags"] == ["夜跑"]
    pkg2 = client.get(f"/api/design-packages/{ctx['pkg']['id']}").json()
    assert pkg2["tags"] == ["万圣节", "NHL"]
    detail2 = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    assert detail2["variants"][0]["tags"] == ["万圣节", "NHL"], "副素材标签不能被主素材修改覆盖"


def test_package_tag_patch_does_not_overwrite_materials_without_sync(client, phase2_package):
    """修改设计包标签不会自动覆盖包内素材的标签（用户要自己点同步）。"""
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    materials = client.get("/api/materials").json()
    mat = next(m for m in materials if m["materialCode"] == "MAT-000001")

    client.patch(f"/api/materials/{mat['materialCode']}/tags", json={"tags": ["独立标签"], "actor": "小柯"})
    client.patch(
        f"/api/design-packages/{ctx['pkg']['id']}",
        json={"tags": ["包新标签"]},
    )
    mat2 = client.get(f"/api/materials/{mat['materialCode']}").json()
    assert mat2["tags"] == ["独立标签"], "未勾选同步时素材标签不能被设计包修改覆盖"


def test_sync_new_package_tags_to_materials(client, phase2_package):
    """勾选「同步新增标签到包内素材」时，只把新增的标签补过去，素材已有标签不动。"""
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    materials = client.get("/api/materials").json()
    mat = next(m for m in materials if m["materialCode"] == "MAT-000001")
    client.patch(f"/api/materials/{mat['materialCode']}/tags", json={"tags": ["已有"], "actor": "小柯"})

    client.patch(
        f"/api/design-packages/{ctx['pkg']['id']}",
        json={"tags": ["万圣节", "夜景"], "syncTagsToMaterials": True},
    )
    mat2 = client.get(f"/api/materials/{mat['materialCode']}").json()
    assert mat2["tags"] == ["已有", "万圣节", "夜景"], "同步只补新增标签，不覆盖素材已有的"

    logs = client.get("/api/activity-logs", params={"design_package_id": ctx["pkg"]["id"]}).json()
    assert any(log["action"] == "BATCH_ADD_TAG" for log in logs), logs


def test_tag_changes_are_logged(client, phase2_package):
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    materials = client.get("/api/materials").json()
    mat = next(m for m in materials if m["materialCode"] == "MAT-000001")

    client.patch(f"/api/materials/{mat['materialCode']}/tags", json={"tags": ["夜跑"], "actor": "肖芸"})
    history = client.get(f"/api/materials/{mat['materialCode']}/history").json()
    tag_logs = [log for log in history if log["action"] in {"ADD_TAG", "REMOVE_TAG", "REPLACE_TAG"}]
    assert tag_logs, "标签修改必须写流转记录"
    assert tag_logs[0]["actor"] == "肖芸"


def test_responsible_change_is_logged(client, phase2_package):
    ctx = phase2_package(2)
    client.patch(
        f"/api/design-packages/{ctx['pkg']['id']}",
        json={"responsibleName": "李晴"},
    )
    pkg2 = client.get(f"/api/design-packages/{ctx['pkg']['id']}").json()
    assert pkg2["responsibleName"] == "李晴"

    logs = client.get("/api/activity-logs", params={"design_package_id": ctx["pkg"]["id"]}).json()
    resp_logs = [log for log in logs if log["action"] == "CHANGE_RESPONSIBLE"]
    assert resp_logs, "负责人修改必须写流转记录"
    assert resp_logs[0]["before"] == "设计美工-肖芸"
    assert resp_logs[0]["after"] == "李晴"


def test_material_history_includes_package_events(client, phase2_package):
    """主素材流转记录 = 自己 + 引用它的设计包的关键事件（建版/配对确认等）。"""
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    history = client.get("/api/materials/MAT-000001/history").json()
    actions = {log["action"] for log in history}
    assert "CREATE_MATERIAL" in actions or "REUSE_MATERIAL" in actions
    assert "CREATE_BATCH" in actions or "CREATE_VARIANT" in actions


def test_variant_history_includes_creation(client, phase2_package):
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    detail = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    variant = detail["variants"][0]
    history = client.get(f"/api/material-variants/{variant['id']}/history").json()
    actions = {log["action"] for log in history}
    assert "CREATE_VARIANT" in actions, "副素材流转记录里要有创建事件"


def test_variant_detail_endpoint(client, phase2_package):
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    detail = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    variant = detail["variants"][0]
    info = client.get(f"/api/material-variants/{variant['id']}").json()
    assert info["displayCode"] == "1-1"
    assert info["materialCode"] == "MAT-000001"
    assert info["designPackageName"] == ctx["pkg"]["name"]
    assert info["batchCode"] == "V1"


def test_batch_tag_op_add_and_remove(client, phase2_package):
    """批量标签：多选素材一次加/删标签，写 BATCH_ADD_TAG / BATCH_REMOVE_TAG。"""
    ctx = phase2_package(2)
    _confirm_and_batch(client, ctx)
    detail = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    variant_ids = [v["id"] for v in detail["variants"]]

    add = client.post(
        "/api/tags/batch",
        json={"op": "add", "tags": ["批量标签"], "targetType": "MATERIAL_VARIANT", "targetIds": variant_ids, "actor": "小柯"},
    )
    assert add.status_code == 200, add.text
    assert add.json()["updated"] == len(variant_ids)

    detail2 = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    assert all("批量标签" in v["tags"] for v in detail2["variants"])

    remove = client.post(
        "/api/tags/batch",
        json={"op": "remove", "tags": ["批量标签"], "targetType": "MATERIAL_VARIANT", "targetIds": variant_ids, "actor": "小柯"},
    )
    assert remove.status_code == 200
    assert remove.json()["updated"] == len(variant_ids)
    detail3 = client.get(f"/api/design-packages/{ctx['pkg']['id']}/overview").json()
    assert all("批量标签" not in v["tags"] for v in detail3["variants"])

    logs = client.get("/api/activity-logs", params={"design_package_id": ctx["pkg"]["id"]}).json()
    batch_actions = {log["action"] for log in logs}
    assert "BATCH_ADD_TAG" in batch_actions and "BATCH_REMOVE_TAG" in batch_actions
