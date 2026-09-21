// ============================================================================
// 后端 API 客户端（Phase 1）
//
// 只负责 HTTP 与会话：
//   - 统一 base url（见下方 resolveApiBase：环境变量 > 当前访问主机名:8000）
//   - 把后端 {code, message} 错误转成带 code 的 ApiError，页面直接显示 message
//   - 幂等键统一用 Idempotency-Key / uploadSessionId
//
// Phase 1 仅实现：
//   设计包 / 上传记录 / 上传会话 / Asset 上传 / 主素材位置提交 / 整包视图
// ============================================================================

/** 后端默认端口（可用 VITE_MATERIAL_API_PORT 覆盖） */
const DEFAULT_API_PORT = '8000'

/**
 * 第1/2条：API 基址绝对不能用 127.0.0.1 硬编码。
 *
 * 局域网里同事用 http://192.168.0.31:3000 打开页面时，浏览器里的
 * 127.0.0.1 指的是**同事自己的电脑**，于是必然 Failed to fetch。
 * 正确做法：跟随页面当前访问的主机名，只换端口。
 *
 * 优先级：
 *   1. VITE_MATERIAL_API_BASE（显式配置，最高优先级）
 *   2. 当前页面的 protocol + hostname + :8000/api
 */
export function resolveApiBase(): string {
  const configured = (import.meta.env.VITE_MATERIAL_API_BASE as string | undefined)?.trim()
  if (configured) return configured.replace(/\/+$/, '')

  const port = (import.meta.env.VITE_MATERIAL_API_PORT as string | undefined)?.trim() || DEFAULT_API_PORT
  if (typeof window !== 'undefined' && window.location?.hostname) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
    return `${protocol}//${window.location.hostname}:${port}/api`
  }
  // SSR / 无 window 时的兜底（仅构建期用到）
  return `http://127.0.0.1:${port}/api`
}

export const API_BASE = resolveApiBase()

/** API 主机（去掉 /api 后缀），用于拼接后端返回的相对图片地址 */
export const API_ORIGIN = API_BASE.replace(/\/api$/, '')

/**
 * 后端返回的图片地址是相对路径（/api/assets/{id}/content）。
 * 这里统一补上 API 主机 —— 局域网访问时同样是局域网地址，不会退回 127.0.0.1。
 */
export function resolveMediaUrl(url: string | null | undefined): string {
  if (!url) return ''
  if (/^(https?:)?\/\//i.test(url) || url.startsWith('data:') || url.startsWith('blob:')) return url
  return `${API_ORIGIN}${url.startsWith('/') ? '' : '/'}${url}`
}

/** 是否走后端真实 API（默认开启；设为 '0' 时回退到本地 Mock Store） */
export const API_ENABLED = (import.meta.env.VITE_MATERIAL_API ?? '1') !== '0'

export interface ApiErrorBody {
  code?: string
  message?: string
  detail?: unknown
}

/**
 * 第4条：区分三种失败，页面才能给出可操作的提示，而不是一句 Failed to fetch。
 *
 *   UNREACHABLE      → 后端没启动 / 只监听了 127.0.0.1（不是 0.0.0.0）/ 端口不对
 *   CORS_BLOCKED     → 服务能连上，但响应缺少 CORS 头（浏览器把结果拦掉了）
 *   HEALTH_DEGRADED  → HTTP 通了，但 /api/health 报 database/storage 不健康
 *   HTTP_ERROR       → 业务接口返回了 4xx/5xx
 */
export type ApiErrorKind = 'HTTP_ERROR' | 'UNREACHABLE' | 'CORS_BLOCKED' | 'HEALTH_DEGRADED' | 'NETWORK_ERROR'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly detail: unknown
  readonly kind: ApiErrorKind
  /** 给用户看的排查建议（多行） */
  readonly hint: string

  constructor(
    message: string,
    code: string,
    status: number,
    detail: unknown = null,
    kind: ApiErrorKind = 'HTTP_ERROR',
    hint = '',
  ) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
    this.kind = kind
    this.hint = hint
  }
}

const UNREACHABLE_HINT = [
  `请确认后端已启动，并且监听 0.0.0.0（不能只监听 127.0.0.1）：`,
  `    uvicorn app.main:app --host 0.0.0.0 --port 8000`,
  `当前页面使用的后端地址：${API_BASE}`,
].join('\n')

const CORS_HINT = `后端已能连通，但响应缺少 CORS 头（浏览器把结果拦截了）。请把当前页面来源加入后端 CORS 白名单。`

async function parseError(response: Response): Promise<never> {
  let body: ApiErrorBody = {}
  try {
    body = (await response.json()) as ApiErrorBody
  } catch {
    // 非 JSON 响应（例如反向代理 502）
  }
  const message =
    body.message ||
    (typeof body.detail === 'string' ? body.detail : '') ||
    `请求失败（HTTP ${response.status}）`
  throw new ApiError(
    message,
    body.code ?? `HTTP_${response.status}`,
    response.status,
    body.detail ?? null,
    'HTTP_ERROR',
  )
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, init)
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    throw new ApiError(
      `无法连接后端服务（${API_BASE}）：${detail}`,
      'NETWORK_ERROR',
      0,
      { apiBase: API_BASE, cause: detail },
      'NETWORK_ERROR',
      UNREACHABLE_HINT,
    )
  }
  if (!response.ok) await parseError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function jsonInit(method: string, body: unknown, headers: Record<string, string> = {}): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
  }
}

// ---------------------------------------------------------------- 类型（与后端 DTO 对齐）

export interface AssetRefDto {
  assetId: string
  uri: string
  fileName: string
  mimeType?: string | null
  sizeBytes?: number | null
  createdAt?: string | null
}

export interface AssetDto {
  id: string
  storageKey: string
  originalFilename: string
  mimeType: string
  sizeBytes: number
  width?: number | null
  height?: number | null
  blake3?: string | null
  phash?: string | null
  phashVersion?: number | null
  createdBy: string
  createdAt: string
  deletedAt?: string | null
  uri: string
}

/** 文件角色：这是「用户传的是主图/PSD/副图」，不是主副素材匹配结果 */
export type UploadFileRoleDto = 'MAIN_PREVIEW' | 'PSD' | 'VARIANT' | 'OTHER'

/**
 * Asset ↔ 上传行为的多对多关联（package_upload_assets）。
 * 一个 Asset 可以被多次上传复用，因此文件归属只能看这个列表。
 */
export interface AssetUploadLinkDto {
  id: string
  packageUploadId: string
  assetId: string
  fileRole: UploadFileRoleDto
  originalFilename: string
  positionHint?: number | null
  createdAt: string
}

export interface UploadedFileDto {
  assetId: string
  fileRole: UploadFileRoleDto
  originalFilename: string
  /** 后端生成的图片地址（相对路径，需用 resolveMediaUrl 拼接） */
  previewUrl: string
  mimeType: string
  sizeBytes: number
  width?: number | null
  height?: number | null
  /** 同名配对键（文件名去扩展名，小写） */
  pairKey: string
  positionHint?: number | null
  packageUploadId?: string | null
  createdAt?: string | null
}

export interface PackageUploadedFilesDto {
  main: UploadedFileDto[]
  psd: UploadedFileDto[]
  variants: UploadedFileDto[]
  other: UploadedFileDto[]
  mainCount: number
  psdCount: number
  variantCount: number
  otherCount: number
}

export interface DesignPackageDto {
  id: string
  code: string
  name: string
  categoryCode: string
  categoryName: string
  /** 设计编码：用户/美工自己的设计编号（素材的「关联设计」按它聚合） */
  designCode: string
  /** 设计包标签（创建时继承给包内素材，之后独立修改） */
  tags: string[]
  /** 负责人：业务上负责这套设计的人（与实际上传人分开） */
  responsibleId?: string | null
  responsibleName: string
  remark?: string | null
  /** 设计美工：设计包的长期业务属性（可与实际上传人不同） */
  designerId?: string | null
  designerName: string
  createdBy: string
  createdAt: string
  updatedAt: string
  archivedAt?: string | null
  mainMaterialCount: number
  uploadCount: number
}

export interface PackageUploadDto {
  id: string
  designPackageId: string
  originalPackageName: string
  fileSize?: number | null
  uploadSessionId: string
  uploaderId: string
  uploaderName: string
  uploadType: 'INITIAL' | 'NEW_BATCH' | 'SUPPLEMENT' | 'REVISION'
  targetBatchId?: string | null
  status: string
  remark?: string | null
  createdAt: string
  updatedAt: string
}

export interface UploadSessionDto {
  id: string
  designPackageId: string
  packageUploadId?: string | null
  originalPackageName: string
  fileSize: number
  stage: string
  uploadType: string
  operatorId?: string | null
  operatorName?: string | null
  remark?: string | null
  receivedFiles: string[]
  createdAt: string
  updatedAt: string
}

export interface MaterialPsdRevisionDto {
  id: string
  materialId: string
  revisionNo: number
  assetId: string
  createdBy: string
  createdAt: string
  deleted: boolean
  note?: string | null
}

export interface MaterialDto {
  id: string
  materialCode: string
  name: string
  previewAssetId: string
  currentPsdRevisionId?: string | null
  tags: string[]
  sourcePackageUploadId?: string | null
  createdBy: string
  createdAt: string
  updatedAt: string
  archivedAt?: string | null
  previewAsset?: AssetRefDto | null
  psdAsset?: AssetRefDto | null
  psdRevisions: MaterialPsdRevisionDto[]
  designCount: number
  reused: boolean
}

export interface DesignPackageMaterialDto {
  id: string
  designPackageId: string
  position: number
  materialId: string
  displayName?: string | null
  sourceFileName: string
  sourceAssetId: string
  createdFromUploadId: string
  createdAt: string
  material?: MaterialDto | null
  previewUri?: string | null
  psdUri?: string | null
}

export interface CountCheckDto {
  mainCount: number
  /** 本次上传的副图 Asset 张数（来自 package_upload_assets） */
  variantUploadCount: number
  /** 已建立的副素材（MaterialVariant）数 */
  variantCount: number
  diff: number
  messages: string[]
  blocked: boolean
}

// ---------------------------------------------------------------- Phase 2：同名配对 / 版本
//
// 主副素材关系只由同一次上传内的同名 pairKey 决定：没有相似度、没有候选排序、
// 没有高/中/低匹配分级。

/** 配对来源：NAME=按同名自动配对；MANUAL=人工改过 */
export type PairSourceDto = 'NAME' | 'MANUAL'
/** 配对状态：PAIRED=已配对待确认；UNPAIRED=缺副图；CONFIRMED=人工确认 */
export type PairStatusDto = 'PAIRED' | 'UNPAIRED' | 'CONFIRMED'

/** 「修改配对」弹窗里可选的一张副图（来自本次上传，按文件名排序，无打分） */
export interface PairingOptionDto {
  assetId: string
  originalFilename: string
  pairKey: string
  previewUrl: string
  sizeBytes: number
  width?: number | null
  height?: number | null
  /** 该副图当前已配对给哪个主素材位置 */
  occupiedByPosition?: number | null
}

export interface MaterialPairingDto {
  id: string
  designPackageId: string
  packageUploadId: string
  designPackageMaterialId: string
  position: number
  materialId: string
  materialCode: string
  materialName: string
  mainPreviewUrl: string
  mainSourceFileName: string
  /** 同名配对键（来自主图文件名；仅本设计包内有效，不是永久身份） */
  pairKey: string
  variantAssetId?: string | null
  variantPreviewUrl?: string | null
  variantFileName?: string | null
  variantPairKey?: string | null
  psdAssetId?: string | null
  psdFileName?: string | null
  source: PairSourceDto
  status: PairStatusDto
  variantId?: string | null
  confirmedBy?: string | null
  confirmedAt?: string | null
  options: PairingOptionDto[]
}

export interface PairingRunResponseDto {
  designPackageId: string
  packageUploadId: string
  mainCount: number
  variantUploadCount: number
  pairedCount: number
  unpairedCount: number
  confirmedCount: number
  manualCount: number
  pairKeys: string[]
  pairings: MaterialPairingDto[]
  anomalies: DataAnomalyDto[]
  countCheck: CountCheckDto
}

export interface PairingConfirmResponseDto {
  confirmedCount: number
  pairings: MaterialPairingDto[]
  anomalies: DataAnomalyDto[]
  summary: Record<string, number>
}

export interface DataAnomalyDto {
  id: string
  code: string
  level: 'BLOCKING' | 'WARNING' | 'INFO'
  message: string
  blocking: boolean
  position?: number | null
  pairingId?: string | null
  resolved: boolean
}

/** 副素材详情（`/api/material-variants/{id}`） */
export interface VariantDetailDto {
  id: string
  displayCode: string
  materialId: string
  materialCode: string
  materialName: string
  designPackageId: string | null
  designPackageName: string | null
  designPackageCode: string | null
  designCode: string | null
  responsibleName: string | null
  position: number
  batchId: string | null
  batchCode: string | null
  versionNo: number | null
  currentRevisionNo: number | null
  assetId: string | null
  previewUrl: string | null
  originalFilename: string | null
  tags: string[]
  createdAt: string
  deleted: boolean
}

export interface MaterialVariantDto {
  id: string
  materialId: string
  materialCode: string
  designPackageMaterialId: string
  position: number
  batchId: string
  batchCode: string
  displayCode: string
  currentRevisionId?: string | null
  currentRevisionNo?: number | null
  assetId?: string | null
  previewUrl?: string | null
  originalFilename?: string | null
  duplicateOfVariantId?: string | null
  duplicateWarning?: string | null
  /** 该副素材被哪个更晚的版本复用（内容与上一版完全相同时） */
  reusedByBatchCode?: string | null
  /** 副素材标签（建版时从主素材复制，之后独立修改） */
  tags: string[]
  deleted: boolean
  createdAt: string
}

/** 副素材详情（`/api/material-variants/{id}`） */
export interface VariantDetailDto {
  id: string
  displayCode: string
  materialId: string
  materialCode: string
  materialName: string
  designPackageId: string | null
  designPackageName: string | null
  designPackageCode: string | null
  designCode: string | null
  responsibleName: string | null
  position: number
  batchId: string | null
  batchCode: string | null
  versionNo: number | null
  currentRevisionNo: number | null
  assetId: string | null
  previewUrl: string | null
  originalFilename: string | null
  tags: string[]
  createdAt: string
  deleted: boolean
}

/** 图片搜索单个结果（以图搜图，与配对无关） */
export interface ImageSearchResultDto {
  assetId: string
  kind: 'MAIN' | 'VARIANT'
  materialCode: string | null
  displayCode: string | null
  name: string
  position: number | null
  designPackageId: string | null
  designPackageName: string | null
  responsibleName: string | null
  tags: string[]
  batchCode: string | null
  mainMaterialCode: string | null
  similarity: number
  /** 与查询素材命中的人工标签（找相似时标签加权） */
  matchedTags?: string[]
  /** BLAKE3 与查询文件完全相同（内容一致的复用） */
  matchedBlake3?: boolean
  /** pHash 感知相似度（1 - 汉明距离/64，0~1） */
  phashSimilarity?: number | null
  previewUrl: string
}

export interface ImageSearchResponseDto {
  results: ImageSearchResultDto[]
  error?: string
}

export interface DerivativeBatchDto {
  id: string
  designPackageId: string
  versionNo: number
  code: string
  createdFromUploadId?: string | null
  mainMaterialCountAtCreation: number
  createdBy: string
  createdAt: string
  variantCount: number
  variants: MaterialVariantDto[]
  /** 本次建版新建/复用了几个副素材（复用的不新建实体） */
  createdVariantCount: number
  reusedVariantCount: number
  reusedNotes: string[]
}

export interface CoverageDto {
  versionCode: string
  batchId: string
  covered: number
  total: number
  missingCodes: string[]
}

export interface ActivityLogDto {
  id: string
  designPackageId?: string | null
  targetType: string
  targetId: string
  actor: string
  action: string
  summary: string
  before?: string | null
  after?: string | null
  createdAt: string
}

export interface PackageOverviewDto {
  designPackage: DesignPackageDto
  upload: PackageUploadDto | null
  uploads: PackageUploadDto[]
  session: UploadSessionDto | null
  positions: DesignPackageMaterialDto[]
  materials: MaterialDto[]
  assets: AssetDto[]
  /** 整个设计包累计上传的文件（按 assetId + fileRole 去重），刷新后依然能拿到副图 */
  uploadedFiles: PackageUploadedFilesDto
  /** 仅最新一次上传交付的文件 */
  latestUploadedFiles: PackageUploadedFilesDto
  uploadAssetLinks: AssetUploadLinkDto[]
  logs: ActivityLogDto[]
  operatorId?: string | null
  operatorName?: string | null
  operators: { operatorId: string; operatorName: string }[]
  mainMaterialCount: number
  variantCount: number
  currentBatchNo?: number | null
  countCheck: CountCheckDto
  coverage: CoverageDto | null
  submitted: boolean
  phase: string

  // ---- Phase 2 ----
  /** 最新一次上传的配对结果（页面表格直接渲染） */
  pairings: MaterialPairingDto[]
  pairingSummary: Record<string, number>
  pairingUploadId?: string | null
  /** 最新一个上架版本（未建版时为 null） */
  batch: DerivativeBatchDto | null
  currentBatch: DerivativeBatchDto | null
  batches: DerivativeBatchDto[]
  variants: MaterialVariantDto[]
  anomalies: DataAnomalyDto[]
  blockingAnomalies: DataAnomalyDto[]
}

export interface CreateUploadResponseDto {
  packageUpload: PackageUploadDto
  session: UploadSessionDto | null
  designPackage: DesignPackageDto
  created: boolean
}

export interface UploadFileResponseDto {
  assetId: string
  originalFilename: string
  sizeBytes: number
  mimeType: string
  width?: number | null
  height?: number | null
  blake3?: string | null
  phash?: string | null
  phashVersion?: number | null
  storageKey: string
  uri: string
  reused: boolean
  kind: 'IMAGE' | 'DESIGN'
  fileRole: UploadFileRoleDto
  /** 同一次上传内按文件名 stem 计算的配对键；不是永久素材身份 */
  pairKey: string
  linkId?: string | null
}

export interface SubmitMaterialsResponseDto {
  designPackageId: string
  positions: DesignPackageMaterialDto[]
  createdMaterialCount: number
  reusedMaterialCount: number
}

// ---- 订单导入（Phase A-D；审核/匹配页面留给后续 Phase E-G） ----
export interface OrderImportBatchDto {
  id: string
  originalFilename: string
  rawZipAssetId: string
  zipBlake3: string
  categoryCode: string
  categoryName: string
  categorySource: string
  categoryConfidence: string
  status: 'PARSED' | 'PARTIAL' | 'FAILED' | 'DUPLICATE'
  duplicateOfBatchId?: string | null
  totalJsonCount: number
  totalItemCount: number
  createdBy: string
  createdAt: string
  updatedAt: string
}

export interface OrderItemDto {
  id: string
  dedupeKey: string
  orderId: string
  orderItemId?: string | null
  childAsin?: string | null
  sku?: string | null
  quantity: number
  categoryCode: string
  categoryName: string
  categorySource: string
  categoryConfidence: string
  parserVersion: string
  rawJsonHash: string
  matchedVariantId?: string | null
  matchedMaterialId?: string | null
  matchMethod?: string | null
  matchScore?: number | null
  matchStatus: 'UNMATCHED' | 'REVIEW_REQUIRED' | 'CONFIRMED' | 'FAILED'
  normalized?: Record<string, unknown> | null
}

export interface OrderImportResponseDto {
  batch: OrderImportBatchDto
  summary: Record<string, number>
}

export interface HealthDto {
  status: string
  phase: string
  database: { ok: boolean; error: string | null }
  storage: { backend: string; ok: boolean; error: string | null }
}

// ---------------------------------------------------------------- 连通性诊断（第4条）

/** 带超时的 fetch，避免后端半死不活时页面一直转圈 */
async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

export interface BackendProbeResult {
  ok: boolean
  kind: ApiErrorKind | 'OK'
  /** 给用户的一句话结论 */
  message: string
  /** 多行排查建议 */
  hint: string
  apiBase: string
  health?: HealthDto
}

/**
 * 后端连通性诊断。
 *
 * 关键技巧：CORS 被拦截和「服务根本没起来」在 JS 里都表现为 fetch 抛 TypeError，
 * 分不清。这里再发一次 `mode: 'no-cors'` 请求：
 *   - 它若成功（得到 opaque 响应）→ 服务器是活的，问题在 CORS 头
 *   - 它若同样失败 → 服务确实连不上（没启动 / 只监听 127.0.0.1 / 端口不对）
 */
export async function probeBackend(timeoutMs = 8000): Promise<BackendProbeResult> {
  const url = `${API_BASE}/health`
  try {
    const response = await fetchWithTimeout(url, { method: 'GET', cache: 'no-store' }, timeoutMs)
    if (!response.ok) {
      return {
        ok: false,
        kind: 'HTTP_ERROR',
        message: `后端健康检查返回 HTTP ${response.status}`,
        hint: `请检查后端日志：${url}`,
        apiBase: API_BASE,
      }
    }
    const health = (await response.json()) as HealthDto
    if (health.status !== 'ok') {
      const reasons: string[] = []
      if (!health.database?.ok) reasons.push(`数据库不可用：${health.database?.error ?? '未知原因'}`)
      if (!health.storage?.ok) reasons.push(`对象存储不可用：${health.storage?.error ?? '未知原因'}`)
      return {
        ok: false,
        kind: 'HEALTH_DEGRADED',
        message: `后端已连通，但依赖不健康（status=${health.status}）`,
        hint: reasons.join('\n'),
        apiBase: API_BASE,
        health,
      }
    }
    return {
      ok: true,
      kind: 'OK',
      message: `后端已连通（${API_BASE}，存储 ${health.storage?.backend ?? '未知'}）`,
      hint: '',
      apiBase: API_BASE,
      health,
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    // 第一次失败：判断到底是「连不上」还是「被 CORS 拦了」
    let serverAlive = false
    try {
      await fetchWithTimeout(url, { method: 'GET', mode: 'no-cors', cache: 'no-store' }, timeoutMs)
      serverAlive = true
    } catch {
      serverAlive = false
    }
    if (serverAlive) {
      return {
        ok: false,
        kind: 'CORS_BLOCKED',
        message: `后端可连通，但浏览器因 CORS 拦截了响应（${API_BASE}）`,
        hint: CORS_HINT,
        apiBase: API_BASE,
      }
    }
    return {
      ok: false,
      kind: 'UNREACHABLE',
      message: `无法连接后端服务（${API_BASE}）：${detail}`,
      hint: UNREACHABLE_HINT,
      apiBase: API_BASE,
    }
  }
}

// ---------------------------------------------------------------- API 方法

export const materialApi = {
  health: () => request<HealthDto>('/health'),

  // ---- 设计包 ----
  createDesignPackage: (payload: {
    name: string
    categoryCode?: string
    categoryName?: string
    /** 设计编码：必填（用户/美工自己的设计编号） */
    designCode: string
    /** 设计包标签（创建时继承给包内素材） */
    tags?: string[]
    /** 负责人：业务上负责这套设计的人 */
    responsibleName?: string
    responsibleId?: string | null
    remark?: string
    createdBy?: string
    /** 设计美工：必填（可以只有姓名，没有 userId） */
    designerName: string
    designerId?: string | null
  }) => request<DesignPackageDto>('/design-packages', jsonInit('POST', payload)),

  /**
   * 设计包列表。
   * `withBatch` = 只要「已经确认整包并生成过版本」的包（素材中心用）：
   * 只上传、还没生成 V1 的草稿不该出现在前端素材列表里。
   * `includeArchived` = 连已删除（归档）的包一起返回（「显示已删除」用）。
   */
  listDesignPackages: (params?: { withBatch?: boolean; includeArchived?: boolean }) => {
    const search = new URLSearchParams()
    if (params?.withBatch) search.set('withBatch', 'true')
    if (params?.includeArchived) search.set('includeArchived', 'true')
    const query = search.toString()
    return request<DesignPackageDto[]>(`/design-packages${query ? `?${query}` : ''}`)
  },

  /** 删除设计包 = 软删除（归档）：列表与素材中心不再显示，数据保留可恢复 */
  deleteDesignPackage: (id: string, actor?: string) => {
    const search = actor ? `?actor=${encodeURIComponent(actor)}` : ''
    return request<DesignPackageDto>(
      `/design-packages/${encodeURIComponent(id)}${search}`,
      { method: 'DELETE' },
    )
  },

  /** 恢复被删除（归档）的设计包 */
  restoreDesignPackage: (id: string, actor?: string) => {
    const search = actor ? `?actor=${encodeURIComponent(actor)}` : ''
    return request<DesignPackageDto>(
      `/design-packages/${encodeURIComponent(id)}/restore${search}`,
      { method: 'POST' },
    )
  },

  /** 设计编码 / 设计美工 / 负责人 / 标签 / 备注允许事后手工修改 */
  patchDesignPackage: (
    id: string,
    payload: {
      designCode?: string
      categoryCode?: string
      categoryName?: string
      designerName?: string
      designerId?: string | null
      responsibleName?: string
      responsibleId?: string | null
      tags?: string[]
      syncTagsToMaterials?: boolean
      remark?: string
    },
  ) =>
    request<DesignPackageDto>(
      `/design-packages/${encodeURIComponent(id)}`,
      jsonInit('PATCH', payload),
    ),

  getDesignPackage: (id: string) =>
    request<DesignPackageDto>(`/design-packages/${encodeURIComponent(id)}`),

  getPackageOverview: (id: string) =>
    request<PackageOverviewDto>(`/design-packages/${encodeURIComponent(id)}/overview`),

  // ---- 上传记录 / 会话（幂等） ----
  createUpload: (
    designPackageId: string,
    payload: {
      originalPackageName: string
      fileSize?: number
      uploadSessionId?: string
      uploaderName?: string
      uploaderId?: string
      uploadType?: 'INITIAL' | 'NEW_BATCH' | 'SUPPLEMENT' | 'REVISION'
      operatorId?: string
      operatorName?: string
      remark?: string
    },
  ) =>
    request<CreateUploadResponseDto>(
      `/design-packages/${encodeURIComponent(designPackageId)}/uploads`,
      jsonInit('POST', payload, payload.uploadSessionId ? { 'Idempotency-Key': payload.uploadSessionId } : {}),
    ),

  getUploadSession: (sessionId: string) =>
    request<UploadSessionDto>(`/upload-sessions/${encodeURIComponent(sessionId)}`),

  patchUploadSession: (
    sessionId: string,
    payload: { operatorId?: string; operatorName?: string; remark?: string; stage?: string },
  ) => request<UploadSessionDto>(`/upload-sessions/${encodeURIComponent(sessionId)}`, jsonInit('PATCH', payload)),

  /** 上传后修正美工 / 归属运营（两者都支持手工自由填写） */
  patchUpload: (
    uploadId: string,
    payload: { uploaderName?: string; operatorId?: string; operatorName?: string; remark?: string },
  ) => request<PackageUploadDto>(`/uploads/${encodeURIComponent(uploadId)}`, jsonInit('PATCH', payload)),

  // ---- Phase 2：同名配对（无相似度） ----
  /** 本次上传按同名 pairKey 自动配对 */
  runPairing: (uploadId: string, actor?: string) => {
    const search = actor ? `?actor=${encodeURIComponent(actor)}` : ''
    return request<PairingRunResponseDto>(
      `/uploads/${encodeURIComponent(uploadId)}/pair${search}`,
      { method: 'POST' },
    )
  },

  listPairings: (uploadId: string) =>
    request<MaterialPairingDto[]>(`/uploads/${encodeURIComponent(uploadId)}/pairings`),

  /** 人工修改配对（换一张本次上传的副图，或传空字符串取消配对） */
  patchPairing: (
    uploadId: string,
    pairingId: string,
    payload: {
      variantAssetId?: string | null
      pairKey?: string | null
      actor?: string
      /** 副图已配对别的位置时，确认重新分配 */
      confirmReassign?: boolean
    },
  ) =>
    request<MaterialPairingDto>(
      `/uploads/${encodeURIComponent(uploadId)}/pairings/${encodeURIComponent(pairingId)}`,
      jsonInit('PATCH', payload),
    ),

  confirmPairings: (uploadId: string, actor?: string) => {
    const search = actor ? `?actor=${encodeURIComponent(actor)}` : ''
    return request<PairingConfirmResponseDto>(
      `/uploads/${encodeURIComponent(uploadId)}/pairings/confirm${search}`,
      { method: 'POST' },
    )
  },

  // ---- Phase 2：上架版本 ----
  createBatch: (
    designPackageId: string,
    payload: { actor?: string; uploadId?: string | null },
  ) =>
    request<DerivativeBatchDto>(
      `/design-packages/${encodeURIComponent(designPackageId)}/batches`,
      jsonInit('POST', payload),
    ),

  listBatches: (designPackageId: string) =>
    request<DerivativeBatchDto[]>(
      `/design-packages/${encodeURIComponent(designPackageId)}/batches`,
    ),

  // ---- Asset 上传 ----
  uploadFile: async (
    uploadId: string,
    file: File,
    kind: 'MAIN' | 'VARIANT' | 'PSD',
    onProgress?: (percent: number) => void,
    fileRole?: UploadFileRoleDto,
  ): Promise<UploadFileResponseDto> => {
    const form = new FormData()
    form.append('file', file, file.name)
    form.append('kind', kind)
    // 显式告诉后端这个文件是什么角色（主素材 / PSD / 副图）。
    // 用户点的是哪个入口最权威，比文件名推断可靠；后端仍会用文件名兜底。
    const role: UploadFileRoleDto =
      fileRole ?? (kind === 'PSD' ? 'PSD' : kind === 'VARIANT' ? 'VARIANT' : 'MAIN_PREVIEW')
    form.append('fileRole', role)
    // 用 XHR 而不是 fetch：需要真实上传进度（大 PSD 时体验明显）
    return new Promise<UploadFileResponseDto>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${API_BASE}/uploads/${encodeURIComponent(uploadId)}/files`)
      xhr.upload.onprogress = (event) => {
        if (onProgress && event.lengthComputable) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      }
      xhr.onerror = () =>
        reject(new ApiError(`上传文件失败（网络中断）：${file.name}`, 'NETWORK_ERROR', 0))
      xhr.onload = () => {
        let body: unknown = null
        try {
          body = JSON.parse(xhr.responseText)
        } catch {
          body = null
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(body as UploadFileResponseDto)
          return
        }
        const parsed = (body ?? {}) as ApiErrorBody
        reject(
          new ApiError(
            parsed.message || `上传失败（HTTP ${xhr.status}）：${file.name}`,
            parsed.code ?? `HTTP_${xhr.status}`,
            xhr.status,
            parsed.detail ?? null,
          ),
        )
      }
      xhr.send(form)
    })
  },

  /** 本次上传交付的文件（可按角色过滤，例如 role=VARIANT 取副图） */
  listUploadAssets: (uploadId: string, role?: UploadFileRoleDto) => {
    const search = role ? `?role=${encodeURIComponent(role)}` : ''
    return request<UploadedFileDto[]>(`/uploads/${encodeURIComponent(uploadId)}/assets${search}`)
  },

  // ---- 主素材位置提交（一个事务） ----
  submitMaterials: (
    uploadId: string,
    payload: {
      actor?: string
      materials: {
        position: number
        previewAssetId: string
        psdAssetId?: string | null
        displayName?: string | null
        sourceFileName?: string | null
      }[]
      /** 本次用到的全部 Asset（含复用的），后端据此归集到本次上传 */
      linkAssetIds?: string[]
    },
  ) => request<SubmitMaterialsResponseDto>(`/uploads/${encodeURIComponent(uploadId)}/materials`, jsonInit('POST', payload)),

  // ---- 素材 ----
  listMaterials: (limit = 200) => request<MaterialDto[]>(`/materials?limit=${limit}`),

  getMaterial: (materialCode: string) =>
    request<MaterialDto>(`/materials/${encodeURIComponent(materialCode)}`),

  /** 主素材流转记录（来自现有 ActivityLog） */
  getMaterialHistory: (materialCode: string) =>
    request<ActivityLogDto[]>(`/materials/${encodeURIComponent(materialCode)}/history`),

  /** 副素材详情（含所属 MAT / 设计包 / 版本 / Revision / 标签 / 负责人） */
  getVariantDetail: (variantId: string) =>
    request<VariantDetailDto>(`/material-variants/${encodeURIComponent(variantId)}`),

  /** 副素材流转记录 */
  getVariantHistory: (variantId: string) =>
    request<ActivityLogDto[]>(`/material-variants/${encodeURIComponent(variantId)}/history`),

  getAsset: (assetId: string) => request<AssetDto>(`/assets/${encodeURIComponent(assetId)}`),

  // ---- 维护记录 ----
  queryLogs: (params: { targetType?: string; targetId?: string; designPackageId?: string }) => {
    const search = new URLSearchParams()
    if (params.targetType) search.set('target_type', params.targetType)
    if (params.targetId) search.set('target_id', params.targetId)
    if (params.designPackageId) search.set('design_package_id', params.designPackageId)
    return request<ActivityLogDto[]>(`/activity-logs?${search.toString()}`)
  },

  // ---- 标签 ----
  /** 全库去重标签（标签选择器用） */
  listTags: () => request<{ tag: string; count: number }[]>('/tags'),

  /** 整组替换某个素材的标签 */
  patchMaterialTags: (materialCode: string, tags: string[], actor?: string) =>
    request<MaterialDto>(`/materials/${encodeURIComponent(materialCode)}/tags`, jsonInit('PATCH', { tags, actor })),

  /** 整组替换某个副素材的标签 */
  patchVariantTags: (variantId: string, tags: string[], actor?: string) =>
    request<VariantDetailDto>(`/material-variants/${encodeURIComponent(variantId)}/tags`, jsonInit('PATCH', { tags, actor })),

  /** 素材中心多选批量调整标签 */
  batchTags: (payload: {
    op: 'add' | 'remove' | 'replace'
    tags: string[]
    targetType: 'MATERIAL' | 'MATERIAL_VARIANT'
    targetIds: string[]
    actor?: string
  }) => request<{ updated: number; action: string }>('/tags/batch', jsonInit('POST', payload)),

  // ---- 图片搜索（以图搜图，与主副素材配对完全独立） ----
  imageSearch: (params: {
    file?: File
    assetId?: string
    scope?: 'all' | 'main' | 'variant'
    designPackageId?: string
    tags?: string
    responsibleName?: string
    topK?: number
  }) => {
    const form = new FormData()
    if (params.file) form.append('file', params.file)
    if (params.assetId) form.append('assetId', params.assetId)
    if (params.scope) form.append('scope', params.scope)
    if (params.designPackageId) form.append('designPackageId', params.designPackageId)
    if (params.tags) form.append('tags', params.tags)
    if (params.responsibleName) form.append('responsibleName', params.responsibleName)
    if (params.topK) form.append('topK', String(params.topK))
    return request<ImageSearchResponseDto>('/image-search/search', { method: 'POST', body: form })
  },

  /** 重建某个 Asset 的搜索向量（或全库） */
  reindexImageSearch: (assetId?: string) => {
    const form = new FormData()
    if (assetId) form.append('assetId', assetId)
    return request<{ indexed: number; failed: number; failedIds: string[] }>('/image-search/reindex', { method: 'POST', body: form })
  },

  // ---- 订单导入（只保留原始 ZIP/JSON 与规范化结果，不创建第二套素材实体） ----
  importOrderZip: (file: File, categoryCode?: string, actor = 'system') => {
    const form = new FormData()
    form.append('file', file, file.name)
    if (categoryCode) form.append('categoryCode', categoryCode)
    form.append('actor', actor)
    return request<OrderImportResponseDto>('/order-imports', { method: 'POST', body: form })
  },
  listOrderImports: () => request<OrderImportBatchDto[]>('/order-imports'),
  getOrderImportItems: (batchId: string) =>
    request<OrderItemDto[]>(`/order-imports/${encodeURIComponent(batchId)}/items`),

  // ---- 订单素材归因（阶段 B） ----
  /** 对单个订单跑自动素材匹配 */
  autoMatchItem: (itemId: string, actor = 'system') =>
    request<OrderItemDto>(`/order-imports/items/${encodeURIComponent(itemId)}/auto-match`, jsonInit('POST', { actor })),
  /** 对整个批次跑自动素材匹配 */
  autoMatchBatch: (batchId: string, actor = 'system') =>
    request<{ batchId: string; confirmed: number; reviewRequired: number; failed: number }>(
      `/order-imports/${encodeURIComponent(batchId)}/auto-match`, jsonInit('POST', { actor })),
  /** 异常审核列表 */
  matchReview: (params: { scope?: string; category?: string; limit?: number } = {}) => {
    const search = new URLSearchParams()
    if (params.scope) search.set('scope', params.scope)
    if (params.category) search.set('category', params.category)
    if (params.limit) search.set('limit', String(params.limit))
    return request<OrderItemDto[]>(`/order-match/review?${search.toString()}`)
  },
  /** 人工审核：确认 / 更换 Variant / 无法识别 */
  reviewMatch: (itemId: string, payload: { action: 'confirm' | 'change' | 'unmatch'; variantId?: string; note?: string }) =>
    request<OrderItemDto>(`/order-imports/items/${encodeURIComponent(itemId)}/match`, jsonInit('POST', payload)),
  /** 正式销量（仅 CONFIRMED 计入） */
  orderSales: () => request<{
    variantSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    materialSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    categorySales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    totalQuantity: number
    totalOrders: number
  }>('/order-sales'),
}
