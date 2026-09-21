import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Clock3, Info } from 'lucide-react'
import type { ActivityAction, ActivityLog, DataAnomaly, PairingSource, PairingStatus, PairingView } from '@/types/material-workflow'
import { formatDateTime } from '@/lib/workflow'

export type BadgeTone = 'neutral' | 'green' | 'amber' | 'red' | 'purple'

export function StatusBadge({ children, tone = 'neutral' }: { children: ReactNode; tone?: BadgeTone }) {
  const tones: Record<BadgeTone, string> = {
    neutral: 'bg-gray-100 text-gray-600',
    green: 'bg-emerald-50 text-emerald-700',
    amber: 'bg-amber-50 text-amber-700',
    red: 'bg-red-50 text-red-700',
    purple: 'bg-[#f0eef9] text-[#3d3192]',
  }
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>{children}</span>
}

/**
 * 配对状态展示口径。
 *
 * 只有三种状态：已配对 / 缺副图 / 已确认；关系只按同名 pairKey。
 */
export const PAIRING_STATUS_META: Record<PairingStatus, { label: string; tone: BadgeTone }> = {
  PAIRED: { label: '已配对（待确认）', tone: 'green' },
  UNPAIRED: { label: '缺副图', tone: 'amber' },
  CONFIRMED: { label: '已确认', tone: 'purple' },
}

export function PairingStatusBadge({
  status,
  source,
}: {
  status: PairingStatus
  source?: PairingSource
}) {
  const meta = PAIRING_STATUS_META[status]
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
      {source === 'MANUAL' && status !== 'UNPAIRED' && (
        <StatusBadge tone="neutral">人工修改</StatusBadge>
      )}
    </div>
  )
}

/**
 * 维护记录：全系统统一一套，包含上传、同名配对、人工确认/修改、
 * 创建版本/Revision，以及后续业务动作。
 */
export function ActivityTimeline({ logs, emptyText = '暂无维护记录' }: { logs: ActivityLog[]; emptyText?: string }) {
  if (!logs.length) return <p className="text-xs text-gray-400">{emptyText}</p>
  return (
    <div className="space-y-3">
      {logs.map((log) => (
        <div key={log.id} className="flex gap-3 text-xs">
          <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#f0eef9] text-[#3d3192]">
            <Clock3 className="h-3 w-3" />
          </div>
          <div className="min-w-0">
            <p className="text-gray-700">
              <span className="font-medium">{log.actor}</span> {log.summary}
            </p>
            {log.before !== undefined && log.after !== undefined && (
              <p className="mt-0.5 text-[11px] text-gray-500">
                {log.before || '（空）'} → <span className="text-gray-700">{log.after || '（空）'}</span>
              </p>
            )}
            <p className="text-gray-400">
              {formatDateTime(log.createdAt)}
              <span className="ml-1.5 text-gray-300">{ACTION_LABEL[log.action] ?? log.action}</span>
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

const ACTION_LABEL: Record<ActivityAction, string> = {
  UPLOAD_PACKAGE: '上传设计包',
  RUN_PAIRING: '按同名配对',
  CONFIRM_PAIRING: '确认配对',
  UPDATE_PAIRING: '修改配对',
  REASSIGN_OUT: '释放原配对',
  CREATE_BATCH: '创建上架版本',
  CREATE_VARIANT: '创建副素材',
  REUSE_VARIANT: '复用副素材',
  VARIANT_REVISION: '副素材 Revision',
  MATERIAL_REVISION: '主素材 PSD Revision',
  DISPATCH: '派发运营',
  RECEIVE: '运营接收',
  COMPLETE_DISTRIBUTION: '派发完成',
  CANCEL_DISTRIBUTION: '取消派发',
  PARENT_ASIN: 'Parent ASIN 回填',
  CHILD_ASIN: 'Child ASIN 回填',
  UPDATE_ASIN: '修改 ASIN',
  SUPPLEMENT_V1: '补充版本素材',
  SUPPLEMENT_DISPATCH: '补充派发',
  RESOLVE_ANOMALY: '异常处理',
  REOPEN_ANOMALY: '异常重开',
}

export const ANOMALY_TYPE_LABEL: Record<DataAnomaly['type'], string> = {
  MATERIAL_COUNT_MISMATCH: '主副图数量不一致',
  MISSING_VARIANT: '缺同名副图',
  EXTRA_VARIANT: '多余副图',
  DUPLICATE_PAIR_KEY: '同名配对键重复',
  PAIRING_NOT_CONFIRMED: '配对待确认',
  DUPLICATE_FILE: '完全重复文件',
  FILENAME_WARNING: '文件命名异常',
  UPLOAD_INTERRUPTED: '上传中断',
  BATCH_INCOMPLETE: '版本缺素材',
  BATCH_VERSION_CONFLICT: '版本号不统一',
  FILE_MISSING: '文件缺失',
  DISTRIBUTION_MISSING: '未派发运营',
  DUPLICATE_DISTRIBUTION: '重复派发',
  ASIN_CONFLICT: 'ASIN 冲突',
  ORDER_URL_MATCH_FAILED: '订单URL识别失败',
}

export const ANOMALY_STATUS_LABEL: Record<DataAnomaly['status'], string> = {
  OPEN: 'OPEN',
  RESOLVED: 'RESOLVED',
  REOPENED: 'REOPENED',
}

/**
 * 异常中心列表：OPEN / RESOLVED / REOPENED，并预留「通知美工组长」入口。
 * 第二十八条：没有钉钉接入时先完成前端异常入口和数据结构。
 */
export function AnomalyList({
  anomalies,
  onResolve,
  onNotifyLead,
}: {
  anomalies: DataAnomaly[]
  onResolve?: (anomaly: DataAnomaly) => void
  onNotifyLead?: (anomaly: DataAnomaly) => void
}) {
  if (!anomalies.length) {
    return (
      <div className="flex items-center gap-2 text-sm text-emerald-700">
        <CheckCircle2 className="h-4 w-4" />
        未发现异常
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {anomalies.map((anomaly) => {
        const resolved = anomaly.status === 'RESOLVED'
        return (
          <div
            key={anomaly.id}
            className={`rounded-md border px-3 py-2 text-sm ${
              resolved
                ? 'border-gray-200 bg-gray-50/70 text-gray-500'
                : 'border-amber-200 bg-amber-50/60 text-amber-800'
            }`}
          >
            <div className="flex items-start gap-2">
              {resolved
                ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
                  <StatusBadge tone={resolved ? 'neutral' : 'amber'}>
                    {ANOMALY_TYPE_LABEL[anomaly.type] ?? anomaly.type}
                  </StatusBadge>
                  <StatusBadge tone={resolved ? 'green' : anomaly.status === 'REOPENED' ? 'red' : 'red'}>
                    {ANOMALY_STATUS_LABEL[anomaly.status]}
                  </StatusBadge>
                </div>
                <span>{anomaly.message}</span>
                <p className="mt-0.5 text-[10px] text-gray-400">
                  首次 {formatDateTime(anomaly.firstOccurredAt)} · 最近 {formatDateTime(anomaly.lastOccurredAt)}
                  {anomaly.resolvedAt ? ` · 已处理 ${formatDateTime(anomaly.resolvedAt)}` : ''}
                </p>
                {(onResolve || onNotifyLead) && !resolved && (
                  <div className="mt-1.5 flex flex-wrap gap-3 text-[11px]">
                    {onResolve && (
                      <button className="text-[#3d3192] hover:underline" onClick={() => onResolve(anomaly)}>
                        标记已处理
                      </button>
                    )}
                    {onNotifyLead && (
                      <button className="text-gray-500 hover:underline" onClick={() => onNotifyLead(anomaly)}>
                        通知美工组长
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** 数量校验提示条：主副素材数量不一致时必须显式报警 */
export function CountMismatchAlert({ messages }: { messages: string[] }) {
  if (!messages.length) return null
  return (
    <div className="rounded-md border border-red-200 bg-red-50/70 px-3 py-2 text-sm text-red-700">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        <div className="space-y-1">
          {messages.map((message) => <p key={message}>{message}</p>)}
          <p className="text-xs text-red-500">数量不一致时禁止直接提交，请补齐或删除多余副素材。</p>
        </div>
      </div>
    </div>
  )
}

export function MockHint({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-start gap-1.5 rounded-md bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-500">
      <Info className="mt-0.5 h-3 w-3 shrink-0" />
      <span>{children}</span>
    </div>
  )
}

/** 已确认的配对所对应的副图（仅用于「本次上传结果」等处展示） */
export function resolvePairedVariant(pairing: PairingView) {
  if (!pairing.variantAssetId || !pairing.variantPreviewUri) return null
  return {
    assetId: pairing.variantAssetId,
    fileName: pairing.variantFileName ?? '',
    previewUri: pairing.variantPreviewUri,
  }
}
