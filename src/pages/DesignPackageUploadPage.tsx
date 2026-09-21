import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { AlertTriangle, Check, ChevronRight, FileArchive, Layers, PackagePlus, UploadCloud, WandSparkles } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ActivityTimeline, AnomalyList, CountMismatchAlert, MockHint, PairingStatusBadge, StatusBadge } from '@/components/material/WorkflowPrimitives'
import { MaterialPairingTable } from '@/components/material/MaterialPairingTable'
import { TagPicker } from '@/components/material/TagPicker'
import { formatDateTime } from '@/lib/workflow'
import {
  confirmAllPairings,
  confirmPairing,
  getPackageOverview,
  getWorkflowState,
  ingestLocalFiles,
  reassignPairing,
  resolveAnomaly,
  runPackagePairing,
  setSessionOperator,
  startPackageSession,
  submitAndDispatch,
  supplementCurrentVersion,
  UPLOAD_TYPE_LABEL,
  useWorkflowState,
  type LocalUploadFile,
} from '@/store/workflowStore'
import { ApiError, API_BASE, materialApi } from '@/services/apiClient'
import {
  checkBackendHealth,
  confirmPairingsApi,
  createBatchApi,
  isApiMode,
  reassignPairingApi,
  refreshPackageFromApi,
  recheckBackendHealth,
  runPairingApi,
  submitDesignPackageToApi,
  type BackendHealthResult,
} from '@/services/phase1UploadApi'
import type { PairingView } from '@/types/material-workflow'

const OPERATORS = [
  { id: 'zhang', name: '张三' },
  { id: 'li', name: '李四' },
  { id: 'wang', name: '王敏' },
]

/** 设计美工（设计包长期属性）的默认值 */
const DEFAULT_DESIGNER = '肖芸'
const ACTIVE_PKG_KEY = 'materialCenter.activePackageId'
/** 四个手工填写字段的记忆键（刷新后不用重填） */
const DESIGN_CODE_KEY = 'materialCenter.designCode'
const DESIGNER_KEY = 'materialCenter.designerName'
const UPLOADER_KEY = 'materialCenter.uploaderName'
const OPERATOR_KEY = 'materialCenter.operatorName'

function readStorage(key: string, fallback = ''): string {
  try {
    return sessionStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

function writeStorage(key: string, value: string): void {
  try {
    if (value) sessionStorage.setItem(key, value)
    else sessionStorage.removeItem(key)
  } catch {
    // sessionStorage 不可用时降级为内存态
  }
}

interface StagedFile {
  file: File
  kind: 'MAIN' | 'VARIANT'
  psd?: File
  previewUrl: string
}

export default function DesignPackageUploadPage() {
  const navigate = useNavigate()
  // 订阅 store，保证确认/撤回/补充后整页数据同步刷新
  useWorkflowState()

  const mainInputRef = useRef<HTMLInputElement>(null)
  const variantInputRef = useRef<HTMLInputElement>(null)
  const previewUrls = useRef<string[]>([])

  const [packageName, setPackageName] = useState('')
  /**
   * 设计编码：**美工自己的设计编号**，上传时手填。
   * 素材详情里的「关联设计」就按它聚合（一个设计编码 = 一个设计，可跨多个设计包）。
   */
  const [designCodeStored, setDesignCodeStored] = useState(() => readStorage(DESIGN_CODE_KEY))
  /** 正在编辑的设计编码（null = 未编辑，显示服务端记录或记忆值） */
  const [designCodeDraft, setDesignCodeDraft] = useState<string | null>(null)
  /** 设计包标签（创建时继承给包内素材，之后独立修改） */
  const [tags, setTags] = useState<string[]>([])
  /** 标签选择器的数据源：全库去重标签（后端 /tags） */
  const [allTags, setAllTags] = useState<{ tag: string; count: number }[]>([])
  useEffect(() => {
    if (!isApiMode()) return
    void materialApi.listTags().then(setAllTags).catch(() => {})
  }, [])
  /** 负责人：业务上负责这套设计的人（= 之前的「设计美工」字段，现在明确叫负责人） */
  const [designer, setDesigner] = useState(() => readStorage(DESIGNER_KEY, DEFAULT_DESIGNER))
  /** 实际上传人：这一次是谁执行上传，自由文本（接登录后改只读） */
  const [uploader, setUploader] = useState(() =>
    readStorage(UPLOADER_KEY, readStorage(DESIGNER_KEY, DEFAULT_DESIGNER)),
  )
  /** 归属运营：自由文本（输入框 + 可选建议），不强制从固定列表里选 */
  const [operator, setOperator] = useState(() => readStorage(OPERATOR_KEY))
  const [remark, setRemark] = useState('')
  const [staged, setStaged] = useState<StagedFile[]>([])
  // 第二十九条：当前设计包 id 落 sessionStorage，刷新后仍停留在同一上传会话，不会重复创建
  const [activePkgId, setActivePkgId] = useState<string>(() => {
    try {
      return sessionStorage.getItem(ACTIVE_PKG_KEY) ?? ''
    } catch {
      return ''
    }
  })
  const [processing, setProcessing] = useState(false)
  const [progressText, setProgressText] = useState('')
  const [progressPercent, setProgressPercent] = useState(0)
  /**
   * 拦截提交的原因（第三、四条）。
   *
   * 只要有任何业务条件阻止提交，就必须在这里留下可见文案 ——
   * 绝不允许 `if (...) return` 这种点了没反应的静默失败。
   */
  const [blockReason, setBlockReason] = useState('')
  /** 上传完成 / 已匹配的即时反馈（第三十三、三十四条） */
  const [uploadState, setUploadState] = useState<'IDLE' | 'UPLOADED' | 'MATCHED'>('IDLE')
  const [lastUploadSummary, setLastUploadSummary] = useState('')
  const [creatingBatch, setCreatingBatch] = useState(false)
  /** 上传前检查面板：用户确认后才真正写入 */
  const [showPreCheck, setShowPreCheck] = useState(false)
  /** 上传成功后的下一步入口 */
  const [showSuccessPanel, setShowSuccessPanel] = useState(false)
  /** 副图已被其他主素材占用时的重新分配确认 */
  const [reassignPrompt, setReassignPrompt] = useState<{
    pairing: PairingView
    variantAssetId: string
    occupiedByPosition: number
  } | null>(null)
  const [health, setHealth] = useState<BackendHealthResult | null>(() =>
    // Mock 模式下不需要真的探活，直接用初始值表达「未接后端」
    isApiMode()
      ? null
      : {
          ok: false,
          kind: 'UNREACHABLE',
          message: '本地 Mock 模式（未接后端）',
          hint: '设置 VITE_MATERIAL_API=1 可切换为真实后端',
          apiBase: API_BASE,
        },
  )
  // API 模式下初始为「检测中」，避免在 effect 里同步 setState（会触发级联渲染）
  const [healthChecking, setHealthChecking] = useState(() => isApiMode())
  const [selected, setSelected] = useState<PairingView | null>(null)
  const [supplementOpen, setSupplementOpen] = useState(false)

  /**
   * 后端连通性检测（第4条）。
   * 三种状态在 UI 上必须能区分：连不上 / 服务通了但依赖不健康 / 被 CORS 拦截。
   */
  useEffect(() => {
    if (!isApiMode()) return
    let cancelled = false
    void checkBackendHealth()
      .then((result) => {
        if (!cancelled) setHealth(result)
      })
      .finally(() => {
        if (!cancelled) setHealthChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const handleRecheck = () => {
    if (!isApiMode()) return
    setHealthChecking(true)
    void recheckBackendHealth()
      .then((result) => setHealth(result))
      .finally(() => setHealthChecking(false))
  }

  const selectPackage = (pkgId: string) => {
    setActivePkgId(pkgId)
    try {
      if (pkgId) sessionStorage.setItem(ACTIVE_PKG_KEY, pkgId)
      else sessionStorage.removeItem(ACTIVE_PKG_KEY)
    } catch {
      // sessionStorage 不可用时降级为内存态
    }
  }

  useEffect(() => () => {
    previewUrls.current.forEach((url) => URL.revokeObjectURL(url))
  }, [])

  /**
   * 刷新页面 / 恢复会话后，从后端重新拉取该设计包真实数据。
   * 用 ref 记录已同步的 pkgId，避免 hydrate 引起的重复请求。
   */
  const syncedPkgRef = useRef<string>('')
  useEffect(() => {
    if (!isApiMode() || !activePkgId) return
    if (syncedPkgRef.current === activePkgId) return
    syncedPkgRef.current = activePkgId
    void refreshPackageFromApi(activePkgId).catch((error: unknown) => {
      syncedPkgRef.current = ''
      // 这个设计包已经被删除（软删除 / 归档）：把本地当前包也清掉，
      // 否则页面会一直停在一个后端已经不返回的包上。
      if (error instanceof ApiError && error.code === 'DESIGN_PACKAGE_ARCHIVED') {
        try {
          sessionStorage.removeItem(ACTIVE_PKG_KEY)
        } catch {
          // 忽略：内存态已一起清掉
        }
        setActivePkgId('')
        setStaged([])
        setSelected(null)
        toast.error('该设计包已被删除（归档），已切换回「新建设计包」', { duration: 8000 })
        return
      }
      toast.error(
        error instanceof ApiError ? error.message : '加载设计包数据失败',
        { duration: 8000 },
      )
    })
  }, [activePkgId])

  const overview = activePkgId ? getPackageOverview(activePkgId) : null
  const state = getWorkflowState()

  /**
   * 设计编码输入框的值：
   *   - 正在编辑（draft）优先
   *   - 否则以当前设计包的服务端记录为准
   *   - 没有设计包时用上次填过的值（sessionStorage）
   * 不用 effect 同步，避免切包时多一次渲染。
   */
  const designCodeValue = designCodeDraft ?? (overview?.pkg.designCode || designCodeStored)

  /** 操作人（Mock 模式与维护记录用）：跟随「美工」输入框 */
  const actor = designer.trim() || DEFAULT_DESIGNER

  /**
   * 演示入口：只在本地 Mock 模式显示。
   * 真实后端模式下 store 里没有演示数据（也不该有），所以不给这个入口，
   * 避免再出现「演示数据混进我的真实素材」。
   */
  const demoPkgId = isApiMode()
    ? ''
    : Object.keys(state.packages).find((id) => (state.positions[id] ?? []).length > 0) ?? ''


  const stagedMains = staged.filter((f) => f.kind === 'MAIN')
  const stagedVariants = staged.filter((f) => f.kind === 'VARIANT')
  const stagedCountCheck = useMemo(
    () => ({
      mainCount: stagedMains.length,
      variantCount: stagedVariants.length,
      diff: stagedVariants.length - stagedMains.length,
    }),
    [stagedMains.length, stagedVariants.length],
  )

  const addFiles = (files: FileList | null, kind: 'MAIN' | 'VARIANT') => {
    if (!files?.length) return
    const next: StagedFile[] = []
    // 本次循环内维护的候选池（PSD 与预览图按数字编号配对）
    const working = [...staged]
    // PSD 延后配对：浏览器给的文件顺序不保证（1.psd 可能先于 1.jpg），
    // 因此先把所有预览图入池，再统一配对 PSD。
    const pendingPsd: File[] = []

    const findPairTarget = (psdFile: File): StagedFile | undefined => {
      const stem = psdFile.name.replace(/\.[^.]+$/, '')
      const byStem = working.find(
        (s) => s.kind === 'MAIN' && !s.psd && s.file.name.replace(/\.[^.]+$/, '') === stem,
      )
      if (byStem) return byStem
      const stemNumber = Number(stem)
      if (!Number.isFinite(stemNumber)) return undefined
      return working.find(
        (s) =>
          s.kind === 'MAIN' &&
          !s.psd &&
          Number.isFinite(Number(s.file.name.replace(/\.[^.]+$/, ''))) &&
          Number(s.file.name.replace(/\.[^.]+$/, '')) === stemNumber,
      )
    }

    for (const file of Array.from(files)) {
      if (kind === 'MAIN') {
        if (/\.(psd|psb)$/i.test(file.name)) {
          pendingPsd.push(file)
          continue
        }
        const item: StagedFile = { file, kind: 'MAIN', previewUrl: URL.createObjectURL(file) }
        working.push(item)
        next.push(item)
      } else {
        const item: StagedFile = { file, kind: 'VARIANT', previewUrl: URL.createObjectURL(file) }
        working.push(item)
        next.push(item)
      }
    }

    // 统一配对 PSD（此时本次所有预览图都已在池中）
    for (const psdFile of pendingPsd) {
      const target = findPairTarget(psdFile)
      if (target) {
        target.psd = psdFile
        continue
      }
      const stem = psdFile.name.replace(/\.[^.]+$/, '')
      toast.error(
        `PSD「${psdFile.name}」没有找到同编号的主素材预览图，已跳过（请同时选择 ${stem}.jpg / ${stem}.png）`,
        { duration: 6000 },
      )
    }

    previewUrls.current.push(...next.map((n) => n.previewUrl))
    // working 已包含本次新增项；仅在配对改动时需要强制刷新引用
    setStaged([...working])
  }

  const removeStaged = (file: File) => {
    setStaged((current) => current.filter((item) => item.file !== file))
  }

  /**
   * 解析并入库。
   *
   * API 模式（默认）：真实调用后端
   *   创建设计包 → 上传记录/会话（幂等）→ Asset（BLAKE3 完全文件去重）→ MAT + 位置
   *   → 数量一致时自动整包匹配
   * Mock 模式（VITE_MATERIAL_API=0）：走本地内存 store，便于无后端时看 UI
   *
   * 注意：所有「阻止提交」的条件都必须通过 block() 留下可见反馈。
   * 绝不写 `if (...) return` —— 那样用户点了按钮只会以为页面坏了（本轮 P0 的根因）。
   */
  const block = (reason: string) => {
    setBlockReason(reason)
    toast.error(reason, { duration: 8000 })
    return false
  }

  /** 收集当前所有阻止提交的原因（按钮附近要显示出来，第四点） */
  const collectBlockers = (): string[] => {
    const reasons: string[] = []
    // 与 analyze() 同一套「有效值」口径：已选设计包时以服务端记录为准
    const nameText = (overview?.pkg.name || packageName).trim()
    const designCodeText = designCodeValue.trim()
    const designerText = (overview?.pkg.designerName || designer).trim()
    const uploaderText = (overview?.upload.uploaderName || uploader).trim()
    const operatorText = (overview?.operatorName || operator).trim()
    // 已有设计包时允许只补传副图，所以「未选主素材」只在没有设计包时才算阻断
    if (!stagedMains.length && !stagedVariants.length) reasons.push('未选择主素材或副素材')
    else if (!stagedMains.length && !overview) reasons.push('未选择主素材')
    if (!nameText) reasons.push('缺少设计包名称')
    if (!designCodeText) reasons.push('缺少设计编码')
    if (!designerText) reasons.push('缺少设计美工')
    if (!uploaderText) reasons.push('缺少实际上传人')
    if (!operatorText) reasons.push('缺少归属运营')
    if (health && !health.ok) reasons.push('后端服务未连接')
    return reasons
  }

  const analyze = async () => {
    // 先清空上一次的提示，保证用户看到的是本次结果
    setBlockReason('')

    // 已经选着设计包时（含刷新后从 sessionStorage 恢复），名称/设计编码与三个人员字段
    // 以服务端记录为准 —— 输入框此时是只读展示，本地 state 可能是空的，
    // 不允许因此把「继续上传新一版」拦下来。
    const effectivePackageName = (overview?.pkg.name || packageName).trim()
    const effectiveDesignCode = designCodeValue.trim()
    const effectiveDesignerName = (overview?.pkg.designerName || designer).trim()
    const effectiveUploaderName = (overview?.upload.uploaderName || uploader).trim()
    const effectiveOperatorName = (overview?.operatorName || operator).trim()

    if (!effectivePackageName) return block('请填写设计包名称')
    // 设计编码必填：素材的「关联设计」按它聚合，空着就无法关联
    if (!effectiveDesignCode) return block('请填写设计编码（美工的设计编号）')
    // 三个人员字段都按「姓名非空」判断：ID 只是未来接用户中心的可选关联，
    // 绝不允许因为没有 userId 就阻止上传（第二点）
    const designerName = effectiveDesignerName
    if (!designerName) return block('请填写设计美工')
    const uploaderName = effectiveUploaderName
    if (!uploaderName) return block('请填写实际上传人')
    const operatorNameText = effectiveOperatorName
    if (!operatorNameText) return block('请填写归属运营')
    if (!stagedMains.length && !stagedVariants.length) return block('请上传主素材或副素材')
    // 已有设计包时允许「只补传新一版副图」：主素材与位置都沿用原来的，
    // 只是这次上传带来新的副图（V2/V3…）。没有任何设计包时主素材必传。
    if (!stagedMains.length && !overview) return block('请上传主素材')
    if (health && !health.ok) {
      return block(`后端服务未连接，请检查服务状态（${health.message}）`)
    }

    setProcessing(true)
    setProgressPercent(0)
    setProgressText('准备上传…')

    const originalPackageName = `${effectivePackageName}-${new Date()
      .toISOString()
      .slice(0, 10)
      .replace(/-/g, '')}.zip`
    // 归属运营是自由文本：能对上系统用户就带 userId，对不上就只存名字（后端 operator_id 为空）
    const knownOperator = OPERATORS.find((o) => o.name === operatorNameText || o.id === operatorNameText)

    try {
      if (isApiMode()) {
        const result = await submitDesignPackageToApi({
          // 已经选着某个设计包时，这次上传是它的「新一版」：
          // 追加为新的上传记录（同包同 MAT 位置），不是新建一个设计包。
          // 否则 V1/V2 会各自挂在自己的设计包上，版本号永远停在 V1。
          designPackageId: activePkgId || undefined,
          packageName: effectivePackageName,
          designCode: effectiveDesignCode,
          originalPackageName,
          operatorId: knownOperator?.id,
          operatorName: operatorNameText,
          designerName,
          uploaderName,
          remark,
          items: stagedMains.map((item) => ({ mainFile: item.file, psdFile: item.psd })),
          variantFiles: stagedVariants.map((item) => item.file),
          onProgress: (stage, percent) => {
            setProgressText(stage)
            setProgressPercent(percent)
          },
        })

        selectPackage(result.designPackageId)
        setStaged([])
        setDesignCodeStored(effectiveDesignCode)
        setDesignCodeDraft(null)
        writeStorage(DESIGN_CODE_KEY, effectiveDesignCode)
        writeStorage(DESIGNER_KEY, designerName)
        writeStorage(UPLOADER_KEY, uploaderName)
        writeStorage(OPERATOR_KEY, operatorNameText)

        const uploaded = result.overview.uploadedFiles
        const summary = [
          `主素材 ${uploaded.mainCount} 个`,
          `PSD ${uploaded.psdCount} 个`,
          `副图 ${uploaded.variantCount} 张`,
          `已建立主素材 ${result.createdMaterials + result.reusedMaterials} 个 MAT`,
        ].join('、')
        setLastUploadSummary(summary)
        setUploadState('MATCHED')
        toast.success(
          `上传完成：${summary}；已按同名 pairKey 完成配对（配对 ${result.autoPairing?.pairedCount ?? 0} 个），请确认后生成版本`,
          { duration: 8000 },
        )
        setShowSuccessPanel(true)
        return
      }

      // ---------------- Mock 模式 ----------------
      const { sessionId, pkgId } = startPackageSession({
        originalPackageName,
        uploaderName,
        operatorId: knownOperator?.id ?? operatorNameText,
        operatorName: operatorNameText,
        packageName: packageName.trim(),
        remark,
      })

      const uploads: LocalUploadFile[] = [
        ...stagedMains.map((item) => ({ file: item.file, kind: 'MAIN' as const, psd: item.psd })),
        ...stagedVariants.map((item) => ({ file: item.file, kind: 'VARIANT' as const })),
      ]
      await ingestLocalFiles(sessionId, uploads)
      selectPackage(pkgId)
      runPackagePairing(pkgId)
      setUploadState('MATCHED')
      setLastUploadSummary(`主素材 ${stagedMains.length} 个、副素材 ${stagedVariants.length} 张`)
      toast.success('已解析设计包并按同名 pairKey 完成配对（本地 Mock）')
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : error instanceof Error
            ? error.message
            : '解析失败'
      setBlockReason(`上传失败：${message}`)
      toast.error(message, { duration: 8000 })
    } finally {
      setProcessing(false)
      setProgressText('')
    }
  }

  // ---- 人工裁决：API 模式打后端，Mock 模式走本地 store（第二十三、二十五、二十六、二十七条） ----

  // 选中的副图是「本次上传的另一张副图」→ 改配；一张副图只能属于一个主素材
  const handleConfirm = async (variantAssetId: string) => {
    if (!selected || !activePkgId) return
    if (!isApiMode()) {
      reassignPairing(activePkgId, selected.id, variantAssetId, actor)
      setSelected(null)
      toast.success('已修改配对')
      return
    }
    const uploadId = overview?.pairingUploadId ?? overview?.upload?.id
    if (!uploadId) return
    const option = selected.options.find((item) => item.assetId === variantAssetId)
    if (option?.occupiedByPosition && option.occupiedByPosition !== selected.position) {
      setReassignPrompt({
        pairing: selected,
        variantAssetId,
        occupiedByPosition: option.occupiedByPosition,
      })
      return
    }
    try {
      await reassignPairingApi(uploadId, selected.id, variantAssetId, uploader.trim() || actor)
      await refreshPackageFromApi(activePkgId)
      setSelected(null)
      toast.success('已修改配对')
    } catch (error) {
      const apiError = error instanceof ApiError ? error : null
      const occupied = (apiError?.detail as { occupiedByPosition?: number } | null)?.occupiedByPosition
      if (apiError?.code === 'VARIANT_ALREADY_PAIRED' && occupied) {
        setReassignPrompt({ pairing: selected, variantAssetId, occupiedByPosition: occupied })
        return
      }
      toast.error(apiError?.message ?? '修改配对失败')
    }
  }

  /** 确认重新分配：原位置退回未配对，绝不双重占用 */
  const handleConfirmReassign = async () => {
    if (!reassignPrompt || !activePkgId) return
    if (!isApiMode()) {
      reassignPairing(
        activePkgId,
        reassignPrompt.pairing.id,
        reassignPrompt.variantAssetId,
        uploader.trim() || actor,
      )
      setReassignPrompt(null)
      setSelected(null)
      return
    }
    const uploadId = overview?.pairingUploadId ?? overview?.upload?.id
    if (!uploadId) return
    try {
      await reassignPairingApi(
        uploadId,
        reassignPrompt.pairing.id,
        reassignPrompt.variantAssetId,
        uploader.trim() || actor,
        true,
      )
      await refreshPackageFromApi(activePkgId)
      toast.success(
        `已把该副图重新分配给位置 ${reassignPrompt.pairing.position}，位置 ${reassignPrompt.occupiedByPosition} 退回未配对`,
      )
      setReassignPrompt(null)
      setSelected(null)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '重新分配失败')
    }
  }

  /** 确认单条配对 */
  const handleConfirmSelected = async () => {
    if (!selected || !activePkgId) return
    if (!selected.variantAssetId) {
      toast.error('该位置还没有副图，请先在列表里选一张')
      return
    }
    if (!isApiMode()) {
      confirmPairing(activePkgId, selected.id, actor)
      setSelected(null)
      toast.success('已确认配对')
      return
    }
    const uploadId = overview?.pairingUploadId ?? overview?.upload?.id
    if (!uploadId) return
    try {
      await reassignPairingApi(
        uploadId,
        selected.id,
        selected.variantAssetId,
        uploader.trim() || actor,
        true,
      )
      await confirmPairingsApi(uploadId, uploader.trim() || actor)
      await refreshPackageFromApi(activePkgId)
      setSelected(null)
      toast.success('已确认该位置的配对')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '确认失败')
    }
  }

  /** 按同名 pairKey 重新配对（手动重跑；人工修改过的行不会被覆盖） */
  const handleRunPairing = async () => {
    if (!activePkgId) return
    if (!isApiMode()) {
      runPackagePairing(activePkgId)
      setUploadState('MATCHED')
      return
    }
    const uploadId = overview?.pairingUploadId ?? overview?.upload?.id
    if (!uploadId) {
      toast.error('还没有上传记录，请先上传主素材与同名副素材')
      return
    }
    setProcessing(true)
    setProgressText('正在按同名 pairKey 配对…')
    try {
      const result = await runPairingApi(uploadId, uploader.trim() || actor)
      await refreshPackageFromApi(activePkgId)
      setUploadState('MATCHED')
      setBlockReason('')
      toast.success(
        `配对完成：已配对 ${result.pairedCount} 个、缺副图 ${result.unpairedCount} 个`,
        { duration: 8000 },
      )
    } catch (error) {
      const message = error instanceof ApiError ? error.message : '配对失败'
      setBlockReason(message)
      toast.error(message)
    } finally {
      setProcessing(false)
      setProgressText('')
    }
  }

  /** 确认整包配对 */
  const handleConfirmAllPairings = async () => {
    if (!activePkgId) return
    if (!isApiMode()) {
      const count = confirmAllPairings(activePkgId, actor)
      if (count) toast.success(`已确认 ${count} 条配对`)
      else toast.info('没有可确认的配对')
      return
    }
    const uploadId = overview?.pairingUploadId ?? overview?.upload?.id
    if (!uploadId) return
    try {
      const count = await confirmPairingsApi(uploadId, uploader.trim() || actor)
      await refreshPackageFromApi(activePkgId)
      if (count) toast.success(`已确认 ${count} 条配对`)
      else toast.info('没有可确认的配对')
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '确认失败')
    }
  }

  /** 确认整包并生成下一版（第一次即 V1）—— 与「上传成功」严格分开（第三十二条） */
  const handleCreateBatch = async () => {
    if (!activePkgId || !overview) return
    if (!isApiMode()) {
      toast.info('本地 Mock 模式不支持建版，请切换到真实后端')
      return
    }
    setCreatingBatch(true)
    setBlockReason('')
    try {
      const batch = await createBatchApi(
        activePkgId,
        uploader.trim() || actor,
        overview.upload?.id,
      )
      setUploadState('MATCHED')
      toast.success(
        `已生成 ${batch.code}：${batch.variantCount} 个副素材（${batch.variants
          .map((v) => v.displayCode)
          .join(' / ')}）`,
        { duration: 10000 },
      )
    } catch (error) {
      const message = error instanceof ApiError ? error.message : '生成版本失败'
      setBlockReason(message)
      toast.error(message, { duration: 10000 })
    } finally {
      setCreatingBatch(false)
    }
  }

  const handleSubmit = () => {
    if (!activePkgId) return
    const result = submitAndDispatch(activePkgId, actor)
    if (result.error) {
      toast.error(result.error)
      return
    }
    toast.success('已提交并派发运营')
    if (result.taskId) navigate(`/materials/distributions/${result.taskId}`)
  }

  const handleSupplement = () => {
    if (!activePkgId || !overview) return
    const missing = overview.coverage.missingCodes
    supplementCurrentVersion(activePkgId, {
      actor: actor,
      variants: missing.map((code, index) => ({
        fileName: `${code}.jpg`,
        // 演示环境使用内置示例图；后端接入后由上传接口返回真实文件与指纹
        previewUri: `/mock/mat-${String(21 + (index % 10)).padStart(2, '0')}.jpg`,
      })),
    })
    setSupplementOpen(false)
    toast.success(
      `已补充 ${missing.join('、')}（${overview.currentBatch?.code ?? 'V1'} 版本号未变，未创建新版本）`,
    )
  }

  const pairingList = overview?.pairings ?? []
  const pairedCount = pairingList.filter((p) => p.variantAssetId).length
  const missingCount = pairingList.filter((p) => p.status === 'UNPAIRED').length
  const confirmedCount = pairingList.filter((p) => p.status === 'CONFIRMED').length
  /** 上传前检查面板里的配对摘要文案 */
  const pairingSummaryText = pairingList.length
    ? `${confirmedCount} / ${pairingList.length} 已确认`
    : '还没有跑过配对'
  const allConfirmed = Boolean(pairingList.length) && confirmedCount === pairingList.length
  const currentBatch = overview?.currentBatch ?? null
  /** 暂存区 + 必填项还没凑齐时，把这些原因显示在按钮旁边 */
  const stagedBlockers = staged.length > 0 ? collectBlockers() : []

  /** 归属运营：自由文本输入，失焦时持久化（没有真实 userId 也允许保存） */
  const handleOperatorChange = (value: string) => {
    setOperator(value)
    writeStorage(OPERATOR_KEY, value.trim())
  }

  const persistOperator = async () => {
    const name = operator.trim()
    writeStorage(OPERATOR_KEY, name)
    if (!name || !overview) return
    const known = OPERATORS.find((o) => o.name === name || o.id === name)
    try {
      if (isApiMode()) {
        await materialApi.patchUploadSession(overview.session.id, {
          operatorId: known?.id,
          operatorName: name,
        })
        await refreshPackageFromApi(overview.pkg.id)
      } else {
        setSessionOperator(overview.session.id, known?.id ?? name, name)
      }
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '归属运营保存失败')
    }
  }

  /** 设计美工：自由文本输入，失焦时写入 design_packages.designer_name */
  const handleDesignerChange = (value: string) => {
    setDesigner(value)
    writeStorage(DESIGNER_KEY, value.trim())
  }

  const persistDesigner = async () => {
    const name = designer.trim()
    writeStorage(DESIGNER_KEY, name)
    if (!name || !overview || !isApiMode()) return
    try {
      // 「负责人」是设计包的业务负责人，写 responsible_name（会写 CHANGE_RESPONSIBLE 维护记录）
      await materialApi.patchDesignPackage(overview.pkg.id, { responsibleName: name })
      await refreshPackageFromApi(overview.pkg.id)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '负责人保存失败')
    }
  }

  /** 实际上传人：自由文本输入，失焦时写入 package_uploads.uploader_name */
  const handleUploaderChange = (value: string) => {
    setUploader(value)
    writeStorage(UPLOADER_KEY, value.trim())
  }

  const persistUploader = async () => {
    const name = uploader.trim()
    writeStorage(UPLOADER_KEY, name)
    if (!name || !overview || !isApiMode()) return
    try {
      await materialApi.patchUpload(overview.upload.id, { uploaderName: name })
      await refreshPackageFromApi(overview.pkg.id)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '实际上传人保存失败')
    }
  }

  /**
   * 设计编码：手填 + 失焦保存。
   *
   * 已选设计包时也允许改（美工改编号是真实场景），改动会 PATCH 到后端；
   * 素材详情的「关联设计」随之更新，不会留下两套口径。
   */
  const handleDesignCodeChange = (value: string) => {
    setDesignCodeDraft(value)
  }

  const persistDesignCode = async () => {
    const code = designCodeValue.trim()
    writeStorage(DESIGN_CODE_KEY, code)
    setDesignCodeStored(code)
    setDesignCodeDraft(null)
    if (!code || !overview || !isApiMode()) return
    if (code === overview.pkg.designCode) return
    try {
      await materialApi.patchDesignPackage(overview.pkg.id, { designCode: code })
      await refreshPackageFromApi(overview.pkg.id)
      toast.success(`设计编码已更新为 ${code}`)
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : '设计编码保存失败')
    }
  }

  /**
   * 从当前设计包切出，改为「新建一个设计包」。
   *
   * 与「继续上传新一版」严格区分：同一个设计包再上传 = 新版本（V2/V3…），
   * 新建设计包 = 另起一套 MAT 与版本号。两者不能让用户靠猜。
   */
  const handleNewPackage = () => {
    try {
      sessionStorage.removeItem(ACTIVE_PKG_KEY)
    } catch {
      // 隐私模式下 sessionStorage 不可用：忽略即可，内存态已一起重置
    }
    setActivePkgId('')
    setPackageName('')
    setStaged([])
    setSelected(null)
    setUploadState('IDLE')
    setLastUploadSummary('')
    setBlockReason('')
    toast.info('已切换到「新建设计包」：下一次上传会创建一个新的设计包，版本从 V1 开始')
  }

  return (
    <div className="min-h-screen bg-[#f5f6f8] text-gray-800">
      <header className="flex h-12 items-center justify-between border-b bg-white px-6 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span className="font-medium text-gray-600">素材中心</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span>上传设计包</span>
        </div>
        <div className="flex items-center gap-2">
          {health && (
            <StatusBadge tone={health.ok ? 'green' : health.kind === 'HEALTH_DEGRADED' ? 'amber' : 'red'}>
              {health.ok
                ? '后端已连接'
                : health.kind === 'HEALTH_DEGRADED'
                  ? '后端依赖异常'
                  : health.kind === 'CORS_BLOCKED'
                    ? 'CORS 被拦截'
                    : '后端连接失败'}
            </StatusBadge>
          )}
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={healthChecking}
            onClick={handleRecheck}
          >
            {healthChecking ? '检测中…' : '重新检测'}
          </Button>
          {demoPkgId && (
            <Button size="sm" variant="ghost" className="text-[#3d3192]" onClick={() => selectPackage(demoPkgId)}>
              <Layers className="h-3.5 w-3.5" />
              打开演示设计包
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">上传设计包</h1>
            <p className="mt-1 text-xs text-gray-400">
              上传 → 解析 → 同名配对 → 人工确认 → 生成版本；同一设计包整包统一生成版本号（V1 / V2 / V3…）。
            </p>
          </div>
          <StatusBadge tone={overview?.submitted ? 'green' : overview ? 'purple' : 'amber'}>
            {overview?.submitted ? '已提交并派发' : overview ? '草稿（未提交）' : '待上传'}
          </StatusBadge>
        </div>

        {/* ---------------------------------------------------------- 后端连通性（第4条：三种状态分开说） */}
        {health && !health.ok && (
          <div
            className={`rounded-md border px-3 py-2 text-xs ${
              health.kind === 'HEALTH_DEGRADED'
                ? 'border-amber-200 bg-amber-50/70 text-amber-900'
                : 'border-red-200 bg-red-50/70 text-red-800'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              <span className="font-medium">
                {health.kind === 'HEALTH_DEGRADED'
                  ? '后端已连通，但依赖不健康'
                  : health.kind === 'CORS_BLOCKED'
                    ? '后端已连通，但响应被浏览器 CORS 拦截'
                    : health.kind === 'HTTP_ERROR'
                      ? '后端健康检查失败'
                      : '无法连接后端服务'}
              </span>
              <span className="text-[11px] text-gray-500">接口地址：{health.apiBase}</span>
              <Button size="sm" variant="outline" className="ml-auto h-7" onClick={handleRecheck}>
                重新检测
              </Button>
            </div>
            <p className="mt-1">{health.message}</p>
            {health.hint && (
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px] leading-5 text-gray-600">
                {health.hint}
              </pre>
            )}
          </div>
        )}

        {/* ---------------------------------------------------------- 基本信息 */}
        <Card>
          <CardHeader><CardTitle className="text-base">设计包基本信息</CardTitle></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-4">
            <label className="space-y-1.5 text-xs">
              <span className="font-medium text-gray-600">设计包名称 <b className="text-red-500">*</b></span>
              <Input
                value={overview?.pkg.name ?? packageName}
                disabled={Boolean(overview)}
                onChange={(event) => setPackageName(event.target.value)}
                placeholder="例如 万圣节夜景球迷款"
              />
            </label>
            <label className="space-y-1.5 text-xs">
              <span className="font-medium text-gray-600">设计编码 <b className="text-red-500">*</b></span>
              <Input
                value={designCodeValue}
                onChange={(event) => handleDesignCodeChange(event.target.value)}
                onBlur={() => void persistDesignCode()}
                placeholder="例如 HB-2026-0917-A（美工的设计编号）"
              />
              <span className="block text-[11px] text-gray-400">
                这个编码就是素材详情里「关联设计」的依据；同一个设计的新一版填同一个编码
              </span>
            </label>
            {/* 负责人：业务上负责这套设计的人（= 之前的设计美工字段；实际上传人是点了上传的那个人） */}
            <label className="space-y-1.5 text-xs">
              <span className="font-medium text-gray-600">负责人 <b className="text-red-500">*</b></span>
              <Input
                value={overview?.pkg.responsibleName ?? designer}
                onChange={(event) => handleDesignerChange(event.target.value)}
                onBlur={() => void persistDesigner()}
                placeholder="可直接手填，例如 肖芸 / 李晴"
              />
            </label>
            {/* 实际上传人：这一次是谁执行上传（接登录系统后改只读） */}
            <label className="space-y-1.5 text-xs">
              <span className="font-medium text-gray-600">实际上传人 <b className="text-red-500">*</b></span>
              <Input
                value={overview?.upload.uploaderName ?? uploader}
                onChange={(event) => handleUploaderChange(event.target.value)}
                onBlur={() => void persistUploader()}
                placeholder="可直接手填，例如 小柯"
              />
            </label>
            <label className="space-y-1.5 text-xs">
              <span className="font-medium text-gray-600">归属运营 <b className="text-red-500">*</b></span>
              {/* 自由文本 + 建议列表：既能选既有运营，也能直接填新同事名字（不要求有 userId） */}
              <Input
                list="operator-suggestions"
                value={overview?.operatorName ?? operator}
                onChange={(event) => handleOperatorChange(event.target.value)}
                onBlur={() => void persistOperator()}
                placeholder="可直接手填，例如 王姐"
              />
              <datalist id="operator-suggestions">
                {OPERATORS.map((item) => <option key={item.id} value={item.name} />)}
              </datalist>
            </label>
            <label className="space-y-1.5 text-xs md:col-span-2">
              <span className="font-medium text-gray-600">标签</span>
              <TagPicker
                value={overview?.pkg.tags ?? tags}
                onChange={(next) => setTags(next)}
                placeholder="如 万圣节 / NHL / 夜景，用逗号或回车分隔"
                suggestions={allTags}
                disabled={Boolean(overview)}
              />
              <span className="block text-[11px] text-gray-400">
                设计包的标签会在建包时复制到包内素材；之后素材标签独立修改，不会互相覆盖
              </span>
            </label>
            <div className="grid gap-3 text-xs md:col-span-4 md:grid-cols-4">
              <div><p className="text-gray-400">设计编码</p><p className="mt-0.5 font-medium">{overview?.pkg.designCode ?? (designCodeValue || '-')}</p></div>
              <div><p className="text-gray-400">设计包编码</p><p className="mt-0.5 font-medium">{overview?.pkg.code ?? '-'}</p></div>
              <div><p className="text-gray-400">上传时间</p><p className="mt-0.5 font-medium">{overview ? formatDateTime(overview.upload.createdAt) : '-'}</p></div>
              <div><p className="text-gray-400">上架版本</p><p className="mt-0.5 font-medium">{currentBatch?.code ?? '尚未生成'}</p></div>
            </div>
            <label className="space-y-1.5 text-xs md:col-span-4">
              <span className="font-medium text-gray-600">备注</span>
              <Textarea
                value={overview?.pkg.remark ?? remark}
                onChange={(event) => setRemark(event.target.value)}
                className="min-h-16"
                placeholder="批次说明、活动信息等"
              />
            </label>
          </CardContent>
        </Card>

        {/* ---------------------------------------------------------- 文件上传 */}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">文件上传</CardTitle>
            {overview && (
              <span className="text-xs text-gray-400">
                已入库：主素材 {overview.uploadedFiles.mainCount} / PSD {overview.uploadedFiles.psdCount} / 副图 {overview.uploadedFiles.variantCount}
              </span>
            )}
          </CardHeader>
          <CardContent className="space-y-3">
            {/* 同一设计包再上传 = 新版本；必须写清楚，不能让用户猜（V1 → V2 → V3） */}
            {overview && (
              <div className="flex flex-wrap items-center gap-2 rounded-md bg-[#faf9ff] px-3 py-2 text-xs text-[#3d3192]">
                <Layers className="h-3.5 w-3.5 shrink-0" />
                <span>
                  当前设计包：<b>{overview.pkg.name}</b>
                  {currentBatch
                    ? `（已到 ${currentBatch.code}）—— 这次上传会作为它的新一版，确认配对后生成 V${currentBatch.versionNo + 1}`
                    : '（还没有版本）—— 这次上传会追加到同一个设计包，确认配对后生成 V1'}
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  className="ml-auto h-7"
                  disabled={processing}
                  onClick={handleNewPackage}
                >
                  <PackagePlus className="h-3.5 w-3.5" />
                  新建设计包（另起一套）
                </Button>
              </div>
            )}
            <div className="grid gap-3 md:grid-cols-2">
              <div
                onClick={() => mainInputRef.current?.click()}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files, 'MAIN') }}
                className="cursor-pointer rounded-lg border border-dashed border-[#3d3192]/35 bg-[#faf9ff] px-4 py-6 text-center hover:border-[#3d3192]"
              >
                <UploadCloud className="mx-auto h-6 w-6 text-[#3d3192]" />
                <p className="mt-2 text-sm text-gray-700">主素材（含 PSD 源文件）</p>
                <p className="mt-1 text-xs text-gray-400">命名如 1.psd / 1.jpg，位置编号不是永久身份</p>
                <input
                  ref={mainInputRef}
                  type="file"
                  multiple
                  accept=".psd,.jpg,.jpeg,.png"
                  className="hidden"
                  onChange={(event) => addFiles(event.target.files, 'MAIN')}
                />
              </div>
              <div
                onClick={() => variantInputRef.current?.click()}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => { event.preventDefault(); addFiles(event.dataTransfer.files, 'VARIANT') }}
                className="cursor-pointer rounded-lg border border-dashed border-[#3d3192]/35 bg-[#faf9ff] px-4 py-6 text-center hover:border-[#3d3192]"
              >
                <UploadCloud className="mx-auto h-6 w-6 text-[#3d3192]" />
                <p className="mt-2 text-sm text-gray-700">副素材（JPG，没有 PSD）</p>
                <p className="mt-1 text-xs text-gray-400">命名如 1.jpg，与主素材按同名 pairKey 配对；版本号在确认后统一生成</p>
                <input
                  ref={variantInputRef}
                  type="file"
                  multiple
                  accept=".jpg,.jpeg,.png"
                  className="hidden"
                  onChange={(event) => addFiles(event.target.files, 'VARIANT')}
                />
              </div>
            </div>

            {staged.length > 0 && (
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <FileArchive className="h-4 w-4 text-[#3d3192]" />
                  待解析：主素材 <b className="text-gray-700">{stagedMains.length}</b> 个、副素材 <b className="text-gray-700">{stagedVariants.length}</b> 张
                  {stagedCountCheck.diff !== 0 && (
                    <StatusBadge tone="red">
                      {stagedCountCheck.diff > 0 ? `多 ${stagedCountCheck.diff} 张` : `缺 ${Math.abs(stagedCountCheck.diff)} 张`}
                    </StatusBadge>
                  )}
                  <Button size="sm" variant="ghost" className="ml-auto h-7 text-gray-400" onClick={() => setStaged([])}>清空</Button>
                </div>
                <div className="flex flex-wrap gap-2">
                  {staged.map((item) => (
                    <div key={`${item.kind}-${item.file.name}`} className="w-[104px] overflow-hidden rounded-md border bg-white">
                      {item.kind === 'MAIN' && /\.(jpg|jpeg|png)$/i.test(item.file.name) ? (
                        <img src={item.previewUrl} alt={item.file.name} className="aspect-square w-full object-cover" />
                      ) : (
                        <div className="flex aspect-square w-full flex-col items-center justify-center bg-gray-50 text-[10px] text-gray-400">
                          <FileArchive className="h-5 w-5" />
                          {item.kind === 'MAIN' ? '主素材源文件' : '副素材'}
                        </div>
                      )}
                      <button
                        className="w-full truncate px-1.5 py-1 text-left text-[10px] text-gray-500 hover:text-red-500"
                        title="点击移除"
                        onClick={() => removeStaged(item.file)}
                      >
                        {item.file.name}{item.psd ? ' + PSD' : ''}
                      </button>
                    </div>
                  ))}
                </div>
                <MockHint>
                  {isApiMode() ? (
                    <>
                      文件将上传到后端并写入对象存储（MinIO）；<b>BLAKE3</b> 只用于判定「完全相同的文件」，
                      相同文件自动复用已有 Asset（去重），<b>不参与主副素材配对</b>。主素材会生成永久身份{' '}
                      <span className="font-mono">MAT-xxxxxx</span>，PSD 记为 Revision 1。
                      副图按 <b>VARIANT</b> 角色登记归属，与同名主素材按 <b>pairKey</b> 配对。
                    </>
                  ) : (
                    <>
                      当前为本地 Mock 模式（浏览器内 SHA-256 + aHash）。设置
                      <span className="font-mono"> VITE_MATERIAL_API=1 </span>
                      即可切换为真实后端。
                    </>
                  )}
                </MockHint>
                {processing && (
                  <div className="space-y-1 rounded-md bg-[#faf9ff] px-3 py-2">
                    <div className="flex items-center justify-between text-xs text-[#3d3192]">
                      <span>{progressText || '处理中…'}</span>
                      <span>{progressPercent}%</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#e6e3f5]">
                      <div
                        className="h-full rounded-full bg-[#3d3192] transition-all"
                        style={{ width: `${progressPercent}%` }}
                      />
                    </div>
                  </div>
                )}

                {/* 阻止提交的原因必须写在按钮旁边，用户不能猜（第四点） */}
                {stagedBlockers.length > 0 && (
                  <div className="rounded-md border border-amber-200 bg-amber-50/70 px-3 py-2 text-xs text-amber-800">
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      <div>
                        <p className="font-medium">还差这些才能提交：</p>
                        <ul className="mt-0.5 list-inside list-disc space-y-0.5">
                          {stagedBlockers.map((reason) => <li key={reason}>{reason}</li>)}
                        </ul>
                      </div>
                    </div>
                  </div>
                )}
                {blockReason && (
                  <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50/70 px-3 py-2 text-xs text-red-700">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span>{blockReason}</span>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2">
                  {/* 只有「正在提交」才 disabled；其余条件点击后校验并明确报错（第四点） */}
                  <Button
                    className="bg-[#3d3192] hover:bg-[#32277a]"
                    disabled={processing}
                    onClick={() => {
                      // 先本地校验，再弹「上传前检查」面板；用户确认后才真正写入
                      const blockers = collectBlockers()
                      if (blockers.length) {
                        setBlockReason(`还差这些才能提交：${blockers.join('、')}`)
                        return
                      }
                      setShowPreCheck(true)
                    }}
                  >
                    <WandSparkles className="h-4 w-4" />
                    {processing
                      ? progressText || '正在上传并计算指纹…'
                      : isApiMode() ? '上传并写入素材库' : '解析并按同名配对'}
                  </Button>
                  {!processing && stagedBlockers.length > 0 && (
                    <span className="text-xs text-gray-400">点击按钮会提示缺少的内容</span>
                  )}

                  {/* 上传完成后：明确区分「上传完成」与「生成 V1」（第三十二条） */}
                  {overview && uploadState !== 'IDLE' && (
                    <>
                      <StatusBadge tone="green">上传完成</StatusBadge>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={processing}
                        onClick={() => void handleRunPairing()}
                      >
                        <WandSparkles className="h-3.5 w-3.5" />
                        {pairingList.length ? '重新配对' : '开始配对'}
                      </Button>
                    </>
                  )}
                </div>
                {lastUploadSummary && uploadState !== 'IDLE' && (
                  <p className="text-xs text-gray-500">本次上传结果：{lastUploadSummary}</p>
                )}
              </div>
            )}

            {overview && (
              <div className="flex flex-wrap items-center gap-3 rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">
                <FileArchive className="h-4 w-4 text-[#3d3192]" />
                <span className="font-medium">{overview.upload.originalPackageName}</span>
                <StatusBadge tone="purple">{UPLOAD_TYPE_LABEL[overview.upload.uploadType]}</StatusBadge>
                <span className="text-gray-400">上传时间：{formatDateTime(overview.upload.createdAt)}</span>
                {overview.uploads.length > 1 && (
                  <span className="text-gray-400">该设计包共 {overview.uploads.length} 次上传</span>
                )}
                <span className="ml-auto text-gray-400">上传会话：{overview.session.id}</span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ---------------------------------------------------------- 本次上传结果 */}
        {overview && (
          <Card>
            <CardHeader className="flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">本次上传结果</CardTitle>
                <p className="mt-1 text-xs text-gray-400">
                  文件归属来自后端 package_upload_assets（上传行为 ↔ 文件 多对多），刷新页面不会丢失。
                </p>
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge tone="purple">主素材 {overview.uploadedFiles.mainCount}</StatusBadge>
                <StatusBadge tone="neutral">PSD {overview.uploadedFiles.psdCount}</StatusBadge>
                <StatusBadge tone={overview.uploadedFiles.variantCount ? 'green' : 'neutral'}>
                  副图 {overview.uploadedFiles.variantCount}
                </StatusBadge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600">
                <span>主素材：<b className="text-gray-800">{overview.uploadedFiles.mainCount}</b> 张</span>
                <span>PSD：<b className="text-gray-800">{overview.uploadedFiles.psdCount}</b> 个</span>
                <span>副图：<b className="text-gray-800">{overview.uploadedFiles.variantCount}</b> 张</span>
                <span className="text-gray-300">|</span>
                <span>已建立主素材：<b className="text-gray-800">{overview.mainMaterials.length}</b> 个 MAT</span>
                <span className="rounded bg-gray-100 px-2 py-0.5 text-gray-500">副素材关系：待 Phase 2 匹配</span>
                {overview.uploadedFiles.otherCount > 0 && (
                  <span>其他文件：<b className="text-gray-800">{overview.uploadedFiles.otherCount}</b> 个</span>
                )}
              </div>

              {overview.uploadedFiles.main.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-gray-500">主素材（{overview.uploadedFiles.mainCount}）</p>
                  <div className="flex flex-wrap gap-2">
                    {overview.uploadedFiles.main.map((file) => (
                      <figure key={file.assetId} className="w-[96px] overflow-hidden rounded-md border bg-white">
                        <img
                          src={file.previewUrl}
                          alt={file.originalFilename}
                          className="aspect-square w-full object-cover"
                          loading="lazy"
                        />
                        <figcaption className="truncate px-1.5 py-1 text-[10px] text-gray-500" title={file.originalFilename}>
                          {file.originalFilename}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              )}

              {overview.uploadedFiles.psd.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
                  <span className="font-medium">PSD：</span>
                  {overview.uploadedFiles.psd.map((file) => (
                    <span key={file.assetId} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[11px]">
                      {file.originalFilename}
                    </span>
                  ))}
                </div>
              )}

              {overview.uploadedFiles.variants.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-gray-500">
                    副图（{overview.uploadedFiles.variantCount} 张）
                    <span className="ml-2 font-normal text-gray-400">已入库为 Asset，主副对应关系由整包匹配 + 人工确认决定</span>
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {overview.uploadedFiles.variants.map((file) => (
                      <figure key={file.assetId} className="w-[96px] overflow-hidden rounded-md border bg-white">
                        <img
                          src={file.previewUrl}
                          alt={file.originalFilename}
                          className="aspect-square w-full object-cover"
                          loading="lazy"
                        />
                        <figcaption className="truncate px-1.5 py-1 text-[10px] text-gray-500" title={file.originalFilename}>
                          {file.originalFilename}
                        </figcaption>
                      </figure>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {overview && (
          <>
            {/* ------------------------------------------------------ 处理进度 */}
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">处理进度</CardTitle>
                <span className="text-xs text-gray-400">上传 → 解析 → 同名配对 → 人工确认 → 生成版本</span>
              </CardHeader>
              <CardContent>
                <div className="grid gap-2 text-xs md:grid-cols-5">
                  <div className="flex items-center gap-2 text-emerald-700"><Check className="h-4 w-4" />文件上传完成</div>
                  <div className="flex items-center gap-2 text-emerald-700"><Check className="h-4 w-4" />读取设计包</div>
                  <div className="flex items-center gap-2 text-emerald-700"><Check className="h-4 w-4" />计算图片指纹</div>
                  <div className={`flex items-center gap-2 ${pairingList.length ? 'text-emerald-700' : 'text-gray-400'}`}>
                    <WandSparkles className="h-4 w-4" />按同名 pairKey 配对
                  </div>
                  <div className={`flex items-center gap-2 ${overview.submitted ? 'text-emerald-700' : 'text-[#3d3192]'}`}>
                    <PackagePlus className="h-4 w-4" />
                    {overview.submitted ? '已提交并派发' : '待人工确认与提交'}
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* ------------------------------------------------------ 数量校验 */}
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle className="text-base">数量校验</CardTitle>
                <StatusBadge tone={overview.countCheck.blocked ? 'red' : 'purple'}>
                  主素材 {overview.countCheck.mainCount} / 副图 {overview.countCheck.variantUploadCount}
                </StatusBadge>
              </CardHeader>
              <CardContent className="space-y-3">
                <CountMismatchAlert messages={overview.countCheck.messages} />
                {!overview.countCheck.blocked && (
                  <div className="flex items-center gap-2 text-sm text-[#3d3192]">
                    <Check className="h-4 w-4" />
                    主素材 {overview.countCheck.mainCount} 个、副图 {overview.countCheck.variantUploadCount} 张，数量一致
                  </div>
                )}
                <div className="grid gap-3 text-xs sm:grid-cols-4">
                  <div className="rounded-md bg-gray-50 px-3 py-2">
                    <p className="text-gray-400">主素材数量</p>
                    <p className="mt-0.5 text-base font-semibold text-gray-800">{overview.countCheck.mainCount}</p>
                  </div>
                  <div className="rounded-md bg-gray-50 px-3 py-2">
                    <p className="text-gray-400">副图数量（已上传）</p>
                    <p className="mt-0.5 text-base font-semibold text-gray-800">{overview.countCheck.variantUploadCount}</p>
                  </div>
                  <div className="rounded-md bg-gray-50 px-3 py-2">
                    <p className="text-gray-400">{overview.coverage.versionCode} 覆盖</p>
                    <p className="mt-0.5 text-base font-semibold text-gray-800">
                      {overview.coverage.covered} / {overview.coverage.total}
                    </p>
                  </div>
                  <div className="rounded-md bg-gray-50 px-3 py-2">
                    <p className="text-gray-400">指纹版本</p>
                    <p className="mt-0.5 text-sm font-medium text-gray-700">指纹 Version 1</p>
                  </div>
                </div>
                {overview.coverage.missingCodes.length > 0 && (
                  <div className="flex flex-wrap items-center gap-3 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 text-sm text-amber-800">
                    <AlertTriangle className="h-4 w-4" />
                    <span>
                      {overview.coverage.versionCode} 覆盖 {overview.coverage.covered} / {overview.coverage.total}，缺：
                      <b className="font-mono">{overview.coverage.missingCodes.join('、')}</b>
                    </span>
                    <span className="text-xs text-amber-700">系统不会自动创建下一个版本</span>
                    <Button size="sm" variant="outline" className="ml-auto h-7" onClick={() => setSupplementOpen(true)}>
                      补充{overview.coverage.versionCode}
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* ------------------------------------------------------ 主副素材配对（同名 pairKey） */}
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <div>
                  <CardTitle className="text-base">主素材与副素材配对</CardTitle>
                  <p className="mt-1 text-xs text-gray-400">
                    同一次上传内按<b>同名 pairKey</b>直接配对：主素材 <span className="font-mono">1.jpg</span> 与副素材{' '}
                    <span className="font-mono">1.jpg</span> 配对，PSD <span className="font-mono">1.psd</span> 建立关联。
                    关系只由文件名同名键决定，系统不会根据图片内容猜测关系。
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {pairingList.length > 0 && (
                    <>
                      <StatusBadge tone="purple">
                        已配对 {pairedCount} / {pairingList.length}
                      </StatusBadge>
                      <StatusBadge tone="green">已确认 {confirmedCount}</StatusBadge>
                      {missingCount > 0 && <StatusBadge tone="amber">缺副图 {missingCount}</StatusBadge>}
                    </>
                  )}
                  <Button size="sm" variant="outline" disabled={processing} onClick={() => void handleRunPairing()}>
                    <WandSparkles className="h-3.5 w-3.5" />
                    {pairingList.length ? '重新配对' : '开始配对'}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!pairingList.length}
                    onClick={() => void handleConfirmAllPairings()}
                  >
                    确认全部配对
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {pairingList.length === 0 ? (
                  <div className="rounded-md bg-gray-50 px-3 py-6 text-center text-xs text-gray-500">
                    {overview.uploadedFiles.variantCount === 0
                      ? '本次上传还没有副素材。请上传与主素材同名的副图（例如主素材 1.jpg 对应副素材 1.jpg），再点「开始配对」。'
                      : '还没有配对结果，点右上角「开始配对」。'}
                  </div>
                ) : (
                  <MaterialPairingTable pairings={pairingList} onModify={setSelected} />
                )}

                {/* 确认整包并生成版本 */}
                <div className="flex flex-wrap items-center gap-3 rounded-md border border-[#e6e3f5] bg-[#faf9ff] px-3 py-3 text-xs">
                  <div className="text-gray-600">
                    阻断异常：
                    <b className={overview.blockingAnomalies.length ? 'text-red-600' : 'text-emerald-700'}>
                      {overview.blockingAnomalies.length ? `${overview.blockingAnomalies.length} 项未解决` : '无'}
                    </b>
                    <span className="ml-3 text-gray-500">
                      已确认 {confirmedCount}/{pairingList.length} 个位置
                    </span>
                    {currentBatch && <span className="ml-3 text-emerald-700">当前版本 {currentBatch.code}</span>}
                  </div>
                  <Button
                    className="ml-auto bg-[#3d3192] hover:bg-[#32277a]"
                    disabled={creatingBatch || overview.countCheck.blocked || !allConfirmed}
                    onClick={() => void handleCreateBatch()}
                  >
                    <PackagePlus className="h-4 w-4" />
                    {creatingBatch
                      ? '正在生成…'
                      : currentBatch
                        ? `确认整包并生成 V${currentBatch.versionNo + 1}`
                        : '确认整包并生成 V1'}
                  </Button>
                  {/* 按钮禁用原因必须写出来，不能让用户猜 */}
                  {overview.countCheck.blocked && (
                    <span className="text-red-600">主素材与副图数量不一致，禁止生成版本</span>
                  )}
                  {!overview.countCheck.blocked && !allConfirmed && pairingList.length > 0 && (
                    <span className="text-amber-700">
                      还有 {pairingList.length - confirmedCount} 个位置未确认配对
                    </span>
                  )}
                  {overview.blockingAnomalies.length > 0 && (
                    <span className="text-red-600">
                      存在未解决的阻断异常（{overview.blockingAnomalies.map((a) => a.message).join('；')}）
                    </span>
                  )}
                </div>

                {/* 版本生成后：明确「下一步是派发」 */}
                {currentBatch && (
                  <div className="flex flex-wrap items-center gap-3 rounded-md bg-emerald-50/70 px-3 py-3 text-xs text-emerald-800">
                    <Check className="h-4 w-4" />
                    <span className="font-medium">{currentBatch.code} 已生成</span>
                    <span className="font-mono">
                      {(currentBatch.variants ?? []).map((v) => v.displayCode).join(' / ')}
                    </span>
                    <span className="text-emerald-700">
                      （新建副素材 {currentBatch.createdVariantCount} 个、复用已有副素材{' '}
                      {currentBatch.reusedVariantCount} 个）
                    </span>
                    <span className="ml-auto text-gray-500">下一步：派发运营（Phase 3 开放）</span>
                  </div>
                )}
                {currentBatch && (currentBatch.reusedNotes ?? []).length > 0 && (
                  <div className="rounded-md border border-[#e6e3f5] bg-[#faf9ff] px-3 py-2 text-xs text-[#3d3192]">
                    {(currentBatch.reusedNotes ?? []).map((note) => (
                      <p key={note}>{note}</p>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* ------------------------------------------------------ 异常 + 维护记录 */}
            <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
              <Card>
                <CardHeader className="flex-row items-center justify-between">
                  <CardTitle className="text-base">异常处理</CardTitle>
                  <StatusBadge tone={overview.anomalies.length ? 'amber' : 'green'}>
                    {overview.anomalies.length ? `${overview.anomalies.length} 项待处理` : '已处理'}
                  </StatusBadge>
                </CardHeader>
                <CardContent>
                  <AnomalyList
                    anomalies={overview.anomalies}
                    onResolve={(anomaly) => {
                      resolveAnomaly(overview.pkg.id, anomaly.id, actor)
                      toast.success('已标记该异常为已处理')
                    }}
                    onNotifyLead={() => toast.info('钉钉通知未接入，已记录异常通知意图（预留 notifyTarget = DESIGN_LEAD）')}
                  />
                </CardContent>
              </Card>
              <Card>
                <CardHeader><CardTitle className="text-base">维护记录</CardTitle></CardHeader>
                <CardContent className="max-h-[420px] overflow-y-auto">
                  <ActivityTimeline logs={overview.logs} />
                </CardContent>
              </Card>
            </div>

            {/* ------------------------------------------------------ 派发运营 */}
            <Card>
              <CardHeader><CardTitle className="text-base">派发运营</CardTitle></CardHeader>
              <CardContent className="space-y-3">
                <div className="flex flex-wrap items-center gap-4 text-sm">
                  <div>
                    将派发给 <span className="font-medium">{overview.operatorName ?? '未指定'}</span>，
                    版本 <span className="font-medium">{currentBatch?.code ?? '尚未生成'}</span>，
                    共 <span className="font-medium">{overview.countCheck.variantUploadCount}</span> 张副图
                  </div>
                  <Select
                    value={overview.operatorId ?? ''}
                    onValueChange={handleOperatorChange}
                  >
                    <SelectTrigger className="h-8 w-[140px] text-xs"><SelectValue placeholder="切换运营" /></SelectTrigger>
                    <SelectContent>
                      {OPERATORS.map((item) => <SelectItem key={item.id} value={item.id}>{item.name}</SelectItem>)}
                    </SelectContent>
                  </Select>
                  <span className="text-xs text-gray-400">后续可新增派发给其他运营，复用同一套底层副素材，不复制图片</span>
                </div>

                <div className="flex flex-wrap items-center gap-3 rounded-md bg-gray-50 px-3 py-3 text-xs">
                  <div className="text-gray-500">设计包：<b className="text-gray-700">{overview.pkg.name}</b></div>
                  <div className="text-gray-500">主素材：<b className="text-gray-700">{overview.countCheck.mainCount}</b></div>
                  <div className="text-gray-500">副图：<b className="text-gray-700">{overview.countCheck.variantUploadCount}</b></div>
                  <div className="text-gray-500">版本：<b className="text-gray-700">{currentBatch?.code ?? '尚未生成'}</b></div>
                  <div className="text-gray-500">配对完成：<b className="text-gray-700">{pairedCount}/{pairingList.length}</b></div>
                </div>

                <div className="flex items-center gap-2">
                  <Button
                    className="bg-[#3d3192] hover:bg-[#32277a]"
                    // 归属运营允许只有名字（无 userId），所以不能用 operatorId 作为禁用条件
                    disabled={overview.submitted || !overview.operatorName || isApiMode()}
                    onClick={handleSubmit}
                  >
                    提交并派发
                  </Button>
                  {isApiMode() && (
                    <span className="text-xs text-gray-500">
                      {currentBatch
                        ? `${currentBatch.code} 已生成；派发运营属 Phase 3，暂不在本阶段开放`
                        : '需先生成版本；派发运营属 Phase 3'}
                    </span>
                  )}
                  {!isApiMode() && overview.countCheck.blocked && (
                    <span className="text-xs text-red-600">主副素材数量不一致，禁止直接提交</span>
                  )}
                  {overview.submitted && (
                    <Button variant="link" onClick={() => navigate('/materials')}>返回素材中心查看</Button>
                  )}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </main>

      {/* ------------------------------------------------------ 上传前检查（确认后才真正写入） */}
      <Dialog open={showPreCheck} onOpenChange={(open) => !open && setShowPreCheck(false)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>上传前检查</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm text-gray-700">
            <div className="grid grid-cols-2 gap-2 rounded-md bg-gray-50 px-3 py-2 text-xs">
              <div>设计包：<b>{overview?.pkg.name ?? packageName.trim()}</b></div>
              <div>负责人：<b>{overview?.pkg.responsibleName ?? designer.trim()}</b></div>
              <div>归属运营：<b>{overview?.operatorName ?? operator.trim()}</b></div>
              <div>设计编码：<b>{overview?.pkg.designCode ?? designCodeValue.trim()}</b></div>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {(overview?.pkg.tags ?? tags).length ? (
                (overview?.pkg.tags ?? tags).map((tag) => (
                  <span key={tag} className="rounded-full bg-[#f0eef9] px-2 py-0.5 text-[11px] text-[#3d3192]">
                    {tag}
                  </span>
                ))
              ) : (
                <span className="text-xs text-gray-400">没有标签</span>
              )}
            </div>
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-md bg-gray-50 py-2">主素材<br /><b className="text-base">{stagedMains.length || overview?.uploadedFiles.mainCount || 0}</b></div>
              <div className="rounded-md bg-gray-50 py-2">PSD<br /><b className="text-base">{stagedMains.filter((m) => m.psd).length || overview?.uploadedFiles.psdCount || 0}</b></div>
              <div className="rounded-md bg-gray-50 py-2">副素材<br /><b className="text-base">{stagedVariants.length || overview?.uploadedFiles.variantCount || 0}</b></div>
            </div>
            {overview && (
              <div className="rounded-md border border-[#e6e3f5] bg-[#faf9ff] px-3 py-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-[#3d3192]">配对</span>
                  <span className="font-medium">{pairingSummaryText}</span>
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <span className="text-gray-500">异常</span>
                  <span className="font-medium">{overview.anomalies.length ? `${overview.anomalies.length} 项待处理` : '0'}</span>
                </div>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowPreCheck(false)}>返回修改</Button>
              <Button
                className="bg-[#3d3192] hover:bg-[#32277a]"
                onClick={() => {
                  setShowPreCheck(false)
                  void analyze()
                }}
              >
                确认上传并写入素材库
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* ------------------------------------------------------ 上传成功后的下一步 */}
      <Dialog open={showSuccessPanel} onOpenChange={(open) => !open && setShowSuccessPanel(false)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>上传成功</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm text-gray-700">
            <div className="grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-md bg-gray-50 py-2">主素材<br /><b className="text-base">{overview?.uploadedFiles.mainCount ?? 0}</b></div>
              <div className="rounded-md bg-gray-50 py-2">PSD<br /><b className="text-base">{overview?.uploadedFiles.psdCount ?? 0}</b></div>
              <div className="rounded-md bg-gray-50 py-2">副素材<br /><b className="text-base">{overview?.uploadedFiles.variantCount ?? 0}</b></div>
            </div>
            <p className="text-xs text-gray-500">配对与版本在上传页继续完成；素材已经写入素材库。</p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setShowSuccessPanel(false)
                }}
              >
                查看设计包
              </Button>
              <Button
                variant="outline"
                onClick={() => {
                  setShowSuccessPanel(false)
                  navigate('/materials')
                }}
              >
                进入素材中心
              </Button>
              {isApiMode() && (
                <Button
                  className="bg-[#3d3192] hover:bg-[#32277a]"
                  onClick={() => {
                    setShowSuccessPanel(false)
                    // 配对已全部确认时直接生成；否则页面底部有「确认整包并生成 V1」
                    void handleConfirmAllPairings().then(() => void handleCreateBatch())
                  }}
                >
                  生成 V1
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ------------------------------------------------------ 副图已被占用：确认重新分配（第二十七条） */}
      <Dialog open={Boolean(reassignPrompt)} onOpenChange={(open) => !open && setReassignPrompt(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>确认重新分配副图</DialogTitle></DialogHeader>
          {reassignPrompt && (
            <div className="space-y-3 text-sm text-gray-700">
              <p>
                该副图当前已关联主素材位置 <b>{reassignPrompt.occupiedByPosition}</b>。是否重新分配？
              </p>
              <p className="rounded-md bg-amber-50/70 px-3 py-2 text-xs text-amber-800">
                确认后：位置 {reassignPrompt.occupiedByPosition} 会退回待处理，位置{' '}
                {reassignPrompt.pairing.position} 使用这张副图。同一个副图不会同时属于两个主素材。
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={() => setReassignPrompt(null)}>取消</Button>
                <Button className="bg-[#3d3192] hover:bg-[#32277a]" onClick={() => void handleConfirmReassign()}>
                  确认重新分配
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ------------------------------------------------------ 修改配对 Dialog */}
      <Dialog open={Boolean(selected)} onOpenChange={(open) => !open && setSelected(null)}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader><DialogTitle>修改主副素材配对</DialogTitle></DialogHeader>
          {selected && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 rounded-md bg-gray-50 p-3 text-sm">
                <img src={selected.mainPreviewUri} alt="" className="h-14 w-14 rounded border bg-white object-cover" />
                <div>
                  <div>
                    主素材位置 <b>{selected.position}</b> ·{' '}
                    <span className="font-mono text-xs">{selected.materialCode || '待分配'}</span>
                  </div>
                  <p className="text-xs text-gray-400">{selected.mainSourceFileName}</p>
                  <div className="mt-1 flex items-center gap-2 text-xs text-gray-500">
                    当前状态：
                    <PairingStatusBadge status={selected.status} source={selected.source} />
                    <span className="rounded bg-gray-100 px-1.5 font-mono text-[10px]">
                      pairKey {selected.pairKey || '—'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium">
                  本次上传的副素材（点击即可改为该配对）
                  <span className="ml-2 font-normal text-xs text-gray-400">
                    同一个副素材只能属于一个主素材
                  </span>
                </p>
                {selected.options.length === 0 ? (
                  <p className="rounded-md bg-gray-50 px-3 py-4 text-center text-xs text-gray-500">
                    本次上传没有副素材，请先上传与主素材同名的副图。
                  </p>
                ) : (
                  <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                    {selected.options.map((option) => {
                      const active = selected.variantAssetId === option.assetId
                      const occupiedByOther =
                        option.occupiedByPosition && option.occupiedByPosition !== selected.position
                      return (
                        <button
                          key={option.assetId}
                          onClick={() => void handleConfirm(option.assetId)}
                          className={`flex w-full items-center gap-3 rounded-md border p-2 text-left hover:border-[#3d3192] ${
                            active ? 'border-[#3d3192] bg-[#faf9ff]' : ''
                          }`}
                        >
                          {option.previewUri ? (
                            <img
                              src={option.previewUri}
                              alt={option.originalFilename}
                              className="h-10 w-10 rounded border object-cover"
                            />
                          ) : (
                            <div className="flex h-10 w-10 items-center justify-center rounded border bg-gray-50 text-[10px] text-gray-300">
                              无图
                            </div>
                          )}
                          <span className="flex-1 text-sm">
                            {option.originalFilename}
                            <span className="ml-2 rounded bg-gray-100 px-1 font-mono text-[10px] text-gray-500">
                              pairKey {option.pairKey || '—'}
                            </span>
                          </span>
                          {active && <StatusBadge tone="purple">当前</StatusBadge>}
                          {occupiedByOther && (
                            <StatusBadge tone="amber">已被位置 {option.occupiedByPosition} 使用</StatusBadge>
                          )}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          )}
          <DialogFooter className="sm:justify-between">
            <Button
              variant="outline"
              className="text-[#3d3192]"
              disabled={!selected?.variantAssetId}
              onClick={() => void handleConfirmSelected()}
            >
              确认当前配对
            </Button>
            <Button variant="outline" onClick={() => setSelected(null)}>取消</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ------------------------------------------------------ 补充版本 Dialog */}
      <Dialog open={supplementOpen} onOpenChange={setSupplementOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle>补充 {overview?.coverage.versionCode ?? 'V1'} 素材</DialogTitle></DialogHeader>
          {overview && (
            <div className="space-y-3 text-sm">
              <p className="text-gray-600">
                设计包新增了主素材，但 {overview.coverage.versionCode} 还没有对应副素材。
                系统不会自动创建新版本，只会把缺失位置补进 {overview.coverage.versionCode}。
              </p>
              <div className="rounded-md bg-gray-50 px-3 py-2 text-xs">
                <div className="text-gray-500">当前覆盖：<b className="text-gray-700">{overview.coverage.covered} / {overview.coverage.total}</b></div>
                <div className="mt-1 text-gray-500">缺失：<b className="font-mono text-gray-700">{overview.coverage.missingCodes.join('、')}</b></div>
              </div>
              <MockHint>演示环境将使用内置示例图片补齐；后端接入后由上传接口返回真实文件与指纹。</MockHint>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setSupplementOpen(false)}>取消</Button>
            <Button className="bg-[#3d3192] hover:bg-[#32277a]" onClick={handleSupplement}>确认补充</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
