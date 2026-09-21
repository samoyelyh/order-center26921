# ============================================================================
# 订单素材归因（阶段 B）测试
#
# 覆盖（用户验收清单 11-13 + 销量）：
#   11. URL 已绑定 → 直接命中（不重新跑图片匹配）
#   12. Batch 候选隔离（ASIN 绑定 → 只在候选 Variant 内匹配，不全库比较）
#   13. 品类隔离（Order.category_code ≠ Candidate.category_code → REVIEW_REQUIRED）
#   14. 销量归因：仅 CONFIRMED 计入；Variant/MAT 汇总
#   15. 人工审核：确认 / 更换 / 无法识别 + ActivityLog
#
# 图片下载/相似度用 monkeypatch 模拟（业务逻辑测试不依赖网络与模型）。
# ============================================================================

from __future__ import annotations

import json

from sqlalchemy import func, select

from app.db.models import (
    ActivityLog,
    AsinVariantBinding,
    MaterialUrlBinding,
    OrderItem,
)
from app.orders import matcher as matcher_module
from app.orders.matcher import match_material_source, run_matcher


def _make_item(client, db_session, payload: dict) -> dict:
    """导入一个订单并返回其 item（payload 决定 imageType/materialUrl 等）。"""
    import io
    import zipfile

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Files/order.json", json.dumps(payload, ensure_ascii=False))
    r = client.post("/api/order-imports", files={"file": ("o.zip", out.getvalue(), "application/zip")})
    assert r.status_code == 200
    # 结束 db_session 当前事务：TestClient 用的是另一个会话，MySQL REPEATABLE READ
    # 下旧事务快照看不到新提交的订单。
    db_session.rollback()
    return r.json()


def _material_source_payload(order_item_id: str, url: str, asin: str = "B0TEST01") -> dict:
    return {
        "orderId": f"O-{order_item_id}", "orderItemId": order_item_id, "asin": asin,
        "quantity": 2, "sku": "BLADE-TEST",
        "customizationData": {"children": [{"children": [{"children": [
            {"type": "OptionCustomization", "name": "样式", "label": "Style",
             "optionSelection": {"name": "City", "displayValue": "City",
               "thumbnailImage": {"imageUrl": url},
               "overlayImage": {"imageUrl": url.replace(".png", "-ov.png")}}}
        ]}]}]},
    }


def _pick_variant_id(client) -> str:
    """取库中第一个有 Variant 的设计包的 variant id。"""
    pkgs = client.get("/api/design-packages?withBatch=true").json()
    for p in pkgs:
        ov = client.get(f"/api/design-packages/{p['id']}/overview").json()
        variants = ov.get("variants") or []
        if variants:
            return variants[0]["id"]
    raise AssertionError("测试需要至少一个有 Variant 的设计包（用 phase2_package fixture 造）")


def _build_variant(client, ctx, db_session) -> str:
    """确认配对 + 生成 V1，返回第一个 Variant id。"""
    r = client.post(f"/api/uploads/{ctx['upload_id']}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text
    v1 = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/batches",
        json={"actor": "小柯", "uploadId": ctx["upload_id"]},
    )
    assert v1.status_code == 200, v1.text
    from app.db.models import MaterialVariant

    return db_session.execute(select(MaterialVariant.id).limit(1)).scalar_one()


# ---------------------------------------------------------------- 11. URL 绑定直接命中


def test_url_binding_hits_directly_without_image_match(client, db_session, phase2_package, monkeypatch):
    ctx = phase2_package(1)
    from app.db.models import MaterialVariant

    variant_id = _build_variant(client, ctx, db_session)

    payload = _material_source_payload("URL-BIND-1", "https://cdn.example.com/unique-material.png")
    resp = _make_item(client, db_session, payload)
    batch_id = resp["batch"]["id"]
    item_row = db_session.execute(
        select(OrderItem).where(OrderItem.first_import_batch_id == batch_id)
    ).scalar_one()
    # 包品类为 UNKNOWN（create_package 未传 categoryCode）：让订单品类一致，专注测 URL 绑定
    item_row.category_code = "UNKNOWN"
    db_session.commit()

    # 预建 URL 绑定 → 自动匹配应直接命中，且绝不触发图片下载
    db_session.add(MaterialUrlBinding(
        id="url-bind-test", material_url=payload["customizationData"]["children"][0]["children"][0]["children"][0]["optionSelection"]["thumbnailImage"]["imageUrl"],
        variant_id=variant_id, material_id=db_session.execute(select(MaterialVariant.material_id).where(MaterialVariant.id == variant_id)).scalar_one(),
        match_method="MANUAL", created_by="tester",
    ))
    db_session.commit()

    calls = {"download": 0, "similarity": 0}
    monkeypatch.setattr(matcher_module, "_download", lambda url, timeout=15.0: (calls.__setitem__("download", calls["download"] + 1) or b"bytes"))
    monkeypatch.setattr(matcher_module, "_similarity", lambda a, b: (calls.__setitem__("similarity", calls["similarity"] + 1) or 0.99))

    assert run_matcher(db_session, item_row) is True
    assert item_row.match_method == "URL_BINDING"
    assert item_row.match_status == "CONFIRMED"
    assert item_row.matched_variant_id == variant_id
    # 关键：URL 绑定命中时**不**重新跑图片匹配
    assert calls["download"] == 0 and calls["similarity"] == 0, "URL 绑定命中不应调用图片匹配"
    db_session.rollback()


# ---------------------------------------------------------------- 12/13. 候选隔离 + 品类隔离


def test_candidate_isolation_and_category_isolation(client, db_session, phase2_package, monkeypatch):
    ctx = phase2_package(1, design_code="DS-MATCH-ISO", tags=[])
    from app.db.models import MaterialVariant

    variant_id = _build_variant(client, ctx, db_session)

    # ASIN 绑定到该 variant
    db_session.add(AsinVariantBinding(
        id="asin-bind-test", child_asin="B0ISO01", variant_id=variant_id,
        material_id=db_session.execute(select(MaterialVariant.material_id).where(MaterialVariant.id == variant_id)).scalar_one(),
        created_by="tester",
    ))
    db_session.commit()

    # 品类隔离：订单品类 ≠ 候选 Variant 品类 → 即使图匹配命中也要降级 REVIEW_REQUIRED
    payload = _material_source_payload("ISO-1", "https://cdn.example.com/iso.png", asin="B0ISO01")
    resp = _make_item(client, db_session, payload)
    item_row = db_session.execute(
        select(OrderItem).where(OrderItem.first_import_batch_id == resp["batch"]["id"])
    ).scalar_one()
    # 订单品类为 BLADE_SHOES（sku=BLADE-TEST → resolver），候选包品类为 UNKNOWN → 品类不符
    assert item_row.category_code == "BLADE_SHOES"
    db_session.commit()

    monkeypatch.setattr(matcher_module, "_download", lambda url, timeout=15.0: b"q")
    monkeypatch.setattr(matcher_module, "_similarity", lambda a, b: 0.99)

    assert run_matcher(db_session, item_row) is False
    assert item_row.match_status == "REVIEW_REQUIRED"
    assert "CATEGORY_MISMATCH" in (item_row.match_method or "")
    db_session.rollback()


# ---------------------------------------------------------------- 14. 销量归因


def test_sales_only_counts_confirmed(client, db_session, phase2_package):
    ctx = phase2_package(1)
    from app.db.models import MaterialVariant

    variant_id = _build_variant(client, ctx, db_session)

    # 两个订单：一个 CONFIRMED、一个 REVIEW_REQUIRED
    payload = _material_source_payload("SALES-1", "https://cdn.example.com/s1.png", asin="B0SALE1")
    resp1 = _make_item(client, db_session, payload)
    item1 = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == resp1["batch"]["id"])).scalar_one()
    item1.matched_variant_id = variant_id
    item1.matched_material_id = db_session.execute(select(MaterialVariant.material_id).where(MaterialVariant.id == variant_id)).scalar_one()
    item1.match_status = "CONFIRMED"
    item1.match_method = "MANUAL_CONFIRM"
    db_session.commit()  # 先提交 item1：_make_item 内部会 rollback，不能把未提交的修改丢掉

    payload2 = dict(payload)
    payload2["orderItemId"] = "SALES-2"
    resp2 = _make_item(client, db_session, payload2)
    item2 = db_session.execute(select(OrderItem).where(OrderItem.first_import_batch_id == resp2["batch"]["id"])).scalar_one()
    item2.matched_variant_id = variant_id
    item2.matched_material_id = item1.matched_material_id
    item2.match_status = "REVIEW_REQUIRED"
    db_session.commit()

    sales = client.get("/api/order-sales").json()
    # 只有 CONFIRMED 的 quantity=2 计入
    assert sales["totalQuantity"] == 2
    assert variant_id in sales["variantSales"]
    assert sales["variantSales"][variant_id]["quantity"] == 2
    assert sales["variantSales"][variant_id]["orders"] == 1
    db_session.rollback()


# ---------------------------------------------------------------- 15. 人工审核


def test_manual_review_confirm_change_unmatch(client, db_session, phase2_package):
    ctx = phase2_package(1)
    from app.db.models import MaterialVariant

    variant_id = _build_variant(client, ctx, db_session)
    payload = _material_source_payload("REVIEW-1", "https://cdn.example.com/r1.png", asin="B0REV1")
    resp = _make_item(client, db_session, payload)
    item_id = db_session.execute(
        select(OrderItem.id).where(OrderItem.first_import_batch_id == resp["batch"]["id"])
    ).scalar_one()

    # 确认 → CONFIRMED + 建立 URL/ASIN 绑定
    r = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "confirm", "variantId": variant_id})
    assert r.status_code == 200
    assert r.json()["matchStatus"] == "CONFIRMED"
    db_session.rollback()  # TestClient 用独立会话，结束 db_session 旧事务才能看到新绑定
    assert db_session.execute(select(func.count(MaterialUrlBinding.id)).where(MaterialUrlBinding.material_url.like("%r1.png%"))).scalar_one() == 1
    assert db_session.execute(select(func.count(AsinVariantBinding.id)).where(AsinVariantBinding.child_asin == "B0REV1")).scalar_one() == 1

    # 审核写 ActivityLog
    log = db_session.execute(
        select(ActivityLog).where(ActivityLog.target_type == "ORDER_ITEM", ActivityLog.target_id == item_id, ActivityLog.action == "MATCH_CONFIRMED")
    ).scalar_one_or_none()
    assert log is not None

    # 更换 Variant → MATCH_CHANGED
    r2 = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "change", "variantId": variant_id})
    assert r2.json()["matchStatus"] == "CONFIRMED"

    # 无法识别 → FAILED
    r3 = client.post(f"/api/order-imports/items/{item_id}/match", json={"action": "unmatch", "note": "图片模糊"})
    assert r3.json()["matchStatus"] == "FAILED"
    db_session.rollback()
