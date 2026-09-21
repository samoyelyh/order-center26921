// ============================================================================
// Phase 1 真实后端调用：设计包 → 上传记录/会话 → Asset → 主素材位置
//
// 这是上传页从 Mock 切到真实后端的唯一入口。
// 不改变既有 Service 调用形态：页面仍调用 service 函数，只是这些函数现在打后端。
// ============================================================================

import {
  API_BASE,
  API_ENABLED,
  materialApi,
  probeBackend,
  type ApiErrorKind,
  type DerivativeBatchDto,
  type PairingRunResponseDto,
  type PackageOverviewDto,
  type UploadFileResponseDto,
  type UploadedFileDto,
} from '@/services/apiClient'
import { buildHydratePatch } from '@/services/overviewMapper'
import { getWorkflowState, hydrateFromServer } from '@/store/workflowStore'

export interface UploadPackageFileInput {
  /** 主素材预览文件 */
  mainFile: File
  /** 该主素材对应的 PSD（可选） */
  psdFile?: File
}

export interface SubmitDesignPackageInput {
  packageName: string
  /** 设计编码：美工自己的设计编号，必填；素材详情「关联设计」按它聚合 */
  designCode: string
  originalPackageName: string
  /** 归属运营：可以只填名字（后端没有真实 userId 时 operatorId 存 null） */
  operatorId?: string
  operatorName: string
  /** 设计美工：设计包的长期业务属性，必填（落入 design_packages.designer_name） */
  designerName: string
  designerId?: string | null
  /** 实际上传人：这一次是谁执行的上传（落入 package_uploads.uploader_name） */
  uploaderName: string
  remark?: string
  /** 设计包内位置 = 上传顺序（1 起） */
  items: UploadPackageFileInput[]
  /** 副素材文件：落 Asset + VARIANT 关联，作为待匹配副图 */
  variantFiles?: File[]
  /** 上传会话幂等键：同一会话重复提交不会重复创建设计包 */
  uploadSessionId?: string
  /** 追加到已有设计包时传入 */
  designPackageId?: string
  onProgress?: (stage: string, percent: number) => void
}

export interface SubmitDesignPackageResult {
  designPackageId: string
  uploadId: string
  sessionId: string
  createdMaterials: number
  reusedMaterials: number
  assets: UploadFileResponseDto[]
  overview: PackageOverviewDto
  /** 上传完成后自动跑的一次整包匹配（数量一致时才有） */
  autoPairing: PairingRunResponseDto | null
}

function newSessionKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return `sess-${crypto.randomUUID()}`
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

export function isApiMode(): boolean {
  return API_ENABLED
}

/**
 * Phase 1 主流程（真实后端）：
 *   1. 创建设计包（或复用传入的 designPackageId）
 *   2. 创建 PackageUpload + UploadSession（幂等）
 *   3. 逐个上传主素材预览 + PSD → Asset（后端算 BLAKE3 / pHash）
 *   4. 上传副素材文件（仅落 Asset，Phase 2 才建 variant）
 *   5. 一次性提交位置清单 → 后端事务内创建/复用 MAT + PSD Revision + DesignPackageMaterial
 *   6. 数量一致时自动跑一次整包匹配（第三十四条）
 *   7. 拉取整包视图并 hydrate 进本地 store
 */
export async function submitDesignPackageToApi(
  input: SubmitDesignPackageInput,
): Promise<SubmitDesignPackageResult> {
  const report = (stage: string, percent: number) => input.onProgress?.(stage, percent)
  // 实际上传人：只允许姓名（接入登录系统后由登录用户自动回填）
  const uploaderName = input.uploaderName.trim()

  // ---- 1. 设计包（设计编码 + 设计美工都是必填的长期属性） ----
  report('正在创建设计包…', 2)
  let designPackageId = input.designPackageId
  if (!designPackageId) {
    const pkg = await materialApi.createDesignPackage({
      name: input.packageName,
      designCode: input.designCode,
      remark: input.remark,
      createdBy: uploaderName,
      designerName: input.designerName.trim(),
      designerId: input.designerId ?? null,
    })
    designPackageId = pkg.id
  }

  // ---- 2. 上传记录 + 会话（幂等） ----
  report('正在创建上传记录…', 6)
  const sessionKey = input.uploadSessionId ?? newSessionKey()
  const created = await materialApi.createUpload(designPackageId, {
    originalPackageName: input.originalPackageName,
    uploaderName,
    // 归属运营允许只有名字：后端 operator_id 可空，绝不因为没有 userId 就拒绝保存
    operatorId: input.operatorId || undefined,
    operatorName: input.operatorName,
    remark: input.remark,
    uploadType: 'INITIAL',
    uploadSessionId: sessionKey,
  })
  const uploadId = created.packageUpload.id
  const sessionId = created.session?.id ?? ''

  // ---- 3/4. 文件上传 ----
  const assets: UploadFileResponseDto[] = []
  const totalFiles = input.items.length * 2 + (input.variantFiles?.length ?? 0)
  let doneFiles = 0
  const step = () => {
    doneFiles += 1
    // 文件阶段占 8% → 78%
    report('正在上传文件并计算 BLAKE3 / pHash…', 8 + Math.round((doneFiles / Math.max(totalFiles, 1)) * 70))
  }

  const materialPayload: {
    position: number
    previewAssetId: string
    psdAssetId?: string | null
    displayName?: string | null
    sourceFileName?: string | null
  }[] = []

  for (let index = 0; index < input.items.length; index += 1) {
    const item = input.items[index]
    const position = index + 1

    report(`上传主素材 ${position}/${input.items.length}…`, 8 + Math.round((doneFiles / Math.max(totalFiles, 1)) * 70))
    const previewAsset = await materialApi.uploadFile(uploadId, item.mainFile, 'MAIN', undefined, 'MAIN_PREVIEW')
    assets.push(previewAsset)
    step()

    let psdAssetId: string | null = null
    if (item.psdFile) {
      report(`上传 PSD（位置 ${position}）…`, 8 + Math.round((doneFiles / Math.max(totalFiles, 1)) * 70))
      const psdAsset = await materialApi.uploadFile(uploadId, item.psdFile, 'PSD', undefined, 'PSD')
      assets.push(psdAsset)
      psdAssetId = psdAsset.assetId
      step()
    }

    materialPayload.push({
      position,
      previewAssetId: previewAsset.assetId,
      psdAssetId,
      displayName: item.mainFile.name.replace(/\.[^.]+$/, ''),
      sourceFileName: item.mainFile.name,
    })
  }

  // 副素材：Phase 1 只入库为 Asset，并以 VARIANT 角色登记归属（供 Phase 2 建 variant）
  for (const variantFile of input.variantFiles ?? []) {
    report(`上传副素材 ${variantFile.name}…`, 8 + Math.round((doneFiles / Math.max(totalFiles, 1)) * 70))
    assets.push(await materialApi.uploadFile(uploadId, variantFile, 'VARIANT', undefined, 'VARIANT'))
    step()
  }

  // ---- 5. 位置清单入库（后端单事务） ----
  report('正在创建主素材 MAT 与设计包位置…', 84)
  const submitted = await materialApi.submitMaterials(uploadId, {
    actor: uploaderName,
    materials: materialPayload,
    // 文件归属已在上传时写入 package_upload_assets；
    // 这里再带一次 assetId 列表，保证历史/异常路径下归属齐全（后端幂等 upsert）
    linkAssetIds: assets.map((a) => a.assetId),
  })

  // ---- 6. 上传后自动跑一遍同名配对 ----
  // 上传成功 ≠ 生成版本：配对只按同名 pairKey，不需要任何相似度计算；
  // 版本必须由人工确认配对后再生成。
  let autoPairing: PairingRunResponseDto | null = null
  report('正在按同名 pairKey 配对…', 92)
  autoPairing = await materialApi.runPairing(uploadId, uploaderName)

  // ---- 7. 拉整包视图并 hydrate ----
  report('正在同步设计包数据…', 95)
  const overview = await materialApi.getPackageOverview(designPackageId)
  hydrateFromServer(buildHydratePatch(overview, getWorkflowState()).patch)

  report('完成', 100)

  return {
    designPackageId,
    uploadId,
    sessionId,
    createdMaterials: submitted.createdMaterialCount,
    reusedMaterials: submitted.reusedMaterialCount,
    assets,
    overview,
    autoPairing,
  }
}

/** 从后端拉取整包视图并 hydrate（刷新页面 / 切换设计包时用） */
export async function refreshPackageFromApi(designPackageId: string): Promise<PackageOverviewDto | null> {
  if (!API_ENABLED || !designPackageId) return null
  const overview = await materialApi.getPackageOverview(designPackageId)
  hydrateFromServer(buildHydratePatch(overview, getWorkflowState()).patch)
  return overview
}

/** 拉取设计包维护记录（后端权威版本） */
export async function fetchPackageLogsFromApi(designPackageId: string) {
  if (!API_ENABLED) return []
  return materialApi.queryLogs({ designPackageId })
}

/**
 * 健康检查：用于上传页提示后端是否可用（而不是等上传时才报错）。
 *
 * 返回 kind 让页面区分三种状态（第4条）：
 *   OK / HEALTH_DEGRADED / CORS_BLOCKED / UNREACHABLE / HTTP_ERROR
 */
export interface BackendHealthResult {
  ok: boolean
  kind: ApiErrorKind | 'OK'
  message: string
  hint: string
  apiBase: string
}

export async function checkBackendHealth(): Promise<BackendHealthResult> {
  if (!API_ENABLED) {
    return {
      ok: false,
      kind: 'UNREACHABLE',
      message: '当前为本地 Mock 模式（VITE_MATERIAL_API=0）',
      hint: '',
      apiBase: API_BASE,
    }
  }
  const result = await probeBackend()
  return {
    ok: result.ok,
    kind: result.kind,
    message: result.message,
    hint: result.hint,
    apiBase: result.apiBase,
  }
}

/** 重新检测：页面上的「重新检测」按钮直接调它 */
export async function recheckBackendHealth(): Promise<BackendHealthResult> {
  return checkBackendHealth()
}

/** 按角色拉取某次上传的文件（副图 = VARIANT） */
export async function fetchUploadFilesByRole(
  uploadId: string,
  role: 'MAIN_PREVIEW' | 'PSD' | 'VARIANT' | 'OTHER',
): Promise<UploadedFileDto[]> {
  if (!API_ENABLED || !uploadId) return []
  return materialApi.listUploadAssets(uploadId, role)
}

// ---------------------------------------------------------------- Phase 2 操作
//
// 全部只围绕「同名 pairKey 配对」：没有相似度、没有候选打分、没有高/中/低分级。

/** 本次上传按同名 pairKey 自动配对（手动触发 / 重跑） */
export async function runPairingApi(
  uploadId: string,
  actor: string,
): Promise<PairingRunResponseDto> {
  return materialApi.runPairing(uploadId, actor)
}

/** 人工改配：换成本次上传里的另一张副图；被占用时抛 409，需要 confirmReassign 再来一次 */
export async function reassignPairingApi(
  uploadId: string,
  pairingId: string,
  variantAssetId: string,
  actor: string,
  confirmReassign = false,
): Promise<void> {
  await materialApi.patchPairing(uploadId, pairingId, {
    variantAssetId,
    actor,
    confirmReassign,
  })
}

/** 取消某个位置的副图配对 */
export async function clearPairingApi(
  uploadId: string,
  pairingId: string,
  actor: string,
): Promise<void> {
  await materialApi.patchPairing(uploadId, pairingId, { variantAssetId: '', actor })
}

/** 确认整包配对（有缺副图等阻断异常时后端会拒绝） */
export async function confirmPairingsApi(
  uploadId: string,
  actor: string,
): Promise<number> {
  const result = await materialApi.confirmPairings(uploadId, actor)
  return result.confirmedCount
}

/**
 * 确认整包配对并生成下一版（第一次即 V1）。
 *
 * 这是「上传成功」与「生成版本」的分界线：
 * 只有所有位置都已确认配对、数量一致时才允许调用。
 */
export async function createBatchApi(
  designPackageId: string,
  actor: string,
  uploadId?: string,
): Promise<DerivativeBatchDto> {
  const batch = await materialApi.createBatch(designPackageId, { actor, uploadId })
  await refreshPackageFromApi(designPackageId)
  return batch
}

