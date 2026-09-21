// ============================================================================
// 设计包工作流引擎（纯函数，无 React / 无副作用）
//
// 覆盖业务规则：
//   四  主素材永久身份 MAT-xxxxxx，设计包内 1/2/3 只是 position
//   七  上架版本属于整套设计包（union batch），禁止 per-material MAX(version)+1
//   八  业务版本 V1/V2/V3 与文件 Revision 分离，旧 Revision 不覆盖删除
//   九  双指纹：BLAKE3 完全相同 → 复用同一 Asset（不再有「相似度匹配」）
//   十  文件名只做命名异常检查
//   十一 整包一次性全局一对一匹配
//   十三 主副素材数量校验，报警并禁止直接提交
//   十四 补素材（不自动创建新版本）
//   二十一/二十二 ASIN 跳转 URL / 唯一标识（站点不参与唯一键）
//   二十六 统一维护记录（带 targetType / targetId）
//   二十八 异常稳定 anomalyKey + OPEN/RESOLVED/REOPENED
// ============================================================================

import { computeFingerprintFromBlob, identityHash, syntheticFingerprint } from '@/lib/fingerprint'
import { buildAsinUrl, parseAsinList } from '@/lib/asin'
import type {
  ActivityAction,
  ActivityLog,
  ActivityTargetType,
  MarketplaceSiteCode,
  AnomalyStatus,
  AnomalyType,
  Asset,
  AssetRef,
  CountCheckResult,
  DataAnomaly,
  DerivativeBatch,
  DesignPackage,
  DesignPackageMaterial,
  DistributionTask,
  DistributionTaskItem,
  DistributionTaskView,
  Fingerprint,
  Material,
  MaterialVariant,
  PackageUpload,
  PackageUploadType,
  PairingView,
  PsdRevision,
  UploadSession,
  VariantRevision,
  VariantSupplementGap,
} from '@/types/material-workflow'

// ---------------------------------------------------------------- 阈值与状态
//
// 主副素材关系**不再有任何相似度阈值**：配对只按同名 pairKey。

// ---------------------------------------------------------------- ID / 编码

let seq = 0
export function nextId(prefix: string): string {
  seq += 1
  return `${prefix}-${Date.now().toString(36)}-${seq.toString(36)}`
}

export function nowIso(): string {
  return new Date().toISOString()
}

export function formatDateTime(iso: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 主素材永久编码 MAT-xxxxxx */
export function formatMaterialCode(index: number): string {
  return `MAT-${String(index).padStart(6, '0')}`
}

export function formatBatchCode(versionNo: number): string {
  return `V${versionNo}`
}

/** 副素材展示编号：主素材位置-上架版本号 */
export function formatVariantCode(position: number, versionNo: number): string {
  return `${position}-${versionNo}`
}

export function formatPackageCode(name: string, date: Date): string {
  const slug = name.replace(/[^0-9A-Za-z\u4e00-\u9fa5]/g, '').slice(0, 6).toUpperCase() || 'PKG'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `DP-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${slug || identityHash(name).slice(0, 4).toUpperCase()}`
}

/** 上传行为类型的中文说明（PackageUpload.uploadType） */
export const UPLOAD_TYPE_LABEL: Record<PackageUploadType, string> = {
  INITIAL: '首次上传，创建新版本',
  NEW_BATCH: '新增上架版本',
  SUPPLEMENT: '补充已有版本素材',
  REVISION: '修正已有文件',
}

// ---------------------------------------------------------------- Asset 与 Revision

/**
 * 创建 Asset 实体（正式模型：文件信息不内联 JSONB，统一 assetId → Asset）。
 * Demo 下 storageKey 用 public/mock 路径代替对象存储 key。
 */
export function createAsset(input: {
  storageKey: string
  uri?: string
  originalFilename: string
  mimeType: string
  sizeBytes: number
  width?: number
  height?: number
  /** 生产指纹：BLAKE3；Demo 下为 SHA256 结果，仅存在于前端内存 */
  blake3?: string
  phash?: string
  phashVersion?: number
  createdBy: string
  createdAt?: string
}): Asset {
  return {
    id: nextId('asset'),
    storageKey: input.storageKey,
    originalFilename: input.originalFilename,
    mimeType: input.mimeType,
    sizeBytes: input.sizeBytes,
    width: input.width,
    height: input.height,
    blake3: input.blake3,
    phash: input.phash,
    phashVersion: input.phashVersion,
    createdBy: input.createdBy,
    createdAt: input.createdAt ?? nowIso(),
  }
}

/** Asset → 前端 AssetRef（DTO 展开） */
export function toAssetRef(asset: Asset | undefined): AssetRef | undefined {
  if (!asset) return undefined
  return {
    assetId: asset.id,
    uri: asset.storageKey,
    fileName: asset.originalFilename,
    mimeType: asset.mimeType,
    sizeBytes: asset.sizeBytes,
    createdAt: asset.createdAt,
  }
}

/** 主素材 PSD Revision（只存 assetId，旧版本永不覆盖删除） */
export function makePsdRevision(input: {
  materialId: string
  revisionNo: number
  assetId: string
  actor: string
  createdAt?: string
  note?: string
}): PsdRevision {
  return {
    id: nextId('psdrev'),
    materialId: input.materialId,
    revisionNo: input.revisionNo,
    assetId: input.assetId,
    createdAt: input.createdAt ?? nowIso(),
    createdBy: input.actor,
    deleted: false,
    note: input.note,
  }
}

/** 副素材 JPG Revision（只存 assetId，旧版本永不覆盖删除） */
export function makeVariantRevision(input: {
  variantId: string
  revisionNo: number
  assetId: string
  actor: string
  createdAt?: string
  note?: string
}): VariantRevision {
  return {
    id: nextId('rev'),
    variantId: input.variantId,
    revisionNo: input.revisionNo,
    assetId: input.assetId,
    createdAt: input.createdAt ?? nowIso(),
    createdBy: input.actor,
    deleted: false,
    note: input.note,
  }
}

/** 当前生效 Revision（按 currentRevisionId 精确取，兜底取最后一个） */
export function currentVariantRevision(variant: MaterialVariant): VariantRevision | null {
  return variant.revisions.find((r) => r.id === variant.currentRevisionId) ?? variant.revisions[variant.revisions.length - 1] ?? null
}

/** 当前生效 PSD Revision */
export function currentPsdRevision(
  material: Material,
  psdRevisions: PsdRevision[],
): PsdRevision | null {
  if (!material.currentPsdRevisionId) {
    return psdRevisions.filter((r) => r.materialId === material.id)[psdRevisions.length - 1] ?? null
  }
  return psdRevisions.find((r) => r.id === material.currentPsdRevisionId) ?? null
}

/** 从 Asset 表取指纹（生产指纹挂在 Asset 上） */
export function assetFingerprint(asset: Asset | undefined): Fingerprint | null {
  if (!asset?.blake3 || !asset.phash) return null
  return {
    exactHash: asset.blake3,
    exactAlgorithm: 'BLAKE3',
    perceptualHash: asset.phash,
    perceptualAlgorithm: 'PHASH',
    perceptualVersion: asset.phashVersion ?? 1,
  }
}

// ---------------------------------------------------------------- 版本规则

/**
 * 设计包统一版本号：下一个上架版本 = 现有最大版本 + 1。
 * 关键点：这是「整套设计包」的函数，不是每个素材各自的函数。
 */
export function nextBatchVersionNo(batches: DerivativeBatch[]): number {
  return batches.reduce((max, batch) => Math.max(max, batch.versionNo), 0) + 1
}

/** 同一批次（版本）内下一个 Revision 号 */
export function nextRevisionNo(revisions: { revisionNo: number }[]): number {
  return revisions.reduce((max, r) => Math.max(max, r.revisionNo), 0) + 1
}

/**
 * 守卫：同一设计包内所有副素材必须属于同一个上架版本。
 * 出现 `1-3 / 2-2 / 3-4` 这类混合版本时直接判为数据错误。
 */
export function collectVersionConflicts(
  pkg: DesignPackage,
  variants: MaterialVariant[],
  batches: DerivativeBatch[],
): DataAnomaly[] {
  const active = variants.filter((v) => !v.deleted)
  const batchIds = new Set(active.map((v) => v.batchId))
  if (batchIds.size <= 1) return []
  const codes = [...batchIds]
    .map((id) => batches.find((b) => b.id === id)?.code ?? id)
    .sort()
    .join('、')
  return [makeAnomaly({
    type: 'BATCH_VERSION_CONFLICT',
    designPackageId: pkg.id,
    anomalyKey: `BATCH_VERSION_CONFLICT:${pkg.id}`,
    message: `设计包「${pkg.name}」内出现多个上架版本（${codes}），上架版本必须整套统一，请修正。`,
    notifyTarget: 'DESIGN_LEAD',
  })]
}

/** 同一副素材的业务版本必须等于其所属批次的版本号 */
export function variantVersionNo(variant: MaterialVariant, batches: DerivativeBatch[]): number | null {
  return batches.find((b) => b.id === variant.batchId)?.versionNo ?? null
}

/** 同一 Batch + 同一运营 是否已有进行中的派发任务（防止重复派发） */
export function findActiveDistribution(
  tasks: DistributionTask[],
  batchId: string,
  operatorId: string,
): DistributionTask | undefined {
  return tasks.find(
    (t) => t.batchId === batchId && t.operatorId === operatorId && (t.status === 'ACTIVE' || t.status === 'RECEIVED'),
  )
}

// ---------------------------------------------------------------- 上传会话 / 上传行为（幂等）

export interface CreateUploadSessionResult {
  pkg: DesignPackage
  upload: PackageUpload
  session: UploadSession
}

/**
 * 创建「设计包 + 一次上传行为 + 上传会话」。
 * 注意：originalPackageName 记在 PackageUpload 上，不记在 DesignPackage 上。
 */
export function createUploadSession(input: {
  originalPackageName: string
  fileSize?: number
  uploaderId: string
  uploaderName: string
  uploadType: PackageUploadType
  packageName?: string
  packageCode?: string
  /** 补充/修正已有批次时指定目标批次 */
  targetBatchId?: string
  remark?: string
  designPackage?: DesignPackage
}): CreateUploadSessionResult {
  const createdAt = nowIso()
  const pkg: DesignPackage = input.designPackage ?? {
    id: nextId('pkg'),
    code: input.packageCode ?? formatPackageCode(input.packageName ?? input.originalPackageName, new Date()),
    name: input.packageName?.trim() || input.originalPackageName.replace(/\.[a-zA-Z0-9]+$/, ''),
    // 本地 Mock 会话没有真实设计编码：用设计包编码兜底（真实后端由用户手填）
    designCode: input.packageCode ?? formatPackageCode(input.packageName ?? input.originalPackageName, new Date()),
    tags: [],
    responsibleName: '',
    remark: input.remark,
    createdBy: input.uploaderName,
    createdAt,
    updatedAt: createdAt,
  }

  const uploadId = nextId('upload')
  const sessionId = nextId('session')

  const upload: PackageUpload = {
    id: uploadId,
    designPackageId: pkg.id,
    originalPackageName: input.originalPackageName,
    fileSize: input.fileSize,
    uploadSessionId: sessionId,
    uploaderId: input.uploaderId,
    uploaderName: input.uploaderName,
    uploadType: input.uploadType,
    targetBatchId: input.targetBatchId,
    status: 'PARSING',
    remark: input.remark,
    createdAt,
    updatedAt: createdAt,
  }

  const session: UploadSession = {
    id: sessionId,
    designPackageId: pkg.id,
    packageUploadId: uploadId,
    originalPackageName: input.originalPackageName,
    fileSize: input.fileSize ?? 0,
    stage: 'PARSING',
    uploadType: input.uploadType,
    remark: input.remark,
    receivedFiles: [],
    createdAt,
    updatedAt: createdAt,
  }

  return { pkg, upload, session }
}

// ---------------------------------------------------------------- 主素材 Material

/** 创建主素材实体（文件信息一律通过 Asset 管理） */
export function createMaterial(input: {
  materialCode: string
  name: string
  previewAsset: Asset
  psdAsset?: Asset
  uploader: string
  createdAt?: string
  tags?: string[]
  sourcePackageUploadId?: string
}): { material: Material; psdRevision?: PsdRevision } {
  const createdAt = input.createdAt ?? nowIso()
  const materialId = input.materialCode
  const psdRevision = input.psdAsset
    ? makePsdRevision({
      materialId,
      revisionNo: 1,
      assetId: input.psdAsset.id,
      actor: input.uploader,
      createdAt,
    })
    : undefined

  return {
    material: {
      id: materialId,
      materialCode: input.materialCode,
      name: input.name,
      type: 'MAIN',
      previewAssetId: input.previewAsset.id,
      currentPsdRevisionId: psdRevision?.id,
      tags: input.tags ?? [],
      createdAt,
      updatedAt: createdAt,
      sourcePackageUploadId: input.sourcePackageUploadId,
      createdBy: input.uploader,
    },
    psdRevision,
  }
}

// ---------------------------------------------------------------- 入库（把文件变成实体）

export interface IngestMainInput {
  position: number
  sourceFileName: string
  previewUri: string
  /** 预览图资源元信息（真实上传时由 File 提供） */
  previewMimeType?: string
  previewSizeBytes?: number
  /** 复用已有 MAT（同一 MAT 被多个设计包复用） */
  reuseMaterialId?: string
  psdFileName?: string
  psdUri?: string
}

export interface IngestInput {
  mains: IngestMainInput[]
  /** 副素材文件（已按上传顺序） */
  variants: { fileName: string; uri: string; mimeType?: string; sizeBytes?: number }[]
  /** 本次要写入的批次（新建或补充目标） */
  batch: DerivativeBatch
  designPackageId: string
  uploadId: string
  actor: string
}

export interface IngestResult {
  /** 新建或复用的 Asset */
  assets: Asset[]
  /** 新建或复用的 PSD Revision */
  psdRevisions: PsdRevision[]
  /** 新建或复用的 Material 实体 */
  materials: Material[]
  /** 设计包位置关联 */
  positions: DesignPackageMaterial[]
  /** 副素材实体 */
  variants: MaterialVariant[]
  /** 新建的 VariantRevision */
  variantRevisions: VariantRevision[]
}

/**
 * 把解析出的文件转成领域实体。
 * 副素材版本号来自传入的 batch（整套设计包统一），不取文件名里的版本号，
 * 也不对每个素材做 MAX(version)+1。
 */
export function ingestFiles(
  input: IngestInput,
  materialIndexStart = 1,
): IngestResult {
  const createdAt = nowIso()
  const materials: Material[] = []
  const assets: Asset[] = []
  const psdRevisions: PsdRevision[] = []
  const variantRevisions: VariantRevision[] = []
  let materialIndex = materialIndexStart

  const positions: DesignPackageMaterial[] = input.mains
    .slice()
    .sort((a, b) => a.position - b.position)
    .map((main) => {
      const materialCode = main.reuseMaterialId ?? formatMaterialCode(materialIndex)
      if (!main.reuseMaterialId) materialIndex += 1

      // 预览文件 → Asset
      const previewAsset = createAsset({
        storageKey: main.previewUri,
        originalFilename: main.sourceFileName,
        mimeType: main.previewMimeType ?? 'application/octet-stream',
        sizeBytes: main.previewSizeBytes ?? 0,
        createdBy: input.actor,
        createdAt,
      })
      assets.push(previewAsset)

      // PSD 源文件 → Asset（仅主素材有）
      let psdAsset: Asset | undefined
      if (main.psdUri) {
        psdAsset = createAsset({
          storageKey: main.psdUri,
          originalFilename: main.psdFileName ?? `${materialCode}.psd`,
          mimeType: 'image/vnd.adobe.photoshop',
          sizeBytes: 0,
          createdBy: input.actor,
          createdAt,
        })
        assets.push(psdAsset)
      }

      const { material, psdRevision } = createMaterial({
        materialCode,
        name: main.sourceFileName.replace(/\.[a-zA-Z0-9]+$/, ''),
        previewAsset,
        psdAsset,
        uploader: input.actor,
        createdAt,
        sourcePackageUploadId: input.uploadId,
      })
      materials.push(material)
      if (psdRevision) psdRevisions.push(psdRevision)

      return {
        id: nextId('dpm'),
        designPackageId: input.designPackageId,
        position: main.position,
        materialId: material.id,
        materialCode: material.materialCode,
        displayName: material.name,
        previewUri: main.previewUri,
        sourceFileName: main.sourceFileName,
        fingerprint: syntheticFingerprint({
          identity: `${input.designPackageId}:main:${main.position}:${main.sourceFileName}`,
        }),
        createdFromUploadId: input.uploadId,
        createdAt,
      }
    })

  const positionByIndex = new Map(positions.map((p) => [p.position, p]))

  const variants: MaterialVariant[] = input.variants.map((variant, index) => {
    // 副图归属哪个位置：按同名 pairKey 的数字部分；解析不出来就按上传顺序
    const keyNumber = Number(pairKeyOfFileName(variant.fileName).split(/[-_]/)[0])
    const position = Number.isFinite(keyNumber) && keyNumber > 0 ? keyNumber : index + 1
    const linked = positionByIndex.get(position) ?? positions[index] ?? positions[0]
    const variantId = nextId('variant')

    // 副素材文件 → Asset（生产在此写入 blake3 / phash）
    const asset = createAsset({
      storageKey: variant.uri,
      originalFilename: variant.fileName,
      mimeType: variant.mimeType ?? 'image/jpeg',
      sizeBytes: variant.sizeBytes ?? 0,
      createdBy: input.actor,
      createdAt,
    })
    assets.push(asset)

    const revision = makeVariantRevision({
      variantId,
      revisionNo: 1,
      assetId: asset.id,
      actor: input.actor,
      createdAt,
    })
    variantRevisions.push(revision)

    return {
      id: variantId,
      materialId: linked?.materialId ?? '',
      designPackageMaterialId: linked?.id ?? '',
      batchId: input.batch.id,
      displayCode: formatVariantCode(linked?.position ?? position, input.batch.versionNo),
      currentRevisionId: revision.id,
      revisions: [revision],
      tags: [],
      deleted: false,
      createdAt,
      updatedAt: createdAt,
    }
  })

  return { assets, psdRevisions, materials, positions, variants, variantRevisions }
}

/** 用真实文件计算指纹的入库（浏览器内 Demo：SHA256 + aHash，仅存在于前端内存） */
export async function ingestRealFiles(
  input: {
    designPackageId: string
    uploadId: string
    batch: DerivativeBatch
    actor: string
    mainFiles: File[]
    variantFiles: File[]
  },
): Promise<IngestResult & { fingerprints: Map<File, Fingerprint> }> {
  const fingerprints = new Map<File, Fingerprint>()
  for (const file of [...input.mainFiles, ...input.variantFiles]) {
    fingerprints.set(file, await computeFingerprintFromBlob(file))
  }

  const urlOf = (file: File) => URL.createObjectURL(file)
  const sortedMains = input.mainFiles
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN', { numeric: true }))
  const sortedVariants = input.variantFiles
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN', { numeric: true }))

  const result = ingestFiles(
    {
      designPackageId: input.designPackageId,
      uploadId: input.uploadId,
      batch: input.batch,
      actor: input.actor,
      mains: sortedMains.map((file, index) => ({
        position: Number(pairKeyOfFileName(file.name).split(/[-_]/)[0]) || index + 1,
        sourceFileName: file.name,
        previewUri: urlOf(file),
        previewMimeType: file.type || 'image/jpeg',
        previewSizeBytes: file.size,
      })),
      variants: sortedVariants.map((file) => ({
        fileName: file.name,
        uri: urlOf(file),
        mimeType: file.type || 'image/jpeg',
        sizeBytes: file.size,
      })),
    },
  )

  // Demo：把前端算出的指纹写入 DesignPackageMaterial（生产指纹在后端写入 Asset）
  result.positions.forEach((position, index) => {
    const file = sortedMains[index]
    if (file && fingerprints.has(file)) position.fingerprint = fingerprints.get(file)!
  })

  void result.variantRevisions
  return { ...result, fingerprints }
}

// ---------------------------------------------------------------- 数量校验

/** 第十三条：数量校验。数量不一致必须明确报警并禁止生成版本。 */
export function checkMaterialCount(mainCount: number, variantCount: number): CountCheckResult {
  const diff = variantCount - mainCount
  const messages: string[] = []
  if (diff < 0) {
    messages.push(`主素材 ${mainCount} 个，副图只有 ${variantCount} 张，缺少 ${Math.abs(diff)} 张副图，无法生成版本。`)
    messages.push(`缺少：${Array.from({ length: Math.abs(diff) }, (_, i) => `${mainCount - Math.abs(diff) + i + 1}-1`).join('、')}`)
  } else if (diff > 0) {
    messages.push(`主素材 ${mainCount} 个，副图 ${variantCount} 张，存在 ${diff} 张无法关联的副图。`)
  }
  // 这里比较的是「主素材数 vs 本次上传的副图数」，因此 variantUploadCount 与 variantCount 相同
  return {
    mainCount,
    variantUploadCount: variantCount,
    variantCount,
    diff,
    messages,
    blocked: diff !== 0,
  }
}

// ---------------------------------------------------------------- 上架版本覆盖

/**
 * 第十四条：补素材。
 * 设计包后来新增了主素材时，不自动创建下一个版本，而是显示当前版本覆盖度与缺失清单。
 */
export function computeVersionGap(
  batch: DerivativeBatch,
  positions: DesignPackageMaterial[],
  variants: MaterialVariant[],
): VariantSupplementGap {
  const activeMains = positions.filter((p) => p.materialId).sort((a, b) => a.position - b.position)
  const batchVariants = variants.filter((v) => v.batchId === batch.id && !v.deleted)
  const coveredPositions = new Set(batchVariants.map((v) => v.designPackageMaterialId))
  const missing = activeMains.filter((p) => !coveredPositions.has(p.id))
  return {
    versionCode: batch.code,
    batchId: batch.id,
    covered: activeMains.length - missing.length,
    total: activeMains.length,
    missingCodes: missing.map((p) => formatVariantCode(p.position, batch.versionNo)),
  }
}

// ---------------------------------------------------------------- 主副素材配对（同名 pairKey）

/**
 * 同名配对键 = 文件名去扩展名（小写）。
 *
 * 主副素材关系**只由它决定**：不再有相似度、不再有汉明距离、不再有匈牙利算法。
 *   主素材 1.jpg + 副素材 1.jpg → pairKey = "1" → 直接配对
 */
export function pairKeyOfFileName(fileName: string): string {
  const base = (fileName || '').replace(/\\/g, '/').split('/').pop() ?? ''
  const stem = base.includes('.') ? base.slice(0, base.lastIndexOf('.')) : base
  return stem.trim().toLowerCase()
}

// ---------------------------------------------------------------- 维护记录

export function makeLog(input: {
  designPackageId?: string | null
  targetType: ActivityTargetType
  targetId: string
  actor: string
  action: ActivityAction
  summary: string
  before?: string
  after?: string
}): ActivityLog {
  return {
    id: nextId('log'),
    designPackageId: input.designPackageId,
    targetType: input.targetType,
    targetId: input.targetId,
    actor: input.actor,
    action: input.action,
    summary: input.summary,
    before: input.before,
    after: input.after,
    createdAt: nowIso(),
  }
}

// ---------------------------------------------------------------- 异常（稳定 key + upsert）

export function makeAnomaly(input: {
  type: AnomalyType
  designPackageId: string
  anomalyKey: string
  message: string
  batchId?: string
  materialPosition?: number
  assetId?: string
  details?: Record<string, unknown>
  notifyTarget?: DataAnomaly['notifyTarget']
  status?: AnomalyStatus
}): DataAnomaly {
  const at = nowIso()
  return {
    id: nextId('anomaly'),
    anomalyKey: input.anomalyKey,
    designPackageId: input.designPackageId,
    batchId: input.batchId,
    materialPosition: input.materialPosition,
    assetId: input.assetId,
    type: input.type,
    message: input.message,
    details: input.details,
    status: input.status ?? 'OPEN',
    firstOccurredAt: at,
    lastOccurredAt: at,
    notifyTarget: input.notifyTarget ?? 'DESIGN_LEAD',
  }
}

/**
 * 按 anomalyKey upsert：
 *  - key 不存在 → 新增（OPEN）
 *  - key 已存在且此前 RESOLVED → 重新出现，标记 REOPENED 并更新 lastOccurredAt
 *  - key 已存在且仍 OPEN/REOPENED → 只刷新 message 与 lastOccurredAt，保留 firstOccurredAt
 *  - 本次未再出现的 OPEN 异常保持原状（由调用方决定是否 RESOLVED）
 */
export function upsertAnomalies(existing: DataAnomaly[], incoming: DataAnomaly[]): DataAnomaly[] {
  const byKey = new Map(existing.map((a) => [a.anomalyKey, a]))
  const at = nowIso()
  const next = existing.slice()

  for (const item of incoming) {
    const found = byKey.get(item.anomalyKey)
    if (!found) {
      byKey.set(item.anomalyKey, item)
      next.push(item)
      continue
    }
    const index = next.findIndex((a) => a.anomalyKey === item.anomalyKey)
    const merged: DataAnomaly = {
      ...found,
      message: item.message,
      type: item.type,
      batchId: item.batchId ?? found.batchId,
      materialPosition: item.materialPosition ?? found.materialPosition,
      assetId: item.assetId ?? found.assetId,
      details: item.details ?? found.details,
      notifyTarget: item.notifyTarget ?? found.notifyTarget,
      lastOccurredAt: at,
      status: found.status === 'RESOLVED' ? 'REOPENED' : found.status,
      resolvedAt: found.status === 'RESOLVED' ? undefined : found.resolvedAt,
    }
    next[index] = merged
    byKey.set(item.anomalyKey, merged)
  }

  return next
}

export interface CollectAnomaliesInput {
  pkg: DesignPackage
  batch: DerivativeBatch
  batches: DerivativeBatch[]
  positions: DesignPackageMaterial[]
  variants: MaterialVariant[]
  pairings: PairingView[]
  coverage: VariantSupplementGap
  countCheck: CountCheckResult
  operatorId?: string
  sessionStage: UploadSession['stage']
  /** assetId → Asset，用于 FILE_MISSING 绑定真实资产 */
  assets?: Record<string, Asset>
}

/**
 * 异常中心：统一产出异常模型（稳定 anomalyKey + upsert，重复计算不新增记录）。
 *
 * 正式粒度：
 *   Batch 级一条 → MATERIAL_COUNT_MISMATCH / BATCH_INCOMPLETE / BATCH_VERSION_CONFLICT /
 *                  DISTRIBUTION_MISSING（具体缺失项放 details）
 *   Position 级一条 → MISSING_VARIANT / PAIRING_NOT_CONFIRMED / DUPLICATE_FILE
 *   Asset 级一条 → FILE_MISSING（绑定 assetId）
 */
export function collectAnomalies(input: CollectAnomaliesInput): DataAnomaly[] {
  const anomalies: DataAnomaly[] = []
  const packageId = input.pkg.id
  const batchId = input.batch.id
  const missingPositions = input.coverage.missingCodes
    .map((code) => Number(code.split('-')[0]))
    .filter((n) => Number.isFinite(n))

  // ---------------- Batch 级：一条异常 + details ----------------
  if (input.countCheck.messages.length) {
    anomalies.push(makeAnomaly({
      type: 'MATERIAL_COUNT_MISMATCH',
      designPackageId: packageId,
      batchId,
      anomalyKey: `MATERIAL_COUNT_MISMATCH:${packageId}:${batchId}`,
      message: input.countCheck.messages.join(' '),
      details: {
        mainCount: input.countCheck.mainCount,
        variantCount: input.countCheck.variantCount,
        diff: input.countCheck.diff,
        missingPositions,
      },
      notifyTarget: 'DESIGN_LEAD',
    }))
  }

  if (input.coverage.missingCodes.length) {
    anomalies.push(makeAnomaly({
      type: 'BATCH_INCOMPLETE',
      designPackageId: packageId,
      batchId,
      anomalyKey: `BATCH_INCOMPLETE:${packageId}:${batchId}`,
      message: `${input.coverage.versionCode} 覆盖 ${input.coverage.covered} / ${input.coverage.total}，缺：${input.coverage.missingCodes.join('、')}。（不会自动创建下一个版本，请使用「补充 ${input.coverage.versionCode}」）`,
      details: {
        versionCode: input.coverage.versionCode,
        covered: input.coverage.covered,
        total: input.coverage.total,
        missingPositions,
        missingCodes: input.coverage.missingCodes,
      },
      notifyTarget: 'DESIGN_LEAD',
    }))
  }

  anomalies.push(...collectVersionConflicts(input.pkg, input.variants, input.batches))

  // ---------------- Position 级：每个位置一条（配对口径，无相似度） ----------------
  for (const pairing of input.pairings) {
    if (pairing.status !== 'UNPAIRED') continue
    const reason = pairing.pairKey
      ? `缺少同名副图（pairKey「${pairing.pairKey}」）`
      : '主素材文件名解析不出同名配对键'
    anomalies.push(makeAnomaly({
      type: 'MISSING_VARIANT',
      designPackageId: packageId,
      batchId,
      materialPosition: pairing.position,
      anomalyKey: `MISSING_VARIANT:${packageId}:${pairing.position}`,
      message: `主素材位置 ${pairing.position} ${reason}，请上传同名副素材或手动指定。`,
      details: { pairKey: pairing.pairKey },
      notifyTarget: 'DESIGN_LEAD',
    }))
  }

  for (const pairing of input.pairings) {
    if (pairing.status === 'CONFIRMED') continue
    if (pairing.status === 'UNPAIRED') continue
    anomalies.push(makeAnomaly({
      type: 'PAIRING_NOT_CONFIRMED',
      designPackageId: packageId,
      batchId,
      materialPosition: pairing.position,
      anomalyKey: `PAIRING_NOT_CONFIRMED:${packageId}:${pairing.position}`,
      message: `主素材位置 ${pairing.position} 已按同名配对（${pairing.variantFileName ?? ''}），等待人工确认。`,
      notifyTarget: 'DESIGN_LEAD',
    }))
  }

  return anomalies
}

// ---------------------------------------------------------------- 派发

/**
 * 生成派发明细（交付快照）。
 * 每条 Item 固定「副素材 + 当时 Revision」，之后 Revision 升级不会改变历史交付内容。
 */
export function makeDistributionItems(input: {
  distributionTaskId: string
  variants: MaterialVariant[]
  deliveryRound: number
  createdAt?: string
}): DistributionTaskItem[] {
  const createdAt = input.createdAt ?? nowIso()
  return input.variants
    .filter((v) => !v.deleted)
    .map((variant) => ({
      id: nextId('taskitem'),
      distributionTaskId: input.distributionTaskId,
      variantId: variant.id,
      // 快照：当前生效 Revision
      revisionId: variant.currentRevisionId,
      deliveryRound: input.deliveryRound,
      createdAt,
    }))
}

export function createDistributionTask(input: {
  pkg: DesignPackage
  batch: DerivativeBatch
  variants: MaterialVariant[]
  designerName: string
  operatorId: string
  operatorName: string
  remark?: string
}): DistributionTask {
  const id = nextId('task')
  return {
    id,
    designPackageId: input.pkg.id,
    batchId: input.batch.id,
    // SNAPSHOT FIELD：派发当时看到的名称，仅供历史文案展示
    packageName: input.pkg.name,
    packageCode: input.pkg.code,
    versionCode: input.batch.code,
    designerName: input.designerName,
    operatorId: input.operatorId,
    operatorName: input.operatorName,
    status: 'ACTIVE',
    items: makeDistributionItems({
      distributionTaskId: id,
      variants: input.variants,
      deliveryRound: 1,
    }),
    children: [],
    assignedAt: nowIso(),
    remark: input.remark,
  }
}

/**
 * 第四部分：补素材后的派发规则。
 * 已派发任务不做静默修改，而是追加一轮交付（deliveryRound + 1）：
 *   第 1 次交付：1-1 ~ 20-1
 *   第 2 次补充：21-1 ~ 23-1
 */
export function appendDistributionDelivery(input: {
  task: DistributionTask
  variants: MaterialVariant[]
  createdAt?: string
}): { task: DistributionTask; items: DistributionTaskItem[]; deliveryRound: number } {
  const deliveryRound = nextDeliveryRound(input.task)
  const items = makeDistributionItems({
    distributionTaskId: input.task.id,
    variants: input.variants,
    deliveryRound,
    createdAt: input.createdAt,
  })
  return {
    task: { ...input.task, items: [...input.task.items, ...items] },
    items,
    deliveryRound,
  }
}

/** 当前最大交付轮次 */
export function nextDeliveryRound(task: DistributionTask): number {
  return task.items.reduce((max, item) => Math.max(max, item.deliveryRound), 0) + 1
}

/** 派发明细按轮次分组（DTO 辅助） */
export function groupDeliveries(task: DistributionTask): { deliveryRound: number; items: DistributionTaskItem[] }[] {
  const rounds = new Map<number, DistributionTaskItem[]>()
  for (const item of task.items) {
    const list = rounds.get(item.deliveryRound) ?? []
    list.push(item)
    rounds.set(item.deliveryRound, list)
  }
  return [...rounds.entries()]
    .map(([deliveryRound, items]) => ({ deliveryRound, items }))
    .sort((a, b) => a.deliveryRound - b.deliveryRound)
}

/** DistributionTask → DTO（展开 items 为 variantIds） */
export function toDistributionTaskView(task: DistributionTask): DistributionTaskView {
  const variantIds = [...new Set(task.items.map((item) => item.variantId))]
  return {
    ...task,
    variantIds,
    deliveryRound: task.items.reduce((max, item) => Math.max(max, item.deliveryRound), 0),
  }
}

// ---------------------------------------------------------------- 运营回填 ASIN

export interface BindAsinInput {
  task: DistributionTask
  parentAsin: string
  childrenText: string
  /** 站点仅用于生成跳转链接，不参与唯一键，可不填 */
  site?: MarketplaceSiteCode
  actor: string
}

export interface BindAsinResult {
  task: DistributionTask
  logs: ActivityLog[]
  error?: string
}

/**
 * 第十八 / 十九 / 二十一 / 二十二 / 二十三 条：
 * 一个 Parent ASIN 关联一整套上架版本；Parent 下所有 Child 默认共享这套副素材；
 * ASIN 本身即唯一业务标识，站点只影响跳转链接，缺失站点不阻止保存。
 */
export function bindTaskAsins(input: BindAsinInput): BindAsinResult {
  const parent = input.parentAsin.trim().toUpperCase()
  if (!/^B0[A-Z0-9]{8}$/.test(parent)) {
    return { task: input.task, logs: [], error: 'Parent ASIN 格式应为 B0 + 8 位字母或数字。' }
  }
  const parsed = parseAsinList(input.childrenText)
  if (parsed.asins.length === 0) {
    return { task: input.task, logs: [], error: '请至少填写 1 个有效的 Child ASIN。' }
  }
  if (parsed.invalid.length) {
    return { task: input.task, logs: [], error: `存在格式不正确的 Child ASIN：${parsed.invalid.slice(0, 5).join('、')}` }
  }

  const logs: ActivityLog[] = []
  const previousParent = input.task.parentAsin?.asin
  const site = input.site ?? input.task.parentAsin?.site
  const previousChildren = new Set(input.task.children.map((c) => c.asin))

  const asinTargetType: ActivityTargetType = 'PARENT_ASIN'
  if (previousParent && previousParent !== parent) {
    logs.push(makeLog({
      designPackageId: input.task.designPackageId,
      targetType: asinTargetType,
      targetId: parent,
      actor: input.actor,
      action: 'UPDATE_ASIN',
      summary: '修改 Parent ASIN',
      before: previousParent,
      after: parent,
    }))
  } else if (!previousParent) {
    logs.push(makeLog({
      designPackageId: input.task.designPackageId,
      targetType: asinTargetType,
      targetId: parent,
      actor: input.actor,
      action: 'PARENT_ASIN',
      summary: '回填 Parent ASIN',
      after: parent,
    }))
  }

  const added = parsed.asins.filter((asin) => !previousChildren.has(asin))
  const removed = input.task.children.filter((c) => !parsed.asins.includes(c.asin)).map((c) => c.asin)
  if (added.length) {
    logs.push(makeLog({
      designPackageId: input.task.designPackageId,
      targetType: 'CHILD_ASIN',
      targetId: parent,
      actor: input.actor,
      action: 'CHILD_ASIN',
      summary: `回填 Child ASIN ${added.length} 个`,
      after: added.join('、'),
    }))
  }
  if (removed.length) {
    logs.push(makeLog({
      designPackageId: input.task.designPackageId,
      targetType: 'CHILD_ASIN',
      targetId: parent,
      actor: input.actor,
      action: 'UPDATE_ASIN',
      summary: `移除 Child ASIN ${removed.length} 个`,
      before: removed.join('、'),
    }))
  }

  return {
    task: {
      ...input.task,
      parentAsin: { asin: parent, site, listingUrl: buildAsinUrl(parent, site) },
      children: parsed.asins.map((asin) => ({
        asin,
        site,
        listingUrl: buildAsinUrl(asin, site),
        // 预留：Child 单独素材覆盖（本轮不做 UI）
        overrideVariantIds: input.task.children.find((c) => c.asin === asin)?.overrideVariantIds,
      })),
      status: 'COMPLETED',
      completedAt: nowIso(),
    },
    logs,
  }
}
