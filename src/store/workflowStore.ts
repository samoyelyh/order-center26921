// ============================================================================
// 工作流 store（内存态）
//
// 领域实体（与后端表一一对应）：
//   DesignPackage         长期逻辑实体（无任何缓存业务字段）
//   PackageUpload         每次实际的文件包上传行为
//   DerivativeBatch       上架版本，UNIQUE(designPackageId, versionNo)
//   Asset                 独立文件实体（storageKey + 生产指纹 blake3/phash）
//   Material              主素材实体 MAT-xxxxxx，跨设计包复用，禁止复制主数据
//   PsdRevision           主素材 PSD 修订历史
//   DesignPackageMaterial 设计包位置 → MAT 关联
//   MaterialVariant       副素材（版本事实只来自 batchId，设计包归属两跳推导）
//   VariantRevision       副素材 JPG 修订历史
//   DistributionTask/Item 派发任务 + 交付快照（副素材 + 当时 Revision + 交付轮次）
//
// 注意：mainMaterialCount / variantCount / currentBatchNo / operatorId / operatorName
//   全部由 DTO 聚合生成，不作为 DesignPackage 字段存储。
//
// 内存态挂在 globalThis 上（HMR / 动态 import 可能实例化多次，必须共享同一份 state）。
// 后端接入时把本文件函数逐个替换为 API 调用即可，接口清单见文件末尾。
// ============================================================================

import { useSyncExternalStore } from 'react'
import {
  appendDistributionDelivery,
  pairKeyOfFileName,
  checkMaterialCount,
  collectAnomalies,
  computeVersionGap,
  createAsset,
  createDistributionTask,
  createUploadSession,
  currentPsdRevision,
  currentVariantRevision,
  findActiveDistribution,
  formatBatchCode,
  formatMaterialCode,
  formatVariantCode,
  groupDeliveries,
  ingestFiles,
  ingestRealFiles,
  makeLog,
  makePsdRevision,
  makeVariantRevision,
  nextBatchVersionNo,
  nextId,
  nextRevisionNo,
  nowIso,
  toAssetRef,
  toDistributionTaskView,
  upsertAnomalies,
  variantVersionNo,
  UPLOAD_TYPE_LABEL,
  type IngestResult,
} from '@/lib/workflow'
import { buildAsinUrl, parseAsinList } from '@/lib/asin'
import { toMainMaterialView, toMaterialVariantRow, toVariantMaterialView, type MaterialVariantRow } from '@/lib/materialView'
import type { Material as MaterialView, MaterialDesignRef } from '@/types/material'
import type { PackageUploadedFilesDto } from '@/services/apiClient'
import { API_ENABLED, materialApi } from '@/services/apiClient'
import { buildHydratePatch } from '@/services/overviewMapper'
import { buildDemoSeed, type DemoSeed } from '@/mock/demoSeed'
import type {
  ActivityLog,
  ActivityTargetType,
  Asset,
  CountCheckResult,
  DataAnomaly,
  DerivativeBatch,
  DesignPackage,
  DesignPackageGroupView,
  DesignPackageMaterial,
  DesignPackageMaterialView,
  DistributionTask,
  Material,
  MaterialDetailView,
  MaterialVariant,
  PairingOption,
  PairingView,
  PackageOverview,
  PackageUpload,
  PackageUploadType,
  PsdRevision,
  TaskOverview,
  UploadSession,
  VariantRevision,
  VariantSupplementGap,
  VariantView,
  MarketplaceSiteCode,
} from '@/types/material-workflow'

/** 后端未返回 / Mock 模式下的空上传结果 */
const EMPTY_UPLOADED_FILES: PackageUploadedFilesDto = {
  main: [],
  psd: [],
  variants: [],
  other: [],
  mainCount: 0,
  psdCount: 0,
  variantCount: 0,
  otherCount: 0,
}

export interface WorkflowState {
  packages: Record<string, DesignPackage>
  /** 每次实际上传行为 */
  uploads: Record<string, PackageUpload>
  sessions: Record<string, UploadSession>
  batches: Record<string, DerivativeBatch[]>
  /** 独立文件实体表 */
  assets: Record<string, Asset>
  /** 主素材实体库：materialId → Material（跨设计包共享） */
  materials: Record<string, Material>
  /** PSD 修订历史 */
  psdRevisions: Record<string, PsdRevision>
  /** 设计包位置关联 */
  positions: Record<string, DesignPackageMaterial[]>
  /**
   * 后端 package_upload_assets 的真实结果：pkgId → 本次上传交付的文件（主素材/PSD/副图/其他）。
   * 副图上传后立即写入这里，刷新页面重新拉整包视图同样能拿到 —— 页面不再出现「副素材 0」。
   */
  uploadedFiles: Record<string, PackageUploadedFilesDto>
  /**
   * 后端权威的数量核对与覆盖度：pkgId → 结果。
   * 前端不自己再算一遍（主素材数 vs 本次上传副图数只有后端知道）。
   */
  countChecks: Record<string, CountCheckResult>
  coverages: Record<string, VariantSupplementGap>
  variants: Record<string, MaterialVariant[]>
  /** 副素材修订历史 */
  variantRevisions: Record<string, VariantRevision>
  /** pkgId → 该设计包最新一次上传的配对结果（按同名 pairKey，无相似度） */
  pairings: Record<string, PairingView[]>
  /** 自动匹配基线，用于「撤回判断」 */
  anomalies: Record<string, DataAnomaly[]>
  logs: Record<string, ActivityLog[]>
  tasks: Record<string, DistributionTask>
  /** MAT 序号分配游标 */
  materialSeq: number
}

function stateFromSeed(seed: DemoSeed): WorkflowState {
  return {
    packages: { ...seed.packages },
    uploads: { ...seed.uploads },
    sessions: { ...seed.sessions },
    batches: { ...seed.batches },
    assets: { ...seed.assets },
    materials: { ...seed.materials },
    psdRevisions: { ...seed.psdRevisions },
    positions: { ...seed.positions },
    uploadedFiles: {},
    countChecks: {},
    coverages: {},
    variants: { ...seed.variants },
    variantRevisions: { ...seed.variantRevisions },
    pairings: {},
    anomalies: { ...seed.anomalies },
    logs: { ...seed.logs },
    tasks: { ...seed.tasks },
    materialSeq: seed.materialSeq,
  }
}

/** 接真实后端时的空状态：**绝不灌演示数据**，避免演示 MAT 与真实 MAT 编号撞车 */
function emptyState(): WorkflowState {
  return {
    packages: {},
    uploads: {},
    sessions: {},
    batches: {},
    assets: {},
    materials: {},
    psdRevisions: {},
    positions: {},
    uploadedFiles: {},
    countChecks: {},
    coverages: {},
    variants: {},
    variantRevisions: {},
    pairings: {},
    anomalies: {},
    logs: {},
    tasks: {},
    materialSeq: 0,
  }
}

/** 初始状态：真实后端模式 = 空，本地 Mock 模式 = 演示种子 */
function initialState(): WorkflowState {
  return API_ENABLED ? emptyState() : stateFromSeed(buildDemoSeed())
}

/**
 * 全局单例：把内存态挂在 globalThis 上。
 * HMR / 动态 import 可能让同一个模块被实例化多次，如果状态是模块局部变量，
 * 页面与其它入口会各自持有一份互不同步的数据。
 */
interface WorkflowStoreSingleton {
  state: WorkflowState
  listeners: Set<() => void>
}

const GLOBAL_KEY = '__materialCenterWorkflowStore__'

function getSingleton(): WorkflowStoreSingleton {
  const holder = globalThis as typeof globalThis & { [GLOBAL_KEY]?: WorkflowStoreSingleton }
  if (!holder[GLOBAL_KEY]) {
    holder[GLOBAL_KEY] = {
      state: initialState(),
      listeners: new Set<() => void>(),
    }
  }
  return holder[GLOBAL_KEY]
}

const singleton = getSingleton()

function emit() {
  singleton.listeners.forEach((fn) => fn())
}

export function getWorkflowState(): WorkflowState {
  return singleton.state
}

function subscribe(listener: () => void) {
  singleton.listeners.add(listener)
  return () => {
    singleton.listeners.delete(listener)
  }
}

export function useWorkflowState(): WorkflowState {
  return useSyncExternalStore(subscribe, getWorkflowState, getWorkflowState)
}

/** 仅用于测试/演示重置（真实后端模式下重置成空，不再灌演示数据） */
export function resetWorkflowStore() {
  singleton.state = initialState()
  emit()
}

/**
 * 真实后端模式：把全库设计包一次性拉进 store，素材中心才有数据可显示。
 *
 * 之前素材中心只显示「本次会话上传过的东西 + 演示种子」，
 * 刷新页面后真实素材就消失了（这也是「示例数据串到我的素材里」的根因之一）。
 *
 * 逐包 hydrate（复用上传页同一条映射路径），包数不多时够用；
 * 包量大了再换成分页列表接口 + 单素材详情接口。
 */
let bootstrapPromise: Promise<{ packageCount: number; failed: number }> | null = null

export function loadAllPackagesFromApi(): Promise<{ packageCount: number; failed: number }> {
  if (!API_ENABLED) return Promise.resolve({ packageCount: 0, failed: 0 })
  if (bootstrapPromise) return bootstrapPromise

  bootstrapPromise = (async () => {
    // 只拉「已确认整包并生成过版本」的设计包：
    // 只上传、还没生成 V1 的草稿留在上传页，不进素材中心。
    const packages = await materialApi.listDesignPackages({ withBatch: true })
    let failed = 0
    for (const pkg of packages) {
      try {
        const overview = await materialApi.getPackageOverview(pkg.id)
        hydrateFromServer(buildHydratePatch(overview, getWorkflowState()).patch)
      } catch {
        failed += 1
      }
    }
    return { packageCount: packages.length, failed }
  })()

  bootstrapPromise.catch(() => {
    bootstrapPromise = null
  })
  return bootstrapPromise
}

/** 强制重新拉取（上传后 / 手动刷新时用） */
export function reloadAllPackagesFromApi(): Promise<{ packageCount: number; failed: number }> {
  bootstrapPromise = null
  return loadAllPackagesFromApi()
}

/**
 * 把「已经被归档（删除）」的设计包从 store 里清掉。
 *
 * 背景：loadAllPackagesFromApi 只 hydrate 未归档的包，但 store 里如果之前
 * hydrate 过一个现在已被归档的包（例如在另一个标签页 / 后台被删除），
 * 这个包会一直留在 store 里 —— 于是素材中心主页会继续显示它的副素材，
 * 直到整页刷新。每次进素材中心都调一下，保证显示的是真实未归档的数据。
 */
export async function pruneArchivedPackagesFromStore(): Promise<number> {
  if (!API_ENABLED) return 0
  let archived: import('@/services/apiClient').DesignPackageDto[] = []
  try {
    archived = await materialApi.listDesignPackages({ includeArchived: true })
  } catch {
    return 0
  }
  let removed = 0
  for (const pkg of archived) {
    if (!pkg.archivedAt) continue
    if (getWorkflowState().packages[pkg.id]) {
      dropPackageFromStore(pkg.id)
      removed += 1
    }
  }
  return removed
}

/**
 * 从 store 里摘掉一个设计包（删除 / 归档后调用）。
 *
 * 只摘这个包自己的位置、版本、副素材与上传记录；
 * 主素材（MAT）与 Asset 保留 —— 它们可能还被别的设计包引用。
 */
export function dropPackageFromStore(packageId: string) {
  const state = getWorkflowState()
  const next: WorkflowState = {
    ...state,
    packages: { ...state.packages },
    positions: { ...state.positions },
    batches: { ...state.batches },
    variants: { ...state.variants },
    pairings: { ...state.pairings },
    anomalies: { ...state.anomalies },
    logs: { ...state.logs },
    countChecks: { ...state.countChecks },
    coverages: { ...state.coverages },
    uploadedFiles: { ...state.uploadedFiles },
  }
  delete next.packages[packageId]
  delete next.positions[packageId]
  delete next.batches[packageId]
  delete next.variants[packageId]
  delete next.pairings[packageId]
  delete next.anomalies[packageId]
  delete next.logs[packageId]
  delete next.countChecks[packageId]
  delete next.coverages[packageId]
  delete next.uploadedFiles[packageId]

  const removedUploadIds = new Set(
    Object.values(next.uploads)
      .filter((upload) => upload.designPackageId === packageId)
      .map((upload) => upload.id),
  )
  next.uploads = Object.fromEntries(
    Object.entries(next.uploads).filter(([, upload]) => upload.designPackageId !== packageId),
  )
  next.sessions = Object.fromEntries(
    Object.entries(next.sessions).filter(
      ([, session]) => !session.packageUploadId || !removedUploadIds.has(session.packageUploadId),
    ),
  )

  singleton.state = next
  emit()
}

/** 删除设计包（软删除 / 归档）：后端归档成功后把本地也摘掉 */
export async function deletePackageFromApi(packageId: string, actor: string) {
  await materialApi.deleteDesignPackage(packageId, actor)
  dropPackageFromStore(packageId)
}

/** 恢复被删除（归档）的设计包，并重新 hydrate */
export async function restorePackageFromApi(packageId: string) {
  await materialApi.restoreDesignPackage(packageId)
  const overview = await materialApi.getPackageOverview(packageId)
  hydrateFromServer(buildHydratePatch(overview, getWorkflowState()).patch)
}

/** 已删除（归档）的设计包列表 */
export async function listArchivedPackagesFromApi() {
  if (!API_ENABLED) return []
  const packages = await materialApi.listDesignPackages({ includeArchived: true })
  return packages.filter((pkg) => Boolean(pkg.archivedAt))
}

/**
 * 后端接入模式：把 API 返回的领域实体合并进 state。
 *
 * 页面上的 selector（getPackageOverview / getMaterials / …）形态不变，
 * 因此上传页既能用本地 Mock，也能用真实后端数据。
 */
export function hydrateFromServer(patch: Partial<WorkflowState>) {
  commit(patch)
}

function commit(patch: Partial<WorkflowState>) {
  singleton.state = { ...singleton.state, ...patch }
  emit()
}

function touchSession(sessionId: string, stage: UploadSession['stage'], files?: string[]) {
  const session = getWorkflowState().sessions[sessionId]
  if (!session) return
  const next: UploadSession = {
    ...session,
    stage,
    updatedAt: nowIso(),
    receivedFiles: files ? Array.from(new Set([...session.receivedFiles, ...files])) : session.receivedFiles,
  }
  commit({ sessions: { ...getWorkflowState().sessions, [sessionId]: next } })
}

function touchUpload(uploadId: string, patch: Partial<PackageUpload>) {
  const upload = getWorkflowState().uploads[uploadId]
  if (!upload) return
  const next: PackageUpload = { ...upload, ...patch, updatedAt: nowIso() }
  commit({ uploads: { ...getWorkflowState().uploads, [uploadId]: next } })
}

function touchPackage(pkgId: string, patch: Partial<DesignPackage> = {}) {
  const pkg = getWorkflowState().packages[pkgId]
  if (!pkg) return
  commit({
    packages: { ...getWorkflowState().packages, [pkgId]: { ...pkg, ...patch, updatedAt: nowIso() } },
  })
}

function appendLog(
  designPackageId: string | null | undefined,
  input: {
    targetType: ActivityTargetType
    targetId: string
    actor: string
    action: ActivityLog['action']
    summary: string
    before?: string
    after?: string
  },
) {
  const log = makeLog({ designPackageId, ...input })
  const key = designPackageId ?? '__global__'
  commit({ logs: { ...getWorkflowState().logs, [key]: [...(getWorkflowState().logs[key] ?? []), log] } })
  return log
}

// ---------------------------------------------------------------- 派生查询辅助（DTO 聚合）

function allMaterials(): Material[] {
  return Object.values(getWorkflowState().materials)
}

/**
 * 该 MAT 关联了几个「设计」。
 *
 * 口径 = 引用它的设计包里**去重后的设计编码（design_code）个数**，
 * 不是「它在几个设计包位置里出现过」——
 * 同一次上传的自己的位置不算「关联设计」，用户视角的关联是「这个素材用在哪些设计上」。
 */
function designCountOf(materialId: string): number {
  return getDesignsOfMaterialById(materialId).length
}

/** 该 MAT 通过派发任务关联的 Child ASIN 数量（Phase 3 才有数据，本阶段恒为 0） */
function asinCountOf(_materialId: string): number {
  const state = getWorkflowState()
  const variantIds = new Set<string>()
  for (const variants of Object.values(state.variants)) {
    for (const variant of variants) {
      if (variant.materialId === _materialId) variantIds.add(variant.id)
    }
  }
  let count = 0
  for (const task of Object.values(state.tasks)) {
    if (task.status === 'CANCELLED') continue
    if (task.items.some((item) => variantIds.has(item.variantId))) count += task.children.length
  }
  return count
}

/** 某 MAT 被哪些设计包的哪个位置引用（RAW：位置级，不是设计级） */
function positionsOfMaterial(materialId: string) {
  const state = getWorkflowState()
  const out: { pkgId: string; position: DesignPackageMaterial; pkg?: DesignPackage }[] = []
  for (const [pkgId, positions] of Object.entries(state.positions)) {
    for (const position of positions) {
      if (position.materialId !== materialId) continue
      out.push({ pkgId, position, pkg: state.packages[pkgId] })
    }
  }
  return out
}

/**
 * 素材详情「关联设计」：按**设计编码**聚合的真实关联。
 *
 * 一个设计编码 = 一个设计，可以对应多个设计包（同一设计的新一版）；
 * 因此这里以 designCode 为主键聚合，并带上位置数 / 副素材数 / 最近更新时间。
 */
export function getDesignsOfMaterialById(materialId: string): MaterialDesignRef[] {
  const state = getWorkflowState()
  const rows = positionsOfMaterial(materialId)
  const byCode = new Map<string, MaterialDesignRef>()
  for (const { pkgId, position, pkg } of rows) {
    if (!pkg) continue
    const designCode = pkg.designCode || pkg.code
    const variantCount = (state.variants[pkgId] ?? []).filter(
      (variant) => variant.designPackageMaterialId === position.id && !variant.deleted,
    ).length
    const existing = byCode.get(designCode)
    if (existing) {
      existing.positionCount += 1
      existing.variantCount += variantCount
      existing.packages.push({ packageId: pkgId, packageName: pkg.name, position: position.position })
      if (pkg.updatedAt > existing.latestUpdate) existing.latestUpdate = pkg.updatedAt
      continue
    }
    byCode.set(designCode, {
      id: designCode,
      designCode,
      designerName: pkg.designerName ?? '',
      positionCount: 1,
      variantCount,
      latestUpdate: pkg.updatedAt,
      packages: [{ packageId: pkgId, packageName: pkg.name, position: position.position }],
    })
  }
  return [...byCode.values()].sort((a, b) => b.latestUpdate.localeCompare(a.latestUpdate))
}

/** 某 MAT 的「关联设计」列表（按素材编码查询） */
export function getDesignsOfMaterial(materialCode: string): MaterialDesignRef[] {
  const material = allMaterials().find((m) => m.materialCode === materialCode)
  if (!material) return []
  return getDesignsOfMaterialById(material.id)
}

/** 该 MAT 名下的副素材数量（真实数据） */
function variantCountOf(materialId: string): number {
  let count = 0
  for (const variants of Object.values(getWorkflowState().variants)) {
    count += variants.filter((variant) => variant.materialId === materialId && !variant.deleted).length
  }
  return count
}

/** 该 MAT 名下副素材的缩略图（主素材卡片直接展示用），按 displayCode 排序、去重 */
function variantPreviewsOf(materialId: string): { code: string; imageUri: string; variantId: string }[] {
  const state = getWorkflowState()
  const seen = new Set<string>()
  const out: { code: string; imageUri: string; variantId: string }[] = []
  for (const variants of Object.values(state.variants)) {
    for (const variant of variants) {
      if (variant.deleted || variant.materialId !== materialId) continue
      if (seen.has(variant.displayCode)) continue
      const revision = currentVariantRevision(variant)
      const uri = revision ? state.assets[revision.assetId]?.storageKey ?? '' : ''
      seen.add(variant.displayCode)
      out.push({ code: variant.displayCode, imageUri: uri, variantId: variant.id })
    }
  }
  return out.sort((a, b) => a.code.localeCompare(b.code, 'zh-Hans-CN', { numeric: true }))
}

/** 某设计包的归属运营列表（一个 Batch 可派发给多个运营） */
function operatorsOfPackage(pkgId: string): { operatorId: string; operatorName: string }[] {
  const state = getWorkflowState()
  const map = new Map<string, string>()
  const uploads = Object.values(state.uploads).filter((u) => u.designPackageId === pkgId && u.uploaderId)
  for (const task of Object.values(state.tasks)) {
    if (task.designPackageId !== pkgId || task.status === 'CANCELLED') continue
    map.set(task.operatorId, task.operatorName)
  }
  if (!map.size) {
    for (const session of Object.values(state.sessions)) {
      if (session.designPackageId !== pkgId || !session.operatorId) continue
      map.set(session.operatorId, session.operatorName ?? session.operatorId)
    }
  }
  void uploads
  return [...map.entries()].map(([operatorId, operatorName]) => ({ operatorId, operatorName }))
}

function currentBatchOf(pkgId: string): DerivativeBatch | undefined {
  const state = getWorkflowState()
  const batches = state.batches[pkgId] ?? []
  return batches.reduce<DerivativeBatch | undefined>(
    (max, b) => (!max || b.versionNo > max.versionNo ? b : max),
    undefined,
  )
}

function psdRevisionsOf(materialId: string): PsdRevision[] {
  return Object.values(getWorkflowState().psdRevisions).filter((r) => r.materialId === materialId)
}

function variantRevisionsOf(variantId: string): VariantRevision[] {
  return Object.values(getWorkflowState().variantRevisions).filter((r) => r.variantId === variantId)
}

// ---------------------------------------------------------------- 上传会话（幂等）

export interface StartSessionInput {
  originalPackageName: string
  fileSize?: number
  uploaderId?: string
  uploaderName?: string
  /** 归属运营：首次上传必填 */
  operatorId: string
  operatorName: string
  packageName?: string
  remark?: string
  /** 追加到已有设计包（新增批次 / 补素材 / 修正）时传入 */
  designPackageId?: string
  uploadType?: PackageUploadType
  /** 补充或修正的目标批次 */
  targetBatchId?: string
}

export function startPackageSession(input: StartSessionInput): { sessionId: string; pkgId: string; uploadId: string } {
  const existingPkg = input.designPackageId ? getWorkflowState().packages[input.designPackageId] : undefined
  const { pkg, upload, session } = createUploadSession({
    originalPackageName: input.originalPackageName,
    fileSize: input.fileSize,
    uploaderId: input.uploaderId ?? 'u-design-001',
    uploaderName: input.uploaderName ?? '肖芸',
    uploadType: input.uploadType ?? (existingPkg ? 'NEW_BATCH' : 'INITIAL'),
    packageName: input.packageName,
    targetBatchId: input.targetBatchId,
    remark: input.remark,
    designPackage: existingPkg,
  })

  const nextPkg: DesignPackage = existingPkg
    ? { ...existingPkg, remark: input.remark ?? existingPkg.remark, updatedAt: nowIso() }
    : pkg

  const nextSession: UploadSession = {
    ...session,
    operatorId: input.operatorId,
    operatorName: input.operatorName,
  }

  commit({
    packages: { ...getWorkflowState().packages, [nextPkg.id]: nextPkg },
    uploads: { ...getWorkflowState().uploads, [upload.id]: upload },
    sessions: { ...getWorkflowState().sessions, [nextSession.id]: nextSession },
  })

  appendLog(nextPkg.id, {
    targetType: 'UPLOAD',
    targetId: upload.id,
    actor: nextPkg.createdBy ?? input.uploaderName ?? '肖芸',
    action: 'UPLOAD_PACKAGE',
    summary: `上传原始文件包 ${input.originalPackageName}（${UPLOAD_TYPE_LABEL[upload.uploadType]}），归属运营 ${input.operatorName}`,
  })

  return { sessionId: nextSession.id, pkgId: nextPkg.id, uploadId: upload.id }
}

export { UPLOAD_TYPE_LABEL }

/** 恢复：上传一半刷新页面时复用已有会话，不重复创建设计包 */
export function recoverOrStartSession(input: {
  originalPackageName: string
  fileSize?: number
  operatorId?: string
  operatorName?: string
}): { sessionId: string; pkgId: string; recovered: boolean } {
  const existing = Object.values(getWorkflowState().sessions).find(
    (s) => s.originalPackageName === input.originalPackageName && s.stage !== 'SUBMITTED',
  )
  if (existing) {
    if (existing.stage !== 'INTERRUPTED') touchSession(existing.id, 'INTERRUPTED')
    return { sessionId: existing.id, pkgId: existing.designPackageId, recovered: true }
  }
  if (!input.operatorId || !input.operatorName) {
    throw new Error('首次上传必须选择归属运营')
  }
  const created = startPackageSession({
    originalPackageName: input.originalPackageName,
    fileSize: input.fileSize,
    operatorId: input.operatorId,
    operatorName: input.operatorName,
  })
  return { sessionId: created.sessionId, pkgId: created.pkgId, recovered: false }
}

export function setSessionOperator(sessionId: string, operatorId: string, operatorName: string) {
  const session = getWorkflowState().sessions[sessionId]
  if (!session) return
  commit({
    sessions: { ...getWorkflowState().sessions, [sessionId]: { ...session, operatorId, operatorName, updatedAt: nowIso() } },
  })
}

// ---------------------------------------------------------------- 解析入库

export interface LocalUploadFile {
  file: File
  kind: 'MAIN' | 'VARIANT'
  /** 主素材 PSD */
  psd?: File
}

/**
 * 真实文件上传：SHA256 + aHash 指纹（Demo）→ 生成 Asset / Material / 位置 / MaterialVariant。
 * 目标批次由 uploadType 决定：
 *   INITIAL / NEW_BATCH → 创建新批次（版本号 = max+1，整套设计包统一）
 *   SUPPLEMENT / REVISION → 复用目标批次，绝不自动创建新版本
 */
export async function ingestLocalFiles(sessionId: string, files: LocalUploadFile[]) {
  const session = getWorkflowState().sessions[sessionId]
  if (!session) throw new Error('上传会话不存在')
  const pkg = getWorkflowState().packages[session.designPackageId]
  if (!pkg) throw new Error('设计包不存在')

  const upload = session.packageUploadId ? getWorkflowState().uploads[session.packageUploadId] : undefined
  const batches = getWorkflowState().batches[pkg.id] ?? []
  const batch = resolveTargetBatch({ pkgId: pkg.id, batches, upload, fallbackUploadId: upload?.id ?? sessionId })

  const mainFiles = files.filter((f) => f.kind === 'MAIN').map((f) => f.file)
  const variantFiles = files.filter((f) => f.kind === 'VARIANT').map((f) => f.file)

  const result = await ingestRealFiles({
    designPackageId: pkg.id,
    uploadId: upload?.id ?? sessionId,
    batch,
    actor: upload?.uploaderName ?? pkg.createdBy,
    mainFiles,
    variantFiles,
  })

  applyIngest(pkg.id, session, batch, result)
  return result
}

/** 无真实文件（演示 / 后端已解析）时的入库 */
export function ingestSynthetic(
  sessionId: string,
  input: {
    mains: { position: number; sourceFileName: string; previewUri: string; psdFileName?: string; psdUri?: string; reuseMaterialId?: string }[]
    variants: { fileName: string; previewUri: string }[]
  },
) {
  const session = getWorkflowState().sessions[sessionId]
  if (!session) throw new Error('上传会话不存在')
  const pkg = getWorkflowState().packages[session.designPackageId]
  if (!pkg) throw new Error('设计包不存在')
  const upload = session.packageUploadId ? getWorkflowState().uploads[session.packageUploadId] : undefined
  const batches = getWorkflowState().batches[pkg.id] ?? []
  const batch = resolveTargetBatch({ pkgId: pkg.id, batches, upload, fallbackUploadId: upload?.id ?? sessionId })

  const result = ingestFiles(
    {
      designPackageId: pkg.id,
      uploadId: upload?.id ?? sessionId,
      batch,
      actor: upload?.uploaderName ?? pkg.createdBy,
      mains: input.mains,
      variants: input.variants.map((v) => ({ fileName: v.fileName, uri: v.previewUri })),
    },
    getWorkflowState().materialSeq + 1,
  )
  applyIngest(pkg.id, session, batch, result)
  return result
}

/** 决定本次写入哪个批次：新建 or 补充目标 */
function resolveTargetBatch(input: {
  pkgId: string
  batches: DerivativeBatch[]
  upload?: PackageUpload
  fallbackUploadId: string
}): DerivativeBatch {
  const { pkgId, batches, upload } = input
  const isNewBatch = !upload || upload.uploadType === 'INITIAL' || upload.uploadType === 'NEW_BATCH'

  if (!isNewBatch && upload?.targetBatchId) {
    const target = batches.find((b) => b.id === upload.targetBatchId)
    if (target) return target
  }

  if (!isNewBatch && batches.length) {
    return batches.reduce((max, b) => (b.versionNo > max.versionNo ? b : max), batches[0])
  }

  const versionNo = nextBatchVersionNo(batches)
  return {
    id: nextId('batch'),
    designPackageId: pkgId,
    versionNo,
    code: formatBatchCode(versionNo),
    createdFromUploadId: input.fallbackUploadId,
    createdAt: nowIso(),
    mainMaterialCountAtCreation: 0,
  }
}

function applyIngest(
  pkgId: string,
  session: UploadSession,
  batch: DerivativeBatch,
  result: IngestResult,
) {
  const state = getWorkflowState()
  const batches = state.batches[pkgId] ?? []
  const existingPositions = state.positions[pkgId] ?? []
  const existingVariants = state.variants[pkgId] ?? []

  const positionsByNumber = new Map(existingPositions.map((p) => [p.position, p]))
  for (const position of result.positions) {
    if (!positionsByNumber.has(position.position)) positionsByNumber.set(position.position, position)
  }
  const nextPositions = [...positionsByNumber.values()].sort((a, b) => a.position - b.position)

  const variantsByCode = new Map(existingVariants.map((v) => [v.displayCode, v]))
  for (const variant of result.variants) {
    if (!variantsByCode.has(variant.displayCode)) variantsByCode.set(variant.displayCode, variant)
  }
  const nextVariants = [...variantsByCode.values()]

  const nextAssets = { ...state.assets }
  for (const asset of result.assets) nextAssets[asset.id] = asset

  const nextPsdRevisions = { ...state.psdRevisions }
  for (const revision of result.psdRevisions) nextPsdRevisions[revision.id] = revision

  const nextVariantRevisions = { ...state.variantRevisions }
  for (const revision of result.variantRevisions) nextVariantRevisions[revision.id] = revision

  const nextMaterials = { ...state.materials }
  let materialSeq = state.materialSeq
  for (const material of result.materials) {
    if (!nextMaterials[material.id]) nextMaterials[material.id] = material
    materialSeq = Math.max(materialSeq, Number(material.materialCode.replace(/\D/g, '')) || 0)
  }

  const nextBatches = batches.some((b) => b.id === batch.id) ? batches : [...batches, batch]

  commit({
    assets: nextAssets,
    materials: nextMaterials,
    psdRevisions: nextPsdRevisions,
    variantRevisions: nextVariantRevisions,
    positions: { ...state.positions, [pkgId]: nextPositions },
    variants: { ...state.variants, [pkgId]: nextVariants },
    batches: { ...state.batches, [pkgId]: nextBatches },
    materialSeq,
  })

  touchPackage(pkgId)
  touchSession(session.id, 'PARSED', session.receivedFiles)
  if (session.packageUploadId) touchUpload(session.packageUploadId, { status: 'PARSED', targetBatchId: batch.id })

  if (!batches.some((b) => b.id === batch.id)) {
    appendLog(pkgId, {
      targetType: 'DERIVATIVE_BATCH',
      targetId: batch.id,
      actor: getWorkflowState().packages[pkgId]?.createdBy ?? '系统',
      action: 'CREATE_BATCH',
      summary: `创建上架版本 ${batch.code}（整套设计包统一版本，非按素材自增）`,
    })
  }
}

// ---------------------------------------------------------------- 自动匹配

/**
 * 本地 Mock 模式的同名配对（与后端规则一致：只按文件名同名，不看图片内容）。
 *
 * 真实后端模式下配对由 POST /api/uploads/{id}/pair 完成，这里只服务无后端时的 UI 预览。
 */
export function runPackagePairing(pkgId: string): PairingView[] {
  const state = getWorkflowState()
  const positions = state.positions[pkgId] ?? []
  const variants = (state.variants[pkgId] ?? []).filter((v) => !v.deleted)

  const variantAssetOf = (variant: MaterialVariant) => {
    const revision = currentVariantRevision(variant)
    return revision ? state.assets[revision.assetId] : undefined
  }

  const assetByPosition = new Map<string, Asset | undefined>()
  for (const position of positions) {
    const key = pairKeyOfFileName(position.sourceFileName)
    const matched = variants.find(
      (variant) =>
        key !== '' && pairKeyOfFileName(variantAssetOf(variant)?.originalFilename ?? '') === key,
    )
    assetByPosition.set(position.id, matched ? variantAssetOf(matched) : undefined)
  }

  const results: PairingView[] = positions.map((position) => {
    const key = pairKeyOfFileName(position.sourceFileName)
    const asset = assetByPosition.get(position.id)
    return {
      id: `pair-${pkgId}-${position.position}`,
      designPackageId: pkgId,
      packageUploadId: position.createdFromUploadId ?? '',
      designPackageMaterialId: position.id,
      position: position.position,
      pairKey: key,
      materialId: position.materialId,
      materialCode: position.materialCode ?? '',
      materialName: state.materials[position.materialId]?.name ?? '',
      mainPreviewUri: position.previewUri,
      mainSourceFileName: position.sourceFileName,
      variantAssetId: asset?.id,
      variantPreviewUri: asset?.storageKey,
      variantFileName: asset?.originalFilename,
      variantPairKey: asset ? pairKeyOfFileName(asset.originalFilename) : undefined,
      source: 'NAME',
      status: asset ? 'PAIRED' : 'UNPAIRED',
      main: position,
      options: variants.map((variant) => {
        const optionAsset = variantAssetOf(variant)
        const assetId = optionAsset?.id ?? variant.id
        const owner = positions.find(
          (candidate) => assetByPosition.get(candidate.id)?.id === assetId,
        )
        return {
          assetId,
          originalFilename: optionAsset?.originalFilename ?? variant.displayCode,
          pairKey: pairKeyOfFileName(optionAsset?.originalFilename ?? ''),
          previewUri: optionAsset?.storageKey ?? '',
          sizeBytes: optionAsset?.sizeBytes ?? 0,
          occupiedByPosition: owner?.position ?? null,
        }
      }),
    } satisfies PairingView
  })

  commit({ pairings: { ...state.pairings, [pkgId]: results } })
  recomputePackage(pkgId, { pairings: results, sessionStage: 'MATCHED' })
  return results
}

/** Mock：确认某个位置的配对 */
export function confirmPairing(pkgId: string, pairingId: string, actor: string) {
  const state = getWorkflowState()
  const next = (state.pairings[pkgId] ?? []).map((item) =>
    item.id === pairingId && item.variantAssetId
      ? { ...item, status: 'CONFIRMED' as const, confirmedBy: actor, confirmedAt: nowIso() }
      : item,
  )
  commit({ pairings: { ...state.pairings, [pkgId]: next } })
}

/** Mock：确认整包配对 */
export function confirmAllPairings(pkgId: string, actor: string): number {
  const state = getWorkflowState()
  let count = 0
  const next = (state.pairings[pkgId] ?? []).map((item) => {
    if (item.status === 'CONFIRMED' || !item.variantAssetId) return item
    count += 1
    return { ...item, status: 'CONFIRMED' as const, confirmedBy: actor, confirmedAt: nowIso() }
  })
  commit({ pairings: { ...state.pairings, [pkgId]: next } })
  return count
}

/** Mock：人工改配到本次上传的另一张副图（一个副图只能属于一个主素材） */
export function reassignPairing(
  pkgId: string,
  pairingId: string,
  variantAssetId: string,
  actor: string,
) {
  const state = getWorkflowState()
  const list = state.pairings[pkgId] ?? []
  const next = list.map((item) => {
    if (item.id === pairingId) {
      const option = item.options.find((o) => o.assetId === variantAssetId)
      return {
        ...item,
        variantAssetId,
        variantPreviewUri: option?.previewUri,
        variantFileName: option?.originalFilename,
        source: 'MANUAL' as const,
        status: 'PAIRED' as const,
        confirmedBy: undefined,
        confirmedAt: undefined,
      }
    }
    if (item.variantAssetId === variantAssetId) {
      return {
        ...item,
        variantAssetId: undefined,
        variantPreviewUri: undefined,
        variantFileName: undefined,
        source: 'MANUAL' as const,
        status: 'UNPAIRED' as const,
      }
    }
    return item
  })
  void actor
  commit({ pairings: { ...state.pairings, [pkgId]: next } })
}

function recomputePackage(
  pkgId: string,
  options: {
    pairings?: PairingView[]
    sessionStage?: UploadSession['stage']
  } = {},
) {
  const state = getWorkflowState()
  const pkg = state.packages[pkgId]
  if (!pkg) return
  const batches = state.batches[pkgId] ?? []
  const positions = state.positions[pkgId] ?? []
  const variants = state.variants[pkgId] ?? []
  const pairings = options.pairings ?? state.pairings[pkgId] ?? []
  const batch = currentBatchOf(pkgId)
  if (!batch) return

  const countCheck = checkMaterialCount(
    positions.filter((p) => p.materialId).length,
    variants.filter((v) => !v.deleted).length,
  )
  const coverage = computeVersionGap(batch, positions, variants)
  const session = Object.values(state.sessions).find((s) => s.designPackageId === pkgId && s.stage !== 'SUBMITTED')
    ?? Object.values(state.sessions).find((s) => s.designPackageId === pkgId)
  const operators = operatorsOfPackage(pkgId)

  const incoming = collectAnomalies({
    pkg,
    batch,
    batches,
    positions,
    variants,
    pairings,
    coverage,
    countCheck,
    operatorId: operators[0]?.operatorId ?? session?.operatorId,
    sessionStage: options.sessionStage ?? session?.stage ?? 'PARSED',
    assets: state.assets,
  })

  commit({
    pairings: { ...state.pairings, [pkgId]: pairings },
    anomalies: { ...state.anomalies, [pkgId]: upsertAnomalies(state.anomalies[pkgId] ?? [], incoming) },
  })
  touchPackage(pkgId)
}

/** 异常：非 RESOLVED → RESOLVED（保留 firstOccurredAt，写入 resolvedAt） */
export function resolveAnomaly(pkgId: string, anomalyId: string, actor: string) {
  const state = getWorkflowState()
  const list = state.anomalies[pkgId] ?? []
  const target = list.find((a) => a.id === anomalyId)
  if (!target) return
  commit({
    anomalies: {
      ...state.anomalies,
      [pkgId]: list.map((a) => (a.id === anomalyId ? { ...a, status: 'RESOLVED', resolvedAt: nowIso() } : a)),
    },
  })
  appendLog(pkgId, {
    targetType: 'DESIGN_PACKAGE',
    targetId: pkgId,
    actor,
    action: 'RESOLVE_ANOMALY',
    summary: `处理异常：${target.message}`,
    after: target.anomalyKey,
  })
}

/** 异常：手工重新打开 */
export function reopenAnomaly(pkgId: string, anomalyId: string, actor: string) {
  const state = getWorkflowState()
  const list = state.anomalies[pkgId] ?? []
  const target = list.find((a) => a.id === anomalyId)
  if (!target) return
  commit({
    anomalies: {
      ...state.anomalies,
      [pkgId]: list.map((a) => (a.id === anomalyId
        ? { ...a, status: 'REOPENED', resolvedAt: undefined, lastOccurredAt: nowIso() }
        : a)),
    },
  })
  appendLog(pkgId, {
    targetType: 'DESIGN_PACKAGE',
    targetId: pkgId,
    actor,
    action: 'REOPEN_ANOMALY',
    summary: `重新打开异常：${target.message}`,
    before: target.anomalyKey,
  })
}

// ---------------------------------------------------------------- 补素材（含补派发）

/**
 * 第十四条：补充当前版本缺失的素材。
 * PackageUpload.type = SUPPLEMENT，targetBatchId = 目标批次，绝不自动创建新版本。
 *
 * 第四部分：如果该版本已经派发过，不做静默修改，而是给已有任务追加 deliveryRound+1 的交付项。
 */
export function supplementCurrentVersion(
  pkgId: string,
  input: { variants: { fileName: string; previewUri: string }[]; actor: string },
) {
  const state = getWorkflowState()
  const pkg = state.packages[pkgId]
  if (!pkg) return
  const batch = currentBatchOf(pkgId)
  if (!batch) return
  const positions = state.positions[pkgId] ?? []
  const variants = state.variants[pkgId] ?? []

  const nextAssets = { ...state.assets }
  const nextVariantRevisions = { ...state.variantRevisions }

  const created: MaterialVariant[] = input.variants.map((v, index) => {
    const parsed = Number(v.fileName.replace(/\.[a-zA-Z0-9]+$/, '').split('-')[0])
    const position = Number.isFinite(parsed) ? parsed : positions.length + index + 1
    const linked = positions.find((p) => p.position === position) ?? positions[index] ?? positions[0]
    const variantId = nextId('variant')

    // 文件 → Asset（生产在此写入 blake3 / phash）
    const asset = createAsset({
      storageKey: v.previewUri,
      originalFilename: v.fileName,
      mimeType: 'image/jpeg',
      sizeBytes: 0,
      createdBy: input.actor,
    })
    nextAssets[asset.id] = asset

    const revision = makeVariantRevision({
      variantId,
      revisionNo: 1,
      assetId: asset.id,
      actor: input.actor,
    })
    nextVariantRevisions[revision.id] = revision

    return {
      id: variantId,
      materialId: linked?.materialId ?? '',
      designPackageMaterialId: linked?.id ?? '',
      batchId: batch.id,
      displayCode: formatVariantCode(linked?.position ?? position, batch.versionNo),
      currentRevisionId: revision.id,
      revisions: [revision],
      tags: [],
      deleted: false,
      createdAt: nowIso(),
      updatedAt: nowIso(),
    }
  })

  commit({
    assets: nextAssets,
    variantRevisions: nextVariantRevisions,
    variants: { ...state.variants, [pkgId]: [...variants, ...created] },
  })

  appendLog(pkgId, {
    targetType: 'DERIVATIVE_BATCH',
    targetId: batch.id,
    actor: input.actor,
    action: 'SUPPLEMENT_V1',
    summary: `补充 ${batch.code} 素材 ${created.map((c) => c.displayCode).join('、')}（未创建新版本）`,
  })

  // 已派发任务：追加交付轮次，不修改历史交付
  appendSupplementDelivery(pkgId, batch.id, created, input.actor)

  touchPackage(pkgId)
  runPackagePairing(pkgId)
}

/** 给该批次所有进行中的派发任务追加一轮交付 */
function appendSupplementDelivery(
  pkgId: string,
  batchId: string,
  created: MaterialVariant[],
  actor: string,
) {
  if (!created.length) return
  const state = getWorkflowState()
  const targets = Object.values(state.tasks).filter(
    (t) => t.batchId === batchId && (t.status === 'ACTIVE' || t.status === 'RECEIVED' || t.status === 'COMPLETED'),
  )
  if (!targets.length) return

  const nextTasks = { ...state.tasks }
  for (const task of targets) {
    const { task: updated, deliveryRound } = appendDistributionDelivery({ task, variants: created })
    nextTasks[updated.id] = updated
    appendLog(pkgId, {
      targetType: 'DISTRIBUTION',
      targetId: task.id,
      actor,
      action: 'SUPPLEMENT_DISPATCH',
      summary: `补充派发${created.length}个副素材（${created.map((c) => c.displayCode).join('、')}）给 ${task.operatorName}，第 ${deliveryRound} 次交付`,
      after: created.map((c) => c.displayCode).join('、'),
    })
  }
  commit({ tasks: nextTasks })
}

// ---------------------------------------------------------------- Revision

/**
 * 第八 / 三十一条：美工修正副素材图片时新建 Revision，
 * 业务版本号（1-2）不变，旧 Revision 保留，不覆盖删除。
 * 已派发任务的交付快照指向旧 Revision，因此历史运营实际收到的图片不变。
 */
export function addVariantRevision(
  pkgId: string,
  variantId: string,
  input: { fileName: string; previewUri: string; actor: string; note?: string },
) {
  const state = getWorkflowState()
  const variants = state.variants[pkgId] ?? []
  const target = variants.find((v) => v.id === variantId)
  if (!target) return
  const revisions = variantRevisionsOf(variantId)
  const revisionNo = nextRevisionNo(revisions)

  const asset = createAsset({
    storageKey: input.previewUri,
    originalFilename: input.fileName,
    mimeType: 'image/jpeg',
    sizeBytes: 0,
    createdBy: input.actor,
  })
  const revision = makeVariantRevision({
    variantId,
    revisionNo,
    assetId: asset.id,
    actor: input.actor,
    note: input.note,
  })

  const nextVariants = variants.map((v) => (v.id === variantId
    ? { ...v, currentRevisionId: revision.id, revisions: [...v.revisions, revision], updatedAt: nowIso() }
    : v))

  commit({
    assets: { ...state.assets, [asset.id]: asset },
    variantRevisions: { ...state.variantRevisions, [revision.id]: revision },
    variants: { ...state.variants, [pkgId]: nextVariants },
  })

  appendLog(pkgId, {
    targetType: 'MATERIAL_VARIANT',
    targetId: variantId,
    actor: input.actor,
    action: 'VARIANT_REVISION',
    summary: `${target.displayCode} 新增 Revision ${revisionNo}（业务版本仍为 ${target.displayCode}，旧 Revision 保留，历史派发快照不变）`,
    before: `Revision ${revisions.length}`,
    after: `Revision ${revisionNo}`,
  })
  recomputePackage(pkgId)
}

/**
 * 主素材 PSD 修订：Material 也有 Revision 能力，旧 PSD 保留。
 * 该动作跨设计包（同一 MAT 可能被多个包复用），因此只记 MATERIAL 级维护记录。
 */
export function addMaterialPsdRevision(
  materialId: string,
  input: { fileName: string; uri: string; actor: string; note?: string },
) {
  const state = getWorkflowState()
  const material = state.materials[materialId]
  if (!material) return
  const revisions = psdRevisionsOf(materialId)
  const revisionNo = nextRevisionNo(revisions)

  const asset = createAsset({
    storageKey: input.uri,
    originalFilename: input.fileName,
    mimeType: 'image/vnd.adobe.photoshop',
    sizeBytes: 0,
    createdBy: input.actor,
  })
  const revision = makePsdRevision({
    materialId,
    revisionNo,
    assetId: asset.id,
    actor: input.actor,
    note: input.note,
  })

  const nextMaterial: Material = {
    ...material,
    currentPsdRevisionId: revision.id,
    updatedAt: nowIso(),
  }

  commit({
    assets: { ...state.assets, [asset.id]: asset },
    psdRevisions: { ...state.psdRevisions, [revision.id]: revision },
    materials: { ...state.materials, [materialId]: nextMaterial },
  })

  // 跨包动作：不绑定具体设计包上下文
  appendLog(null, {
    targetType: 'MATERIAL',
    targetId: materialId,
    actor: input.actor,
    action: 'MATERIAL_REVISION',
    summary: `${material.materialCode} 新增 PSD Revision ${revisionNo}（旧 PSD 保留）`,
    before: `Revision ${revisions.length}`,
    after: `Revision ${revisionNo}`,
  })

  for (const [pkgId, positions] of Object.entries(state.positions)) {
    if (positions.some((p) => p.materialId === materialId)) touchPackage(pkgId)
  }
}

// ---------------------------------------------------------------- 提交与派发

export function submitAndDispatch(pkgId: string, actor: string): { error?: string; taskId?: string } {
  const state = getWorkflowState()
  const pkg = state.packages[pkgId]
  if (!pkg) return { error: '设计包不存在' }

  const session = Object.values(state.sessions)
    .filter((s) => s.designPackageId === pkgId)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))[0]
  const pairings = state.pairings[pkgId] ?? []
  const positions = (state.positions[pkgId] ?? []).filter((p) => p.materialId)
  const variants = state.variants[pkgId] ?? []

  // 第十三条：数量不一致禁止直接提交
  const countCheck = checkMaterialCount(positions.length, variants.filter((v) => !v.deleted).length)
  if (countCheck.blocked) return { error: countCheck.messages.join(' ') }

  const operatorId = operatorsOfPackage(pkgId)[0]?.operatorId ?? session?.operatorId
  const operatorName = operatorsOfPackage(pkgId)[0]?.operatorName ?? session?.operatorName
  if (!operatorId || !operatorName) return { error: '首次上传必须选择归属运营后才能派发。' }

  const missing = pairings.filter((p) => p.status === 'UNPAIRED')
  if (missing.length) {
    return {
      error: `仍有 ${missing.length} 个位置缺少同名副图（位置 ${missing
        .map((p) => p.position)
        .join('、')}），请补齐后再派发。`,
    }
  }

  const batch = currentBatchOf(pkgId)
  if (!batch) return { error: '缺少上架版本' }

  const duplicate = findActiveDistribution(Object.values(state.tasks), batch.id, operatorId)
  if (duplicate) return { error: `该版本已经派发给${operatorName}。`, taskId: duplicate.id }

  const task = createDistributionTask({
    pkg,
    batch,
    variants,
    designerName: pkg.createdBy,
    operatorId,
    operatorName,
    remark: pkg.remark,
  })

  commit({ tasks: { ...state.tasks, [task.id]: task } })
  if (session) {
    touchSession(session.id, 'SUBMITTED')
    if (session.packageUploadId) touchUpload(session.packageUploadId, { status: 'SUBMITTED', targetBatchId: batch.id })
  }

  appendLog(pkgId, {
    targetType: 'DISTRIBUTION',
    targetId: task.id,
    actor,
    action: 'DISPATCH',
    summary: `派发给 ${operatorName}，版本 ${batch.code}，第 1 次交付 ${task.items.length} 张副素材（复用同一套底层素材，不复制图片）`,
  })

  return { taskId: task.id }
}

/**
 * 第十五条：新增派发给其他运营，复用同一套底层副素材。
 * 第九条：同一 Batch + 同一运营 不允许存在两个进行中的任务。
 */
export function addDistribution(
  pkgId: string,
  operatorId: string,
  operatorName: string,
  actor: string,
): { error?: string; taskId?: string } {
  const state = getWorkflowState()
  const pkg = state.packages[pkgId]
  if (!pkg) return { error: '设计包不存在' }
  const batch = currentBatchOf(pkgId)
  if (!batch) return { error: '缺少上架版本' }

  const duplicate = findActiveDistribution(Object.values(state.tasks), batch.id, operatorId)
  if (duplicate) return { error: `该版本已经派发给${operatorName}。`, taskId: duplicate.id }

  const task = createDistributionTask({
    pkg,
    batch,
    variants: state.variants[pkgId] ?? [],
    designerName: pkg.createdBy,
    operatorId,
    operatorName,
    remark: pkg.remark,
  })
  commit({ tasks: { ...state.tasks, [task.id]: task } })
  appendLog(pkgId, {
    targetType: 'DISTRIBUTION',
    targetId: task.id,
    actor,
    action: 'DISPATCH',
    summary: `新增派发给 ${operatorName}（版本 ${batch.code}，复用同一套底层副素材）`,
  })
  return { taskId: task.id }
}

/** 取消派发（取消后才允许重新派发给同一运营） */
export function cancelDistribution(taskId: string, actor: string) {
  const state = getWorkflowState()
  const task = state.tasks[taskId]
  if (!task) return
  commit({ tasks: { ...state.tasks, [taskId]: { ...task, status: 'CANCELLED', cancelledAt: nowIso() } } })
  appendLog(task.designPackageId, {
    targetType: 'DISTRIBUTION',
    targetId: taskId,
    actor,
    action: 'CANCEL_DISTRIBUTION',
    summary: `取消派发给 ${task.operatorName}（版本 ${task.versionCode}）`,
  })
}

// ---------------------------------------------------------------- 运营端

export function receiveTask(taskId: string, actor: string) {
  const state = getWorkflowState()
  const task = state.tasks[taskId]
  if (!task) return
  commit({ tasks: { ...state.tasks, [taskId]: { ...task, status: 'RECEIVED', receivedAt: nowIso() } } })
  appendLog(task.designPackageId, {
    targetType: 'DISTRIBUTION',
    targetId: taskId,
    actor,
    action: 'RECEIVE',
    summary: `接收素材（版本 ${task.versionCode}，${task.items.length} 项交付）`,
  })
}

export function bindAsins(
  taskId: string,
  input: { parentAsin: string; childrenText: string; site?: MarketplaceSiteCode; actor: string },
) {
  const state = getWorkflowState()
  const task = state.tasks[taskId]
  if (!task) return { error: '派发任务不存在' }
  const result = buildTaskAsinBinding(task, input)
  if (result.error) return { error: result.error }
  commit({ tasks: { ...state.tasks, [taskId]: result.task } })
  for (const log of result.logs) {
    const key = task.designPackageId ?? '__global__'
    commit({ logs: { ...getWorkflowState().logs, [key]: [...(getWorkflowState().logs[key] ?? []), log] } })
  }
  return {}
}

function buildTaskAsinBinding(
  task: DistributionTask,
  input: { parentAsin: string; childrenText: string; site?: MarketplaceSiteCode; actor: string },
): { task: DistributionTask; logs: ActivityLog[]; error?: string } {
  const parsed = parseAsinList(input.childrenText)
  const parent = input.parentAsin.trim().toUpperCase()
  if (!/^B0[A-Z0-9]{8}$/.test(parent)) {
    return { task, logs: [], error: 'Parent ASIN 格式应为 B0 + 8 位字母或数字。' }
  }
  if (parsed.asins.length === 0) return { task, logs: [], error: '请至少填写 1 个有效的 Child ASIN。' }
  if (parsed.invalid.length) {
    return { task, logs: [], error: `存在格式不正确的 Child ASIN：${parsed.invalid.slice(0, 5).join('、')}` }
  }

  const logs: ActivityLog[] = []
  const previousParent = task.parentAsin?.asin
  const site = input.site ?? task.parentAsin?.site
  const previousChildren = new Set(task.children.map((c) => c.asin))

  if (previousParent && previousParent !== parent) {
    logs.push(makeLog({
      designPackageId: task.designPackageId,
      targetType: 'PARENT_ASIN',
      targetId: parent,
      actor: input.actor,
      action: 'UPDATE_ASIN',
      summary: '修改 Parent ASIN',
      before: previousParent,
      after: parent,
    }))
  } else if (!previousParent) {
    logs.push(makeLog({
      designPackageId: task.designPackageId,
      targetType: 'PARENT_ASIN',
      targetId: parent,
      actor: input.actor,
      action: 'PARENT_ASIN',
      summary: '回填 Parent ASIN',
      after: parent,
    }))
  }

  const added = parsed.asins.filter((asin) => !previousChildren.has(asin))
  const removed = task.children.filter((c) => !parsed.asins.includes(c.asin)).map((c) => c.asin)
  if (added.length) {
    logs.push(makeLog({
      designPackageId: task.designPackageId,
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
      designPackageId: task.designPackageId,
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
      ...task,
      parentAsin: { asin: parent, site, listingUrl: buildAsinUrl(parent, site) },
      children: parsed.asins.map((asin) => ({
        asin,
        site,
        listingUrl: buildAsinUrl(asin, site),
        // 预留：Child 单独素材覆盖（本轮不做 UI）
        overrideVariantIds: task.children.find((c) => c.asin === asin)?.overrideVariantIds,
      })),
      status: 'COMPLETED',
      completedAt: nowIso(),
    },
    logs,
  }
}

// ---------------------------------------------------------------- 视图查询（DTO 聚合）

function toVariantViews(pkgId: string, variants: MaterialVariant[]): VariantView[] {
  const state = getWorkflowState()
  const batches = state.batches[pkgId] ?? []
  const pkg = state.packages[pkgId]
  return variants
    .filter((v) => !v.deleted)
    .map((variant) => {
      const batch = batches.find((b) => b.id === variant.batchId)
      const revision = currentVariantRevision(variant)
      const asset = revision ? state.assets[revision.assetId] : undefined
      const material = state.materials[variant.materialId]
      return {
        ...variant,
        batchVersionNo: variantVersionNo(variant, batches),
        versionCode: batch?.code ?? '-',
        packageName: pkg?.name ?? '',
        materialCode: material?.materialCode ?? '',
        currentRevision: revision?.revisionNo ?? 0,
        imageUri: asset?.storageKey ?? '',
        fileName: asset?.originalFilename ?? '',
      }
    })
}

export function getPackageOverview(pkgId: string): PackageOverview | null {
  const state = getWorkflowState()
  const pkg = state.packages[pkgId]
  if (!pkg) return null

  const uploads = Object.values(state.uploads)
    .filter((u) => u.designPackageId === pkgId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  const upload = uploads[0]
  // 会话查找对两种后端都成立：
  //   Mock：sessions key = upload.uploadSessionId
  //   真实后端：sessions 按 packageUploadId 建立索引
  const session = upload
    ? (Object.values(state.sessions).find((s) => s.packageUploadId === upload.id)
      ?? state.sessions[upload.uploadSessionId])
    : undefined
  if (!upload || !session) return null

  // Mock 模式的覆盖度兜底需要一个「当前版本」概念；真实批次优先。
  const batch = currentBatchOf(pkgId) ?? {
    id: `placeholder-batch-${pkgId}`,
    designPackageId: pkgId,
    versionNo: 1,
    code: 'V1',
    createdFromUploadId: upload.id,
    createdAt: pkg.createdAt,
    mainMaterialCountAtCreation: 0,
  }

  const positions = state.positions[pkgId] ?? []
  const variants = state.variants[pkgId] ?? []
  const materialsById: Record<string, Material> = {}
  const assetsById: Record<string, Asset> = {}
  for (const position of positions) {
    const material = state.materials[position.materialId]
    if (!material) continue
    materialsById[material.id] = material
    const preview = state.assets[material.previewAssetId]
    if (preview) assetsById[preview.id] = preview
    if (material.currentPsdRevisionId) {
      const psd = state.psdRevisions[material.currentPsdRevisionId]
      const psdAsset = psd ? state.assets[psd.assetId] : undefined
      if (psdAsset) assetsById[psdAsset.id] = psdAsset
    }
  }
  for (const variant of variants) {
    const revision = currentVariantRevision(variant)
    const asset = revision ? state.assets[revision.assetId] : undefined
    if (asset) assetsById[asset.id] = asset
  }

  const countCheck = state.countChecks[pkgId] ?? checkMaterialCount(
    positions.filter((p) => p.materialId).length,
    variants.filter((v) => !v.deleted).length,
  )
  const distributions = Object.values(state.tasks)
    .filter((t) => t.designPackageId === pkgId)
    .map(toDistributionTaskView)

  // 真实批次优先；Mock 模式没有版本记录时保持 null（不要用占位批次冒充已生成）
  const realBatches = state.batches[pkgId] ?? []
  const currentBatch = realBatches.length ? realBatches[realBatches.length - 1] : null

  return {
    pkg,
    upload,
    uploads,
    session,
    mainMaterials: positions,
    materialsById,
    assetsById,
    variants,
    variantViews: toVariantViews(pkgId, variants),
    currentBatch,
    batches: realBatches,
    pairings: state.pairings[pkgId] ?? [],
    pairingUploadId: state.uploads[upload?.id ?? '']?.id,
    anomalies: (state.anomalies[pkgId] ?? []).filter((a) => a.status !== 'RESOLVED'),
    // 未解决的阻断异常：非空时禁止生成版本（第三十一条）
    blockingAnomalies: (state.anomalies[pkgId] ?? []).filter(
      (a) => a.blocking && a.status !== 'RESOLVED',
    ),
    logs: [...(state.logs[pkgId] ?? [])].sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
    // 后端上传结果（主素材/PSD/副图）。Mock 模式下后端未接入，回退为空结构。
    uploadedFiles: state.uploadedFiles[pkgId] ?? EMPTY_UPLOADED_FILES,
    // ---- 聚合字段（DTO，不在 DesignPackage 上）----
    operators: operatorsOfPackage(pkgId),
    operatorId: operatorsOfPackage(pkgId)[0]?.operatorId ?? session.operatorId,
    operatorName: operatorsOfPackage(pkgId)[0]?.operatorName ?? session.operatorName,
    mainMaterialCount: countCheck.mainCount,
    variantCount: countCheck.variantCount,
    currentBatchNo: currentBatch ? currentBatch.versionNo : 0,
    countCheck,
    coverage: state.coverages[pkgId] ?? computeVersionGap(batch, positions, variants),
    submitted: session.stage === 'SUBMITTED',
    distributions,
  }
}

export function getTask(taskId: string): DistributionTask | null {
  return getWorkflowState().tasks[taskId] ?? null
}

export function getTaskOverview(taskId: string): TaskOverview | null {
  const state = getWorkflowState()
  const task = state.tasks[taskId]
  if (!task) return null
  const pkg = state.packages[task.designPackageId]
  const batches = state.batches[task.designPackageId] ?? []
  const batch = batches.find((b) => b.id === task.batchId)
  const upload = Object.values(state.uploads)
    .filter((u) => u.designPackageId === task.designPackageId)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]

  // 按交付快照展开：每个 item 指向当时那个 Revision
  const variants: VariantView[] = task.items
    .map((item) => {
      const variant = (state.variants[task.designPackageId] ?? []).find((v) => v.id === item.variantId)
      if (!variant || variant.deleted) return null
      const revision = state.variantRevisions[item.revisionId] ?? currentVariantRevision(variant)
      const asset = revision ? state.assets[revision.assetId] : undefined
      const material = state.materials[variant.materialId]
      return {
        ...variant,
        batchVersionNo: batch?.versionNo ?? null,
        versionCode: batch?.code ?? '-',
        packageName: pkg?.name ?? '',
        materialCode: material?.materialCode ?? '',
        currentRevision: revision?.revisionNo ?? 0,
        imageUri: asset?.storageKey ?? '',
        fileName: asset?.originalFilename ?? '',
      } satisfies VariantView
    })
    .filter((v): v is VariantView => Boolean(v))

  const assetsById: Record<string, Asset> = {}
  for (const variant of variants) {
    const asset = Object.values(state.assets).find((a) => a.storageKey === variant.imageUri)
    if (asset) assetsById[asset.id] = asset
  }

  return {
    task: toDistributionTaskView(task),
    pkg,
    batch,
    upload,
    variants,
    assetsById,
    deliveries: groupDeliveries(task),
    logs: [...(state.logs[task.designPackageId] ?? [])].sort((a, b) => a.createdAt.localeCompare(b.createdAt)),
  }
}

export function getTaskVariants(task: DistributionTask): MaterialVariant[] {
  const variants = getWorkflowState().variants[task.designPackageId] ?? []
  const variantIds = new Set(task.items.map((item) => item.variantId))
  return variants.filter((v) => !v.deleted && variantIds.has(v.id))
}

/** 素材中心：全部主素材视图（每个 MAT 一条） */
export function getMaterials(): MaterialView[] {
  const state = getWorkflowState()
  const out: MaterialView[] = []
  const seen = new Set<string>()

  for (const [pkgId, positions] of Object.entries(state.positions)) {
    const pkg = state.packages[pkgId]
    for (const position of positions) {
      if (!position.materialId || seen.has(position.materialId)) continue
      const material = state.materials[position.materialId]
      if (!material) continue
      seen.add(material.id)
      out.push(toMainMaterialView({
        material,
        asset: state.assets[material.previewAssetId],
        position,
        designPackage: pkg,
        designCount: designCountOf(material.id),
        asinCount: asinCountOf(material.id),
        variantCount: variantCountOf(material.id),
        variantPreviews: variantPreviewsOf(material.id),
        tags: material.tags,
      }))
    }
  }
  return out.sort((a, b) => a.id.localeCompare(b.id))
}

/** 素材中心：全部副素材视图 */
export function getVariantMaterials(): MaterialView[] {
  const state = getWorkflowState()
  const out: MaterialView[] = []
  for (const [pkgId, variants] of Object.entries(state.variants)) {
    const pkg = state.packages[pkgId]
    const batches = state.batches[pkgId] ?? []
    for (const variant of variants) {
      if (variant.deleted) continue
      const revision = currentVariantRevision(variant)
      out.push(toVariantMaterialView({
        variant,
        asset: revision ? state.assets[revision.assetId] : undefined,
        batch: batches.find((b) => b.id === variant.batchId),
        mainMaterial: state.materials[variant.materialId],
        designPackage: pkg,
      }))
    }
  }
  return out.sort((a, b) => a.id.localeCompare(b.id, 'zh-Hans-CN', { numeric: true }))
}

export function getMaterial(materialCode: string): MaterialView | null {
  return getMaterials().find((m) => m.id === materialCode) ?? null
}

/** 主素材详情：跨设计包收集该 MAT 的全部副素材（副素材 Tab） */
export function getVariantsOfMaterial(materialCode: string): MaterialVariantRow[] {
  const state = getWorkflowState()
  const out: MaterialVariantRow[] = []
  for (const [pkgId, variants] of Object.entries(state.variants)) {
    const pkg = state.packages[pkgId]
    const batches = state.batches[pkgId] ?? []
    for (const variant of variants) {
      if (variant.deleted) continue
      if (state.materials[variant.materialId]?.materialCode !== materialCode) continue
      out.push(toMaterialVariantRow({
        variant,
        designPackage: pkg,
        batches,
        assets: state.assets,
        revisions: variantRevisionsOf(variant.id),
      }))
    }
  }
  return out.sort((a, b) => a.variant.displayCode.localeCompare(b.variant.displayCode, 'zh-Hans-CN', { numeric: true }))
}

/** 某 MAT 被哪些设计包的哪个位置引用 */
export function getPositionsOfMaterial(materialCode: string) {
  const state = getWorkflowState()
  const out: { pkgId: string; packageName: string; packageCode: string; position: number; psdUri?: string }[] = []
  for (const [pkgId, positions] of Object.entries(state.positions)) {
    const pkg = state.packages[pkgId]
    for (const position of positions) {
      if (position.materialCode !== materialCode) continue
      const material = state.materials[position.materialId]
      const psd = material ? currentPsdRevision(material, Object.values(state.psdRevisions)) : null
      out.push({
        pkgId,
        packageName: pkg?.name ?? pkgId,
        packageCode: pkg?.code ?? pkgId,
        position: position.position,
        psdUri: psd ? state.assets[psd.assetId]?.storageKey : undefined,
      })
    }
  }
  return out
}

/** 素材详情（含 PSD Revision 历史，DTO 已展开 asset） */
export function getMaterialDetail(materialCode: string): MaterialDetailView | null {
  const state = getWorkflowState()
  const material = allMaterials().find((m) => m.materialCode === materialCode)
  if (!material) return null
  const revisions = psdRevisionsOf(material.id)
  const current = currentPsdRevision(material, revisions)
  return {
    material,
    previewAsset: state.assets[material.previewAssetId],
    psdAsset: current ? state.assets[current.assetId] : undefined,
    currentPsdRevision: current,
    psdRevisions: revisions.map((r) => ({ ...r, asset: state.assets[r.assetId] })),
    positions: getPositionsOfMaterial(materialCode),
    variants: getVariantsOfMaterial(materialCode),
    designCount: designCountOf(material.id),
    asinCount: asinCountOf(material.id),
  }
}

/** 素材中心：按设计包折叠的分组（DTO 聚合，不再读 DesignPackage 缓存字段） */
export function getDesignPackageGroups(): DesignPackageGroupView[] {
  const state = getWorkflowState()
  return Object.keys(state.packages)
    .map((pkgId) => {
      const pkg = state.packages[pkgId]
      const positions = (state.positions[pkgId] ?? []).slice().sort((a, b) => a.position - b.position)
      const variants = (state.variants[pkgId] ?? []).filter((v) => !v.deleted)
      const batches = state.batches[pkgId] ?? []
      const batch = currentBatchOf(pkgId)
      const latestUpload = Object.values(state.uploads)
        .filter((u) => u.designPackageId === pkgId)
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0]

      const positionViews: DesignPackageMaterialView[] = positions.map((position) => {
        const material = state.materials[position.materialId]
        const psd = material ? currentPsdRevision(material, Object.values(state.psdRevisions)) : null
        return {
          position,
          material: material ?? ({} as Material),
          previewUri: state.assets[material?.previewAssetId ?? '']?.storageKey ?? position.previewUri,
          psdUri: psd ? state.assets[psd.assetId]?.storageKey : undefined,
          variantCount: variants.filter((v) => v.designPackageMaterialId === position.id).length,
        }
      })

      return {
        pkg,
        mainMaterialCount: positions.filter((p) => p.materialId).length,
        variantCount: variants.length,
        batchCount: batches.length,
        currentBatchNo: batch?.versionNo ?? 0,
        latestUpload,
        operators: operatorsOfPackage(pkgId),
        positions: positionViews,
      }
    })
    .filter((group) => group.positions.length > 0)
}

export function getPackageMaterialsForDisplay(pkgId: string): DesignPackageMaterial[] {
  return getWorkflowState().positions[pkgId] ?? []
}

export function allocateMaterialCodes(count: number): string[] {
  const start = getWorkflowState().materialSeq + 1
  return Array.from({ length: count }, (_, i) => formatMaterialCode(start + i))
}

/** 本次上传可选的副图（仅 DTO，不入库）——按文件名排序，无相似度打分 */
export function getPairingOptions(pkgId: string): PairingOption[] {
  const pairings = getWorkflowState().pairings[pkgId] ?? []
  return pairings[0]?.options ?? []
}

export function getMaterialEntity(materialId: string): Material | null {
  return getWorkflowState().materials[materialId] ?? null
}

export function getAsset(assetId: string): Asset | null {
  return getWorkflowState().assets[assetId] ?? null
}

/** Asset → AssetRef（供 UI 直接消费） */
export function getAssetRef(assetId: string) {
  return toAssetRef(getWorkflowState().assets[assetId])
}

/** 跨设计包的素材维护记录（按 targetType + targetId 查询） */
export function getLogsByTarget(targetType: ActivityTargetType, targetId: string): ActivityLog[] {
  const state = getWorkflowState()
  const all = Object.values(state.logs).flat()
  return all
    .filter((l) => l.targetType === targetType && l.targetId === targetId)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
}

export { toMainMaterialView, toVariantMaterialView }

/** 主素材流转记录（来自后端 ActivityLog；素材中心叫「流转记录」） */
export async function getMaterialHistoryFromApi(materialCode: string) {
  if (!API_ENABLED) return []
  return materialApi.getMaterialHistory(materialCode)
}

/** 副素材详情（点击副素材卡片 → 抽屉下钻） */
export async function getVariantDetailFromApi(variantId: string) {
  if (!API_ENABLED) return null
  return materialApi.getVariantDetail(variantId)
}

/** 副素材流转记录 */
export async function getVariantHistoryFromApi(variantId: string) {
  if (!API_ENABLED) return []
  return materialApi.getVariantHistory(variantId)
}

/** 单个素材改标签 */
export async function patchMaterialTagsFromApi(materialCode: string, tags: string[], actor: string) {
  if (!API_ENABLED) return
  await materialApi.patchMaterialTags(materialCode, tags, actor)
}

/** 单个副素材改标签 */
export async function patchVariantTagsFromApi(variantId: string, tags: string[], actor: string) {
  if (!API_ENABLED) return
  await materialApi.patchVariantTags(variantId, tags, actor)
}

/** 素材中心多选批量调整标签（add / remove / replace） */
export async function batchTagsFromApi(payload: {
  op: 'add' | 'remove' | 'replace'
  tags: string[]
  targetType: 'MATERIAL' | 'MATERIAL_VARIANT'
  targetIds: string[]
  actor: string
}) {
  if (!API_ENABLED) return { updated: 0, action: '' }
  return materialApi.batchTags(payload)
}

/** 设计包改负责人（写 CHANGE_RESPONSIBLE 维护记录） */
export async function patchPackageResponsibleFromApi(
  packageId: string,
  responsibleName: string,
) {
  if (!API_ENABLED) return
  await materialApi.patchDesignPackage(packageId, { responsibleName })
}

/** 设计包整组改标签 + 可选「同步新增标签到包内素材」 */
export async function patchPackageTagsFromApi(
  packageId: string,
  tags: string[],
  syncToMaterials: boolean,
  actor: string,
) {
  if (!API_ENABLED) return
  await materialApi.patchDesignPackage(packageId, { tags, syncTagsToMaterials: syncToMaterials, designerName: actor })
}

/**
 * 后端接入清单（本文件所有写操作对应一个后端 API）：
 *
 *   startPackageSession     POST   /api/design-packages/:id/uploads        创建一次 PackageUpload（幂等键 uploadSessionId）
 *   recoverOrStartSession   GET    /api/upload-sessions?packageName=...
 *   setSessionOperator      PATCH  /api/upload-sessions/:id
 *   ingestLocalFiles        POST   /api/uploads/:id/files                  后端计算 BLAKE3 + pHash 写入 Asset
 *   runPackagePairing       POST   /api/uploads/:id/pair                   本次上传按同名 pairKey 配对（无相似度）
 *   reassignPairing         PATCH  /api/uploads/:id/pairings/:pairingId    人工改配
 *   confirmPairing          PATCH  /api/uploads/:id/pairings/:pairingId    人工确认单条
 *   confirmAllPairings      POST   /api/uploads/:id/pairings/confirm       确认整包配对
 *   （生成版本）             POST   /api/design-packages/:id/batches        确认整包并生成 V1/V2
 *   resolveAnomaly          PATCH  /api/anomalies/:id/resolve
 *   reopenAnomaly           PATCH  /api/anomalies/:id/reopen
 *   supplementCurrentVersion POST  /api/design-packages/:id/batches/:batchId/supplement
 *   addVariantRevision      POST   /api/material-variants/:id/revisions
 *   addMaterialPsdRevision  POST   /api/materials/:id/psd-revisions
 *   submitAndDispatch       POST   /api/design-packages/:id/submit
 *   addDistribution         POST   /api/design-packages/:id/distributions
 *   cancelDistribution      POST   /api/distributions/:id/cancel
 *   receiveTask             POST   /api/distributions/:id/receive
 *   bindAsins               PUT    /api/distributions/:id/asins
 *   getPackageOverview      GET    /api/design-packages/:id/overview
 *   getTaskOverview         GET    /api/distributions/:id/overview
 *   getMaterials            GET    /api/materials
 *   getMaterialDetail       GET    /api/materials/:code
 *   getVariantsOfMaterial   GET    /api/materials/:code/variants
 *   getDesignPackageGroups  GET    /api/design-packages/groups
 *   getLogsByTarget         GET    /api/activity-logs?targetType=&targetId=
 */
export const USE_BACKEND_API = true
