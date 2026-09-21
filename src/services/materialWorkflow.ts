import { MOCK_ANOMALIES, MOCK_BATCH, MOCK_PACKAGE, MOCK_TASK } from '@/mock/materialWorkflow'
import type { DataAnomaly, DerivativeBatch, DesignPackage, DistributionTask, PairingView } from '@/types/material-workflow'

/**
 * 服务层：组件只依赖这里的函数签名。
 * 当前实现指向内存 store（可完整走通业务链路），后端接入时逐个替换为真实 API 即可，
 * 组件层不需要改动。真实浏览器内文件指纹计算见 src/lib/fingerprint.ts。
 */

export { computeFingerprintFromBlob } from '@/lib/fingerprint'

export async function calculateFingerprint(file: File) {
  const { computeFingerprintFromBlob } = await import('@/lib/fingerprint')
  return computeFingerprintFromBlob(file)
}

/**
 * 后端接入前：返回演示设计包的解析结果。
 *
 * 主副素材配对由后端按同名 pairKey 生成，这里**不预置任何配对结果**。
 */
export async function analyzeDesignPackage(
  file: File,
): Promise<{ package: DesignPackage; pairings: PairingView[] }> {
  await Promise.resolve()
  return { package: { ...MOCK_PACKAGE, name: file.name }, pairings: [] }
}

/** 按同名 pairKey 配对（真实实现见后端 POST /api/uploads/{id}/pair） */
export async function pairPackageMaterials(
  packageId: string,
): Promise<{ packageId: string; pairings: PairingView[]; batch: DerivativeBatch }> {
  const { getWorkflowState } = await import('@/store/workflowStore')
  const batches = getWorkflowState().batches[packageId] ?? []
  return {
    packageId,
    pairings: getWorkflowState().pairings[packageId] ?? [],
    batch: batches[batches.length - 1] ?? MOCK_BATCH,
  }
}

/** 确认整包配对 */
export async function confirmMaterialPairings(pairings: PairingView[]) {
  return {
    confirmedAt: new Date().toISOString(),
    count: pairings.filter((item) => item.variantAssetId).length,
  }
}

export async function downloadDistributionPackage(taskId: string) {
  const { getWorkflowState } = await import('@/store/workflowStore')
  const task = getWorkflowState().tasks[taskId] ?? MOCK_TASK
  return {
    taskId,
    fileName: `${task.packageName}_${task.versionCode}.zip`,
    url: '#',
    mock: true,
  }
}

export async function getDistributionTask(taskId: string): Promise<DistributionTask> {
  const { getWorkflowState } = await import('@/store/workflowStore')
  return getWorkflowState().tasks[taskId] ?? { ...MOCK_TASK, id: taskId }
}

export async function getPackageAnomalies(): Promise<DataAnomaly[]> {
  const { getWorkflowState } = await import('@/store/workflowStore')
  const all = Object.values(getWorkflowState().anomalies).flat()
  return all.length ? all : MOCK_ANOMALIES
}

export { MOCK_BATCH, MOCK_PACKAGE, MOCK_TASK }
