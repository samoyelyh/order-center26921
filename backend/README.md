# order-center 后端

order-center 是独立订单中心，只拥有订单事实、解析、匹配缓存、人工审核和销量归因。
`Material`、`Variant`、`Batch`、`Distribution`、`Asset`（素材域）属于独立的
material-platform；本项目不导入其 ORM、不查询其数据库，也不复制素材表。

## 当前链路

```text
领星 ZIP
  ├─ 外层 Excel：订单号 + ASIN 对应行，补齐 MSKU / 数量 / 买家留言 / 原图路径
  └─ 内层 ZIP：JSON + XML + JPG + SVG
       ↓
GenericOrderParser
       ↓
CategoryResolver
       ↓
ParserRegistry → BladeShoesParser(BLADE_SHOES_V1) / UNKNOWN
       ↓
NormalizedOrder → OrderItem
       ↓
MaterialMatcher → MaterialPlatformClient → MatchResult
```

外层 Excel 与定制 JSON 以 `(orderId, ASIN)` 关联，并保留重复键的出现顺序；真实领星
导出中 JSON 常没有 SKU，因此不能跳过 Excel。订单以 `orderItemId` 去重；缺失时使用不含
ZIP 路径的稳定业务内容哈希，避免重新导出改变路径后重复计单。

## 领域边界与自有表

- `order_assets`
- `order_import_batches`
- `order_items`
- `order_import_batch_items`
- `order_buyer_assets`
- `material_url_bindings`
- `asin_variant_bindings`（过渡期候选定位，不是永久的一对一事实）
- `order_match_actions`

Variant/MAT 只保存平台稳定 ID/code，不建立跨库外键。

## BladeShoesParser V1

- 同一非预览 `OptionCustomization` 中 `thumbnailImage != overlayImage`：
  `MATERIAL_SOURCE`，`material_url = thumbnailImage`。
- 同一非预览节点中两图相同，且选项含 Black/White：`FINAL_EFFECT`，记录
  `final_effect_url` 和 `sole_color`。
- `Add Your Logo`、`Front Blank` 入口：`PREVIEW_ONLY`，即使两图不同也不当素材原图。
- 其他未覆盖结构：`UNKNOWN + REVIEW_REQUIRED`，不随意取第一张图。
- 买家原图来自 `ImageCustomization.image`；SVG 来自 version3.0
  `ImagePrinting.svgImage`。两者同时存在时都保存，原图为 primary。
- 普通 Name/Number 与明确 Front/BACK 字段分开，不自动复制。

## 素材平台契约

配置 `MATERIAL_PLATFORM_BASE_URL` 后使用 HTTP 客户端，当前依赖：

- `GET /api/health`
- `GET /api/material-platform/variants/by-asin/{asin}`
- `GET /api/material-platform/variants/{variantId}`
- `GET /api/material-platform/variants/{variantId}/image?role=MATERIAL_SOURCE`
- `GET /api/material-platform/variants/{variantId}/image?role=FINAL_EFFECT&soleColor=Black|White`
- `GET /api/material-platform/variants?limit=...`

匹配优先使用平台的 ASIN 候选契约；只有平台明确返回空候选时，才使用本地
`asin_variant_bindings` 作为过渡期回退。平台不可访问会返回明确的
`MATERIAL_PLATFORM_UNAVAILABLE`（HTTP 503），不会伪造候选。

`APP_ENV=production|prod|staging` 时禁止 Fake；缺少 `MATERIAL_PLATFORM_BASE_URL` 会直接
报依赖不可用。开发/测试只有在 `MATERIAL_PLATFORM_ALLOW_FAKE=1` 时才允许 Fake。

## 启动

```powershell
cd backend
python -m pip install -r requirements.txt
$env:MYSQL_DATABASE = "order_center"
$env:STORAGE_BACKEND = "local"
$env:MATERIAL_PLATFORM_BASE_URL = "http://127.0.0.1:8000"
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010
```

也可在仓库根目录运行：

```powershell
docker compose up -d mysql backend
```

生产环境至少设置：

```text
APP_ENV=production
MATERIAL_PLATFORM_BASE_URL=https://material-platform.example
MATERIAL_PLATFORM_ALLOW_FAKE=0
```

## 测试

```powershell
cd backend
python -m pytest -q
```

数据库测试使用独立的 `order_center_test` MySQL 库。脱敏的真实领星结构 fixture 位于
`tests/fixtures/orders/`；真实导出文件本身不进入仓库。

## API

- `POST /api/order-imports`
- `GET /api/order-imports`
- `GET /api/order-imports/{batchId}`
- `GET /api/order-imports/{batchId}/items`
- `POST /api/order-imports/items/{itemId}/auto-match`
- `POST /api/order-imports/{batchId}/auto-match`
- `POST /api/order-imports/items/{itemId}/match`
- `GET /api/order-match/review`
- `GET /api/order-sales`
- `GET /api/material-platform/variants`
- `GET /api/health`

销量只聚合 `match_status=CONFIRMED` 的 `OrderItem.quantity`；重新匹配会自然改变聚合结果，
不向素材表写累计计数。
