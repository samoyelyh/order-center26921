import { ImageOff, Link2, Pencil, Unlink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PairingStatusBadge, StatusBadge } from './WorkflowPrimitives'
import type { PairingView } from '@/types/material-workflow'

interface Props {
  pairings: PairingView[]
  onModify: (pairing: PairingView) => void
}

/**
 * 主素材 ↔ 副素材 配对表。
 *
 * 只显示「主素材 | 副素材 | 配对状态 | 修改」；
 * 主副素材关系只由同一次上传内的同名 pairKey 决定。
 */
export function MaterialPairingTable({ pairings, onModify }: Props) {
  if (!pairings.length) {
    return (
      <div className="py-12 text-center text-sm text-gray-400">
        还没有配对结果，请先上传主素材与同名副素材，然后点「开始配对」
      </div>
    )
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>主素材</TableHead>
          <TableHead>副素材</TableHead>
          <TableHead>配对状态</TableHead>
          <TableHead>操作</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {pairings.map((pairing) => {
          const occupied = pairing.options.find(
            (option) => option.assetId === pairing.variantAssetId,
          )
          return (
            <TableRow key={pairing.id}>
              <TableCell>
                <div className="flex items-center gap-2">
                  {pairing.mainPreviewUri ? (
                    <img
                      src={pairing.mainPreviewUri}
                      alt={`主素材 ${pairing.position}`}
                      className="h-10 w-10 rounded border border-gray-100 bg-gray-50 object-cover"
                    />
                  ) : (
                    <div className="flex h-10 w-10 items-center justify-center rounded border border-gray-100 bg-gray-50 text-[10px] text-gray-300">
                      无图
                    </div>
                  )}
                  <div className="text-xs">
                    <div className="font-medium text-gray-700">位置 {pairing.position}</div>
                    <div className="text-gray-400">
                      {pairing.mainSourceFileName}
                      <span className="ml-1.5 rounded bg-gray-100 px-1 font-mono text-[10px] text-gray-500">
                        pairKey {pairing.pairKey || '—'}
                      </span>
                    </div>
                    <div className="font-mono text-[10px] text-gray-400">
                      {pairing.materialCode || '未分配 MAT'}
                    </div>
                  </div>
                </div>
              </TableCell>
              <TableCell>
                {pairing.variantPreviewUri ? (
                  <div className="flex items-center gap-2">
                    <img
                      src={pairing.variantPreviewUri}
                      alt={pairing.variantFileName ?? ''}
                      className="h-10 w-10 rounded border border-gray-100 bg-gray-50 object-cover"
                    />
                    <div className="text-xs">
                      <div className="font-medium text-gray-700">
                        {pairing.variantFileName ?? '副图'}
                      </div>
                      <div className="text-gray-400">
                        {pairing.status === 'CONFIRMED' ? '已确认' : '待确认'}
                        {pairing.source === 'MANUAL' && (
                          <span className="ml-1 text-[#3d3192]">人工指定</span>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-xs text-gray-400">
                    <Unlink className="h-3.5 w-3.5" />
                    缺副图（需要同名 {pairing.pairKey || '?'}）
                  </div>
                )}
              </TableCell>
              <TableCell>
                <PairingStatusBadge status={pairing.status} source={pairing.source} />
                {pairing.psdFileName && (
                  <p className="mt-1 text-[10px] text-gray-400">PSD：{pairing.psdFileName}</p>
                )}
                {pairing.confirmedBy && (
                  <p className="mt-1 text-[10px] text-gray-400">由 {pairing.confirmedBy} 确认</p>
                )}
                {pairing.status === 'UNPAIRED' && (
                  <p className="mt-1 max-w-[220px] text-[10px] leading-4 text-amber-700">
                    缺少同名副图，或文件名解析不出 pairKey。可点「修改」手动指定，或重新上传同名副图。
                  </p>
                )}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5">
                  <Button size="sm" variant="outline" onClick={() => onModify(pairing)}>
                    <Pencil className="h-3.5 w-3.5" />
                    修改
                  </Button>
                  {occupied?.occupiedByPosition &&
                    occupied.occupiedByPosition !== pairing.position && (
                      <StatusBadge tone="amber">
                        已被位置 {occupied.occupiedByPosition} 使用
                      </StatusBadge>
                    )}
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/** 空态提示：还没有上传副图时的说明 */
export function PairingEmptyHint() {
  return (
    <div className="flex items-center gap-2 rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
      <ImageOff className="h-3.5 w-3.5" />
      配对只按文件名同名：主素材 <span className="font-mono">1.jpg</span> 会与副素材{' '}
      <span className="font-mono">1.jpg</span> 直接配对，与图片内容无关。
      <Link2 className="ml-2 h-3.5 w-3.5" />
    </div>
  )
}
