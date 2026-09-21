import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { CheckCircle2, ChevronRight, Download, ExternalLink, PackageCheck } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ActivityTimeline, StatusBadge } from '@/components/material/WorkflowPrimitives'
import { AMAZON_SITES, buildAsinUrl, parseAsinList, resolveListingUrl } from '@/lib/asin'
import { UPLOAD_TYPE_LABEL, formatDateTime } from '@/lib/workflow'
import {
  bindAsins,
  getTaskOverview,
  receiveTask,
  useWorkflowState,
} from '@/store/workflowStore'
import type { MarketplaceSiteCode } from '@/types/material-workflow'

interface Props {
  /** 由路由传入，默认取当前登录运营 */
  actorName?: string
}

/** 运营端：接收素材 → 查看/下载副素材 → 回填 Parent / Child ASIN */
export default function DistributionDetailPage({ actorName }: Props) {
  const navigate = useNavigate()
  const { taskId = '' } = useParams()
  useWorkflowState()

  const overview = getTaskOverview(taskId)
  const task = overview?.task

  const [parent, setParent] = useState(task?.parentAsin?.asin ?? '')
  const [childrenText, setChildrenText] = useState((task?.children ?? []).map((c) => c.asin).join('\n'))
  const [site, setSite] = useState<MarketplaceSiteCode>(task?.parentAsin?.site ?? 'US')
  const [downloaded, setDownloaded] = useState(false)

  const parsed = useMemo(() => parseAsinList(childrenText), [childrenText])
  const variants = overview?.variants ?? []
  const logs = overview?.logs ?? []
  const actor = actorName || task?.operatorName || '运营'

  if (!overview || !task) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#f5f6f8] text-sm text-gray-500">
        <p>派发任务不存在或已失效：{taskId || '(缺少 taskId)'}</p>
        <Button variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
      </div>
    )
  }

  const parentValid = /^B0[A-Z0-9]{8}$/.test(parent.trim().toUpperCase())
  const canSubmit = task.status !== 'ACTIVE' && parentValid && parsed.asins.length > 0 && parsed.invalid.length === 0

  const handleReceive = () => {
    receiveTask(task.id, actor)
    toast.success('已接收素材')
  }

  const handleDownload = () => {
    setDownloaded(true)
    toast.success(`已生成素材包 ${task.packageName}_${task.versionCode}.zip（演示环境未接入真实文件服务）`)
  }

  const handleSubmit = () => {
    const result = bindAsins(task.id, { parentAsin: parent, childrenText, site, actor })
    if (result.error) {
      toast.error(result.error)
      return
    }
    toast.success('ASIN 关联完成，已写入维护记录')
  }

  return (
    <div className="min-h-screen bg-[#f5f6f8] text-gray-800">
      <header className="flex h-12 items-center justify-between border-b bg-white px-6 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span className="font-medium text-gray-600">素材中心</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span>运营派发</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span className="truncate">{task.packageName}</span>
        </div>
        <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
      </header>

      <main className="mx-auto max-w-[1400px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">运营接收素材</h1>
            <p className="mt-1 text-xs text-gray-400">确认接收后查看副素材并回填 Parent / Child ASIN。</p>
          </div>
          <StatusBadge tone={task.status === 'COMPLETED' ? 'green' : task.status === 'RECEIVED' ? 'purple' : task.status === 'CANCELLED' ? 'neutral' : 'amber'}>
            {DISTRIBUTION_STATUS_LABEL[task.status]}
          </StatusBadge>
        </div>

        {/* ------------------------------------------------------ 设计包信息 */}
        <Card>
          <CardContent className="grid gap-4 py-5 md:grid-cols-6">
            <div className="md:col-span-2">
              <p className="text-xs text-gray-400">设计包</p>
              <p className="mt-1 font-medium">{task.packageName}</p>
              <p className="text-xs text-gray-400">{task.packageCode}</p>
            </div>
            <div><p className="text-xs text-gray-400">上架版本</p><p className="mt-1 font-medium">{task.versionCode}</p></div>
            <div><p className="text-xs text-gray-400">美工</p><p className="mt-1 font-medium">{task.designerName}</p></div>
            <div><p className="text-xs text-gray-400">副素材数量</p><p className="mt-1 font-medium">{variants.length}</p></div>
            <div><p className="text-xs text-gray-400">派发时间</p><p className="mt-1 font-medium">{formatDateTime(task.assignedAt)}</p></div>
            <div className="md:col-span-6 border-t pt-3 text-xs text-gray-500">
              原始文件包：<span className="font-medium text-gray-700">{overview.upload?.originalPackageName ?? '-'}</span>
              {overview.upload && (
                <span className="ml-3 text-gray-400">（{UPLOAD_TYPE_LABEL[overview.upload.uploadType]}）</span>
              )}
              {task.remark && <span className="ml-4">备注：<span className="font-medium text-gray-700">{task.remark}</span></span>}
            </div>
          </CardContent>
        </Card>

        {task.status === 'ACTIVE' && (
          <Card>
            <CardContent className="flex items-center justify-between py-4">
              <div className="flex items-center gap-3 text-sm">
                <PackageCheck className="h-5 w-5 text-[#3d3192]" />
                <span>该任务由 {task.operatorName} 接收，首次进入请确认接收。</span>
              </div>
              <Button className="bg-[#3d3192] hover:bg-[#32277a]" onClick={handleReceive}>接收素材</Button>
            </CardContent>
          </Card>
        )}

        {task.status === 'CANCELLED' && (
          <Card>
            <CardContent className="py-4 text-sm text-gray-500">
              该派发任务已取消，如需继续请由美工重新派发（同一版本取消后允许重新派发给 {task.operatorName}）。
            </CardContent>
          </Card>
        )}

        {/* ------------------------------------------------------ 副素材 Grid */}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base">副素材（{task.versionCode}）</CardTitle>
              <p className="mt-1 text-xs text-gray-400">
                副素材就是一张 JPG；`1-1` 表示主素材1在第1套上架版本中的副素材。多个派发任务复用同一套底层素材，不复制图片。
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={handleDownload}>
              <Download className="h-4 w-4" />
              {downloaded ? '已准备下载' : '下载素材包'}
            </Button>
          </CardHeader>
          <CardContent>
            {variants.length === 0 ? (
              <p className="py-10 text-center text-sm text-gray-400">该版本暂无副素材</p>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
                {variants.map((material) => (
                  <div key={material.id} className="overflow-hidden rounded-md border bg-white">
                    <img
                      src={material.imageUri || `/mock/mat-01.jpg`}
                      alt={material.displayCode}
                      className="aspect-square w-full bg-gray-50 object-cover"
                    />
                    <div className="flex items-center justify-between px-2 py-1.5 text-xs">
                      <span className="font-medium text-gray-700">{material.displayCode}</span>
                      <a
                        href={material.imageUri || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-400 hover:text-[#3d3192]"
                        title="查看大图"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </div>
                    {material.currentRevision > 1 && (
                      <div className="border-t px-2 py-1 text-[10px] text-gray-400">
                        当前 Revision {material.currentRevision}（共 {material.revisions.length} 版）
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* ------------------------------------------------------ ASIN 回填 */}
        <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader><CardTitle className="text-base">回填 ASIN</CardTitle></CardHeader>
            <CardContent className="space-y-5">
              <div className="grid gap-4 md:grid-cols-[1fr_180px]">
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">Parent ASIN <b className="text-red-500">*</b></span>
                  <Input
                    placeholder="B0XXXXXXXX"
                    value={parent}
                    onChange={(event) => setParent(event.target.value.toUpperCase().trim())}
                  />
                  <span className="text-xs text-gray-400">
                    Parent ASIN 本身即唯一业务标识；绑定后该 Parent 下全部 Child 默认共享
                    {task.versionCode} 的 {variants.length} 张副素材。
                  </span>
                </label>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">站点<span className="ml-1 text-xs font-normal text-gray-400">（选填，仅用于生成跳转链接）</span></span>
                  <Select value={site} onValueChange={(value) => setSite(value as MarketplaceSiteCode)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {AMAZON_SITES.map((item) => (
                        <SelectItem key={item.code} value={item.code}>{item.label}（{item.domain}）</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <span className="text-xs text-gray-400">站点不参与唯一键，缺失也不影响 ASIN 保存。</span>
                </label>
              </div>

              {parent && !parentValid && <p className="text-xs text-red-600">Parent ASIN 格式应为 B0 + 8 位字母或数字。</p>}
              {parentValid && (
                <a
                  href={buildAsinUrl(parent, site)}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 font-mono text-xs text-[#3d3192] hover:underline"
                >
                  {parent} <ExternalLink className="h-3 w-3" />
                </a>
              )}

              <label className="block space-y-1.5 text-sm">
                <span className="font-medium">Child ASIN <b className="text-red-500">*</b></span>
                <Textarea
                  value={childrenText}
                  onChange={(event) => setChildrenText(event.target.value)}
                  className="min-h-40 font-mono text-xs"
                  placeholder="一行一个，也支持 Excel 整列粘贴（自动 trim / 去空行 / 去重）"
                />
                <span className="text-xs text-gray-400">
                  识别到 <b className="text-gray-700">{parsed.asins.length}</b> 个 Child ASIN
                  {parsed.duplicateCount > 0 && <>，已自动去重 {parsed.duplicateCount} 个</>}
                </span>
              </label>
              {parsed.invalid.length > 0 && (
                <p className="text-xs text-red-600">存在格式不正确的 Child ASIN：{parsed.invalid.slice(0, 6).join('、')}</p>
              )}

              <div className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-500">
                关联关系：Parent ASIN → 一整套上架版本 {task.versionCode}（{variants.map((v) => v.displayCode).slice(0, 6).join('、')}
                {variants.length > 6 ? ' …' : ''}）。Parent 下所有 Child ASIN 默认共享这套素材，不是 Child → 单张副素材的一一关系。
              </div>

              <Button className="bg-[#3d3192] hover:bg-[#32277a]" disabled={!canSubmit} onClick={handleSubmit}>
                <CheckCircle2 className="h-4 w-4" />
                {task.status === 'COMPLETED' ? '更新 ASIN 关联' : '提交ASIN关联'}
              </Button>
              {task.status === 'ACTIVE' && <p className="text-xs text-gray-400">请先点击上方「接收素材」后再提交。</p>}
            </CardContent>
          </Card>

          <div className="space-y-4">
            {task.parentAsin && (
              <Card>
                <CardHeader><CardTitle className="text-base">当前 ASIN 关联</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-xs">
                  <div>
                    <p className="text-gray-400">Parent ASIN</p>
                    <a
                      href={resolveListingUrl({ asin: task.parentAsin.asin, listingUrl: task.parentAsin.listingUrl, site: task.parentAsin.site })}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-[#3d3192] hover:underline"
                    >
                      {task.parentAsin.asin} <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                  <div>
                    <p className="text-gray-400">Child ASIN（{task.children.length}）</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {task.children.map((child) => (
                        <a
                          key={child.asin}
                          href={resolveListingUrl({ asin: child.asin, listingUrl: child.listingUrl, site: child.site })}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 rounded border border-gray-200 px-1.5 py-0.5 font-mono text-[11px] text-gray-600 hover:border-[#3d3192] hover:text-[#3d3192]"
                        >
                          {child.asin}
                          <ExternalLink className="h-2.5 w-2.5" />
                          {child.overrideVariantIds?.length ? <span className="ml-1 text-amber-600">单独素材</span> : null}
                        </a>
                      ))}
                    </div>
                    <p className="mt-2 text-[11px] text-gray-400">默认继承 Parent 素材组；Child 单独素材覆盖（overrideVariantIds）已在数据结构中预留。</p>
                  </div>
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader><CardTitle className="text-base">维护记录</CardTitle></CardHeader>
              <CardContent className="max-h-[420px] overflow-y-auto">
                <ActivityTimeline logs={logs} />
              </CardContent>
            </Card>
          </div>
        </div>
      </main>
    </div>
  )
}

const DISTRIBUTION_STATUS_LABEL: Record<string, string> = {
  ACTIVE: '待接收',
  RECEIVED: '已接收',
  COMPLETED: '已回填ASIN',
  CANCELLED: '已取消',
}
