# ============================================================================
# 指纹服务
#
#   BLAKE3  → 完全相同文件检测（assets.blake3）
#   pHash   → 图片视觉相似（assets.phash / assets.phash_version）
#
# pHash 采用标准 DCT 实现（与 imagehash.phash 同算法），输出 64bit，
# 存 MySQL BINARY(8)。PSD 不做 pHash（不可稳定解码）。
# ============================================================================

from __future__ import annotations

import io
import logging

import blake3
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

PHASH_VERSION = 1
PHASH_BITS = 64


def compute_blake3(data: bytes) -> str:
    """计算 BLAKE3（十六进制小写，64 字符）。"""
    return blake3.blake3(data).hexdigest()


def is_image_mime(mime_type: str) -> bool:
    return mime_type.startswith("image/") and mime_type not in {
        "image/vnd.adobe.photoshop",
        "image/x-photoshop",
    }


def compute_phash(data: bytes, hash_size: int = 8, highfreq_factor: int = 4) -> bytes | None:
    """
    计算 64bit pHash，返回 8 字节。

    实现：灰度 → 缩放到 hash_size*highfreq_factor → 2D DCT → 取左上低频 → 与均值比较。
    无法解码时返回 None（不抛异常，避免影响上传主流程）。
    只用 Pillow 自带的 DCT（Image.core 无 DCT），因此用 numpy-free 的纯 Python DCT。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("L")
            img_size = hash_size * highfreq_factor
            img = img.resize((img_size, img_size), Image.Resampling.LANCZOS)
            pixels = list(img.getdata())
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("pHash 跳过：图片无法解码 (%s)", exc)
        return None

    # 二维 DCT-II（分离式）
    matrix = [pixels[i * img_size:(i + 1) * img_size] for i in range(img_size)]
    dct_rows = [_dct(row) for row in matrix]
    dct_cols = []
    for col in range(img_size):
        column = [dct_rows[row][col] for row in range(img_size)]
        dct_cols.append(_dct(column))

    # 取左上 hash_size×hash_size 的低频块
    low = []
    for u in range(hash_size):
        for v in range(hash_size):
            low.append(dct_cols[v][u])

    # 去掉 DC 分量后取均值
    ac = low[1:]
    mean = sum(ac) / len(ac) if ac else 0.0

    bits = []
    for value in low:
        bits.append(1 if value > mean else 0)
    # 只保留 PHASH_BITS 位
    bits = bits[:PHASH_BITS]
    while len(bits) < PHASH_BITS:
        bits.append(0)

    out = bytearray(8)
    for index, bit in enumerate(bits):
        if bit:
            out[index // 8] |= 1 << (7 - (index % 8))
    return bytes(out)


def _dct(vector: list[float]) -> list[float]:
    """一维 DCT-II（纯 Python，输入长度 <= 64，性能足够）。"""
    import math

    n = len(vector)
    result = []
    for k in range(n):
        total = 0.0
        factor = math.pi / n
        for i, value in enumerate(vector):
            total += value * math.cos(factor * (i + 0.5) * k)
        result.append(total)
    return result


def phash_to_hex(phash: bytes | None) -> str | None:
    return phash.hex() if phash else None


def phash_from_hex(value: str | None) -> bytes | None:
    if not value:
        return None
    raw = bytes.fromhex(value)
    if len(raw) != 8:
        raise ValueError("phash 必须是 8 字节（16 位十六进制）")
    return raw


def extract_image_metadata(data: bytes) -> tuple[int | None, int | None]:
    """提取图片宽高。无法解码返回 (None, None)。"""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.width, img.height
    except (UnidentifiedImageError, OSError, ValueError):
        return None, None
