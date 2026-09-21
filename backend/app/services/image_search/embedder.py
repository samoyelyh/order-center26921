# ============================================================================
# 图片向量生成（ImageSearchService 的 Embedding 层）
#
# 环境评估：本机无 torch / transformers / faiss / cv2 / sklearn，
# 因此**不引入深度模型**，用 numpy + Pillow 的轻量颜色/空间特征向量。
# 这与之前「pHash 汉明距离自动匹配」完全无关 —— 它是独立的向量化方案，
# 并且 Embedding 层是接口化封装，以后可以换 CLIP / DINO / FAISS 而不动上层。
# ============================================================================

from __future__ import annotations

import io
from typing import Protocol

import numpy as np
from PIL import Image

# 模型标识：换算法时 embedding_version 递增，新旧向量按 model+version 区分
EMBEDDING_MODEL = "lightweight-hsv-v1"
EMBEDDING_VERSION = 1
# H16 × S8 × V8 直方图 + 6 维全局统计 + 8×8 空间亮度
EMBEDDING_DIM = 16 * 8 * 8 + 6 + 64


class ImageEmbedder(Protocol):
    """向量化器接口：给图片字节，返回归一化 float32 向量。"""

    model: str
    version: int
    dim: int

    def embed(self, data: bytes) -> np.ndarray: ...


class LightweightHsvEmbedder:
    """轻量视觉向量：颜色直方图 + 全局统计 + 空间亮度（numpy + Pillow）。"""

    model = EMBEDDING_MODEL
    version = EMBEDDING_VERSION
    dim = EMBEDDING_DIM

    def embed(self, data: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((64, 64), Image.LANCZOS)
        hsv = img.convert("HSV")
        arr = np.asarray(hsv, dtype=np.uint8).reshape(-1, 3)  # (N, H,S,V)

        # 1) HSV 颜色直方图 16×8×8
        hist, _ = np.histogramdd(
            arr,
            bins=(16, 8, 8),
            range=((0, 256), (0, 256), (0, 256)),
        )
        hist = hist.ravel().astype(np.float32)
        total = hist.sum()
        if total > 0:
            hist /= total

        # 2) 全局统计：H/S/V 的均值与标准差
        h = arr[:, 0].astype(np.float32) / 255.0
        s = arr[:, 1].astype(np.float32) / 255.0
        v = arr[:, 2].astype(np.float32) / 255.0
        stats = np.array(
            [h.mean(), s.mean(), v.mean(), h.std(), s.std(), v.std()],
            dtype=np.float32,
        )

        # 3) 空间亮度 8×8（保留粗略构图）
        small = img.convert("L").resize((8, 8), Image.LANCZOS)
        spatial = np.asarray(small, dtype=np.float32).ravel() / 255.0

        vec = np.concatenate([hist, stats, spatial]).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


def get_embedder() -> ImageEmbedder:
    """优先 ResNet-50（Milvus 官方 quickstart 的做法）；torch 不可用时回退轻量向量。"""
    try:
        return ResNet50Embedder()
    except Exception:  # noqa: BLE001
        return LightweightHsvEmbedder()


# ---------------------------------------------------------------- ResNet-50

import os as _os
import time as _time

RESNET50_MODEL = "resnet50-imagenet-v1"
RESNET50_VERSION = 1
RESNET50_DIM = 2048

_model_cache = None
_model_loaded_at = 0.0


class ResNet50Embedder:
    """ResNet-50（ImageNet 预训练，去掉 fc）→ 2048 维特征向量。

    对齐 Milvus 官方 bootcamp 的 image_search_with_milvus：用 torchvision 的
    ResNet-50 作为图片特征提取器，维度 2048，余弦相似度检索。
    权重优先从本地文件加载（backend/models/resnet50-0676ba61.pth），
    不存在时尝试 torch 官方下载（需要外网）。
    """

    model = RESNET50_MODEL
    version = RESNET50_VERSION
    dim = RESNET50_DIM

    def __init__(self) -> None:
        self._model = _load_resnet50()
        self._transforms = _resnet50_transforms()

    def embed(self, data: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        x = self._transforms(img).unsqueeze(0)
        import torch

        with torch.no_grad():
            vec = self._model(x).squeeze(0).numpy().astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


def _resnet50_transforms():
    from torchvision import transforms as T

    return T.Compose(
        [
            T.Resize(256),
            T.CenterCrop(224),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )


def _load_resnet50():
    global _model_cache, _model_loaded_at
    if _model_cache is not None and (_time.time() - _model_loaded_at) < 3600:
        return _model_cache
    import torch
    from torchvision.models import resnet50

    from app.core.config import settings

    model = resnet50(weights=None)
    weight_path = settings.image_search_model_weights
    if _os.path.exists(weight_path):
        model.load_state_dict(torch.load(weight_path, map_location="cpu"))
    else:
        # 本地没有权重：尝试 torch 官方下载（外网）
        from torchvision.models import ResNet50_Weights

        model = resnet50(weights=ResNet50_Weights.IMAGENET1K_V1)
    model.fc = torch.nn.Identity()
    model.eval()
    _model_cache = model
    _model_loaded_at = _time.time()
    return model
