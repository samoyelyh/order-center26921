// 演示占位数据：真实数据由 src/store/workflowStore.ts 与 src/mock/demoSeed.ts 提供，
// 这里保留空对象仅用于未接入后端时的兜底渲染。

import type {
  ActivityLog,
  Asset,
  DataAnomaly,
  DerivativeBatch,
  DesignPackage,
  DistributionTask,
  Material,
  PairingView,
  MaterialVariant,
  PackageUpload,
} from '@/types/material-workflow'

/** 兜底用的空 Asset id 常量 */
export const EMPTY_ASSET_ID = ''

export const MOCK_ASSET: Asset = {
  id: '', storageKey: '', originalFilename: '', mimeType: '', sizeBytes: 0,
  createdBy: '', createdAt: '',
}

export const MOCK_MATERIAL: Material = {
  id: '', materialCode: '', name: '', type: 'MAIN',
  previewAssetId: EMPTY_ASSET_ID, tags: [], createdAt: '', updatedAt: '', createdBy: '',
}

export const MOCK_PACKAGE: DesignPackage = {
  id: '', code: '', name: '', designCode: '', tags: [], responsibleName: '', createdBy: '', createdAt: '', updatedAt: '',
}

export const MOCK_UPLOAD: PackageUpload = {
  id: '', designPackageId: '', originalPackageName: '', uploadSessionId: '',
  uploaderId: '', uploaderName: '', uploadType: 'INITIAL', status: 'PARSING',
  createdAt: '', updatedAt: '',
}

export const MOCK_BATCH: DerivativeBatch = {
  id: '', designPackageId: '', versionNo: 0, code: '',
  createdFromUploadId: '', createdAt: '', mainMaterialCountAtCreation: 0,
}

export const MOCK_VARIANTS: MaterialVariant[] = []

export const MOCK_PAIRINGS: PairingView[] = []

export const MOCK_ANOMALIES: DataAnomaly[] = []

export const MOCK_ACTIVITY: ActivityLog[] = []

export const MOCK_TASK: DistributionTask = {
  id: '', designPackageId: '', batchId: '',
  packageName: '', packageCode: '', versionCode: '',
  designerName: '', operatorId: '', operatorName: '',
  status: 'ACTIVE', items: [], children: [],
  assignedAt: '',
}
