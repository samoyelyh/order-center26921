// ============================================================================
// 领域实体 → 素材中心 UI 视图模型 的适配层
//
// 素材中心既有组件（MaterialCard / MaterialGrid / MaterialDrawer / MaterialBasicInfo）
// 消费的是 src/types/material.ts 里的 Material（UI 视图模型）。
// 领域模型是 Material / Asset / DesignPackageMaterial / DerivativeBatch / MaterialVariant，
// 因此所有拼装逻辑集中放在这里，组件不再关心领域结构，也不关心 assetId → Asset 的展开。
// ============================================================================

import type { Material as MaterialView, MaterialSource, MaterialType as ViewMaterialType } from '@/types/material'
import { currentPsdRevision, currentVariantRevision, variantVersionNo } from '@/lib/workflow'
import type {
  Asset,
  DerivativeBatch,
  DesignPackage,
  DesignPackageMaterial,
  Material,
  MaterialVariant,
  MaterialVariantRow,
  PsdRevision,
  VariantRevision,
} from '@/types/material-workflow'

export type { MaterialVariantRow }

const UI_DEFAULTS = {
  category: '图案',
  subCategory: '通用',
  source: '原创' as MaterialSource,
  type: '插画' as ViewMaterialType,
  copyright: '公司自有',
}

export function formatUploadTime(iso: string): string {
  if (!iso) return '-'
  return iso.slice(0, 16).replace('T', ' ')
}

/**
 * 订单/销量类指标属于 Phase 3（派发 + ASIN 归因），本阶段**没有任何真实来源**。
 * 之前这里按 MAT 编号编了一个「30 天订单」的假数字，会让用户以为系统已经统计了销量，
 * 因此本轮直接置 0，页面也不再展示（绝不显示编造的业务数据）。
 */

export interface MainMaterialViewInput {
  material: Material
  /** 预览图资产（由 previewAssetId → Asset 展开） */
  asset?: Asset
  /** 该 MAT 在设计包中的位置（取第一个） */
  position?: DesignPackageMaterial
  designPackage?: DesignPackage
  /** 该 MAT 被多少个设计包位置引用 */
  designCount: number
  /** 该 MAT 关联的 ASIN 数量（Phase 3） */
  asinCount: number
  /** 该 MAT 名下的副素材数量（真实数据，副素材 Tab / 卡片展示用） */
  variantCount?: number
  /** 主素材卡片上直接显示的副素材缩略图（来自真实副素材，按 displayCode 排序） */
  variantPreviews?: { code: string; imageUri: string; variantId: string }[]
  /** 主素材标签（来自 Material.tags，建包时继承自设计包） */
  tags?: string[]
  /** PSD 修订历史（用于判断是否有 PSD） */
  psdRevisions?: PsdRevision[]
}

/** 主素材：Material 实体 + Asset + 位置上下文 → UI 视图模型 */
export function toMainMaterialView(input: MainMaterialViewInput): MaterialView {
  const { material, asset, position, designPackage } = input
  const psd = currentPsdRevision(material, input.psdRevisions ?? [])
  const hasPsd = Boolean(psd)
  return {
    id: material.materialCode,
    name: material.name,
    image: asset?.storageKey ?? position?.previewUri ?? '',
    transparent: false,
    ...UI_DEFAULTS,
    scenes: designPackage ? [designPackage.name] : [],
    styles: [],
    crafts: [],
    areas: [],
    fitCategories: [],
    designCount: input.designCount,
    asinCount: input.asinCount,
    variantCount: input.variantCount ?? 0,
    variantPreviews: input.variantPreviews ?? [],
    // Phase 3 才有真实来源，本阶段一律 0
    orders30: 0,
    prevOrders30: 0,
    uploadTime: formatUploadTime(material.createdAt),
    uploader: material.createdBy,
    size: '4000 × 4000',
    format: hasPsd ? 'PSD / JPG' : 'JPG',
    fileName: asset?.originalFilename ?? position?.sourceFileName ?? '',
    tags: input.tags ?? material.tags,
    risk: false,
    kind: 'MAIN',
    // 「找相似」用：主素材的图片 Asset
    assetId: material.previewAssetId,
    designPackageId: designPackage?.id,
    designPackageName: designPackage?.name,
    // 负责人来自所属设计包（不复制到素材上）
    responsibleName: designPackage?.responsibleName ?? designPackage?.designerName,
    mainMaterialCode: material.materialCode,
    currentRevision: psd?.revisionNo ?? 0,
    // 位置上下文：抽屉里用来显示位置信息
    versionCode: position ? String(position.position) : undefined,
  }
}

export interface VariantViewInput {
  variant: MaterialVariant
  /** 当前 Revision 对应的资产 */
  asset?: Asset
  batch?: DerivativeBatch
  /** 所属主素材（副素材的主素材） */
  mainMaterial?: Material
  designPackage?: DesignPackage
}

/** 副素材：MaterialVariant 实体 → UI 视图模型（素材中心按「副素材」筛选时使用） */
export function toVariantMaterialView(input: VariantViewInput): MaterialView {
  const { variant, asset, batch, mainMaterial, designPackage } = input
  const revision = currentVariantRevision(variant)
  return {
    id: variant.id,
    name: `副素材 ${variant.displayCode}`,
    image: asset?.storageKey ?? '',
    ...UI_DEFAULTS,
    scenes: designPackage ? [designPackage.name] : [],
    styles: [],
    crafts: [],
    areas: [],
    fitCategories: [],
    designCount: 0,
    asinCount: 0,
    orders30: 0,
    prevOrders30: 0,
    uploadTime: formatUploadTime(revision?.createdAt ?? variant.createdAt),
    uploader: revision?.createdBy ?? designPackage?.createdBy ?? '',
    size: '2000 × 2000',
    format: 'JPG',
    fileName: asset?.originalFilename ?? '',
    tags: variant.tags ?? [],
    kind: 'VARIANT',
    designPackageId: designPackage?.id,
    designPackageName: designPackage?.name,
    versionCode: batch?.code ?? '-',
    batchVersionNo: batch?.versionNo,
    mainMaterialCode: mainMaterial?.materialCode,
    currentRevision: revision?.revisionNo ?? 0,
    // 「找相似」用：副素材当前 Revision 的图片 Asset
    assetId: revision?.assetId,
    // 副素材卡片要显示负责人：取自所属设计包（不复制到素材上）
    responsibleName: designPackage?.responsibleName ?? designPackage?.designerName,
  }
}

/** 主素材详情里的「副素材」条目（Drawer 副素材 Tab） */
export function toMaterialVariantRow(input: {
  variant: MaterialVariant
  designPackage?: DesignPackage
  batches: DerivativeBatch[]
  assets?: Record<string, Asset>
  revisions?: VariantRevision[]
}): MaterialVariantRow {
  const { variant, designPackage, batches, assets = {} } = input
  const revision = currentVariantRevision(variant)
  const asset = revision ? assets[revision.assetId] : undefined
  const batch = batches.find((b) => b.id === variant.batchId)
  const revisions = input.revisions ?? variant.revisions
  return {
    variant,
    designPackageName: designPackage?.name ?? '',
    versionCode: batch?.code ?? '-',
    batchVersionNo: variantVersionNo(variant, batches),
    imageUri: asset?.storageKey ?? '',
    fileName: asset?.originalFilename ?? '',
    currentRevision: revision?.revisionNo ?? 0,
    reusedByBatchCode: variant.reusedByBatchCode,
    revisionHistory: revisions
      .slice()
      .sort((a, b) => b.revisionNo - a.revisionNo)
      .map((r) => ({ ...r, asset: assets[r.assetId] })),
  }
}
