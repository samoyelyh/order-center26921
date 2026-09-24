"""Build the small, typed image candidate pool used by the matcher."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.material_platform import ChildAsinCandidates, ContractImage, VariantCandidate


@dataclass(frozen=True)
class CandidateImage:
    variant_id: str
    material_id: str | None
    category_code: str | None
    image_role: str
    sole_color: str | None
    asset_id: str | None
    image_url: str | None
    image: ContractImage


@dataclass(frozen=True)
class CandidatePool:
    contract: ChildAsinCandidates
    candidates: tuple[CandidateImage, ...]
    reason: str | None = None


def build_pool(contract: ChildAsinCandidates, *, order_category: str | None,
               image_role: str, sole_color: str | None = None) -> CandidatePool:
    if not order_category or contract.category_code != order_category:
        return CandidatePool(contract, (), "CATEGORY_MISMATCH")
    variants = [v for v in contract.variants if v.category_code == order_category]
    if not variants:
        return CandidatePool(contract, (), "CATEGORY_MISMATCH" if contract.variants else "NO_VARIANT_CANDIDATES")
    images: list[CandidateImage] = []
    wanted_color = sole_color.upper() if sole_color else None
    for variant in variants:
        for image in variant.images:
            if image.image_role != image_role:
                continue
            if image_role == "FINAL_EFFECT" and (image.sole_color or "").upper() != wanted_color:
                continue
            images.append(CandidateImage(
                variant_id=variant.variant_id, material_id=variant.material_id,
                category_code=variant.category_code, image_role=image.image_role,
                sole_color=image.sole_color, asset_id=image.asset_id,
                image_url=image.uri, image=image,
            ))
    if not images:
        if image_role == "MATERIAL_SOURCE":
            reason = "NO_MATERIAL_SOURCE_IMAGE"
        elif wanted_color:
            reason = f"NO_FINAL_EFFECT_{wanted_color}"
        else:
            reason = "NO_FINAL_EFFECT_COLOR"
        return CandidatePool(contract, (), reason)
    return CandidatePool(contract, tuple(images))
