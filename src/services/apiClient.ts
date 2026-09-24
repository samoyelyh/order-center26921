// ============================================================================
// order-center 后端 API 客户端
//
// 只负责订单域 HTTP：
//   - 统一 base url（跟随页面 hostname:8010）
//   - 把后端 {code, message} 错误转成带 code 的 ApiError
//   - 素材域信息（Variant 候选）走 order-center 后端 /material-platform/* 契约代理
// ============================================================================

const DEFAULT_API_PORT = '8010'

export function resolveApiBase(): string {
  const configured = (import.meta.env.VITE_MATERIAL_API_BASE as string | undefined)?.trim()
  if (configured) return configured.replace(/\/+$/, '')

  const port = (import.meta.env.VITE_MATERIAL_API_PORT as string | undefined)?.trim() || DEFAULT_API_PORT
  if (typeof window !== 'undefined' && window.location?.hostname) {
    const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
    return `${protocol}//${window.location.hostname}:${port}/api`
  }
  return `http://127.0.0.1:${port}/api`
}

export const API_BASE = resolveApiBase()

export class ApiError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, init)
  if (!res.ok) {
    let detail: { code?: string; message?: string; detail?: { code?: string; message?: string } } = {}
    try {
      detail = await res.json()
    } catch { /* ignore */ }
    const error = detail.detail ?? detail
    throw new ApiError(error.code ?? 'HTTP_ERROR', error.message ?? `请求失败（${res.status}）`)
  }
  return res.json() as Promise<T>
}

function jsonInit(method: string, payload?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }
}

// ================================================================ DTO

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
  parsedAt: string
  parseStatus: 'PARSED' | 'PARTIAL' | 'FAILED' | 'REVIEW_REQUIRED' | 'NON_CUSTOM'
  rawJsonHash: string
  matchedVariantId?: string | null
  matchedMaterialId?: string | null
  matchMethod?: string | null
  matchScore?: number | null
  secondMatchScore?: number | null
  scoreGap?: number | null
  matchReason?: string | null
  matchStatus: 'NOT_STARTED' | 'UNMATCHED' | 'REVIEW_REQUIRED' | 'CONFIRMED' | 'FAILED'
  normalized?: Record<string, unknown> | null
}

export interface OrderImportResponseDto {
  batch: OrderImportBatchDto
  summary: Record<string, number>
}

export interface RawOrderJsonDto {
  itemId: string
  available: boolean
  path?: string | null
  rawJson?: unknown
}

/** 素材平台契约返回的 Variant 候选（order-center 前端只读这些展示字段） */
export interface MaterialVariantOption {
  variantId: string
  materialId: string | null
  categoryCode: string | null
  displayCode: string | null
  materialCode?: string | null
  batchId?: string | null
  batchCode?: string | null
  versionNo?: number | null
  designPackageId?: string | null
  images?: Array<{ imageRole: string; soleColor?: string | null; assetId?: string | null; uri?: string | null }>
}

export interface MaterialVariantContract {
  childAsin: string
  categoryCode: string | null
  batch: { batchId: string; batchCode?: string | null; versionNo?: number | null; categoryCode?: string | null; designPackageId?: string | null } | null
  variants: MaterialVariantOption[]
}

export interface OrderSalesDto {
  variantSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
  materialSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
  categorySales: Record<string, { quantity: number; orders: number; categoryCode: string }>
  totalQuantity: number
  totalOrders: number
}

// ================================================================ API

export const materialApi = {
  // ---- 订单导入 ----
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
  getOrderItemRawJson: (itemId: string) =>
    request<RawOrderJsonDto>(`/order-items/${encodeURIComponent(itemId)}/raw-json`),

  // ---- 素材归因 ----
  autoMatchItem: (itemId: string, actor = 'system') =>
    request<OrderItemDto>(`/order-imports/items/${encodeURIComponent(itemId)}/auto-match`, jsonInit('POST', { actor })),
  autoMatchBatch: (batchId: string, actor = 'system') =>
    request<{ batchId: string; confirmed: number; reviewRequired: number; failed: number }>(
      `/order-imports/${encodeURIComponent(batchId)}/auto-match`, jsonInit('POST', { actor })),
  matchReview: (params: { scope?: string; category?: string; limit?: number } = {}) => {
    const search = new URLSearchParams()
    if (params.scope) search.set('scope', params.scope)
    if (params.category) search.set('category', params.category)
    if (params.limit) search.set('limit', String(params.limit))
    return request<OrderItemDto[]>(`/order-match/review?${search.toString()}`)
  },
  reviewMatch: (itemId: string, payload: { action: 'confirm' | 'change' | 'unmatch'; variantId?: string; note?: string }) =>
    request<OrderItemDto>(`/order-imports/items/${encodeURIComponent(itemId)}/match`, jsonInit('POST', payload)),
  orderSales: () => request<OrderSalesDto>('/order-sales'),

  // ---- 素材平台契约代理（order-center 后端转发到 material-platform） ----
  materialPlatformVariants: (childAsin: string) =>
    request<MaterialVariantContract>(`/material-platform/variants?childAsin=${encodeURIComponent(childAsin)}`),
}
