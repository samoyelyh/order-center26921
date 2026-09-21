# ============================================================================
# order-center 图片相似度 embedder（自包含，不依赖 material-platform）
#
# 订单匹配（MATERIAL_SOURCE / FINAL_EFFECT）需要 ResNet-50 特征做余弦相似度。
# 这是通用图像工具，不属于素材域模型，order-center 自带一份。
# 无 torch / 权重时回退轻量颜色直方图（仅测试/降级）。
# ============================================================================

from __future__ import annotations

import io

import numpy as np
from PIL import Image


def get_embedder():
    try:
        return _ResNet50Embedder()
    except Exception:  # noqa: BLE001
        return _LightweightEmbedder()


class _LightweightEmbedder:
    """轻量视觉向量（HSV 直方图 + 空间亮度），无 torch 依赖时的回退。"""

    model = "lightweight-hsv-v1"
    version = 1
    dim = 16 * 8 * 8 + 6 + 64

    def embed(self, data: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((64, 64), Image.LANCZOS)
        hsv = img.convert("HSV")
        arr = np.asarray(hsv, dtype=np.uint8).reshape(-1, 3)
        hist, _ = np.histogramdd(arr, bins=(16, 8, 8), range=((0, 256), (0, 256), (0, 256)))
        hist = hist.ravel().astype(np.float32)
        total = hist.sum()
        if total > 0:
            hist /= total
        h, s, v = arr[:, 0] / 255.0, arr[:, 1] / 255.0, arr[:, 2] / 255.0
        stats = np.array([h.mean(), s.mean(), v.mean(), h.std(), s.std(), v.std()], dtype=np.float32)
        spatial = np.asarray(img.convert("L").resize((8, 8), Image.LANCZOS), dtype=np.float32).ravel() / 255.0
        vec = np.concatenate([hist, stats, spatial]).astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


class _ResNet50Embedder:
    """ResNet-50（ImageNet 预训练，去 fc）→ 2048 维特征。权重本地文件优先。"""

    model = "resnet50-imagenet-v1"
    version = 1
    dim = 2048

    def __init__(self) -> None:
        import torch
        from torchvision import transforms as T
        from torchvision.models import resnet50

        from app.core.config import settings

        import os

        self._model = resnet50(weights=None)
        weight_path = settings.image_search_model_weights
        if os.path.exists(weight_path):
            self._model.load_state_dict(torch.load(weight_path, map_location="cpu"))
        else:
            from torchvision.models import ResNet50_Weights

            self._model = resnet50(weights=ResNet50_Weights.IMAGENET1K_V1)
        self._model.fc = torch.nn.Identity()
        self._model.eval()
        self._transforms = T.Compose(
            [
                T.Resize(256),
                T.CenterCrop(224),
                T.ToTensor(),
                T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ]
        )

    def embed(self, data: bytes) -> np.ndarray:
        import torch

        img = Image.open(io.BytesIO(data)).convert("RGB")
        x = self._transforms(img).unsqueeze(0)
        with torch.no_grad():
            vec = self._model(x).squeeze(0).numpy().astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec /= norm
        return vec


def embed_image_bytes(data: bytes) -> np.ndarray:
    return get_embedder().embed(data)
