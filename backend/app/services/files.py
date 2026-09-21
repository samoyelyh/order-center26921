# ============================================================================
# 上传文件角色识别 + 同名配对键（pairKey）
#
# 两件事分清楚：
#   1. file_role：这是「主图 / PSD / 副图 / 其他」——用户点的是哪个上传入口最权威。
#   2. pair_key（同名配对键）：**同一次上传内**把主图/PSD/副图配到一起的键，
#      就是文件名去掉扩展名：
#          main/1.jpg  psd/1.psd  variant/1.jpg   →  pair_key = "1"
#
# 主副素材关系**只由同名 pairKey 决定**，不根据图片内容推断关系。
# pairKey 只在当前设计包内有效，不是永久身份；
# 永久主素材身份仍然是 MAT-xxxxxx。
# ============================================================================

from __future__ import annotations

import re
from typing import Literal

UploadFileRole = Literal["MAIN_PREVIEW", "PSD", "VARIANT", "OTHER"]

ALL_FILE_ROLES: tuple[UploadFileRole, ...] = ("MAIN_PREVIEW", "PSD", "VARIANT", "OTHER")

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff"}
DESIGN_EXTENSIONS = {"psd", "psb"}

_MAIN_PREVIEW_RE = re.compile(r"^\d{1,4}$")
_VARIANT_RE = re.compile(r"^\d{1,4}[-_]\d{1,3}$")

# 文件名的最大长度保护（pair_key 列宽 255）
MAX_PAIR_KEY_LENGTH = 255


def split_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def stem_of(filename: str) -> str:
    """去掉扩展名与目录，保留用于分类/配对的名字部分。"""
    base = filename.replace("\\", "/").split("/")[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def pair_key_of(filename: str) -> str:
    """
    同名配对键 = 文件 basename 去掉扩展名（去首尾空白、转小写）。

    例：main/1.jpg → 1；psd/1.PSD → 1；variant/1.jpeg → 1
    解析不出来（空名字 / 只有扩展名）时返回空字符串，
    配对阶段会把它报成「无法解析 pairKey」异常。
    """
    key = stem_of(filename or "").strip().lower()
    if not key:
        return ""
    return key[:MAX_PAIR_KEY_LENGTH]


def classify_by_filename(filename: str) -> UploadFileRole:
    """
    按文件名推断角色。仅用于给用户上传的文件归类，不参与配对。
    """
    ext = split_extension(filename)
    stem = stem_of(filename).strip()

    if ext in DESIGN_EXTENSIONS:
        return "PSD"
    if ext not in IMAGE_EXTENSIONS:
        return "OTHER"
    if _VARIANT_RE.match(stem):
        return "VARIANT"
    if _MAIN_PREVIEW_RE.match(stem):
        return "MAIN_PREVIEW"
    return "OTHER"


def normalize_role(value: str | None, filename: str) -> UploadFileRole:
    """
    前端可显式传 fileRole（用户点的是「主素材」还是「副素材」入口，最权威）；
    传了就校验后用，没传则按文件名推断。
    """
    if value:
        candidate = value.strip().upper()
        for role in ALL_FILE_ROLES:
            if role == candidate:
                return role
        raise ValueError(f"未知的文件角色：{value}")
    return classify_by_filename(filename)


def position_hint_of(filename: str) -> int | None:
    """
    从文件名提取「位置提示」，仅用于展示排序与人工核对。
    例如 1.jpg → 1、3-1.jpg → 3。
    绝不用它决定主副素材配对关系（配对只按 pair_key 同名）。
    """
    stem = stem_of(filename).strip()
    match = re.match(r"^(\d{1,4})", stem)
    if not match:
        return None
    position = int(match.group(1))
    return position if 1 <= position <= 9999 else None
