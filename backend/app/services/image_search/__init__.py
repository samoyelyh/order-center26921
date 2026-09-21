# 图片搜索服务（与主副素材配对完全独立，只做「找相似」）
from app.services.image_search.embedder import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    EMBEDDING_VERSION,
    RESNET50_DIM,
    RESNET50_MODEL,
    RESNET50_VERSION,
    LightweightHsvEmbedder,
    ResNet50Embedder,
    get_embedder,
)
from app.services.image_search.index import MilvusVectorIndex, NumpyCosineIndex, get_index
from app.services.image_search.service import (
    embed_image_bytes,
    get_asset_vector,
    index_asset,
    indexable_asset_ids,
    rebuild_all,
    save_embedding,
    search,
)

__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL",
    "EMBEDDING_VERSION",
    "RESNET50_DIM",
    "RESNET50_MODEL",
    "RESNET50_VERSION",
    "LightweightHsvEmbedder",
    "ResNet50Embedder",
    "MilvusVectorIndex",
    "NumpyCosineIndex",
    "embed_image_bytes",
    "get_asset_vector",
    "get_embedder",
    "get_index",
    "index_asset",
    "indexable_asset_ids",
    "rebuild_all",
    "save_embedding",
    "search",
]
