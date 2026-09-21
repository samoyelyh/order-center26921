// ============================================================================
// API DTO → 本地实体 state 的映射
//
// 作用：后端已经返回真实数据，前端不必把整套 store 改写成「异步 selector」。
// 把 DTO 映射成既有领域实体后注入 store，页面上已有的 selector
// （getPackageOverview / getMaterials / getVariantsOfMaterial …）继续可用。
//
// Phase 1 后端尚无 derivative_batches / material_variants，因此这两个集合保持为空，
// 相关 UI 走空态。
// ============================================================================

import type {
  ActivityLogDto,
  AssetDto,
  DataAnomalyDto,
  DerivativeBatchDto,
  DesignPackageDto,
  DesignPackageMaterialDto,
  MaterialDto,
  MaterialPairingDto,
  MaterialPsdRevisionDto,
  MaterialVariantDto,
  PackageOverviewDto,
  PackageUploadedFilesDto,
  PackageUploadDto,
  UploadedFileDto,
  UploadSessionDto,
} from '@/services/apiClient'
import { resolveMediaUrl } from '@/services/apiClient'
import type {
  ActivityLog,
  Asset,
  CountCheckResult,
  DataAnomaly,
  DerivativeBatch,
  DesignPackage,
  DesignPackageMaterial,
  Material,
  MaterialVariant,
  PairingView,
  PackageUpload,
  PsdRevision,
  UploadSession,
  VariantRevision,
  VariantSupplementGap,
} from '@/types/material-workflow'
import type { WorkflowState } from '@/store/workflowStore'

/**
 * 后端代理图片地址（/api/assets/{id}/content）补成完整地址。
 * /mock/* 是前端自带静态资源，不能加 API 主机前缀。
 */
function toAbsolute(url: string | null | undefined): string {
  if (!url) return ''
  return url.startsWith('/api/') ? resolveMediaUrl(url) : url
}

function toAsset(dto: AssetDto): Asset {
  return {
    id: dto.id,
    storageKey: toAbsolute(dto.uri) || dto.storageKey,
    originalFilename: dto.originalFilename,
    mimeType: dto.mimeType,
    sizeBytes: dto.sizeBytes,
    width: dto.width ?? undefined,
    height: dto.height ?? undefined,
    blake3: dto.blake3 ?? undefined,
    phash: dto.phash ?? undefined,
    phashVersion: dto.phashVersion ?? undefined,
    createdBy: dto.createdBy,
    createdAt: dto.createdAt,
    deletedAt: dto.deletedAt ?? undefined,
  }
}

function toPsdRevision(dto: MaterialPsdRevisionDto): PsdRevision {
  return {
    id: dto.id,
    materialId: dto.materialId,
    revisionNo: dto.revisionNo,
    assetId: dto.assetId,
    createdAt: dto.createdAt,
    createdBy: dto.createdBy,
    deleted: dto.deleted,
    note: dto.note ?? undefined,
  }
}

function toMaterial(dto: MaterialDto): Material {
  return {
    id: dto.id,
    materialCode: dto.materialCode,
    name: dto.name,
    // Phase 1 的 materials 表只代表主素材；副素材在 Phase 2 独立表
    type: 'MAIN',
    previewAssetId: dto.previewAssetId,
    currentPsdRevisionId: dto.currentPsdRevisionId ?? undefined,
    tags: dto.tags ?? [],
    createdAt: dto.createdAt,
    updatedAt: dto.updatedAt,
    sourcePackageUploadId: dto.sourcePackageUploadId ?? undefined,
    createdBy: dto.createdBy,
  }
}

function toPosition(dto: DesignPackageMaterialDto): DesignPackageMaterial {
  return {
    id: dto.id,
    designPackageId: dto.designPackageId,
    position: dto.position,
    materialId: dto.materialId,
    materialCode: dto.material?.materialCode ?? '',
    displayName: dto.displayName ?? undefined,
    previewUri: toAbsolute(dto.previewUri) || '',
    sourceFileName: dto.sourceFileName,
    // Phase 1 后端不复刻位置级指纹字段（指纹统一在 Asset 上），这里用空占位，
    // 匹配相关逻辑属于 Phase 2。
    fingerprint: {
      exactHash: '',
      exactAlgorithm: 'SHA256',
      perceptualHash: '',
      perceptualAlgorithm: 'AHASH',
      perceptualVersion: 1,
    },
    createdFromUploadId: dto.createdFromUploadId,
    createdAt: dto.createdAt,
  }
}

function toPackage(dto: DesignPackageDto): DesignPackage {
  return {
    id: dto.id,
    code: dto.code,
    name: dto.name,
    designCode: dto.designCode ?? dto.code,
    tags: dto.tags ?? [],
    responsibleName: dto.responsibleName ?? dto.designerName ?? '',
    responsibleId: dto.responsibleId ?? undefined,
    remark: dto.remark ?? undefined,
    designerId: dto.designerId ?? undefined,
    designerName: dto.designerName ?? undefined,
    createdBy: dto.createdBy,
    createdAt: dto.createdAt,
    updatedAt: dto.updatedAt,
    archivedAt: dto.archivedAt ?? undefined,
  }
}

function toUpload(dto: PackageUploadDto): PackageUpload {
  return {
    id: dto.id,
    designPackageId: dto.designPackageId,
    originalPackageName: dto.originalPackageName,
    fileSize: dto.fileSize ?? undefined,
    uploadSessionId: dto.uploadSessionId,
    uploaderId: dto.uploaderId,
    uploaderName: dto.uploaderName,
    uploadType: dto.uploadType,
    targetBatchId: dto.targetBatchId ?? undefined,
    status: dto.status as PackageUpload['status'],
    remark: dto.remark ?? undefined,
    createdAt: dto.createdAt,
    updatedAt: dto.updatedAt,
  }
}

function toSession(dto: UploadSessionDto): UploadSession {
  return {
    id: dto.id,
    designPackageId: dto.designPackageId,
    packageUploadId: dto.packageUploadId ?? undefined,
    originalPackageName: dto.originalPackageName,
    fileSize: dto.fileSize,
    stage: dto.stage as UploadSession['stage'],
    uploadType: dto.uploadType as UploadSession['uploadType'],
    operatorId: dto.operatorId ?? undefined,
    operatorName: dto.operatorName ?? undefined,
    remark: dto.remark ?? undefined,
    receivedFiles: dto.receivedFiles ?? [],
    createdAt: dto.createdAt,
    updatedAt: dto.updatedAt,
  }
}

/**
 * 前端 store 通过 `upload.uploadSessionId` 查会话，因此 state.sessions 的 key
 * 必须是 packageUploadId（后端 uploadSessionId），而不是 session 自己的 id。
 */
function sessionKeys(dto: UploadSessionDto): string[] {
  return Array.from(new Set([dto.packageUploadId, dto.id].filter((k): k is string => Boolean(k))))
}

function toLog(dto: ActivityLogDto): ActivityLog {
  return {
    id: dto.id,
    designPackageId: dto.designPackageId ?? null,
    targetType: dto.targetType as ActivityLog['targetType'],
    targetId: dto.targetId,
    actor: dto.actor,
    action: dto.action as ActivityLog['action'],
    summary: dto.summary,
    before: dto.before ?? undefined,
    after: dto.after ?? undefined,
    createdAt: dto.createdAt,
  }
}

/** 后端未返回 uploadedFiles 时的空结构（老后端兼容） */
function emptyUploadedFiles(): PackageUploadedFilesDto {
  return { main: [], psd: [], variants: [], other: [], mainCount: 0, psdCount: 0, variantCount: 0, otherCount: 0 }
}

// ---------------------------------------------------------------- Phase 2：配对 / 版本

/** 后端异常 → 页面异常视图 */
function toAnomaly(dto: DataAnomalyDto, packageId: string): DataAnomaly {
  const now = new Date().toISOString()
  return {
    id: dto.id,
    designPackageId: packageId,
    type: (dto.code as DataAnomaly['type']) ?? 'MISSING_VARIANT',
    level: dto.level,
    blocking: dto.blocking,
    message: dto.message,
    position: dto.position ?? undefined,
    status: dto.resolved ? 'RESOLVED' : 'OPEN',
    firstOccurredAt: now,
    lastOccurredAt: now,
  }
}

/**
 * 真实副素材实体（用于「本次上传结果 / 版本」与素材中心的副素材展示）。
 *
 * **必须把当前 Revision 一起带上**：
 *   currentVariantRevision() 只从 variant.revisions 里找当前 Revision，
 *   再通过 revision.assetId → state.assets 拿图片。
 *   之前这里写死 revisions: []，于是副素材永远拿不到 Asset，
 *   页面上的副素材缩略图就是空白（本轮修复的第一个问题）。
 */
function toMaterialVariantEntity(dto: MaterialVariantDto): MaterialVariant {
  const revisionNo = dto.currentRevisionNo ?? 1
  const revisionId = dto.currentRevisionId ?? `${dto.id}-r${revisionNo}`
  const revision: VariantRevision = {
    id: revisionId,
    variantId: dto.id,
    revisionNo,
    assetId: dto.assetId ?? '',
    createdAt: dto.createdAt,
    createdBy: '',
    deleted: false,
  }
  return {
    id: dto.id,
    materialId: dto.materialId,
    designPackageMaterialId: dto.designPackageMaterialId,
    batchId: dto.batchId,
    displayCode: dto.displayCode,
    currentRevisionId: revisionId,
    revisions: [revision],
    reusedByBatchCode: dto.reusedByBatchCode ?? undefined,
    tags: dto.tags ?? [],
    deleted: dto.deleted,
    createdAt: dto.createdAt,
    updatedAt: dto.createdAt,
  } as unknown as MaterialVariant
}

/** 副素材的图片 Asset：优先用整包视图里的真实 Asset，缺失时按 DTO 的 previewUrl 兜底 */
function variantAssetFallback(dto: MaterialVariantDto): Asset | null {
  if (!dto.assetId) return null
  const uri = toAbsolute(dto.previewUrl)
  if (!uri) return null
  return {
    id: dto.assetId,
    storageKey: uri,
    originalFilename: dto.originalFilename ?? '',
    mimeType: 'image/*',
    sizeBytes: 0,
    createdBy: '',
    createdAt: dto.createdAt,
  }
}

/**
 * 同名配对 → 页面视图模型。
 *
 * 主副素材关系只由同一次上传内的同名 pairKey 决定：
 * 这里不做任何相似度计算，也没有候选打分/排序。
 */
function toPairing(
  dto: MaterialPairingDto,
  positions: Record<string, DesignPackageMaterial>,
): PairingView {
  const main = positions[dto.designPackageMaterialId]
  if (!main) {
    throw new Error(`配对记录引用了不存在的设计包位置：${dto.designPackageMaterialId}`)
  }

  return {
    id: dto.id,
    designPackageId: dto.designPackageId,
    packageUploadId: dto.packageUploadId,
    designPackageMaterialId: dto.designPackageMaterialId,
    position: dto.position,
    pairKey: dto.pairKey ?? '',
    materialId: dto.materialId,
    materialCode: dto.materialCode,
    materialName: dto.materialName,
    mainPreviewUri: toAbsolute(dto.mainPreviewUrl),
    mainSourceFileName: dto.mainSourceFileName,
    variantAssetId: dto.variantAssetId ?? undefined,
    variantPreviewUri: toAbsolute(dto.variantPreviewUrl) || undefined,
    variantFileName: dto.variantFileName ?? undefined,
    variantPairKey: dto.variantPairKey ?? undefined,
    psdAssetId: dto.psdAssetId ?? undefined,
    psdFileName: dto.psdFileName ?? undefined,
    source: dto.source,
    status: dto.status,
    variantId: dto.variantId ?? undefined,
    confirmedBy: dto.confirmedBy ?? undefined,
    confirmedAt: dto.confirmedAt ?? undefined,
    main,
    options: (dto.options ?? []).map((option) => ({
      assetId: option.assetId,
      originalFilename: option.originalFilename,
      pairKey: option.pairKey,
      previewUri: toAbsolute(option.previewUrl),
      sizeBytes: option.sizeBytes,
      occupiedByPosition: option.occupiedByPosition ?? null,
    })),
  }
}

function toBatch(
  dto: DerivativeBatchDto,
  packageId: string,
): DerivativeBatch {
  return {
    id: dto.id,
    designPackageId: packageId,
    versionNo: dto.versionNo,
    code: dto.code,
    createdFromUploadId: dto.createdFromUploadId ?? '',
    createdAt: dto.createdAt,
    mainMaterialCountAtCreation: dto.mainMaterialCountAtCreation,
  }
}

/**
 * 把后端返回的相对图片地址（/api/assets/{id}/content）补成完整地址。
 * 局域网访问时必须指向局域网后端，不能退回 127.0.0.1。
 */
export function withAbsoluteUrls(files: PackageUploadedFilesDto): PackageUploadedFilesDto {
  const fix = (list: UploadedFileDto[]): UploadedFileDto[] =>
    list.map((item) => ({ ...item, previewUrl: resolveMediaUrl(item.previewUrl) }))
  return {
    ...files,
    main: fix(files.main ?? []),
    psd: fix(files.psd ?? []),
    variants: fix(files.variants ?? []),
    other: fix(files.other ?? []),
  }
}

export interface HydrateResult {
  patch: Partial<WorkflowState>
  packageId: string
}

/**
 * 把整包视图 DTO 映射成 store patch。
 *
 * 注意：只 patch 涉及到的 key，不会清空其它设计包的数据。
 */
export function buildHydratePatch(
  overview: PackageOverviewDto,
  current: WorkflowState,
): HydrateResult {
  const packageId = overview.designPackage.id

  const assets: Record<string, Asset> = { ...current.assets }
  for (const dto of overview.assets) assets[dto.id] = toAsset(dto)

  const materials: Record<string, Material> = { ...current.materials }
  for (const dto of overview.materials) materials[dto.id] = toMaterial(dto)

  const psdRevisions: Record<string, PsdRevision> = { ...current.psdRevisions }
  for (const material of overview.materials) {
    for (const revision of material.psdRevisions ?? []) {
      psdRevisions[revision.id] = toPsdRevision(revision)
    }
  }

  const positions: Record<string, DesignPackageMaterial[]> = {
    ...current.positions,
    [packageId]: overview.positions.map(toPosition),
  }

  const packages: Record<string, DesignPackage> = {
    ...current.packages,
    [packageId]: toPackage(overview.designPackage),
  }

  const uploads: Record<string, PackageUpload> = { ...current.uploads }
  for (const dto of overview.uploads) uploads[dto.id] = toUpload(dto)
  if (overview.upload) uploads[overview.upload.id] = toUpload(overview.upload)

  const sessions: Record<string, UploadSession> = { ...current.sessions }
  if (overview.session) {
    const session = toSession(overview.session)
    for (const key of sessionKeys(overview.session)) sessions[key] = session
  }

  const logs: Record<string, ActivityLog[]> = {
    ...current.logs,
    [packageId]: (overview.logs ?? []).map(toLog),
  }

  // 后端 package_upload_assets 的真实归属结果（副图就靠这里在刷新后仍然显示）
  const uploadedFiles: Record<string, PackageUploadedFilesDto> = {
    ...current.uploadedFiles,
    [packageId]: withAbsoluteUrls(overview.uploadedFiles ?? emptyUploadedFiles()),
  }

  // ---- Phase 2：匹配结果 / 版本 / 副素材 / 阻断异常 ----
  const positionById: Record<string, DesignPackageMaterial> = {}
  for (const position of positions[packageId]) positionById[position.id] = position

  const pairings: PairingView[] = (overview.pairings ?? [])
    .map((dto) => {
      try {
        return toPairing(dto, positionById)
      } catch {
        return null
      }
    })
    .filter((item): item is PairingView => Boolean(item))

  // 自动匹配基线：撤回判断后回到这里（后端也会自己恢复，这里只做前端兜底）
  const batches = (overview.batches ?? []).map((dto) => toBatch(dto, packageId))

  // 副素材：实体 + 当前 Revision + 图片 Asset（Asset 缺失时按 previewUrl 兜底，
  // 否则素材中心的副素材缩略图为空）
  const variantRevisions: Record<string, VariantRevision> = { ...current.variantRevisions }
  const variants: Record<string, MaterialVariant[]> = {
    ...current.variants,
    [packageId]: (overview.variants ?? []).map((dto) => {
      const entity = toMaterialVariantEntity(dto)
      for (const revision of entity.revisions) variantRevisions[revision.id] = revision
      if (dto.assetId && !assets[dto.assetId]) {
        const fallback = variantAssetFallback(dto)
        if (fallback) assets[dto.assetId] = fallback
      }
      return entity
    }),
  }
  const anomalies = (overview.anomalies ?? []).map((dto) => toAnomaly(dto, packageId))

  // 数量核对 / 覆盖度以后端为准（只有后端知道「本次上传了几张副图」）
  const countChecks: Record<string, CountCheckResult> = {
    ...current.countChecks,
    [packageId]: {
      mainCount: overview.countCheck.mainCount,
      variantUploadCount: overview.countCheck.variantUploadCount,
      variantCount: overview.countCheck.variantCount,
      diff: overview.countCheck.diff,
      messages: overview.countCheck.messages ?? [],
      blocked: overview.countCheck.blocked,
    },
  }
  const coverages: Record<string, VariantSupplementGap> = { ...current.coverages }
  if (overview.coverage) {
    coverages[packageId] = {
      versionCode: overview.coverage.versionCode,
      batchId: overview.coverage.batchId,
      covered: overview.coverage.covered,
      total: overview.coverage.total,
      missingCodes: overview.coverage.missingCodes ?? [],
    }
  }

  let materialSeq = current.materialSeq
  for (const material of Object.values(materials)) {
    const index = Number(material.materialCode.replace(/\D/g, '')) || 0
    materialSeq = Math.max(materialSeq, index)
  }

  return {
    packageId,
    patch: {
      assets,
      materials,
      psdRevisions,
      positions,
      packages,
      uploads,
      sessions,
      logs,
      uploadedFiles,
      variants,
      variantRevisions,
      countChecks,
      coverages,
      anomalies: { ...current.anomalies, [packageId]: anomalies },
      batches: { ...current.batches, [packageId]: batches },
      pairings: { ...current.pairings, [packageId]: pairings },
      materialSeq,
    },
  }
}
