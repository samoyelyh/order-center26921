import { useState } from 'react'
import { ChevronDown, ChevronRight, Package, Trash2, Undo2 } from 'lucide-react'
import type { DesignPackageGroupView } from '@/types/material-workflow'

export interface ArchivedPackageView {
  id: string
  name: string
  code: string
  designCode: string
  archivedAt?: string | null
}

interface Props {
  /** DTO：由 store 聚合生成（计数不再来自 DesignPackage 自身字段） */
  groups: DesignPackageGroupView[]
  expanded: Set<string>
  onToggle: (packageId: string) => void
  onOpenMaterial: (materialCode: string) => void
  /** 删除（软删除 / 归档）设计包；返回 Promise 以便按钮显示进行中 */
  onDeletePackage?: (group: DesignPackageGroupView) => Promise<void>
  /** 已删除（归档）的设计包 */
  archived?: ArchivedPackageView[]
  onRestorePackage?: (pkg: ArchivedPackageView) => Promise<void>
}

/**
 * 第二十五条：按设计包折叠。
 * 折叠时只显示该设计包第一个主素材作为封面，并展示 主素材/副素材/上架版本 数量。
 *
 * 每个设计包可以直接删除（软删除 / 归档：列表不再显示，数据保留可恢复）。
 */
export function MaterialPackageGroups({
  groups,
  expanded,
  onToggle,
  onOpenMaterial,
  onDeletePackage,
  archived = [],
  onRestorePackage,
}: Props) {
  const [pending, setPending] = useState('')
  const [confirm, setConfirm] = useState<DesignPackageGroupView | null>(null)
  const [showArchived, setShowArchived] = useState(false)

  if (!groups.length) {
    return (
      <div className="px-4 pb-6">
        <div className="flex flex-col items-center justify-center py-24 text-gray-400">
          <div className="text-sm">暂无设计包</div>
          <div className="mt-1 text-xs">
            在上传页确认整包配对并生成版本（V1）之后，设计包才会出现在这里
          </div>
        </div>
        {archived.length > 0 && (
          <div className="mx-auto max-w-md text-center text-xs text-gray-400">
            有 {archived.length} 个已删除的设计包（可在上方「显示已删除」里恢复）
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3 px-4 pb-6">
      {archived.length > 0 && (
        <div className="rounded-md border border-gray-200 bg-white px-3 py-2 text-xs">
          <button
            onClick={() => setShowArchived((v) => !v)}
            className="flex items-center gap-1.5 text-gray-500 hover:text-[#3d3192]"
          >
            {showArchived ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
            显示已删除的设计包（{archived.length}）
          </button>
          {showArchived && (
            <ul className="mt-2 space-y-1.5">
              {archived.map((pkg) => (
                <li key={pkg.id} className="flex items-center gap-2 text-gray-500">
                  <span className="truncate">
                    {pkg.name}
                    <span className="ml-1.5 font-mono text-[11px] text-gray-400">{pkg.designCode}</span>
                  </span>
                  <button
                    disabled={pending === pkg.id}
                    onClick={async () => {
                      if (!onRestorePackage) return
                      setPending(pkg.id)
                      try {
                        await onRestorePackage(pkg)
                      } finally {
                        setPending('')
                      }
                    }}
                    className="ml-auto flex shrink-0 items-center gap-1 rounded border border-gray-300 px-2 py-0.5 text-[11px] hover:border-[#3d3192] hover:text-[#3d3192] disabled:opacity-50"
                  >
                    <Undo2 className="h-3 w-3" />
                    恢复
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {groups.map((group) => {
        const isOpen = expanded.has(group.pkg.id)
        const cover = group.positions[0]
        return (
          <div key={group.pkg.id} className="overflow-hidden rounded-md border border-gray-200 bg-white">
            <div className="flex items-center gap-3 px-3 py-2.5 hover:bg-gray-50">
              <button
                onClick={() => onToggle(group.pkg.id)}
                className="flex min-w-0 flex-1 items-center gap-3 text-left"
              >
                {isOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-gray-400" /> : <ChevronRight className="h-4 w-4 shrink-0 text-gray-400" />}
                {cover ? (
                  <img src={cover.previewUri} alt={group.pkg.name} className="h-12 w-12 shrink-0 rounded border border-gray-100 bg-gray-50 object-cover" />
                ) : (
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded border border-gray-100 bg-gray-50 text-gray-300">
                    <Package className="h-5 w-5" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-[13px] font-medium text-gray-800">{group.pkg.name}</span>
                    <span className="shrink-0 font-mono text-[11px] text-gray-400">{group.pkg.code}</span>
                    {group.pkg.designCode && (
                      <span className="shrink-0 rounded bg-[#f0eef9] px-1.5 py-0.5 font-mono text-[11px] text-[#3d3192]">
                        设计编码 {group.pkg.designCode}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-[11px] text-gray-400">
                    {group.mainMaterialCount} 个主素材 · {group.variantCount} 个副素材 · {group.batchCount} 个上架版本
                    {group.currentBatchNo > 0 ? `（当前 V${group.currentBatchNo}）` : ''}
                  </div>
                </div>
                <div className="hidden shrink-0 text-right text-[11px] text-gray-400 sm:block">
                  <div>原始文件包：{group.latestUpload?.originalPackageName ?? '-'}</div>
                  <div>上传人：{group.latestUpload?.uploaderName ?? group.pkg.createdBy}</div>
                </div>
              </button>
              {onDeletePackage && (
                <button
                  onClick={() => setConfirm(group)}
                  disabled={pending === group.pkg.id}
                  title="删除设计包（软删除 / 归档，可恢复）"
                  className="flex shrink-0 items-center gap-1 rounded border border-red-200 px-2 py-1 text-[11px] text-red-500 transition-colors hover:bg-red-50 disabled:opacity-50"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除
                </button>
              )}
            </div>

            {isOpen && (
              <div className="border-t border-gray-100 bg-gray-50/40 p-3">
                <div className="grid grid-cols-3 gap-3 md:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
                  {group.positions.map((item) => (
                    <button
                      key={item.position.id}
                      onClick={() => onOpenMaterial(item.position.materialCode)}
                      className="group overflow-hidden rounded border border-gray-100 bg-white text-left transition-shadow hover:shadow-md"
                    >
                      <div className="aspect-square overflow-hidden bg-gray-50">
                        <img
                          src={item.previewUri}
                          alt={item.position.materialCode}
                          loading="lazy"
                          className="h-full w-full object-cover transition-transform group-hover:scale-[1.03]"
                        />
                      </div>
                      <div className="px-1.5 py-1.5">
                        <div className="truncate font-mono text-[11px] font-medium text-gray-700">{item.position.materialCode}</div>
                        <div className="text-[10px] text-gray-400">
                          位置 {item.position.position} · 副素材 {item.variantCount}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )
      })}

      {/* 删除确认：软删除要说清楚「列表不再显示 + 数据保留可恢复」 */}
      {confirm && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center">
          <div className="absolute inset-0 bg-black/30" onClick={() => setConfirm(null)} />
          <div className="relative w-[420px] max-w-[92vw] rounded-lg bg-white p-5 shadow-2xl">
            <h3 className="text-sm font-semibold text-gray-900">删除设计包「{confirm.pkg.name}」？</h3>
            <ul className="mt-2 space-y-1 text-xs text-gray-600">
              <li>· 这是**软删除（归档）**：素材中心与设计包列表不再显示它</li>
              <li>
                · 主素材（{confirm.mainMaterialCount} 个 MAT）、{confirm.variantCount} 个副素材与{' '}
                {confirm.batchCount} 个上架版本的数据与维护记录**都会保留**
              </li>
              <li>· 已上传的文件不会被删除（别的设计包可能还在用同一份文件）</li>
              <li>· 之后可以在「显示已删除的设计包」里恢复</li>
            </ul>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setConfirm(null)}
                className="rounded border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={async () => {
                  const target = confirm
                  setConfirm(null)
                  if (!onDeletePackage) return
                  setPending(target.pkg.id)
                  try {
                    await onDeletePackage(target)
                  } finally {
                    setPending('')
                  }
                }}
                className="rounded bg-red-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-600"
              >
                删除（可恢复）
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
