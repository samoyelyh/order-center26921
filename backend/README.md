# 素材中心 V2 后端 —— Phase 2

**Phase 2 范围：同一次上传内按「同名 pairKey」直接配对 → 人工确认 → 整包统一生成 V1 / V2 / V3。**

> Phase 1 已具备：设计包 / 上传记录 / Asset（BLAKE3 + pHash）/ MAT / 设计包位置 / 上传会话幂等。
> **Phase 3 才做**：派发运营（DistributionTask）/ ASIN / 订单 URL 识别 / 销量归因。
>
> **重要（本轮重构）**：主副素材关系**只由文件名（同名 pairKey）决定**，
> 与图片内容无关；**pHash 相似度 / 匈牙利算法 / TopN 候选 / 高-中-低匹配分级已全部删除**。
> pHash 仍会随文件落库，但**不参与任何主副素材关联判断**；BLAKE3 只用于「完全相同的文件」去重。

```
设计包 DesignPackage
  → 每次上传记录 PackageUpload
  → 文件 Asset（MinIO）
  → BLAKE3 / pHash
  → 主素材 Material（MAT-xxxxxx）
  → 设计包位置 DesignPackageMaterial
  → 上传会话与幂等
  → 同名 pairKey 配对 MaterialPairing（可人工修改 / 确认）
  → 整包生成 DerivativeBatch V1/V2 + MaterialVariant + VariantRevision
  → 前端上传页真实接入
```

技术栈：**FastAPI + SQLAlchemy 2.x + Alembic + MySQL 8 + MinIO**（单体，无微服务 / 无 MQ / 无 ES / 无 Kafka）。

---

## 一、快速开始（三种方式）

### 方式 A：Docker 全量（推荐给新同学）

```bash
# 1. 起 MySQL + MinIO + 后端（后端会自动 alembic upgrade head）
docker compose up -d --build

# 2. 验证
curl http://127.0.0.1:8000/api/health
# {"status":"ok","phase":"PHASE_2","database":{"ok":true,...},"storage":{"backend":"minio","ok":true,...}}

# 3. 起前端（另开终端，见第五节）
```

MinIO 控制台：http://127.0.0.1:9001 （minioadmin / minioadmin），bucket `material-center` 自动创建。

### 方式 B：Docker 只起依赖，后端本地跑（推荐开发）

```bash
docker compose up -d mysql minio

cd backend
python -m venv .venv && . .venv/Scripts/activate     # Windows
pip install -r requirements.txt

# 环境变量（PowerShell）
$env:MYSQL_HOST="127.0.0.1"; $env:MYSQL_PORT="3306"
$env:MYSQL_USER="root"; $env:MYSQL_PASSWORD="rootpass"; $env:MYSQL_DATABASE="material_center"
$env:STORAGE_BACKEND="minio"
$env:S3_ENDPOINT="http://127.0.0.1:9000"
$env:S3_ACCESS_KEY="minioadmin"; $env:S3_SECRET_KEY="minioadmin"
$env:S3_PUBLIC_BASE_URL="http://127.0.0.1:9000/material-center"

python -m alembic upgrade head
# 必须监听 0.0.0.0：只监听 127.0.0.1 时，局域网其它机器无法访问后端
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 方式 C：无 Docker（本机 MySQL + 本地文件存储）

后端支持 `STORAGE_BACKEND=local`，把对象存储换成后端本地目录（接口语义与 MinIO 一致），
并通过 `/storage/**` 静态路由把文件暴露给浏览器。**仅用于开发**，生产必须用 MinIO。

```bash
# 先准备好一个 MySQL 库
mysql -h 127.0.0.1 -P 3306 -u root -p -e \
  "CREATE DATABASE material_center CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"

cd backend
pip install -r requirements.txt

# PowerShell
$env:MYSQL_HOST="127.0.0.1"; $env:MYSQL_PORT="3306"
$env:MYSQL_USER="root"; $env:MYSQL_PASSWORD="你的密码"; $env:MYSQL_DATABASE="material_center"
$env:STORAGE_BACKEND="local"
$env:LOCAL_STORAGE_ROOT="./.storage"

python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 二、环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_DEBUG` | `1` | 调试模式 |
| `MYSQL_HOST` | `127.0.0.1` | MySQL 主机 |
| `MYSQL_PORT` | `3306` | MySQL 端口 |
| `MYSQL_USER` | `root` | MySQL 用户 |
| `MYSQL_PASSWORD` | 空 | MySQL 密码 |
| `MYSQL_DATABASE` | `material_center` | 业务库 |
| `STORAGE_BACKEND` | `minio` | `minio` / `local` |
| `S3_ENDPOINT` | `http://127.0.0.1:9000` | MinIO S3 地址 |
| `S3_BUCKET` | `material-center` | 桶名（不存在自动创建） |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | `minioadmin` | 凭据 |
| `S3_PUBLIC_BASE_URL` | 空 | 浏览器可访问前缀；空则用 `S3_ENDPOINT/bucket` |
| `LOCAL_STORAGE_ROOT` | `./.storage` | `STORAGE_BACKEND=local` 时的根目录 |
| `MAX_UPLOAD_SIZE_BYTES` | `209715200` | 单文件上限（200MB） |
| `CORS_ORIGINS` | localhost/127.0.0.1 的 3000 / 4173 / 5173 | 逗号分隔的精确来源白名单 |
| `CORS_ALLOW_ORIGIN_REGEX` | 本机 + 任意私网 IPv4 | 来源正则（局域网 IP 换一个也不用改配置） |

参考 `backend/.env.example`。

---

## 三、数据库 Migration（Alembic）

```bash
cd backend

# 建表
python -m alembic upgrade head

# 当前版本
python -m alembic current

# 回滚
python -m alembic downgrade base

# 新增迁移（改完 models 后）
python -m alembic revision --autogenerate -m "your_change"
```

现有迁移：

| Revision | 内容 |
|---|---|
| `0001_phase1_material_assets` | Phase 1 全部表（设计包/上传/Asset/主素材/位置/会话/发号器/维护记录） |
| `0002_package_upload_assets` | **Asset ↔ 上传行为改为多对多**：新建 `package_upload_assets`、搬迁存量归属、删除 `assets.upload_session_id` |
| `0003_designer_and_phase2` | `design_packages.designer_id/designer_name`、`package_uploads.uploader_id` 改为可空；新建 Phase 2 四表 |
| `0004_pua_role_unique` | `package_upload_assets` 唯一键加上 `file_role`（同一份字节可同时是主图与副图，不能被改写） |
| `0005_pairkey_pairings` | **同名 pairKey 重构**：`package_upload_assets.pair_key`（存量按文件名回填、同角色重名回填空串）、`UNIQUE(package_upload_id, file_role, pair_key)`；**删除 `material_match_results`**，新建 `material_pairings` |
| `0006_design_code` | **设计编码**：`design_packages.design_code`（用户上传时手填，非唯一 + 建索引）；存量回填成系统 `code` |
| `0007_tags_responsible` | **标签 / 负责人**：`design_packages.tags`、`material_variants.tags`（JSON 数组），`design_packages.responsible_id / responsible_name`（负责人）；存量负责人回填成设计美工、副素材标签回填成所属主素材 |
| `0008_asset_image_embeddings` | Asset 图片搜索向量元数据（与素材配对流程隔离） |
| `0009_order_imports` | **订单导入 Phase A-D**：设计包品类字段；允许非图片文件仅使用 BLAKE3；`order_import_batches`、`order_items`、`order_import_batch_items`、`order_buyer_assets` |

> 注意：`alembic.ini` 刻意保持 **纯 ASCII**。configparser 在本机用 GBK 读取该文件，含中文会抛 `UnicodeDecodeError`。

---

## 四、测试

```bash
cd backend
python -m pytest            # 全量
python -m pytest -q         # 简版
```

测试会：
- 自动重建 `material_center_test` 库并 `alembic upgrade head`
- 使用 `STORAGE_BACKEND=local` + 临时目录，**不碰 MinIO / 开发库**
- 每个测试前清空业务表并重置 MAT 发号器

覆盖范围见 `tests/`：
| 文件 | 覆盖 |
|---|---|
| `test_phase1.py` | 设计包 / 上传会话幂等 / Asset 与指纹 / Material 复用 / 位置重传复用与换图冲突 / PSD Revision / DTO / 事务回滚 / 存储失败 |
| `test_asset_linking.py` | **Asset ↔ 上传多对多归属**：新文件一条关联行 / BLAKE3 复用新增关联行且旧归属不被改写 / 同上传同角色重名 `DUPLICATE_PAIR_KEY` / 同一份字节可同时是主图与副图 / `?role=VARIANT` 过滤 / 刷新后副图仍在 / 美工与归属运营自由文本并持久化 / 内容代理 / 文件名兜底分类 |
| `test_phase2.py` | **人员字段拆分 / 设计编码 / 同名 pairKey 配对 / 异常（缺副图·多余副图·重名·无法解析）/ 人工改配与确认 / V1 与 V2 整版生成 / 内容相同则复用副素材 / 整包重传 V2 / 设计包删除（软删除）与草稿隔离 / 标签继承与独立修改 / 批量标签 / 负责人流转 / 主副素材流转记录**（共 48 个用例） |
| `test_concurrency.py` | MAT 编码并发安全 / 同一 uploadSessionId 并发幂等 / 同一 position 并发只成功一次 |

> `test_matching.py`（hamming / 匈牙利 / 相似度）已随相似度匹配一并删除。

---

## 五、启动前端

```bash
# 仓库根目录
npm install
npm run dev          # http://localhost:3000
```

前端默认走真实后端（`VITE_MATERIAL_API=1`）。

**API 基址解析顺序（重要）：**

1. `VITE_MATERIAL_API_BASE`（显式配置，最高优先级）
2. 否则：`当前页面的 protocol + hostname + :8000/api`

第 2 条是关键：局域网同事用 `http://192.168.0.31:3000` 打开页面时，请求会自动打到
`http://192.168.0.31:8000/api`。**绝不能把 `127.0.0.1:8000` 写死**，否则同事浏览器里的
`127.0.0.1` 指的是他自己的电脑，页面必然报 `Failed to fetch`。

需要本地 Mock 回退时：

```bash
# .env.local
VITE_MATERIAL_API=0
```

上传页 `/materials/upload` 顶部会显示连通状态，并区分三种失败（配「重新检测」按钮）：

| 状态 | 含义 | 页面动作 |
|---|---|---|
| 后端已连接 | `/api/health` 返回 `status=ok` | 正常上传 |
| 后端依赖异常 | HTTP 通了，但 `database.ok=false` 或 `storage.ok=false` | 显示具体不健康的依赖 |
| CORS 被拦截 | 服务能连上（`no-cors` 探测成功），但响应缺少 CORS 头 | 提示把页面来源加入白名单 |
| 后端连接失败 | 两次探测都失败（没启动 / 只监听 127.0.0.1 / 端口不对） | 直接给出 `uvicorn --host 0.0.0.0` 命令 |

> 判定技巧：CORS 拦截与「服务没起来」在 JS 里都是 `fetch` 抛 `TypeError`，分不清。
> 所以探活时先发一次普通请求，失败后再发一次 `mode: 'no-cors'`：
> 后者成功 = 服务器活着（问题在 CORS 头），后者也失败 = 确实连不上。

**局域网自测：**

```powershell
# 后端必须在 0.0.0.0 上（否则只有本机能连）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# 前端也允许局域网访问（vite --host）
npm run dev -- --host

# 在另一台机器上验证
curl http://192.168.0.31:8000/api/health
# 浏览器打开 http://192.168.0.31:3000/materials/upload
```

---

## 六、API 一览（Phase 1）

| Method | Path | 功能 |
|---|---|---|
| GET | `/api/health` | 健康检查（DB + 存储） |
| POST | `/api/design-packages` | 创建设计包（**必填** `designCode` 设计编码 + `designerName` 设计美工；可带 `tags` 标签、`responsibleName` 负责人；设计编码可跨设计包重复） |
| GET | `/api/design-packages` | 设计包列表（素材中心启动时用它把全库拉进 store）；`?withBatch=true` 只返回已生成版本的包，`?includeArchived=true` 连已删除的一起返回 |
| GET | `/api/design-packages/{id}` | 设计包详情 |
| DELETE | `/api/design-packages/{id}` | **删除设计包（软删除 / 归档）**：列表与素材中心不再显示，数据与维护记录保留，可恢复 |
| POST | `/api/design-packages/{id}/restore` | **恢复**被删除（归档）的设计包 |
| GET | `/api/design-packages/{id}/overview` | **上传页整包视图（聚合 DTO）**；`variants` 返回**全部版本**的副素材（每条带 `assetId/previewUrl`，素材中心缩略图靠它） |
| GET | `/api/design-packages/{id}/logs` | 设计包维护记录 |
| POST | `/api/design-packages/{id}/uploads` | **创建上传记录 + 会话（Idempotency-Key / uploadSessionId 幂等）** |
| GET | `/api/design-packages/{id}/uploads` | 上传历史 |
| GET | `/api/uploads/{uploadId}` | 上传记录详情 |
| POST | `/api/uploads/{uploadId}/files` | **上传文件 → Asset（BLAKE3 去重 + pHash 落库；记录 `fileRole` 与 `pairKey`）** |
| POST | `/api/uploads/{uploadId}/materials` | **提交整包位置 → 复用/新建 MAT + PSD Revision + 位置（单事务；同位置重传同一张主素材=复用，换图=409）** |
| GET | `/api/upload-sessions/{id}` | 查询上传会话（stage / received_files） |
| PATCH | `/api/upload-sessions/{id}` | 更新会话（运营 / 备注 / stage） |
| GET | `/api/uploads/{uploadId}/assets` | **查询本次上传交付的文件（可 `?role=VARIANT` 取副图）** |
| POST | `/api/uploads/{uploadId}/pair` | **按同名 pairKey 自动配对（可重复执行；人工改过的行不覆盖）** |
| GET | `/api/uploads/{uploadId}/pairings` | 本次上传的配对表（主素材 / 副素材 / 状态） |
| PATCH | `/api/uploads/{uploadId}/pairings/{pairingId}` | **人工改配 / 取消配对（副图被别的位置占用 → 409 `VARIANT_ALREADY_PAIRED`，带 `confirmReassign` 再来一次即完成重新分配）** |
| POST | `/api/uploads/{uploadId}/pairings/confirm` | **确认整包配对（缺副图等阻断异常时拒绝）** |
| PATCH | `/api/design-packages/{id}` | 修改设计包（设计编码 / **负责人**（写 `CHANGE_RESPONSIBLE`）/ **标签**（写 `REPLACE_TAG`，`syncTagsToMaterials=true` 时把新增标签同步给包内主素材）/ 美工 / 备注） |
| POST | `/api/design-packages/{id}/batches` | **确认整包并生成下一版（第一次即 V1，第二次 V2…；一个事务里整套统一发版）** |
| GET | `/api/design-packages/{id}/batches` | 版本列表（含副素材与 Revision，标注复用来源） |
| PATCH | `/api/uploads/{uploadId}` | 更新上传记录（美工 / 归属运营 / 备注，均支持手工自由填写） |
| GET | `/api/assets/{id}` | 查询 Asset |
| GET | `/api/assets/{id}/content` | **Asset 内容代理（浏览器出图统一走这里）** |
| GET | `/api/materials` | 主素材列表 |
| GET | `/api/materials/{materialCode}` | 主素材详情（含 PSD 修订历史、跨包引用数） |
| PATCH | `/api/materials/{materialCode}/tags` | **单个主素材改标签**（写 `ADD_TAG` / `REMOVE_TAG` / `REPLACE_TAG` 维护记录） |
| GET | `/api/materials/{materialCode}/history` | **主素材流转记录**（自己的 ActivityLog + 引用它的设计包的关键事件） |
| GET | `/api/material-variants/{variantId}` | **副素材详情**（编号 / 所属 MAT / 设计包 / 版本 / Revision / 标签 / 负责人 / 创建时间） |
| PATCH | `/api/material-variants/{variantId}/tags` | **单个副素材改标签** |
| GET | `/api/material-variants/{variantId}/history` | **副素材流转记录**（自己的 ActivityLog） |
| POST | `/api/tags/batch` | **素材中心多选批量调标签**（`op=add/remove/replace`，`targetType=MATERIAL/MATERIAL_VARIANT`，写 `BATCH_ADD_TAG` / `BATCH_REMOVE_TAG` / `REPLACE_TAG`） |
| GET | `/api/tags` | **全库去重标签**（标签选择器数据源，带出现次数） |
| GET | `/api/activity-logs` | 按 `target_type` / `target_id` / `design_package_id` 查维护记录 |

交互式文档：http://127.0.0.1:8000/docs

---

## 七、数据模型要点

- **`DesignPackage` 不存任何缓存业务字段**（无 `main_material_count` / `variant_count` / `current_batch_no` / `operator_*`），全部由 `/overview` 聚合。
  `design_code`（设计编码）是**用户填的业务编码**，`code`（DP-…）是系统技术编码，两者分开；
  素材的「关联设计」= 引用它的设计包 `design_code` 去重集合，所以 `design_code` 不建唯一约束。
- **删除设计包 = 软删除（`archived_at` 打时间戳）**，绝不物理删除：
  主素材 MAT、已上传文件、位置/配对/版本与维护记录全部保留（同一份文件可能被别的设计包复用）。
  归档后：列表 / 素材中心 / 整包视图都不再返回（`DESIGN_PACKAGE_ARCHIVED` 409），
  也不能再上传、改配对或生成版本；`POST .../restore` 可恢复。
- **草稿设计包（只上传、还没生成版本）不进素材中心**：
  素材中心用 `GET /design-packages?withBatch=true` 拉列表，SQL 侧就排除掉没有 `derivative_batches` 的包。
- **`PackageUpload` 承载每一次真实上传**：`original_package_name` / `upload_session_id` / `uploader_*` / `upload_type`。
- **`Asset` 是唯一文件实体，且不承担上传归属职责**：`storage_key` 为 `assets/{yyyy}/{mm}/{uuid}.{ext}`，**不使用业务名做目录**；`UNIQUE(storage_key)`；BLAKE3 只建普通索引，重复时业务层复用已有 Asset。
- **`PackageUploadAsset` 表达「这次上传以什么角色交付了这个文件」**（多对多，只追加不修改）：
  一个物理文件可以被多次上传复用，所以归属关系**不能**放在 `assets` 的单个字段上
  （旧设计 `assets.upload_session_id` 在 BLAKE3 复用时会被改写，直接把上一次上传的归属改坏，
  表现就是整包视图里旧上传的副图消失 —— 0002 迁移已移除该字段）。
  `file_role ∈ MAIN_PREVIEW / PSD / VARIANT / OTHER`，**只表示文件用途**；
  `pair_key` 是**本次上传内的配对键**（文件名去掉扩展名、转小写，如 `1.jpg` → `1`），
  唯一约束 `UNIQUE(package_upload_id, file_role, pair_key)`。
- **`MaterialPairing`（`material_pairings`）是主副素材关系的唯一事实来源**：
  `design_package_id / package_upload_id / design_package_material_id / variant_asset_id / pair_key /
  source(NAME|MANUAL) / status(PAIRED|UNPAIRED|CONFIRMED)`，`variant_id` 记录该位置最终采用的副素材实体。
  一个副图只能属于一个主素材：`UNIQUE(package_upload_id, variant_asset_id)`。
  **关系只由同名 pairKey 决定，与图片内容无关。**
- **浏览器出图统一走后端代理 `GET /api/assets/{id}/content`**：不暴露 `storage_key` / MinIO 内网地址，
  本地回退与 MinIO 用同一个地址，桶也不需要开公共读。
- **`Material` 只代表主素材**（没有 `type = MAIN/VARIANT`）；副素材在 Phase 2 的 `material_variants`。
- **`DesignPackageMaterial` 只表示「设计包某位置 → 某 MAT」**，文件信息一律通过 `source_asset_id → Asset`，不内联 URL / hash。
- **MAT 编码并发安全**：`code_sequences` 行级锁（`SELECT ... FOR UPDATE` + `UPDATE`），不用 `SELECT MAX()+1`。
- **时间列统一 `DATETIME(6)`**：秒级精度会让同一秒内的多次上传无法定序（会导致「最近一次上传」取错）。
- **Phase 1 未建表**（Phase 3+）：`distribution_tasks` / `distribution_task_items` / `amazon_*_asins` / `order_option_binding`。

---

## 八、目录结构

```
backend/
  app/
    core/config.py          环境配置
    core/errors.py          业务错误（每个失败都有独立 code + 中文消息）
    db/models.py            SQLAlchemy 2.x ORM（Phase 1 表）
    db/session.py           Engine / SessionLocal / get_db
    schemas/dto.py          API DTO（绝不直接序列化 ORM 对象）
    schemas/mappers.py      实体 → DTO 映射
    services/files.py       上传文件角色识别（MAIN_PREVIEW/PSD/VARIANT/OTHER）+ pairKey 解析
    services/fingerprint.py BLAKE3（完全相同文件去重）+ pHash（仅落库，不参与配对）
    services/storage.py     MinIO / 本地文件存储抽象
    services/repository.py  发号器 / 幂等查询 / Asset 复用
    services/activity.py    维护记录写入
    services/pairing_service.py   同名 pairKey 配对 / 人工改配 / 确认 / 异常判定
    services/batch_service.py     整包统一发版（V1/V2…）+ 副素材复用
    services/package_overview.py  整包视图聚合
    api/design_packages.py  设计包 / 上传记录 / 会话 / 日志
    api/uploads.py          Asset 上传 / Asset 查询
    api/materials.py        主素材位置提交 / 素材查询
    api/pairings.py         配对：自动配对 / 查询 / 改配 / 确认
    api/batches.py          整包建版与版本查询
    main.py                 FastAPI 入口（含 CORS、统一错误响应、本地存储静态路由）
  alembic/                  迁移
  tests/                    自动测试
  requirements.txt
  Dockerfile
  .env.example
```

---

## 九、已知限制

- **主素材与副图的对应关系只看文件名**：主素材 `1.jpg` 配副素材 `1.jpg`（`pairKey` 都是 `1`）。
  命名不规范（无法解析出 pairKey）会在配对表里标成异常，必须人工改配，系统不会去猜。
- 同一设计包的**主素材位置不允许换图**（新一版只换副图）：同位置重传同一张主素材 → 复用；
  想让位置 1 换成另一张主图，请新建一个设计包（`POST /materials` 返回 409 `POSITION_CONFLICT`）。
- `display_code`（如 `1-1`）**不做全局唯一**，只在同一个 Batch 内有意义。
- `psd` 尺寸不解析（`width/height` 为 null），PSD 不计算 pHash。
- 演示级鉴权：`actor` / `uploaderName` 由请求传入，未接入真实登录体系。
- 指纹中台化：生产 BLAKE3/pHash 由后端计算；浏览器端 Demo 走 SHA-256 + aHash，两者不混存。
- 派发运营 / ASIN 回填 / 销量归因属 Phase 3，尚未实现。

---

## 十、本轮修复：三个线上问题 + 一个结构问题

### 1. 页面报「无法连接后端服务（http://127.0.0.1:8000/api）：Failed to fetch」

两个真实原因叠加：

| 原因 | 表现 | 修复 |
|---|---|---|
| 后端只监听 `127.0.0.1` | 别的机器访问 `http://192.168.0.31:8000` 连不上 | 统一用 `--host 0.0.0.0` 启动 |
| 前端把 API 基址写死成 `127.0.0.1:8000` | 局域网用户浏览器里的 `127.0.0.1` 是他自己的电脑 | 改为「环境变量 > 当前页面 hostname:8000」，见第五节 |

另外把「连不上 / 依赖不健康 / 被 CORS 拦」三种状态在页面上分开显示（含「重新检测」按钮），
不再是一句无法定位的 `Failed to fetch`。

### 2. 副图上传后看不到（页面显示「副素材 0」）

根因是**结构问题**（见下条第 4 点）：BLAKE3 命中复用时后端把 `assets.upload_session_id`
改写成「最近一次上传」，于是**上一次上传的归属被改坏**，整包视图按 `upload_session_id`
归集文件时，旧上传的副图就不见了。

修复：
- 归属改为 `package_upload_assets` 多对多，复用只新增关联行，永不改写旧归属；
- 整包视图新增 `uploadedFiles`（整包去重）/ `latestUploadedFiles`（最新一次上传），
  按 `main / psd / variants / other` 分组并带 `previewUrl`，刷新页面照样能显示；
- 数量校验文案改为「已建立主素材 N 个、副图 N 张已入库；主素材与副图的对应关系待 Phase 2 匹配」，
  **不再出现「副素材 0 / 缺少 N 张副素材」**。

### 3. 美工、归属运营无法自由手动填写

原来「上传人」是只读、归属运营只能从 3 个写死的运营里选。现在两者都是自由文本输入框：

- 美工 → `package_uploads.uploader_name`（`PATCH /api/uploads/{uploadId}` 可事后修正）
- 归属运营 → `upload_sessions.operator_name`（能对上系统用户时同时写 `operator_id`，
  **对不上就只存名字，绝不因为缺少 userId 拒绝保存**）
- 两者都落 sessionStorage，刷新后输入框里还在

运营聚合查询也不再按 `operator_id IS NOT NULL` 过滤，否则纯手工填写的运营刷新后就「消失」了。

### 4. Asset 与 Upload 的关系结构（`assets.upload_session_id` 单归属错误）

| | 旧设计 | 新设计 |
|---|---|---|
| 归属位置 | `assets.upload_session_id`（单值 FK） | `package_upload_assets`（多对多关联表） |
| 表达能力 | 一个文件只能属于一次上传 | 同一文件可被任意多次上传复用 |
| 复用行为 | 命中 BLAKE3 时**改写**该字段 → 破坏旧上传归属 | 只**新增**一条关联行，旧归属永远不变 |
| 文件角色 | 无处存放 | `file_role` + `original_filename` + `position_hint` |

概念划分：**`Asset` = 物理文件身份**（BLAKE3 去重，可复用）；**`PackageUploadAsset` = 本次上传
以什么角色交付了这个文件**（append-only）。

迁移 `0002_package_upload_assets` 会把存量 `assets.upload_session_id` 关系搬迁成关联行
（`file_role` 按文件名推断），然后删除 `ix_assets_upload`、`fk_assets_upload` 与
`assets.upload_session_id`。MySQL DDL 非事务性，迁移内做了存在性判断，可安全重跑。

> `file_role` 与文件名推断**只用于文件归类与展示排序，以及同名 pairKey 配对**
> —— 配对只看文件名（`1.jpg` ↔ `1.jpg`），与图片内容、相似度算法无关（见第十一节）。
---

## 十一、Phase 2：同名 pairKey 配对与整版生成

### 1. pairKey：本次上传内的配对键

```
pair_key_of("Main/1.JPG")  ->  "1"     # 去掉扩展名 → 转小写 → 去首尾空格，最长 255
pair_key_of("1.psd")       ->  "1"
pair_key_of(".DS_Store")   ->  ""      # 解析不出来 → 归入 UNRESOLVED_PAIR_KEY 异常
```

- `pair_key` **只用于当前设计包的这一次上传**，不是永久身份；永久主素材身份始终是 `MAT-xxxxxx`
- 落库位置：`package_upload_assets.pair_key`，唯一约束 `UNIQUE(package_upload_id, file_role, pair_key)`
  → 同一次上传里，同一个角色出现两个 `1.jpg` 直接 422 `DUPLICATE_PAIR_KEY`，不会静默覆盖
- `pair_key` **大小写不敏感、与图片内容完全无关**：`1.jpg` 和 `1.jpg` 哪怕画的是两张毫不相干的图，也照样配对

### 2. 配对规则（`app/services/pairing_service.py`）

| 情况 | 结果 |
|---|---|
| `MAIN_PREVIEW` + 同 `pairKey` 的 `VARIANT` | 自动配对 `PAIRED`，`source = NAME` |
| `MAIN_PREVIEW` + 同 `pairKey` 的 `PSD` | 建立 PSD 关联（`psd_asset_id`），不参与主副配对 |
| 主素材找不到同名副图 | `status = UNPAIRED` + 阻断异常 `MISSING_VARIANT`（缺副图） |
| 副图没有同名主素材 | 阻断异常 `EXTRA_VARIANT`（多余副图） |
| 两个主素材解析到同一个 `pairKey` | 阻断异常 `DUPLICATE_PAIR_KEY` |
| 文件名解析不出 `pairKey` | 阻断异常 `UNRESOLVED_PAIR_KEY` |
| 还没人工确认就建版 | 阻断异常 `PAIRING_NOT_CONFIRMED` |

- 位置映射：**本次上传的主素材 → 设计包位置**；本次只传副图时（新一版增量上传），
  通过该设计包的历史主素材关联把 `pairKey` 还原成位置，所以「只补传副图」也能配对
- 一个副图只能属于一个主素材：`UNIQUE(package_upload_id, variant_asset_id)`
- 改配时用**两阶段写**（先释放旧占用 + `flush()`，再占用新目标），否则会撞上唯一约束的瞬态冲突

### 3. 人工干预（`PATCH /pairings/{id}`、`POST /pairings/confirm`）

| 动作 | 行为 |
|---|---|
| 改配 | 绑到本次上传的另一张副图，`source = MANUAL` |
| 改配目标已被别的位置占用 | 409 `VARIANT_ALREADY_PAIRED`（响应里带 `confirmReassign`）→ 界面弹「确认重新分配」→ 再发一次即完成：原位置退回 `UNPAIRED`，**绝不双重关联** |
| 取消配对 | `variant_asset_id = NULL`，回到 `UNPAIRED` |
| 重新自动配对 | 只重算 `source = NAME` 的行，**人工改过的行不会被覆盖** |
| 确认 | 整包 `status = CONFIRMED`（有缺副图等阻断异常时 409 `PAIRING_INCOMPLETE`） |

所有动作都写 `ActivityLog`。

### 4. 整包统一发版（`app/services/batch_service.py`）

`POST /design-packages/{id}/batches` 在**一个事务**里完成：

```
DerivativeBatch（version_no 由 SELECT ... FOR UPDATE 发放，禁止每个 Variant 自己算 MAX+1）
+ 每个位置一条 MaterialVariant（display_code = 位置-版本，如 1-1 / 1-2）
+ 内容有变化：新建 VariantRevision(revision_no=1) 并回填 current_revision_id
+ 内容没变化：复用该位置已有的 MaterialVariant（不新建实体，也不新建 Revision）
+ MaterialPairing.variant_id 正式关联
+ ActivityLog
```

- **版本号是整包的，不是每个 MAT 各算各的**：V1 → `1-1 / 2-1 / 3-1`，V2 → `1-2 / 2-2 / 3-2`
- 生成前校验（全部满足才允许）：主素材数 = 副图数 → 每个位置都有配对行 → 全部已确认 → 一个副图只用在一个位置
- 副素材复用判定：配对里的 Asset 与该位置已有副素材**当前 Revision** 是同一个 `asset_id`（或同一个 BLAKE3）
  → 直接沿用原 `MaterialVariant`，Batch 响应用 `createdVariantCount / reusedVariantCount / reusedNotes` 如实报告
- 任一步失败 → 整套回滚（有专门的回归测试：让第 5 次 id 分配失败，断言库里 0 个 Batch/Variant/Revision）
- 真实唯一性是 `UNIQUE(batch_id, design_package_material_id)`；`display_code` 只在 Batch 内有意义

### 5. 「第二次上传」的两种真实路径

| 路径 | 请求 | 结果 |
|---|---|---|
| 只补传新一版副图 | `POST /uploads/{id}/files`（`VARIANT`）→ `/pair` → `/confirm` → `/batches` | 位置与 MAT 不变，V2 = `1-2 / 2-2 / 3-2` |
| 整包重传（页面上就是把主素材一起再选一遍） | 同上，但多传一次主素材 → `POST /materials` | 主素材 BLAKE3 去重后是同一个 Asset → **位置复用、MAT 不重新发号**；副图有变化 → V2 |

> 同一个设计包的同一个位置**不允许换主图**：换了会返回 409 `POSITION_CONFLICT`
> （位置与 MAT 的身份不能被顶替），需要换主素材请新建设计包。

### 6. 本轮删除的东西（相似度路线全部下线）

- 删除文件：`app/services/matching.py`、`app/services/match_service.py`、`app/api/matches.py`、`tests/test_matching.py`
- 删除符号：`hamming_distance_hex`、`similarity_from_distance`、`plan_global_assignment`、
  `MaterialMatchResult` / `material_match_results` 表、`MATCH_STATUS_META` 高/中/低分级、TopN 候选
- 配套清理：`app/services/fingerprint.py` 只保留 BLAKE3 + pHash 计算，不再有任何「距离/相似度」辅助函数
- 回归守护：`tests/test_phase2.py` 里有一条**源码扫描**用例，禁止这些词再次出现在 `app/` 下
  （`hungarian` / `linear_sum_assignment` / `phash_similarity` / `hamming_distance` /
  `matched_similarity` / `material_match_results` / `match_service`）

---

## 十二、P0 回归：「上传并写入素材库」点击没有任何反应

**根因（真实复现 + 浏览器证据）：`src/components/ui/sonner.tsx` 里的 `<Toaster />` 只被定义、从未挂载。**

- `main.tsx` 只渲染了 `<App />`，全仓库没有一处 `<Toaster />`
- sonner 的 `toast()` 只是一个事件发射器：**没有挂载的 Toaster 就没有订阅者，所有
  `toast.error` / `toast.success` 都是静默 no-op**
- 上一轮把「归属运营」从 Select 改成**默认为空的自由文本输入框**后，新会话里它一定是空的，
  于是点击必然命中 `if (!operatorNameText) { toast.error(...); return }` ——
  而这条唯一的反馈被静默吞掉，用户看到的就是「点了没反应」

浏览器实测（修复前）：

```
[data-sonner-toaster] = 0
点击「上传并写入素材库」→ 新增 API 请求 0 个、页面文本无变化、Toast 0 个
```

**修复：**

1. `main.tsx` 挂载 `<Toaster position="top-center" richColors closeButton />`（放在路由之外，切页不卸载）
2. 上传页所有「阻止提交」的条件改成 `block(reason)`：**同时** 弹 Toast **和** 在按钮旁写出一条内联原因，
   并且按钮上方常驻「还差这些才能提交：缺少设计美工 / 缺少归属运营 / 未选择主素材 / 后端服务未连接」
3. 按钮 `disabled` **只由 `processing`（正在提交）决定**，其余条件一律「可点击 → 点击后校验 → 明确报错」；
   禁用类按钮（生成 V1）旁边必须写出禁用原因
4. 人员字段一律按**姓名非空**判断（`designerName.trim()` / `operatorName.trim()`），
   **绝不**再用 `if (!operatorId) return` 或 `disabled={!operatorId}` —— ID 只是未来接用户中心的可选关联

修复后实测：

```
Toaster 已挂载
点击 → Toast「请填写归属运营」+ 按钮旁「缺少归属运营」
未发任何 design-packages 请求（缺少必填时不误发请求）
补齐后提示消失、可以提交
```

---

## 十三、本轮端到端验收证据

| 层级 | 命令 | 结果 |
|---|---|---|
| 后端单测 | `cd backend; python -m pytest` | **87 passed** |
| 真实 HTTP 验收 | `python .tmp-verify/verify_pairing.py`（需后端在 8000） | **50 / 50 通过** |
| 设计包删除 / 草稿隔离（HTTP） | `python .tmp-verify/verify_archive.py` | **15 / 15 通过** |
| 浏览器验收（Playwright + 真实后端） | `node .tmp-verify/acceptance.cjs`（需前端 3000 + 后端 8000） | **问题清单：无**（0 console error） |
| 删除设计包 / 草稿隔离（浏览器） | `python .tmp-verify/setup_archive_fixture.py` → `node .tmp-verify/verify-package-delete.cjs` | **问题清单：无** |
| 用户 4 问题专项验收 | `node .tmp-verify/verify-issues.cjs` | **问题清单：无** |
| 素材中心冒烟 | `node .tmp-verify/smoke-materials.cjs` | **问题清单：无** |

浏览器验收脚本覆盖的完整链路：

```
填名称/设计美工/实际上传人/归属运营
→ 选 3 个主素材 + 3 个同名副素材 → 点「上传并写入素材库」（点击即时有进度反馈）
→ 请求序列：POST /design-packages → /uploads → 6× /files → /materials → /pair → GET /overview（无任何 /match）
→ 配对表 3 行：1↔1、2↔2、3↔3，主副缩略图都在；表头 = 主素材 | 副素材 | 配对状态 | 操作
→ 打开「修改配对」：列出本次上传的副图，标出「当前」与「已被位置 N 使用」
→ 改配到被占用的副图 → 弹「确认重新分配」→ 确认后位置 2 退回缺副图
→ 「确认全部配对」→ 已确认 3/3
→ 「确认整包并生成 V1」→ Toast「已生成 V1：3 个副素材（1-1 / 2-1 / 3-1）」
→ 再把主素材 + 新一版副图整包重传 → 仍是同一个设计包（新增上传记录，MAT 不重新发号）
→ 确认配对 →「确认整包并生成 V2」→ 1-2 / 2-2 / 3-2
→ 刷新页面：1-1…3-2 全在、配对状态仍是已确认、三个人员字段都还在
→ 页面不出现「匹配相似度 / 按相似度排序 / 高匹配 / 低匹配 / 匈牙利 / Top 候选」
→ 点「新建设计包（另起一套）」：切换出当前设计包，名称输入框恢复可编辑
```

> `verify_pairing.py` 里还带一条**源码扫描**用例：`backend/app/**/*.py` 中不允许出现
> `hungarian` / `linear_sum_assignment` / `phash_similarity` / `hamming_distance` /
> `matched_similarity` / `material_match_results` / `match_service`。

### 素材中心这一轮修掉的 4 个问题（用户实测反馈）

| 用户看到的现象 | 真实根因 | 修复 |
|---|---|---|
| 点素材打开抽屉，**副素材缩略图空白** | `overviewMapper.toMaterialVariantEntity()` 把副素材的 `revisions` 写死成 `[]`，而 `currentVariantRevision()` 只从 `variant.revisions` 找当前 Revision → 拿不到 Asset → `imageUri = ''` | 规范化副素材 Revision（`currentRevisionId` / `currentRevisionNo` / `assetId`）并写进 `state.variantRevisions`；Asset 缺失时按 DTO 的 `previewUrl` 兜底。另：整包视图的 `variants` 改为返回**全部版本**（原来只有当前版本，历史副素材在详情里凭空消失） |
| **演示数据串进我的真实素材** | store 无论是否连后端都先灌一份演示种子（演示包「万圣节夜景球迷款」占 MAT-000001~23），真实库的 MAT 从 16 开始 → **编号撞车**，演示的位置/副素材挂到了真实主素材上；素材中心也不从后端加载 | `VITE_MATERIAL_API=1` 时初始状态为空（不灌演示数据）；素材中心进页面 `loadAllPackagesFromApi()` 拉全库设计包并 hydrate；上传页「打开演示设计包」只在 Mock 模式出现 |
| 没关联却显示「**关联设计 1**」 | 那个数字是「这个 MAT 在几个设计包位置里出现过」（自己那次上传=1），不是设计关联；列表又来自 Mock 生成器（`MATERIALS=[]` → 永远空） | 「关联设计」改为**按设计编码去重**的真实关联（`getDesignsOfMaterialById`），抽屉顶部改为 关联设计 / 设计包 / 副素材 / 关联 ASIN（Phase 3 显示 `—`）；ASIN、订单、销量这些 Phase 3 指标不再显示编造数字（卡片与筛选入口同步隐藏） |
| 上传时**没有地方填设计编码** | `design_packages` 只有系统编码 `code`，没有业务编码字段 | 新增 `design_code`（迁移 `0006`）+ 上传页「设计编码」必填输入（失焦即 PATCH，可事后修正），素材详情「关联设计」直接显示它 |

> 设计编码语义：**一个设计编码 = 一个设计**，同一设计的新一版继续填同一个编码（所以多个设计包可以共用一个 `design_code`）；
> 素材详情里按编码聚合展示，点到某个设计编码可以直接跳到「副素材」看这个设计下的图。

用户 4 问题专项验收覆盖（`.tmp-verify/verify-issues.cjs`，用真实素材编码跑）：

```
store.demoMaterials == 0 且页面 /mock 图片 == 0        → 演示数据不再混进真实素材
MAT-000043 的 28-1 副素材缩略图 naturalWidth > 0       → 副素材缩略图正常显示
抽屉计数：关联设计 3 / 设计包 3 / 副素材 1 / 关联 ASIN —  → 计数是真实关联，不再有编造数字
「关联设计」里显示该设计包真实 design_code（DP-…/DS-…）  → 设计编码关联生效
```

### 删除设计包 + 草稿不进素材中心（再一轮反馈）

| 需求 | 实现 |
|---|---|
| 「按设计包折叠」时能删除设计包 | 每个设计包分组右侧一个「删除」按钮 → 确认弹窗写明**软删除（归档）**、会保留什么 → `DELETE /api/design-packages/{id}` → 分组立刻消失（前端同时把该包从 store 摘掉，MAT 与 Asset 保留）。弹窗确认：「显示已删除的设计包（N）」里可以「恢复」 |
| 没确认整包生成前，设计包不上传到前端 | 素材中心启动改调 `GET /design-packages?withBatch=true`（SQL 侧排除没有版本的草稿）；草稿只在上传页可见，继续完成配对与生成 V1 |

顺手修掉的真实 bug：`MaterialCenterPage` 里 `const packageGroups = useMemo(() => getDesignPackageGroups(), [])`
**依赖数组是空的** —— 首次渲染（后端还没 hydrate 完）算一次并永久缓存，
于是「按设计包折叠」永远看不到刚上传/刚生成版本的设计包。改为开折叠时直接计算（组件已订阅 store）。

浏览器验收覆盖（`.tmp-verify/verify-package-delete.cjs`）：

```
GET /design-packages?withBatch=true              → 素材中心只拉已生成版本的包
草稿包名不出现、已生成包名出现                      → 草稿隔离生效
分组里显示设计编码（DS-BKEEP-…）                   → 分组信息是真实的
点「删除」→ 弹窗说明软删除 → 确认                   → 发出 DELETE，分组从列表消失
「显示已删除的设计包」→「恢复」                     → 设计包回到列表
console error = 0
```

### 前端为「第二次上传 = 新版本」做的两件事

1. `analyze()` 把当前设计包 `designPackageId` 传给 `submitDesignPackageToApi`
   —— 同一个设计包再上传是**新增一条上传记录**（同一个 MAT 位置、下一个版本号），
   而不是新建一个设计包（否则版本号永远停在 V1）。
2. 「文件上传」卡片顶部常驻提示「当前设计包：X（已到 V1）—— 这次上传会作为它的新一版，
   确认配对后生成 V2」，并提供**「新建设计包（另起一套）」**按钮；
   同时允许**只补传副图**（已有设计包时不再强制选主素材）。

---

## 十四、本轮优化：副素材详情 / 标签体系 / 负责人 / 流转记录 / 上传前检查

### 1. 副素材详情跳转（抽屉支持 Material 与 MaterialVariant 两种实体）

- 主素材详情 →「副素材」Tab → 点击 `1-1 / 1-2` → 抽屉下钻到**副素材详情视图**（VariantDetailView）
- 副素材详情显示：副素材编号 / 图片 / 所属主素材 MAT（可点击返回）/ 所属设计包 / 版本批次（V1/V2…）/ Revision / 负责人 / 创建时间 / 标签 / 关联 ASIN（Phase 3 显示空态）/ **流转记录**
- 顶部「返回主素材 MAT-xxx」一键回到主素材详情
- 素材中心「副素材」筛选的卡片点击也直接进入副素材详情

### 2. 标签体系（创建时复制，之后独立）

```
DesignPackage.tags →（建包时复制）→ Material.tags →（建版时复制）→ MaterialVariant.tags
```

- **创建时复制，不是动态绑定**：改设计包标签不会自动覆盖包内素材；改主素材标签也不会覆盖副素材
- 设计包标签编辑提供「同步新增标签到包内素材」（syncTagsToMaterials=true）：只把**这次新增**的标签补到包内主素材，已有标签不动
- 单个素材改标签：素材抽屉「基本信息」里内联 TagPicker（可搜索已有标签 / 新增 / 删除）
- 批量改标签：素材中心多选（卡片左上角勾选）→ 底部批量操作条：添加 / 删除 / 替换标签
- 所有标签变更写 ActivityLog：ADD_TAG / REMOVE_TAG / REPLACE_TAG / BATCH_ADD_TAG / BATCH_REMOVE_TAG

### 3. 负责人（design_packages.responsible_id / responsible_name）

- 「上传人」展示字段改为「**负责人**」= 业务上负责这套设计的人；uploader_id / uploader_name 保留 = 实际上传人（点了上传的那个人）
- 负责人属于设计包；包下主素材 / 副素材**默认展示所属包负责人**（不复制字段）
- 上传页字段调整为：设计包名称* / 负责人* / 归属运营* / 标签 / 备注（实际上传人次要位置）
- 负责人前端可改（上传页输入框失焦即 PATCH），写 CHANGE_RESPONSIBLE 维护记录（修改前 / 修改后 / 操作人 / 时间）

### 4. 流转记录（全部来自现有 ActivityLog）

- 素材详情新增「**流转记录**」Tab；基本信息顶部显示「当前状态 + 最近流转」
- 主素材流转记录 = 自己的 ActivityLog（创建 MAT / 标签修改 / 负责人修改 / PSD Revision 等）+ 引用它的设计包的关键事件（上传 / 配对确认 / 生成版本 / 派发）
- 副素材流转记录 = 自己的 ActivityLog（CREATE_VARIANT / REUSE_VARIANT / 标签修改 等）
- 前端统一叫「流转记录」，后台仍叫 ActivityLog，不新建第二套历史表

### 5. 上传前检查 + 成功后下一步

- 点「上传并写入素材库」→ 先弹「**上传前检查**」面板（设计包 / 负责人 / 归属运营 / 标签 / 主素材 / PSD / 副素材数量 / 配对 / 异常）→ 点「确认上传并写入素材库」才真正写入
- 上传成功后弹「上传成功」下一步面板：[查看设计包] [进入素材中心] [生成 V1]

### 6. 主副素材关系维持不变

- 仍然只按同一次上传的同名 pairKey 配对（1.jpg / 1.psd / 1.jpg → pairKey = 1），
  支持人工修改配对；**没有恢复** Hungarian / 相似度 / HIGH/LOW/REVIEW / Top 候选。

### 验收结果

| 层级 | 结果 |
|---|---|
| 后端单测 | **96 passed**（新增标签继承/独立/批量/负责人流转/流转记录等 9 个用例） |
| 真实 HTTP（verify_tags.py） | **18 / 18** |
| 浏览器（verify-variant-detail.cjs / verify-history-tags.cjs / verify-upload-tags.cjs / verify-batch-tags.cjs / acceptance.cjs） | **问题清单：无** |
| npm build / 后端测试 / Console | 全绿 / 96 passed / 0 error |



---

## 十五、图片搜索（以图搜图，与主副素材配对完全独立）

### 1. 入口与三种查询方式

- 素材中心搜索框右侧「图片导航」按钮（Camera）打开上传面板
- **点击上传 / 拖拽图片 / Ctrl+V 粘贴剪贴板图片** 三种方式，最终都走同一套搜索接口 `POST /api/image-search/search`
- 查询图是**临时文件**：只用于本次搜索，不创建 Material / PackageUploadAsset / DesignPackageMaterial，搜索完立即删除

### 2. 搜索范围与结果

- 搜索主素材 + 副素材（索引的是「被业务引用的图」：主素材 previewAsset + 副素材当前 Revision）
- 结果 Grid 显示：图片 / MAT 或副素材编号 / **相似度（%）** / 主/副素材类型 / 设计包 / 标签 / 负责人
- 默认按相似度降序；支持 **Top 20 / 50 / 100**
- 筛选：全部 / 主素材 / 副素材 / 设计包 / 标签 / 负责人（命中任一标签即可）
- 点击结果直接打开对应素材详情（主素材 → 主素材抽屉；副素材 → 副素材详情）
- 素材卡片 Hover「找相似」+ 素材详情「找相似」：直接用当前 Asset 已有向量搜索，不用重新上传图

### 3. 搜索算法（ImageSearchService）

```
查询图 / Asset → Embedding（图片向量）→ 向量索引 Top-K → MySQL 补充标签/负责人/设计包
```

- 独立服务包：`backend/app/services/image_search/`
  - `embedder.py`：Embedding 层（接口化，可换 CLIP/DINO）
  - `index.py`：向量检索层（接口化：Milvus / numpy 双实现）
  - `service.py`：编排（反查业务信息 / 融合打分 / 重建索引）
- **使用的 embedding 模型**：`resnet50-imagenet-v1`（**ResNet-50，ImageNet 预训练**，去掉 fc 输出 **2048 维**特征向量）—— 对齐 Milvus 官方 bootcamp `image_search_with_milvus` 的做法
  - 权重本地文件 `backend/models/resnet50-0676ba61.pth`（97.7MB，首次需联网下载一次）
- **向量索引方案**：**Milvus Lite**（嵌入式向量库，COSINE 距离）—— 对齐官方 quickstart
  - `MilvusVectorIndex`：collection `image_search`（dim=2048, id=string）；`embedder/index` 都是接口化，torch/milvus 不可用时自动回退 `lightweight-hsv-v1` + `NumpyCosineIndex`
  - Milvus 数据文件默认 `%LOCALAPPDATA%\mc-milvus\mc.db`（**必须英文路径**：faiss 索引无法写入中文目录）
- **相似度融合**（查询图/找相似都用）：
  - BLAKE3 内容完全相同 → **100%**
  - 否则 视觉向量（ResNet-50 余弦）60% + pHash 感知相似度 40%
  - 找相似时查询素材的人工标签命中 → 每个 +2%（封顶 +20%），返回 `matchedTags`
- **环境依赖**：`pip install pymilvus milvus-lite torch torchvision`（torch 2.14 支持 Python 3.14）
- 主副素材配对**完全不读取搜索结果**（pairing/batch 不 import image_search，有测试守护）

### 4. Asset 向量表（迁移 0008）

`asset_image_embeddings`：

| 字段 | 说明 |
|---|---|
| id / asset_id（UNIQUE） | 每个可搜索 Asset 一条向量 |
| embedding_model / embedding_version | 模型标识，换模型时版本递增，新旧向量不混算 |
| dim / vector | 向量维度；float32 字节（BLOB，DB 副本；检索以 Milvus 为准） |
| indexed_at | 索引时间 |

- 向量**不代表业务身份**；同一 Asset 被多个业务对象引用时只算一次
- **新素材自动入索引**：`POST /api/uploads/{id}/files` 入库图片 Asset 后自动生成向量写入 Milvus
- **索引失败不阻断上传**：只记录 `IMAGE_INDEX_FAILED`（ActivityLog，target_type=ASSET），上传事务不受影响
- **重建索引**：`POST /api/image-search/reindex`（指定 assetId 或全库；逐个用嵌套事务，失败只回滚单个并记录）

### 5. 订单导入 Phase A-D（迁移 0009）

订单导入只新增订单事实和原始文件留存，不复制 `Material` / `Variant` / `Batch`。

- `POST /api/order-imports`：multipart 上传领星 ZIP，可选 `categoryCode` 和 `actor`；保存原始 ZIP/JSON 为 `Asset`，按 `orderItemId`（缺失时可靠复合键）去重。
- `GET /api/order-imports`、`GET /api/order-imports/{batchId}`：导入批次及状态（`PARSED/PARTIAL/FAILED/DUPLICATE`）。
- `GET /api/order-imports/{batchId}/items`：规范化订单行，包含 `categoryCode`、`parserVersion`、图片类型、定制字段、买家 Logo 附件引用和审核状态。
- 解析链路固定为 `GenericOrderParser → CategoryResolver → ParserRegistry → CategoryParser`；通用解析器不包含品类图片规则，未知品类保留为 `REVIEW_REQUIRED`。
- 当前品类实现为 `BLADE_SHOES_V1`，只识别 `MATERIAL_SOURCE`、`FINAL_EFFECT`、`PREVIEW_ONLY`、`UNKNOWN`；买家 Logo 保存在 `order_buyer_assets`，不创建素材实体。

### 6. 搜索 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/image-search/search` | multipart：`file`（查询图，临时）或 `assetId`（找相似）；`scope=all/main/variant`、`designPackageId`、`tags`（逗号分隔）、`responsibleName`、`topK=20/50/100` |
| POST | `/api/image-search/reindex` | `assetId` 可选；重建单个或全库搜索向量 |

### 7. 测试与验收

| 层级 | 结果 |
|---|---|
| 后端单测 | **108 passed**（test_image_search.py 10 个：向量归一化 / 自动入索引 / 同色高相似 / scope 筛选 / 临时查询图不建素材 / 标签负责人筛选 / reindex / 索引失败不阻断 + IMAGE_INDEX_FAILED / 配对不读搜索结果；test_milvus_index.py 2 个：Milvus 读写/搜索/删除协议） |
| 浏览器验收（verify-image-search.cjs，生产走 Milvus + ResNet-50） | **问题清单：无**（上传/拖拽/粘贴可搜、主副素材结果、类型筛选、点结果进详情、详情找相似、0 console error） |
| 既有回归（acceptance / variant-detail / upload-tags / history-tags / issues） | 全部 **无** |
| npm build / 后端测试 | 全绿 / 108 passed |

### 8. 当前搜索准确率问题

- ResNet-50（ImageNet 预训练）是**通用图像语义特征**：对内容/物体/风格相似的素材效果好（实测不同图案主素材 0.97、主副图 0.31）
- **局限**：
  - 领域差异：ImageNet 预训练对「服装/球衣/图案」这类素材不是专门的；同系列设计（同一套图案的不同配色）会得到偏高相似度
  - 纯色/低纹理图之间区分度仍有限（ResNet 对纹理敏感，对纯色欠敏感）
  - 相似度绝对值普遍在 60~70%（余弦），适合**排序**而非硬阈值
- **升级路径**：换领域模型（CLIP / 专门微调的检索模型）只需新增一个 Embedder 实现 + 提升 `embedding_version` 重建索引，检索层（Milvus）、API、前端都不动


---

## 十六、订单识别与素材归因

### 1. 概述

基于现有 Material / Variant / Batch / ASIN 体系新增「订单识别与素材归因」模块：
上传领星订单 ZIP → 解析（GenericOrderParser → CategoryResolver → ParserRegistry → BladeShoesParser）→ NormalizedOrder 落库 → 素材匹配（URL 绑定 / ASIN 候选图片匹配）→ 人工审核 → 销量归因。

### 2. 数据模型（迁移 0009 / 0010 / 0011）

| 表 | 说明 |
|---|---|
| `order_import_batches` | 一次 ZIP 导入；保留原始 ZIP Asset / ZIP blake3 / 品类 / 状态（PARSED/PARTIAL/FAILED/DUPLICATE）/ JSON 数与订单数 |
| `order_items` | 每个订单行；orderId/orderItemId/asin/sku/quantity/品类/parser_version/raw_json_hash/normalized_payload/匹配状态与分数；`dedupe_key`（orderItemId）去重，重复导入不重复计销量 |
| `order_import_batch_items` | 导入批次 × 订单 快照（原始 JSON Asset、parse_status、normalized_payload） |
| `order_buyer_assets` | 买家 Logo（原始 + SVG）——订单生产附件，**不属于素材库** |
| `material_url_bindings` | 买家素材 URL → Variant（人工确认后建立，URL 命中直接复用，不跑图匹配） |
| `asin_variant_bindings` | Child ASIN → Variant（替代不存在的 Distribution 表定位候选） |
| `variant_effect_images` | Variant 的最终效果图（FINAL_EFFECT / Black / White），复用已有 Asset |
| `activity_logs` | target_type 增加 `ORDER_ITEM`（审核记录） |

### 3. 解析架构与规则（BLADE_SHOES_V1）

```
GenericOrderParser → CategoryResolver → ParserRegistry(BLADE_SHOES→BladeShoesParser) → NormalizedOrder
```

- **GenericOrderParser**：只解析 Amazon 通用结构（orderId/orderItemId/asin/sku/quantity + 图片候选 + 买家附件 + 文字/选项），不放品类业务判断；兼容 `type` 字段（领星真实结构）与 key 名；支持 customizationData（`inputValue`）与 version3.0（`text`）两种文字来源
- **CategoryResolver**：品类从 SKU 前缀 / ASIN 绑定 / ERP / 设计包分类确定，**不靠图片猜品类**
- **BladeShoesParser V1**（规则版本 BLADE_SHOES_V1）：
  - `image_type`：同一 OptionCustomization 节点 `thumbnailImage != overlayImage` → `MATERIAL_SOURCE`（material_url）；`==` 且 Black/White → `FINAL_EFFECT`（final_effect_url + sole_color）；Add Your Logo / Front Blank 入口预览 → `PREVIEW_ONLY`；未知结构 → `UNKNOWN`（REVIEW_REQUIRED），禁止随便取第一张图
  - 买家 Logo：`ImageCustomization.image.imageName` → buyer_logo_original；version3.0 `ImagePrinting.svgImage` → buyer_logo_svg；不依赖卖家 label（Add Your Logo 等）判断
  - 文字：Enter Your Name/Number → custom_name/custom_number；明确 Front/BACK → front/back 字段；普通字段不复制到 front/back；buyer_request 统一

### 4. 素材匹配（MaterialMatcher）

- **MATERIAL_SOURCE**：`material_url` → MaterialUrlBinding（已绑定 → URL → Variant → MAT，不跑图匹配）；未绑定 → Child ASIN → 候选 Variant（仅该 ASIN 绑定内）→ ResNet-50 图片匹配 → 命中建 URL 绑定
- **FINAL_EFFECT**：`final_effect_url` → Child ASIN → 候选 Variant → 该 Variant 的 FINAL_EFFECT 图 → 图片匹配（不与其他素材源图混比）
- **品类隔离**：`Order.category_code = Candidate.category_code`，不符自动降级 REVIEW_REQUIRED
- 匹配失败 → REVIEW_REQUIRED / UNMATCHED，交给人工审核

> **冲突说明**：现有系统后端没有 Distribution（派发）表/API（前端 DistributionDetailPage 为 Mock），且没有 ASIN↔Variant 映射。因此「Child ASIN → Distribution/Batch → Variant」链路用 **asin_variant_bindings**（审核确认时建立）替代定位候选，实现同等的「候选隔离、不全库比较」效果。

### 5. 人工审核与销量归因

- 审核状态：CONFIRMED / REVIEW_REQUIRED / UNMATCHED / FAILED；异常审核页支持 确认 / 更换 Variant / 无法识别，全部写 ActivityLog（MATCH_CONFIRMED / MATCH_CHANGED / MATCH_FAILED）
- 销量：最小单位 OrderItem.quantity；**仅 CONFIRMED 计入**；Variant 销量 = SUM(quantity) WHERE matched_variant_id AND CONFIRMED；MAT 销量 = 该 MAT 下 Variant 汇总；按品类汇总；REVIEW_REQUIRED/UNMATCHED/FAILED 不计入

### 6. API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/order-imports` | 导入领星 ZIP（保留原始 ZIP/JSON，orderItemId 去重） |
| GET | `/api/order-imports` | 导入批次列表 |
| GET | `/api/order-imports/{id}` | 批次详情 |
| GET | `/api/order-imports/{id}/items` | 批次解析结果（含 normalized payload） |
| POST | `/api/order-imports/items/{id}/auto-match` | 单个订单自动匹配 |
| POST | `/api/order-imports/{batch}/auto-match` | 整批自动匹配 |
| POST | `/api/order-imports/items/{id}/match` | 人工审核（confirm/change/unmatch + variantId） |
| GET | `/api/order-match/review` | 异常审核列表（REVIEW_REQUIRED/UNMATCHED/FAILED） |
| GET | `/api/order-sales` | 正式销量（Variant/MAT/品类汇总，仅 CONFIRMED） |

### 7. 前端

`/orders` 订单识别页（素材中心右上「订单识别」入口）：上传订单 / 导入记录 / 解析结果 / 素材匹配 / 异常审核 / 销量归因 六个视图。

### 8. 测试与验收

| 层级 | 结果 |
|---|---|
| 后端单测 | 全量通过（新增：test_order_real_structure 8 个真实领星结构解析；test_order_match 4 个 URL绑定/候选隔离/品类隔离/销量/审核；既有 test_order_phase_a_d 7 个） |
| 浏览器验收（verify-orders.cjs，真实 ZIP 全流程） | **问题清单：无**（上传/导入记录/解析结果表/素材匹配/异常审核/销量页，0 console error） |
| 既有回归（acceptance / image-search / variant-detail） | 全部 **无** |
| 真实数据 | 61 单：FINAL_EFFECT 58 / MATERIAL_SOURCE 3，全部 PARSED，55 单有定制文字，sole Black 43/White 15 |

### 9. 未完成项与下一步

- 真实 ZIP 样本里**没有买家 Logo（ImageCustomization）订单**：Logo 解析逻辑已实现并通过构造 fixture 测试，但需要真实带 Logo 的订单样本进一步验证
- `asin_variant_bindings` 目前靠人工审核建立；后续可接入「Distribution/派发」表实现 ASIN→Batch→Variant 的原生链路
- FINAL_EFFECT 匹配需要 Variant 的效果图库（variant_effect_images），当前为空——后续上传/学习效果图后自动匹配才能真正命中
- 订单导入的外层 Excel（订单基础信息）暂未解析（JSON 已含订单号/ASIN 等核心字段）
