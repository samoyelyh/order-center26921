// ============================================================================
// 演示种子数据（领域实体）
//
// 目的：让「设计包 → 上传行为 → Asset → 主素材(MAT) → 设计包位置 → 副素材
//      → 上架版本 → 派发（交付快照）→ ASIN 回填 → 维护记录 → 异常」
// 整条链路在浏览器里真实可验收。
//
// 两个设计包分别模拟两类真实场景：
//   A. 万圣节夜景球迷款：主素材 23 个、V1 只覆盖前 20 个 → 触发「缺素材 + 数量校验报警」
//      （专门验证：补素材不得自动创建 V2）
//   B. 圣诞麋鹿款：主素材 4 个、V1 覆盖 4 个 → 完整可提交并派发，已回填 ASIN
//      （其中位置 1 复用设计包 A 的 MAT-000005，验证「同一 MAT 被多个设计包复用」）
//
// 指纹说明：种子数据用同一套感知哈希语义合成（Demo，仅用于展示指纹字段）。
//   正式库的指纹是 Asset.blake3 / Asset.phash，由后端 BLAKE3 + pHash 计算。
// ============================================================================

import { identityHash, hashWithDistance } from '@/lib/fingerprint'
import {
  checkMaterialCount,
  collectAnomalies,
  computeVersionGap,
  createAsset,
  createMaterial,
  formatBatchCode,
  formatMaterialCode,
  formatVariantCode,
  makeDistributionItems,
  makeLog,
  makeVariantRevision,
  upsertAnomalies,
} from '@/lib/workflow'
import { buildAsinUrl, resolveListingUrl } from '@/lib/asin'
import type {
  ActivityLog,
  Asset,
  DataAnomaly,
  DerivativeBatch,
  DesignPackage,
  DesignPackageMaterial,
  DistributionTask,
  DistributionTaskItem,
  Material,
  PairingView,
  MaterialVariant,
  PackageUpload,
  PsdRevision,
  UploadSession,
  VariantRevision,
} from '@/types/material-workflow'

export interface DemoSeed {
  packages: Record<string, DesignPackage>
  uploads: Record<string, PackageUpload>
  sessions: Record<string, UploadSession>
  batches: Record<string, DerivativeBatch[]>
  assets: Record<string, Asset>
  /** 主素材实体：id → Material（跨设计包共享，禁止复制） */
  materials: Record<string, Material>
  psdRevisions: Record<string, PsdRevision>
  /** 设计包位置：packageId → 位置关联 */
  positions: Record<string, DesignPackageMaterial[]>
  variants: Record<string, MaterialVariant[]>
  variantRevisions: Record<string, VariantRevision>
  /** 演示数据不预置配对：真实配对由后端按同名 pairKey 生成 */
  pairings: Record<string, PairingView[]>
  anomalies: Record<string, DataAnomaly[]>
  logs: Record<string, ActivityLog[]>
  tasks: Record<string, DistributionTask>
  materialSeq: number
}

const DESIGNER = '肖芸'
const DESIGNER_ID = 'u-design-001'
const OPERATOR_ZHANG = { id: 'zhang', name: '张三' }
const OPERATOR_LI = { id: 'li', name: '李四' }

const PACKAGE_A_ID = 'pkg-halloween-001'
const PACKAGE_B_ID = 'pkg-christmas-002'
const UPLOAD_A_ID = 'upload-halloween-001'
const UPLOAD_B_ID = 'upload-christmas-001'
const SESSION_A_ID = 'session-halloween-001'
const SESSION_B_ID = 'session-christmas-001'
const BATCH_A_ID = 'batch-halloween-v1'
const BATCH_B_ID = 'batch-christmas-v1'
const TASK_A_ID = 'task-halloween-v1-zhang'
const TASK_B_ID = 'task-christmas-v1-zhang'

const BASE = new Date('2026-09-16T15:22:00+08:00').getTime()

const at = (hoursFromBase: number, minutes = 0) =>
  new Date(BASE + hoursFromBase * 60 * 60 * 1000 + minutes * 60 * 1000).toISOString()

const mockImage = (index: number) => `/mock/mat-${String(index).padStart(2, '0')}.jpg`

interface VariantSpec {
  position: number
  fileName: string
  previewUri: string
  /** 目标主素材位置（图片内容真实对应的主素材位置） */
  targetPosition: number
}

interface PackageSpec {
  id: string
  uploadId: string
  sessionId: string
  batchId: string
  name: string
  code: string
  originalPackageName: string
  createdAt: string
  mainCount: number
  materialIndexStart: number
  mainImageStart: number
  variantImageStart: number
  versionNo: number
  operator: { id: string; name: string }
  remark: string
  /** 设计包标签（演示用） */
  tags?: string[]
  /** 负责人（演示用，通常是美工） */
  designerName?: string
  /** 位置 1 复用指定 MAT（验证同一 MAT 多包复用），不填则新建 */
  reuseMaterialForFirst?: string
  task?: {
    id: string
    status: DistributionTask['status']
    assignedAt: string
    parentAsin?: string
    childCount?: number
  }
}

function phashOf(seed: string): string {
  return identityHash(seed).slice(0, 16)
}

/** 设计包位置指纹（Demo：匹配用；生产的类似信息挂在 Asset.blake3 / Asset.phash） */
function positionFingerprint(identity: string, perceptualHash?: string) {
  return {
    exactHash: identityHash(`exact:${identity}`),
    exactAlgorithm: 'SHA256' as const,
    perceptualHash: perceptualHash ?? phashOf(identity),
    perceptualAlgorithm: 'AHASH' as const,
    perceptualVersion: 1,
  }
}

export function buildDemoSeed(): DemoSeed {
  const seed: DemoSeed = {
    packages: {},
    uploads: {},
    sessions: {},
    batches: {},
    assets: {},
    materials: {},
    psdRevisions: {},
    positions: {},
    variants: {},
    variantRevisions: {},
    pairings: {},
    anomalies: {},
    logs: {},
    tasks: {},
    materialSeq: 0,
  }

  // ------------------------------------------------------------ 设计包 A
  const specA: PackageSpec = {
    id: PACKAGE_A_ID,
    uploadId: UPLOAD_A_ID,
    sessionId: SESSION_A_ID,
    batchId: BATCH_A_ID,
    name: '万圣节夜景球迷款',
    code: 'DP-20260916-HW001',
    originalPackageName: '万圣节夜景球迷款-20260916.zip',
    createdAt: at(0),
    mainCount: 23,
    materialIndexStart: 1,
    mainImageStart: 1,
    variantImageStart: 11,
    versionNo: 1,
    operator: OPERATOR_ZHANG,
    remark: '万圣节主推款，V1 已确认上架',
    task: { id: TASK_A_ID, status: 'RECEIVED', assignedAt: at(1) },
  }

  /**
   * 副素材与主素材的图片对应关系（故意制造真实错位/缺口场景）：
   *   1-1..4-1   → 正确对应
   *   5-1 ↔ 6-1  → 互换（图片内容与文件名编号不一致 → 命名异常检查）
   *   10-1       → 只有副图、没有同名主素材（多余副图，人工必须处理）
   *   11-1..20-1 → 正确对应
   *   21/22/23   → 本版本没有副素材（缺素材，触发补素材流程）
   */
  const variantSpecsA: VariantSpec[] = []
  for (let position = 1; position <= 20; position += 1) {
    let targetPosition = position
    if (position === 5) targetPosition = 6
    else if (position === 6) targetPosition = 5
    else if (position === 10) targetPosition = 12
    variantSpecsA.push({
      position,
      fileName: `${position}-1.jpg`,
      previewUri: mockImage(specA.variantImageStart + position - 1),
      targetPosition,
    })
  }

  buildPackage(seed, specA, variantSpecsA, { autoMatch: true, confirmHigh: false })

  // ------------------------------------------------------------ 设计包 B
  const specB: PackageSpec = {
    id: PACKAGE_B_ID,
    uploadId: UPLOAD_B_ID,
    sessionId: SESSION_B_ID,
    batchId: BATCH_B_ID,
    name: '圣诞麋鹿款',
    code: 'DP-20260917-CM002',
    originalPackageName: '圣诞麋鹿款-20260917.zip',
    createdAt: at(30),
    mainCount: 4,
    materialIndexStart: 24,
    mainImageStart: 5,
    variantImageStart: 25,
    versionNo: 1,
    operator: OPERATOR_LI,
    remark: '圣诞系列，20 个 Child ASIN 已上架',
    reuseMaterialForFirst: formatMaterialCode(5),
    task: { id: TASK_B_ID, status: 'COMPLETED', assignedAt: at(31), parentAsin: 'B0CM000001', childCount: 20 },
  }

  const variantSpecsB: VariantSpec[] = Array.from({ length: 4 }, (_, index) => {
    const position = index + 1
    return {
      position,
      fileName: `${position}-1.jpg`,
      previewUri: mockImage(specB.variantImageStart + index),
      targetPosition: position,
    }
  })

  buildPackage(seed, specB, variantSpecsB, { autoMatch: true, confirmHigh: true })

  // 设计包 B：运营回填与派发历史
  if (specB.task) {
    const asins = Array.from({ length: specB.task.childCount ?? 0 }, (_, index) =>
      `B0CM${String(index + 1).padStart(6, '0')}`)
    seed.logs[PACKAGE_B_ID] = [
      ...(seed.logs[PACKAGE_B_ID] ?? []),
      makeLog({
        designPackageId: PACKAGE_B_ID,
        targetType: 'DISTRIBUTION',
        targetId: TASK_B_ID,
        actor: DESIGNER,
        action: 'DISPATCH',
        summary: `派发给 ${OPERATOR_LI.name}，版本 V1，第 1 次交付 4 张副素材（复用同一套底层素材，不复制图片）`,
      }),
      makeLog({
        designPackageId: PACKAGE_B_ID,
        targetType: 'DISTRIBUTION',
        targetId: TASK_B_ID,
        actor: OPERATOR_LI.name,
        action: 'RECEIVE',
        summary: '接收素材（版本 V1，4 项交付）',
      }),
      makeLog({
        designPackageId: PACKAGE_B_ID,
        targetType: 'PARENT_ASIN',
        targetId: specB.task.parentAsin ?? '',
        actor: OPERATOR_LI.name,
        action: 'PARENT_ASIN',
        summary: '回填 Parent ASIN',
        after: specB.task.parentAsin,
      }),
      makeLog({
        designPackageId: PACKAGE_B_ID,
        targetType: 'CHILD_ASIN',
        targetId: specB.task.parentAsin ?? '',
        actor: OPERATOR_LI.name,
        action: 'CHILD_ASIN',
        summary: `回填 Child ASIN ${asins.length} 个`,
        after: `${asins[0]} ~ ${asins[asins.length - 1]}`,
      }),
    ]
  }

  return seed
}

// ---------------------------------------------------------------- 构造单个设计包

interface BuildOptions {
  autoMatch: boolean
  confirmHigh: boolean
}

function buildPackage(seed: DemoSeed, spec: PackageSpec, variantSpecs: VariantSpec[], options: BuildOptions) {
  const createdAt = spec.createdAt

  const pkg: DesignPackage = {
    id: spec.id,
    code: spec.code,
    name: spec.name,
    // 演示数据：设计编码就用演示设计包编码（真实数据由用户上传时填写）
    designCode: spec.code,
    tags: spec.tags ?? [],
    responsibleName: spec.designerName ?? DESIGNER,
    remark: spec.remark,
    createdBy: DESIGNER,
    createdAt,
    updatedAt: at(1),
  }

  const upload: PackageUpload = {
    id: spec.uploadId,
    designPackageId: spec.id,
    originalPackageName: spec.originalPackageName,
    fileSize: 1024 * 1024 * (12 + spec.mainCount),
    uploadSessionId: spec.sessionId,
    uploaderId: DESIGNER_ID,
    uploaderName: DESIGNER,
    uploadType: 'INITIAL',
    targetBatchId: spec.batchId,
    status: spec.task ? 'SUBMITTED' : 'MATCHED',
    remark: spec.remark,
    createdAt,
    updatedAt: at(1),
  }

  const session: UploadSession = {
    id: spec.sessionId,
    designPackageId: spec.id,
    packageUploadId: spec.uploadId,
    originalPackageName: spec.originalPackageName,
    fileSize: upload.fileSize ?? 0,
    stage: spec.task ? 'SUBMITTED' : 'MATCHED',
    uploadType: 'INITIAL',
    operatorId: spec.operator.id,
    operatorName: spec.operator.name,
    remark: spec.remark,
    receivedFiles: [
      ...Array.from({ length: spec.mainCount }, (_, i) => `${i + 1}.psd`),
      ...variantSpecs.map((v) => v.fileName),
    ],
    createdAt,
    updatedAt: at(1),
  }

  const batch: DerivativeBatch = {
    id: spec.batchId,
    designPackageId: spec.id,
    versionNo: spec.versionNo,
    code: formatBatchCode(spec.versionNo),
    createdFromUploadId: spec.uploadId,
    createdAt,
    mainMaterialCountAtCreation: spec.mainCount,
  }

  const pushAsset = (asset: Asset) => {
    seed.assets[asset.id] = asset
  }

  // ---- 主素材（MAT）+ Asset + PSD Revision + 设计包位置 ----
  const materials: Material[] = []
  const positions: DesignPackageMaterial[] = []

  for (let index = 0; index < spec.mainCount; index += 1) {
    const position = index + 1
    const previewUri = mockImage(spec.mainImageStart + index)
    const isReuseSlot = position === 1 && Boolean(spec.reuseMaterialForFirst)

    if (isReuseSlot) {
      // 复用已有 MAT：不新建、不复制主数据，只新增位置关联
      const reused = seed.materials[spec.reuseMaterialForFirst!]
      positions.push({
        id: `${spec.id}-pos-${position}`,
        designPackageId: spec.id,
        position,
        materialId: reused.id,
        materialCode: reused.materialCode,
        displayName: reused.name,
        previewUri: seed.assets[reused.previewAssetId]?.storageKey ?? previewUri,
        sourceFileName: `${position}.psd`,
        fingerprint: positionFingerprint(`main:${spec.id}:${position}`),
        createdFromUploadId: spec.uploadId,
        createdAt,
      })
      continue
    }

    const materialCode = formatMaterialCode(spec.materialIndexStart + index)

    const previewAsset = createAsset({
      storageKey: previewUri,
      originalFilename: `${position}.psd`,
      mimeType: 'image/jpeg',
      sizeBytes: 1024 * 512,
      width: 4000,
      height: 4000,
      blake3: identityHash(`blake3:preview:${materialCode}`),
      phash: phashOf(`phash:${spec.id}:${position}`),
      phashVersion: 1,
      createdBy: DESIGNER,
      createdAt,
    })
    const psdAsset = createAsset({
      storageKey: `/mock/${materialCode}-source.psd`,
      originalFilename: `${materialCode}.psd`,
      mimeType: 'image/vnd.adobe.photoshop',
      sizeBytes: 1024 * 1024 * 86,
      width: 4000,
      height: 4000,
      blake3: identityHash(`blake3:psd:${materialCode}:1`),
      createdBy: DESIGNER,
      createdAt,
    })
    pushAsset(previewAsset)
    pushAsset(psdAsset)

    const { material, psdRevision } = createMaterial({
      materialCode,
      name: `${spec.name} ${position}`,
      previewAsset,
      psdAsset,
      uploader: DESIGNER,
      createdAt,
      tags: [spec.name.slice(0, 3)],
      sourcePackageUploadId: spec.uploadId,
    })
    if (psdRevision) seed.psdRevisions[psdRevision.id] = psdRevision
    materials.push(material)
    seed.materials[material.id] = material
    seed.materialSeq = Math.max(seed.materialSeq, Number(materialCode.replace(/\D/g, '')))

    positions.push({
      id: `${spec.id}-pos-${position}`,
      designPackageId: spec.id,
      position,
      materialId: material.id,
      materialCode: material.materialCode,
      displayName: material.name,
      previewUri,
      sourceFileName: `${position}.psd`,
      fingerprint: {
        exactHash: identityHash(`main-blake3:${materialCode}`),
        exactAlgorithm: 'SHA256',
        perceptualHash: phashOf(`phash:${spec.id}:${position}`),
        perceptualAlgorithm: 'AHASH',
        perceptualVersion: 1,
      },
      createdFromUploadId: spec.uploadId,
      createdAt,
    })
  }

  // ---- 副素材（MaterialVariant）+ Asset + VariantRevision ----
  const positionByIndex = new Map(positions.map((p) => [p.position, p]))
  const variants: MaterialVariant[] = variantSpecs.map((variantSpec, index) => {
    const variantId = `${spec.id}-variant-${variantSpec.position}-${spec.versionNo}`
    const linkedPosition = positionByIndex.get(variantSpec.position) ?? positions[0]
    const targetPosition = positionByIndex.get(variantSpec.targetPosition) ?? linkedPosition
    const distance = variantSpec.targetPosition === variantSpec.position ? 0 : 10
    const perceptualHash = hashWithDistance(
      targetPosition.fingerprint.perceptualHash,
      distance,
      spec.mainCount * 131 + index + 1,
    )

    const asset = createAsset({
      storageKey: variantSpec.previewUri,
      originalFilename: variantSpec.fileName,
      mimeType: 'image/jpeg',
      sizeBytes: 1024 * 320,
      width: 2000,
      height: 2000,
      blake3: identityHash(`blake3:variant:${spec.id}:${variantSpec.fileName}:${index}`),
      phash: perceptualHash,
      phashVersion: 1,
      createdBy: DESIGNER,
      createdAt,
    })
    pushAsset(asset)

    const revision = makeVariantRevision({
      variantId,
      revisionNo: 1,
      assetId: asset.id,
      actor: DESIGNER,
      createdAt,
    })
    seed.variantRevisions[revision.id] = revision

    return {
      id: variantId,
      materialId: linkedPosition?.materialId ?? '',
      designPackageMaterialId: linkedPosition?.id ?? '',
      batchId: spec.batchId,
      displayCode: formatVariantCode(linkedPosition?.position ?? variantSpec.position, spec.versionNo),
      currentRevisionId: revision.id,
      revisions: [revision],
      tags: [],
      deleted: false,
      createdAt,
      updatedAt: createdAt,
    }
  })

  seed.packages[spec.id] = pkg
  seed.uploads[spec.uploadId] = upload
  seed.sessions[spec.sessionId] = session
  seed.batches[spec.id] = [batch]
  seed.positions[spec.id] = positions
  seed.variants[spec.id] = variants

  // ---- 维护记录 ----
  seed.logs[spec.id] = [
    makeLog({
      designPackageId: spec.id,
      targetType: 'UPLOAD',
      targetId: spec.uploadId,
      actor: DESIGNER,
      action: 'UPLOAD_PACKAGE',
      summary: `上传原始文件包 ${spec.originalPackageName}（首次上传，创建新版本），归属运营 ${spec.operator.name}`,
    }),
    makeLog({
      designPackageId: spec.id,
      targetType: 'DERIVATIVE_BATCH',
      targetId: spec.batchId,
      actor: '系统',
      action: 'CREATE_BATCH',
      summary: `创建上架版本 ${batch.code}（整套设计包统一版本，非按素材自增）`,
    }),
  ]

  // ---- 配对（只按同名 pairKey，不做任何相似度匹配）----
  // 演示数据不预置配对结果：真实配对由后端 POST /api/uploads/{id}/pair 按同名生成。
  if (options.autoMatch) {
    const countCheck = checkMaterialCount(positions.length, variants.length)
    const coverage = computeVersionGap(batch, positions, variants)
    const incoming = collectAnomalies({
      pkg,
      batch,
      batches: [batch],
      positions,
      variants,
      pairings: [],
      coverage,
      countCheck,
      operatorId: spec.operator.id,
      sessionStage: spec.task ? 'SUBMITTED' : 'MATCHED',
      assets: seed.assets,
    })
    seed.anomalies[spec.id] = upsertAnomalies([], incoming)
    seed.logs[spec.id] = [
      ...seed.logs[spec.id],
      ...incoming.map((a) => makeLog({
        designPackageId: spec.id,
        targetType: 'DERIVATIVE_BATCH',
        targetId: spec.batchId,
        actor: '系统',
        action: 'RUN_PAIRING' as const,
        summary: a.message,
      })),
      makeLog({
        designPackageId: spec.id,
        targetType: 'DESIGN_PACKAGE',
        targetId: spec.id,
        actor: '系统',
        action: 'RUN_PAIRING',
        summary: '主副素材配对只按文件名同名（pairKey）；演示数据未预置配对结果',
      }),
    ]
  }

  // ---- 派发任务（含交付快照 items）----
  if (spec.task) {
    const children = Array.from({ length: spec.task.childCount ?? 0 }, (_, index) => {
      const asin = `B0CM${String(index + 1).padStart(6, '0')}`
      return { asin, site: 'US' as const, listingUrl: buildAsinUrl(asin, 'US') }
    })
    const items: DistributionTaskItem[] = makeDistributionItems({
      distributionTaskId: spec.task.id,
      variants,
      deliveryRound: 1,
      createdAt: spec.task.assignedAt,
    })
    const task: DistributionTask = {
      id: spec.task.id,
      designPackageId: spec.id,
      batchId: spec.batchId,
      // SNAPSHOT FIELD：派发当时看到的名称
      packageName: spec.name,
      packageCode: spec.code,
      versionCode: batch.code,
      designerName: DESIGNER,
      operatorId: spec.operator.id,
      operatorName: spec.operator.name,
      status: spec.task.status,
      items,
      parentAsin: spec.task.parentAsin
        ? {
          asin: spec.task.parentAsin,
          site: 'US',
          listingUrl: resolveListingUrl({ asin: spec.task.parentAsin, site: 'US' }),
        }
        : undefined,
      children,
      assignedAt: spec.task.assignedAt,
      receivedAt: spec.task.status === 'ACTIVE' ? undefined : at(31, 10),
      completedAt: spec.task.parentAsin ? at(32) : undefined,
      remark: spec.remark,
    }
    seed.tasks = { ...seed.tasks, [task.id]: task }
  }

  return { pkg, upload, session, batch, materials, positions, variants }
}
