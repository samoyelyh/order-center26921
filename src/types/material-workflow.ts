// ============================================================================
// 素材中心 V2 —— 领域数据模型（与后端数据库表一一对应）
//
// 设计原则：
//   1. 本文件里的接口 = 数据库实体，后端可直接按这些字段建表。
//   2. 从实体派生出来的展示用结构统一放在文件末尾「DTO / 视图模型」，前缀无 Entity 的
//      那些 interface（PackageOverview / MaterialView / PairingView 等）只是前端 DTO，
//      后端不需要照搬建表。
//   3. 版本号只有唯一事实来源：MaterialVariant.batchId → DerivativeBatch.versionNo。
//      副素材不存 batchVersionNo，DTO 里可以带（名为 batchVersionNo 的 DTO 字段）。
//   4. 指纹字段与算法解耦：exactHash/exactAlgorithm + perceptualHash/perceptualAlgorithm。
//      当前浏览器实现是 SHA256 + AHASH，后端换成 BLAKE3 + PHASH 时只改算法字段与值。
// ============================================================================

// ---------------------------------------------------------------- 算法枚举

import type { PackageUploadedFilesDto, MaterialVariantDto } from '@/services/apiClient'

/** 完全相同判定算法（当前 Demo：SHA256；后端接入后：BLAKE3） */
export type ExactHashAlgorithm = 'BLAKE3' | 'SHA256'
/** 感知哈希算法（当前 Demo：AHASH；后端接入后：PHASH）。仅落库留痕，不参与配对 */
export type PerceptualHashAlgorithm = 'AHASH' | 'PHASH'

/** 只用于展示/筛选，不参与任何唯一键 */
export type MarketplaceSiteCode =
  | 'US' | 'UK' | 'DE' | 'JP' | 'CA' | 'FR' | 'IT' | 'ES' | 'MX' | 'AU'

// 配对状态见下方 PairingStatus（同名 pairKey 配对，无相似度分级）
export type MaterialType = 'MAIN' | 'VARIANT'

/**
 * 上传文件角色（后端 package_upload_assets.file_role）：
 * 只表示「用户上传的是主图 / PSD / 副图 / 其他」，
 * **不是**主副素材匹配结果（匹配属 Phase 2，由 pHash + 人工确认决定）。
 */
export type UploadFileRole = 'MAIN_PREVIEW' | 'PSD' | 'VARIANT' | 'OTHER'

/** 一个已上传文件（DTO：后端 package_upload_assets 行 + Asset 展开） */
export interface UploadedFileView {
  assetId: string
  fileRole: UploadFileRole
  originalFilename: string
  /** 可直接用于 <img src> 的地址（后端代理） */
  previewUrl: string
  mimeType: string
  sizeBytes: number
  width?: number | null
  height?: number | null
  positionHint?: number | null
  packageUploadId?: string | null
}

/** 上传结果分组（整包视图 uploadedFiles） */
export interface PackageUploadedFilesView {
  main: UploadedFileView[]
  psd: UploadedFileView[]
  variants: UploadedFileView[]
  other: UploadedFileView[]
  mainCount: number
  psdCount: number
  variantCount: number
  otherCount: number
}

/** 兼容：store 内统一使用 apiClient 的 DTO 形状 */
export type { PackageUploadedFilesDto }

// ---------------------------------------------------------------- 1. Asset（独立实体）

/**
 * Asset = 一个真实存在的文件（对象存储对象）。
 *
 * 正式领域模型与数据库**不把文件信息内联成 JSONB**：
 *   Material.previewAssetId → Asset
 *   Material.psdAssetId     → Asset
 *   PsdRevision.assetId     → Asset
 *   VariantRevision.assetId → Asset
 *
 * 生产指纹统一由后端计算 BLAKE3 + pHash 并写在 Asset 上：
 *   blake3        → 完全一致文件判定
 *   phash         → 感知哈希，仅落库留痕（不参与主副素材配对）
 *   phashVersion  → 指纹算法版本，算法升级后可分辨历史值
 * 生产库不混存 SHA256 / aHash；浏览器 Demo 的 SHA256 + aHash 只存在于前端内存，
 * 不进入正式库设计。未来若要并存多算法（LightGlue / Color / DINO 等），
 * 再新增 asset_fingerprints 表，本轮不提前增加。
 */
export interface Asset {
  id: string
  /** 对象存储 key，例如 designs/2026/MAT-000001/psd/r1.psd */
  storageKey: string
  /** 上传时的原始文件名 */
  originalFilename: string

  mimeType: string
  sizeBytes: number

  /** 图片/PSD 尺寸，非图片资源为空 */
  width?: number
  height?: number

  /** 生产指纹：完全相同判定 */
  blake3?: string
  /** 生产指纹：感知哈希（仅落库，不参与配对） */
  phash?: string
  phashVersion?: number

  createdBy: string
  createdAt: string

  /** 软删除：旧文件永不物理覆盖删除 */
  deletedAt?: string
}

/**
 * AssetRef：前端 DTO，UI 直接消费的轻量资源引用。
 * 由后端把 assetId → Asset 展开后返回，前端不需要自己 join Asset 表。
 */
export interface AssetRef {
  assetId: string
  /** 与 Asset.storageKey 对应的可访问 URL */
  uri: string
  fileName: string
  mimeType?: string
  sizeBytes?: number
  createdAt: string
}

/**
 * PSD 源文件修订历史。主素材同样需要 Revision 能力。
 * 旧 Revision 永不覆盖删除，只能软删除。
 */
export interface PsdRevision {
  id: string
  materialId: string
  revisionNo: number

  /** 文件统一通过 Asset 管理 */
  assetId: string

  createdAt: string
  createdBy: string
  /** 软删除标记 */
  deleted: boolean
  note?: string
}

/**
 * 主素材：永久身份 MAT-xxxxxx，可被多个设计包复用。
 * 这是独立实体，**不从设计包位置派生**。
 */
export interface Material {
  id: string
  /** 永久业务编号 MAT-000001 */
  materialCode: string
  name: string
  type: MaterialType

  /** → Asset.id（预览图） */
  previewAssetId: string
  /** 当前生效 PSD Revision */
  currentPsdRevisionId?: string

  tags: string[]

  createdAt: string
  updatedAt: string

  /** 首个上传来源（用于回溯，不做业务判断） */
  sourcePackageUploadId?: string
  createdBy: string
}

// ---------------------------------------------------------------- 2. 设计包 DesignPackage

/**
 * 设计包是长期逻辑实体。
 * 注意：这里**不再持有** originalPackageName / uploadSessionId / uploadTime，
 * 那些属于 PackageUpload（每次实际的文件包上传）。
 */
export interface DesignPackage {
  id: string
  /** 设计包编码 DP-20260916-HW001（系统生成） */
  code: string
  /** 设计编码：用户/美工自己的设计编号，上传时手填；素材的「关联设计」按它聚合 */
  designCode: string
  name: string
  /** 设计包标签（创建时继承给包内素材，之后独立修改） */
  tags: string[]
  /** 负责人：业务上负责这套设计的人（与实际上传人 uploader 分开） */
  responsibleName: string
  responsibleId?: string
  remark?: string

  /** 设计美工：设计包的长期业务属性（不是「谁点了上传」） */
  designerId?: string
  designerName?: string

  createdBy: string
  createdAt: string
  updatedAt: string

  /** 归档时间（软删除；归档不删除任何素材与历史） */
  archivedAt?: string
}

// ---------------------------------------------------------------- 3. 每次实际上传 PackageUpload

/**
 * 每次实际的文件包上传行为。
 * 一个设计包可以有多次上传：创建 V1、创建 V2、补充 V1、修正文件。
 */
export type PackageUploadType =
  /** 首次上传，创建 V1 */
  | 'INITIAL'
  /** 新增上架批次，创建 V2/V3 */
  | 'NEW_BATCH'
  /** 补充已有批次缺失素材（例如补 V1），不创建新版本 */
  | 'SUPPLEMENT'
  /** 修正已有文件的图片（走 Revision），不创建新版本 */
  | 'REVISION'

export type PackageUploadStatus = 'PARSING' | 'PARSED' | 'MATCHED' | 'CONFIRMED' | 'SUBMITTED' | 'INTERRUPTED' | 'FAILED'

export interface PackageUpload {
  id: string
  designPackageId: string

  /** 原始文件包名称，例如 万圣节夜景球迷款-20260916.zip */
  originalPackageName: string
  fileSize?: number

  /** 上传会话 ID（幂等 / 断点恢复锚点） */
  uploadSessionId: string

  uploaderId: string
  uploaderName: string

  uploadType: PackageUploadType

  /**
   * 本次上传的目标批次：
   *  - 新建批次：创建后回填该 batchId
   *  - 补充批次：指向被补充的 batchId（例如补 V1）
   * PackageUpload 本身不拥有版本号。
   */
  targetBatchId?: string

  status: PackageUploadStatus
  remark?: string

  createdAt: string
  updatedAt: string
}

// ---------------------------------------------------------------- 4. 上架版本 DerivativeBatch

/**
 * 上架版本 = 设计包的上架批次。
 * 约束：UNIQUE(designPackageId, versionNo)
 * 版本号属于整套设计包，禁止 per-material MAX(version)+1。
 */
export interface DerivativeBatch {
  id: string
  designPackageId: string

  /** 1 = V1, 2 = V2 ... */
  versionNo: number
  code: string

  /** 由哪次上传创建 */
  createdFromUploadId: string

  createdAt: string
  /** 创建时设计包内主素材位置数（用于覆盖度分析） */
  mainMaterialCountAtCreation: number

  /** DTO：该版本下的副素材（后端 batches 接口返回） */
  variants?: MaterialVariantDto[]
  /** 本次建版新建 / 复用（内容完全相同，不新建实体）的副素材数 */
  createdVariantCount?: number
  reusedVariantCount?: number
  reusedNotes?: string[]
}

// ---------------------------------------------------------------- 5. 指纹

/**
 * 指纹信息（可内联在实体上，也可拆独立表）。
 * 字段与算法解耦：不要把这些结果伪装成 BLAKE3 / pHash。
 */
export interface Fingerprint {
  /** 完全相同判定指纹（同一算法域内可比） */
  exactHash: string
  exactAlgorithm: ExactHashAlgorithm

  /** 感知哈希指纹（仅落库，不参与配对） */
  perceptualHash: string
  perceptualAlgorithm: PerceptualHashAlgorithm
  /** 指纹算法版本，算法升级后可并存比对 */
  perceptualVersion: number
}

// ---------------------------------------------------------------- 6. 设计包位置 DesignPackageMaterial

/**
 * 设计包位置 → 永久 MAT 的关联。
 * 只表示「某个设计包中的某个位置指向某个 MAT」，不复制 Material 主数据。
 */
export interface DesignPackageMaterial {
  id: string
  designPackageId: string
  /** 设计包内部位置编号 1/2/3...（不是永久身份） */
  position: number

  /** → Material.id */
  materialId: string
  /** 冗余展示用 MAT 编号 */
  materialCode: string

  /** 该位置在设计包内的命名（可能与 MAT 名称不同） */
  displayName?: string
  /** 位置级预览图（通常等于 Material.previewAsset.uri） */
  previewUri: string
  sourceFileName: string

  /** 该位置在本次上传中计算出的指纹（用于整包匹配） */
  fingerprint: Fingerprint

  /** 首次出现于本设计包的上传 ID */
  createdFromUploadId: string
  createdAt: string
}

// ---------------------------------------------------------------- 7. 副素材 MaterialVariant

/**
 * 副素材 Revision（一张 JPG 的修改历史）。旧 Revision 永不覆盖删除。
 * 文件统一通过 assetId → Asset 管理。
 */
export interface VariantRevision {
  id: string
  variantId: string
  revisionNo: number

  /** → Asset.id */
  assetId: string

  createdAt: string
  createdBy: string
  /** 软删除 */
  deleted: boolean
  note?: string
}

/**
 * 副素材：一张 JPG。
 * 显示编号 displayCode 形如 `1-2` = 主素材位置 1 + 上架版本 V2。
 * displayCode 只是业务展示编号，不是业务身份，不建全局 UNIQUE。
 * 版本事实只来自 batchId → DerivativeBatch.versionNo（不存 batchVersionNo）。
 * 设计包归属由 designPackageMaterialId / batchId 两跳推导，不冗余 designPackageId。
 */
export interface MaterialVariant {
  id: string

  /** → Material.id（副素材所属主素材） */
  materialId: string
  /** → DesignPackageMaterial.id（所属设计包位置，设计包归属由此推导） */
  designPackageMaterialId: string

  /** → DerivativeBatch.id（唯一版本事实来源） */
  batchId: string

  /** 展示编号 1-2（展示用，不参与唯一键） */
  displayCode: string

  /** 当前生效 Revision */
  currentRevisionId: string
  revisions: VariantRevision[]

  /** 完全相同文件命中的已有副素材（可直接复用，不新建） */
  duplicateOfVariantId?: string
  /** 该副素材被哪个更晚的版本复用（内容与上一版完全相同时，不新建实体） */
  reusedByBatchCode?: string

  /** 副素材标签（建版时从主素材复制，之后独立修改） */
  tags: string[]

  /** 软删除 */
  deleted: boolean

  createdAt: string
  updatedAt: string
}

/** 副素材视图（带 batchVersionNo 等 DTO 展示字段） */
export interface VariantView extends MaterialVariant {
  /** DTO：来自 DerivativeBatch.versionNo，不可修改 */
  batchVersionNo: number | null
  versionCode: string
  packageName: string
  materialCode: string
  /** DTO：当前 Revision 展开 */
  currentRevision: number
  imageUri: string
  fileName: string
}

// ---------------------------------------------------------------- 8. 主副素材配对（同名 pairKey）

/**
 * 配对状态。**没有**高/中/低匹配分级 —— 关系只由同名 pairKey 决定。
 *   PAIRED    已配对（按同名），等待人工确认
 *   UNPAIRED  缺副图 / pairKey 解析不出来
 *   CONFIRMED 人工已确认
 */
export type PairingStatus = 'PAIRED' | 'UNPAIRED' | 'CONFIRMED'
/** 配对来源：NAME=同名自动配对；MANUAL=人工改过 */
export type PairingSource = 'NAME' | 'MANUAL'

/** 「修改配对」弹窗里可选的一张副图（本次上传，无打分） */
export interface PairingOption {
  assetId: string
  originalFilename: string
  pairKey: string
  previewUri: string
  sizeBytes: number
  /** 已配对给哪个主素材位置（未配对为 null） */
  occupiedByPosition?: number | null
}

/** 一个设计包位置在某一次上传里的配对结果（页面表格的视图模型） */
export interface PairingView {
  id: string
  designPackageId: string
  packageUploadId: string
  designPackageMaterialId: string
  position: number
  /** 同名配对键（来自主图文件名；仅本设计包内有效，不是永久身份） */
  pairKey: string

  materialId: string
  materialCode: string
  materialName: string

  mainPreviewUri: string
  mainSourceFileName: string

  variantAssetId?: string
  variantPreviewUri?: string
  variantFileName?: string
  variantPairKey?: string

  psdAssetId?: string
  psdFileName?: string

  source: PairingSource
  status: PairingStatus
  variantId?: string
  confirmedBy?: string
  confirmedAt?: string

  /** 位置信息（表格左侧缩略图与位置号） */
  main: DesignPackageMaterial
  /** 本次上传的全部副图（供「修改」弹窗直接选择） */
  options: PairingOption[]
}

// ---------------------------------------------------------------- 9. 派发

/**
 * 派发任务状态。
 * 同一个 (batchId, operatorId) 不允许存在多个进行中的任务（ACTIVE / RECEIVED）。
 */
export type DistributionStatus = 'ACTIVE' | 'RECEIVED' | 'COMPLETED' | 'CANCELLED'

/**
 * 派发明细 = 一次交付快照。
 * 必须同时记录「具体副素材 + 当时具体 Revision」：
 *   1-1 从 Revision1 改成 Revision2 时，不能让历史运营实际收到的图片发生变化。
 * 补充素材给已派发任务时，追加 deliveryRound+1 的 Item，不修改已有 Item。
 */
export interface DistributionTaskItem {
  id: string
  distributionTaskId: string

  /** → MaterialVariant.id */
  variantId: string
  /** → VariantRevision.id（派发那一刻的 Revision 快照） */
  revisionId: string

  /** 交付轮次：1 = 首次派发，2 = 补充派发，以此类推 */
  deliveryRound: number

  createdAt: string
}

export interface DistributionTask {
  id: string
  designPackageId: string
  batchId: string

  // ---- SNAPSHOT FIELD：派发当时看到的名称，仅供历史文案展示 ----
  // 不是 join / 搜索 / 数据一致性的事实来源；需要真实名称请查 DesignPackage / DerivativeBatch。
  packageName: string
  packageCode: string
  versionCode: string
  designerName: string
  // ------------------------------------------------------------

  operatorId: string
  operatorName: string

  status: DistributionStatus

  /**
   * 交付明细（正式模型）。
   * 不再使用 variantIds JSONB；前端 DTO 可展开出 variantIds 供 UI 使用。
   */
  items: DistributionTaskItem[]

  /** Parent ASIN：本身即唯一业务标识 */
  parentAsin?: AmazonParent
  /** Child ASIN 列表（正式库为独立表 distribution_task_children） */
  children: AmazonChild[]

  assignedAt: string
  receivedAt?: string
  completedAt?: string
  cancelledAt?: string
  remark?: string
}

/** Parent ASIN：本身即唯一业务标识，不叠加站点/店铺 */
export interface AmazonParent {
  asin: string
  /** 站点仅用于生成跳转链接，不参与唯一键 */
  site?: MarketplaceSiteCode
  /** 直接保存的 Listing 链接（可选，后端也可由 site 推导） */
  listingUrl?: string
}

/** Child ASIN：本身即唯一业务标识（正式库为独立表） */
export interface AmazonChild {
  asin: string
  site?: MarketplaceSiteCode
  listingUrl?: string
  /** 预留：Child 单独素材覆盖（本轮不做 UI） */
  overrideVariantIds?: string[]
}

/**
 * 派发任务 DTO（前端消费）。
 * variantIds 只是从 items 展开的便捷字段，不是后端存储字段。
 */
export interface DistributionTaskView extends DistributionTask {
  /** DTO：items 去重后的 variantId 列表 */
  variantIds: string[]
  /** DTO：当前交付轮次 */
  deliveryRound: number
}

// ---------------------------------------------------------------- 10. 维护记录 ActivityLog

export type ActivityTargetType =
  | 'DESIGN_PACKAGE'
  | 'MATERIAL'
  | 'MATERIAL_VARIANT'
  | 'DERIVATIVE_BATCH'
  | 'DISTRIBUTION'
  | 'PARENT_ASIN'
  | 'CHILD_ASIN'
  | 'UPLOAD'

export type ActivityAction =
  | 'UPLOAD_PACKAGE'
  | 'RUN_PAIRING'
  | 'CONFIRM_PAIRING'
  | 'UPDATE_PAIRING'
  | 'REASSIGN_OUT'
  | 'CREATE_BATCH'
  | 'CREATE_VARIANT'
  | 'REUSE_VARIANT'
  | 'VARIANT_REVISION'
  | 'MATERIAL_REVISION'
  | 'DISPATCH'
  | 'RECEIVE'
  | 'COMPLETE_DISTRIBUTION'
  | 'CANCEL_DISTRIBUTION'
  | 'PARENT_ASIN'
  | 'CHILD_ASIN'
  | 'UPDATE_ASIN'
  | 'SUPPLEMENT_V1'
  | 'SUPPLEMENT_DISPATCH'
  | 'RESOLVE_ANOMALY'
  | 'REOPEN_ANOMALY'

/**
 * 全系统统一一套「维护记录」，不拆多个日志系统。
 * 真正归属依据是 targetType + targetId；
 * designPackageId 只在动作发生在具体设计包上下文时填写（无包上下文时为空）。
 */
export interface ActivityLog {
  id: string
  /** 可选：动作发生的设计包上下文；跨包动作（如修改共用 MAT 的 PSD）为 null */
  designPackageId?: string | null
  /** 业务目标类型（真正的归属依据） */
  targetType: ActivityTargetType
  /** 业务目标 ID（MAT id / variant id / batch id / task id / ASIN 等） */
  targetId: string
  actor: string
  action: ActivityAction
  /** 展示文案 */
  summary: string
  /** 变更前后，例如 Parent ASIN 的 A → B */
  before?: string
  after?: string
  createdAt: string
}

// ---------------------------------------------------------------- 11. 异常 DataAnomaly

export type AnomalyType =
  | 'MATERIAL_COUNT_MISMATCH'   // 主副素材数量不一致
  | 'MISSING_VARIANT'           // 缺同名副图（含 pairKey 解析不出来）
  | 'EXTRA_VARIANT'             // 多余副图（没有同名主素材）
  | 'DUPLICATE_PAIR_KEY'        // 两个位置解析到同一个 pairKey
  | 'PAIRING_NOT_CONFIRMED'     // 还有位置没确认配对
  | 'DUPLICATE_FILE'            // 完全重复文件（BLAKE3 相同）
  | 'FILENAME_WARNING'          // 文件命名异常
  | 'UPLOAD_INTERRUPTED'        // 上传中断
  | 'BATCH_INCOMPLETE'          // 版本缺素材
  | 'BATCH_VERSION_CONFLICT'    // 版本号不统一
  | 'FILE_MISSING'              // 文件缺失
  | 'DISTRIBUTION_MISSING'      // 未派发运营
  | 'DUPLICATE_DISTRIBUTION'    // 重复派发
  | 'ASIN_CONFLICT'             // ASIN 冲突
  | 'ORDER_URL_MATCH_FAILED'    // 订单图片 URL 识别失败

export type AnomalyStatus = 'OPEN' | 'RESOLVED' | 'REOPENED'

/**
 * 异常使用稳定 anomalyKey 做 upsert，重复 recompute 不会产生新记录。
 *
 * 正式粒度（第八条）：
 *   Batch 级一条：MATERIAL_COUNT_MISMATCH / BATCH_INCOMPLETE
 *     key = TYPE:<packageId>:<batchId>，具体缺失项放 details
 *   Position 级一条：配对异常（由当前上传的确定性文件关系产生）
 *     key = TYPE:<packageId>:<position>
 *   Asset 级一条：FILE_MISSING
 *     key = TYPE:<assetId>，必须绑定 assetId
 */
export interface DataAnomaly {
  id: string
  /** 稳定业务键，唯一索引建议 UNIQUE(anomaly_key) */
  anomalyKey?: string

  designPackageId: string
  /** 相关批次（Batch 级异常必填） */
  batchId?: string
  /** 相关主素材位置（Position 级异常必填） */
  materialPosition?: number
  /** 相关文件（FILE_MISSING 必填，便于定位真实丢失文件） */
  assetId?: string

  type: AnomalyType
  message: string

  /** Phase 2：后端给的严重级别；blocking=true 时禁止生成版本 */
  level?: 'BLOCKING' | 'WARNING' | 'INFO'
  blocking?: boolean
  /** Phase 2：相关设计包位置号 */
  position?: number
  /** Phase 2：相关匹配结果 id */

  /** 结构化细节，例如 { missingPositions: [21,22,23], missingCodes: ['21-1'] } */
  details?: Record<string, unknown>

  status: AnomalyStatus

  firstOccurredAt: string
  lastOccurredAt: string
  resolvedAt?: string

  /** 预留：通知美工组长 */
  notifyTarget?: 'DESIGN_LEAD' | 'OPERATOR' | 'NONE'
}

// ---------------------------------------------------------------- 12. 上传会话 UploadSession

export type UploadSessionStage = PackageUploadStatus

/**
 * 上传会话：刷新/断点恢复的唯一锚点，保证幂等。
 * 不重复创建 DesignPackage / Material / Variant / DerivativeBatch / DistributionTask。
 */
export interface UploadSession {
  id: string
  designPackageId: string
  /** 同一次上传行为的记录 */
  packageUploadId?: string

  originalPackageName: string
  fileSize: number
  stage: UploadSessionStage

  uploadType: PackageUploadType

  /** 归属运营（首次上传必填，派发用） */
  operatorId?: string
  operatorName?: string
  remark?: string

  /** 已接收文件清单，可断点续传 */
  receivedFiles: string[]

  createdAt: string
  updatedAt: string
}

// ---------------------------------------------------------------- 13. 订单图片 URL 识别（预留，本轮不实现 UI）

export interface OrderOptionBinding {
  id: string
  childAsin: string
  originalUrl: string
  normalizedUrl?: string
  imageExactHash?: string
  imagePerceptualHash?: string
  materialVariantId?: string
  /** 只在当前 Parent 的副素材范围内比较 */
  parentAsin?: string
  similarity?: number
  confirmed: boolean
  createdAt: string
}

// ============================================================================
// DTO / 视图模型（后端不需要照搬建表）
// ============================================================================

/** 当前上架版本的覆盖情况（第十四条：不会自动创建下一个版本） */
export interface VariantSupplementGap {
  versionCode: string
  batchId: string
  covered: number
  total: number
  missingCodes: string[]
}

export interface CountCheckResult {
  mainCount: number
  /** 本次上传的副图 Asset 张数（与 mainCount 比较的是这个） */
  variantUploadCount: number
  /** 已建立的副素材（MaterialVariant）数 */
  variantCount: number
  diff: number
  messages: string[]
  /** true 表示**禁止生成版本**（不是禁止上传） */
  blocked: boolean
}

/** 上传页整包视图 */
export interface PackageOverview {
  pkg: DesignPackage
  /** 当前上传行为 */
  upload: PackageUpload
  session: UploadSession
  /** 该设计包的全部上传历史（时间倒序） */
  uploads: PackageUpload[]

  mainMaterials: DesignPackageMaterial[]
  /** 位置 id → 真实 Material 实体 */
  materialsById: Record<string, Material>
  /** assetId → Asset（DTO：由后端展开，前端不自行 join） */
  assetsById: Record<string, Asset>
  variants: MaterialVariant[]
  /** 版本 id → 派生展示信息 */
  variantViews: VariantView[]

  currentBatch: DerivativeBatch | null
  batches: DerivativeBatch[]

  /** 最新一次上传的配对结果（按同名 pairKey） */
  pairings: PairingView[]
  /** 配对所属的上传记录 id */
  pairingUploadId?: string
  pairingSummary?: Record<string, number>
  anomalies: DataAnomaly[]
  /** 未解决的阻断异常；非空时禁止生成版本（第三十一条） */
  blockingAnomalies: DataAnomaly[]
  logs: ActivityLog[]

  /**
   * 后端 package_upload_assets 的真实上传结果（主素材 / PSD / 副图 / 其他）。
   * 副图上传后立即出现在这里，刷新页面重新拉整包视图同样有值。
   */
  uploadedFiles: PackageUploadedFilesDto

  // ---- 聚合结果（DTO，不在 DesignPackage 上存储）----
  operatorId?: string
  operatorName?: string
  mainMaterialCount: number
  variantCount: number
  currentBatchNo: number
  /** 该设计包的归属运营列表（一个 Batch 可派发给多个运营） */
  operators: { operatorId: string; operatorName: string }[]

  countCheck: CountCheckResult
  coverage: VariantSupplementGap
  /** 是否已提交并派发 */
  submitted: boolean
  /** 当前设计包的派发任务 */
  distributions: DistributionTaskView[]
}

/** 运营端任务视图 */
export interface TaskOverview {
  task: DistributionTaskView
  pkg?: DesignPackage
  batch?: DerivativeBatch
  upload?: PackageUpload
  /** 已交付的副素材（按 items 的 Revision 快照展开） */
  variants: VariantView[]
  assetsById: Record<string, Asset>
  /** 交付轮次 → 该轮的交付项 */
  deliveries: { deliveryRound: number; items: DistributionTaskItem[] }[]
  logs: ActivityLog[]
}

/** 素材中心按设计包折叠的分组 DTO */
export interface DesignPackageGroupView {
  pkg: DesignPackage
  mainMaterialCount: number
  variantCount: number
  batchCount: number
  currentBatchNo: number
  latestUpload?: PackageUpload
  /** 全部归属运营 */
  operators: { operatorId: string; operatorName: string }[]
  /** DTO：设计包内位置列表（含素材实体展开信息） */
  positions: DesignPackageMaterialView[]
}

/** 设计包位置 DTO（含素材实体展开字段，供卡片直接渲染） */
export interface DesignPackageMaterialView {
  position: DesignPackageMaterial
  material: Material
  /** 预览图 URL（由 previewAssetId → Asset 展开） */
  previewUri: string
  psdUri?: string
  /** 该位置在各版本下的副素材数量 */
  variantCount: number
}

/**
 * 素材中心卡片 / 抽屉的视图模型说明：
 * UI DTO 复用 `src/types/material.ts` 里的 `Material`（素材中心既有组件的契约），
 * 由 `src/lib/materialView.ts` 的适配器从
 *   Material 实体 + Asset + DesignPackageMaterial + DerivativeBatch + MaterialVariant
 * 拼装而成。该结构不对应任何数据库表，后端无需照搬。
 */
export interface MaterialViewSource {
  /** 真实 Material 实体 */
  material: Material
  /** 设计包位置关联（主素材视图需要） */
  position?: DesignPackageMaterial
  /** 该 MAT 关联的设计包数量 */
  designCount: number
  asinCount: number
}

/** 主素材详情（素材抽屉 / 详情接口） */
export interface MaterialDetailView {
  material: Material
  /** DTO：预览图 / PSD 当前文件展开 */
  previewAsset?: Asset
  psdAsset?: Asset
  /** 当前生效 PSD Revision */
  currentPsdRevision: PsdRevision | null
  /** PSD 修订历史（含软删除，DTO 已展开 asset） */
  psdRevisions: (PsdRevision & { asset?: Asset })[]
  /** 该 MAT 被哪些设计包的哪个位置引用 */
  positions: { pkgId: string; packageName: string; packageCode: string; position: number; psdUri?: string }[]
  /** 该 MAT 的全部副素材（跨设计包） */
  variants: MaterialVariantRow[]
  designCount: number
  asinCount: number
}

/** 主素材详情里的「副素材」条目（Drawer 副素材 Tab） */
export interface MaterialVariantRow {
  variant: MaterialVariant
  designPackageName: string
  versionCode: string
  /** DTO：来自 DerivativeBatch.versionNo */
  batchVersionNo: number | null
  imageUri: string
  fileName: string
  currentRevision: number
  /** 该副素材被哪个更晚的版本复用（内容完全相同，未新建实体） */
  reusedByBatchCode?: string
  /** DTO：Revision 历史（含软删除） */
  revisionHistory: (VariantRevision & { asset?: Asset })[]
}
