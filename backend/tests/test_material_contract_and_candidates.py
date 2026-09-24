from __future__ import annotations

import httpx
import pytest

from app.orders.candidate_builder import build_pool
from app.services.material_platform import (
    ChildAsinCandidates,
    ContractImage,
    HttpMaterialPlatformClient,
    MaterialPlatformContractError,
    VariantCandidate,
)


def _contract() -> ChildAsinCandidates:
    return ChildAsinCandidates(
        "B0TEST", "BLADE_SHOES", None,
        (
            VariantCandidate("v1", material_id="m1", category_code="BLADE_SHOES", images=(
                ContractImage("MATERIAL_SOURCE", asset_id="s1", uri="/api/assets/s1/content"),
                ContractImage("FINAL_EFFECT", sole_color="BLACK", asset_id="b1"),
                ContractImage("FINAL_EFFECT", sole_color="WHITE", asset_id="w1"),
            )),
            VariantCandidate("v2", material_id="m2", category_code="OTHER", images=(
                ContractImage("MATERIAL_SOURCE", asset_id="s2"),
            )),
        ),
    )


def test_candidate_builder_isolates_role_color_and_category():
    contract = _contract()
    assert [x.asset_id for x in build_pool(contract, order_category="BLADE_SHOES", image_role="MATERIAL_SOURCE").candidates] == ["s1"]
    assert [x.asset_id for x in build_pool(contract, order_category="BLADE_SHOES", image_role="FINAL_EFFECT", sole_color="BLACK").candidates] == ["b1"]
    assert [x.asset_id for x in build_pool(contract, order_category="BLADE_SHOES", image_role="FINAL_EFFECT", sole_color="WHITE").candidates] == ["w1"]
    assert build_pool(contract, order_category="OTHER", image_role="MATERIAL_SOURCE").reason == "CATEGORY_MISMATCH"


def test_http_contract_relative_asset_uri_and_404():
    payload = {"childAsin": "B0TEST", "categoryCode": "BLADE_SHOES", "batch": None, "variants": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})
        if request.url.path.endswith("B0MISSING"):
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    client = HttpMaterialPlatformClient("http://material.test")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    result = client.get_candidates_by_child_asin("B0TEST")
    assert result.child_asin == "B0TEST"
    assert client.get_image_bytes(ContractImage("MATERIAL_SOURCE", uri="/api/assets/a/content")) == b"image"
    with pytest.raises(MaterialPlatformContractError) as exc:
        client.get_candidates_by_child_asin("B0MISSING")
    assert exc.value.code == "CHILD_ASIN_NOT_FOUND"
