import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ClipboardPaste, FileUp, PackagePlus, RefreshCw, ScanSearch, UploadCloud, ClipboardCheck, BarChart3 } from 'lucide-react';
import { toast } from 'sonner';
import { materialApi, type MaterialVariantContract, type OrderImportBatchDto, type OrderItemDto } from '@/services/apiClient';

type Tab = 'upload' | 'records' | 'match' | 'review' | 'sales';

const TYPE_LABEL: Record<string, string> = {
  MATERIAL_SOURCE: '素材源图',
  FINAL_EFFECT: '最终效果图',
  PREVIEW_ONLY: '仅预览',
  UNKNOWN: '未知',
};
const STATUS_TONE: Record<string, string> = {
  PARSED: 'bg-emerald-50 text-emerald-600',
  PARTIAL: 'bg-amber-50 text-amber-600',
  FAILED: 'bg-red-50 text-red-600',
  DUPLICATE: 'bg-gray-100 text-gray-500',
};
const MATCH_TONE: Record<string, string> = {
  NOT_STARTED: 'bg-gray-100 text-gray-500',
  CONFIRMED: 'bg-emerald-50 text-emerald-600',
  REVIEW_REQUIRED: 'bg-amber-50 text-amber-600',
  UNMATCHED: 'bg-gray-100 text-gray-500',
  FAILED: 'bg-red-50 text-red-600',
};

/** 订单识别：上传领星 ZIP / 导入记录 / 解析结果（素材匹配与异常审核在后续阶段） */
export default function OrderCenterPage() {
  const [tab, setTab] = useState<Tab>('upload');
  const [batches, setBatches] = useState<OrderImportBatchDto[]>([]);
  const [categoryCode, setCategoryCode] = useState('BLADE_SHOES');
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [selectedBatch, setSelectedBatch] = useState<OrderImportBatchDto | null>(null);
  const [items, setItems] = useState<OrderItemDto[]>([]);
  const [itemsLoading, setItemsLoading] = useState(false);

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await materialApi.listOrderImports());
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '加载导入记录失败');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await materialApi.listOrderImports();
        if (!cancelled) setBatches(list);
      } catch (error) {
        if (!cancelled) toast.error(error instanceof Error ? error.message : '加载导入记录失败');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const resp = await materialApi.importOrderZip(file, categoryCode);
        toast.success(
          `导入完成：${resp.summary.itemCount ?? 0} 个订单（新增 ${resp.summary.newItemCount ?? 0}，去重 ${resp.summary.duplicateItemCount ?? 0}）`,
        );
        await loadBatches();
        setTab('records');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : '导入失败');
      } finally {
        setUploading(false);
      }
    },
    [categoryCode, loadBatches],
  );

  const openBatch = useCallback(async (batch: OrderImportBatchDto) => {
    setSelectedBatch(batch);
    setItemsLoading(true);
    setItems([]);
    try {
      const list = await materialApi.getOrderImportItems(batch.id);
      setItems(list);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '加载解析结果失败');
    } finally {
      setItemsLoading(false);
    }
  }, []);

  const norm = (item: OrderItemDto): Record<string, unknown> => item.normalized ?? {};

  return (
    <div className="flex h-screen flex-col bg-[#f5f6f8] text-gray-800">
      <header className="flex h-10 shrink-0 items-center gap-2 px-4 text-xs text-gray-400">
        <span className="font-medium text-gray-600">订单识别</span>
        <span className="mx-1">/</span>
        <span>{selectedBatch ? '解析结果' : tab === 'upload' ? '上传订单' : '导入记录'}</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => {
              void loadBatches();
              setSelectedBatch(null);
            }}
            className="flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-500 hover:text-[#3d3192]"
          >
            <RefreshCw className="h-3 w-3" /> 刷新
          </button>
          <button
            onClick={() => {
              setSelectedBatch(null);
              setTab('upload');
            }}
            className="flex items-center gap-1 rounded bg-[#3d3192] px-2.5 py-1 text-xs text-white hover:bg-[#32277a]"
          >
            <FileUp className="h-3 w-3" /> 上传订单
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* 左侧导航 */}
        <aside className="w-44 shrink-0 border-r border-gray-200 bg-white p-2">
          <div className="space-y-0.5">
            {(
              [
                { key: 'upload', label: '上传订单', icon: UploadCloud },
                { key: 'records', label: '导入记录', icon: PackagePlus },
                { key: 'match', label: '素材匹配', icon: ScanSearch },
                { key: 'review', label: '异常审核', icon: ClipboardCheck },
                { key: 'sales', label: '销量归因', icon: BarChart3 },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                onClick={() => {
                  setTab(t.key);
                  setSelectedBatch(null);
                }}
                className={`flex w-full items-center gap-2 rounded px-2.5 py-1.5 text-[13px] ${
                  !selectedBatch && tab === t.key
                    ? 'bg-[#f0eef9] font-medium text-[#3d3192]'
                    : 'text-gray-600 hover:bg-gray-50'
                }`}
              >
                <t.icon className="h-3.5 w-3.5" />
                {t.label}
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto p-4">
          {selectedBatch ? (
            /* ---------- 解析结果 ---------- */
            <div>
              <button
                onClick={() => setSelectedBatch(null)}
                className="mb-3 flex items-center gap-1 text-xs text-[#3d3192] hover:underline"
              >
                <ArrowLeft className="h-3.5 w-3.5" /> 返回导入记录
              </button>
              <div className="rounded-md border border-gray-100 bg-white p-3">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
                  <span className="font-medium text-gray-800">{selectedBatch.originalFilename}</span>
                  <span className={`rounded px-1.5 py-0.5 ${STATUS_TONE[selectedBatch.status] ?? 'bg-gray-100 text-gray-600'}`}>
                    {selectedBatch.status}
                  </span>
                  <span className="text-gray-400">{selectedBatch.createdAt.replace('T', ' ').slice(0, 19)}</span>
                  <span className="text-gray-400">
                    {selectedBatch.categoryCode}（{selectedBatch.categoryName}）
                  </span>
                  <span className="text-gray-400">
                    JSON {selectedBatch.totalJsonCount} · 订单 {selectedBatch.totalItemCount}
                  </span>
                </div>
              </div>

              <div className="mt-3">
                {itemsLoading ? (
                  <div className="py-10 text-center text-[13px] text-gray-400">正在加载解析结果…</div>
                ) : items.length === 0 ? (
                  <div className="py-10 text-center text-[13px] text-gray-400">该批次没有解析出订单</div>
                ) : (
                  <>
                  <div className="mb-2 flex flex-wrap gap-2 text-[10px] text-gray-500">
                    <span>解析 {items.length}</span>
                    <span>PARSED {items.filter((x) => x.parseStatus === 'PARSED').length}</span>
                    <span>REVIEW {items.filter((x) => x.parseStatus === 'REVIEW_REQUIRED').length}</span>
                    <span>NON_CUSTOM {items.filter((x) => x.parseStatus === 'NON_CUSTOM').length}</span>
                    {(['MATERIAL_SOURCE', 'FINAL_EFFECT', 'PREVIEW_ONLY', 'UNKNOWN'] as const).map((type) => (
                      <span key={type}>{type} {items.filter((x) => x.parseStatus !== 'NON_CUSTOM' && norm(x).imageType === type).length}</span>
                    ))}
                    <span>BLACK {items.filter((x) => x.parseStatus !== 'NON_CUSTOM' && String(norm(x).soleColor).toUpperCase() === 'BLACK').length}</span>
                    <span>WHITE {items.filter((x) => x.parseStatus !== 'NON_CUSTOM' && String(norm(x).soleColor).toUpperCase() === 'WHITE').length}</span>
                    <span>文字 {items.filter((x) => Boolean(norm(x).customName || norm(x).customNumber || norm(x).frontName || norm(x).frontNumber || norm(x).backName || norm(x).backNumber)).length}</span>
                    <span>Logo {items.filter((x) => Boolean(norm(x).hasBuyerLogo)).length}</span>
                  </div>
                  <table className="w-full border-collapse text-left text-xs">
                    <thead>
                      <tr className="border-b border-gray-200 text-gray-400">
                        <th className="px-2 py-1.5 font-normal">订单号</th>
                        <th className="px-2 py-1.5 font-normal">订单行</th>
                        <th className="px-2 py-1.5 font-normal">ASIN</th>
                        <th className="px-2 py-1.5 font-normal">数量</th>
                        <th className="px-2 py-1.5 font-normal">解析状态</th>
                        <th className="px-2 py-1.5 font-normal">定制名/号码</th>
                        <th className="px-2 py-1.5 font-normal">鞋底色</th>
                        <th className="px-2 py-1.5 font-normal">图片类型</th>
                        <th className="px-2 py-1.5 font-normal">素材图 / 最终效果图</th>
                        <th className="px-2 py-1.5 font-normal">Logo</th>
                        <th className="px-2 py-1.5 font-normal">匹配</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((item) => {
                        const p = norm(item);
                        return (
                          <tr key={item.id} className="border-b border-gray-50 align-top hover:bg-gray-50/50">
                            <td className="px-2 py-1.5 font-mono text-[11px]">{item.orderId}</td>
                            <td className="px-2 py-1.5 font-mono text-[11px]">{item.orderItemId ?? '—'}</td>
                            <td className="px-2 py-1.5 font-mono text-[11px]">{item.childAsin ?? '—'}</td>
                            <td className="px-2 py-1.5">{item.quantity}</td>
                            <td className="px-2 py-1.5"><span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_TONE[item.parseStatus] ?? 'bg-gray-100 text-gray-500'}`}>{item.parseStatus}</span></td>
                            <td className="px-2 py-1.5">
                              {[p.customName, p.customNumber].filter(Boolean).join(' · ') || '—'}
                              {Boolean(p.frontName || p.backName) && (
                                <span className="ml-1 text-[10px] text-gray-400">
                                  F:{String(p.frontName ?? '')} {String(p.frontNumber ?? '')} B:{String(p.backName ?? '')} {String(p.backNumber ?? '')}
                                </span>
                              )}
                              {Boolean(p.buyerRequest) && <div className="mt-0.5 max-w-[180px] truncate text-[10px] text-gray-400" title={String(p.buyerRequest)}>留言：{String(p.buyerRequest)}</div>}
                            </td>
                            <td className="px-2 py-1.5">{String(p.soleColor ?? '—')}</td>
                            <td className="px-2 py-1.5">
                              <span className={`rounded px-1.5 py-0.5 text-[10px] ${
                                item.parseStatus === 'NON_CUSTOM' ? 'bg-blue-50 text-blue-600' : p.imageType === 'UNKNOWN' ? 'bg-red-50 text-red-600' : 'bg-gray-100 text-gray-600'
                              }`}>
                                {item.parseStatus === 'NON_CUSTOM' ? '无定制信息' : TYPE_LABEL[String(p.imageType)] ?? String(p.imageType ?? '—')}
                              </span>
                            </td>
                            <td className="max-w-[220px] truncate px-2 py-1.5 font-mono text-[10px] text-gray-500" title={String(p.materialUrl ?? p.finalEffectUrl ?? '')}>
                              {p.materialUrl ? '素材:' : p.finalEffectUrl ? '效果:' : ''}
                              {String(p.materialUrl ?? p.finalEffectUrl ?? '—').slice(0, 40)}
                            </td>
                            <td className="px-2 py-1.5">
                              {p.hasBuyerLogo ? (
                                <span className="rounded bg-blue-50 px-1.5 py-0.5 text-[10px] text-blue-600">
                                  原始{Array.isArray(p.buyerLogoOriginal) ? p.buyerLogoOriginal.length : 0}
                                  {p.buyerLogoSvg ? '·SVG' : ''}
                                </span>
                              ) : '—'}
                            </td>
                            <td className="px-2 py-1.5">
                              <span className={`rounded px-1.5 py-0.5 text-[10px] ${MATCH_TONE[item.matchStatus] ?? 'bg-gray-100 text-gray-500'}`}>
                                {item.matchStatus}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  </>
                )}
              </div>
            </div>
          ) : tab === 'upload' ? (
            /* ---------- 上传订单 ---------- */
            <div className="mx-auto max-w-2xl">
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  const f = e.dataTransfer.files?.[0];
                  if (f) void upload(f);
                }}
                onClick={() => fileRef.current?.click()}
                className={`flex h-52 cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed transition-colors ${
                  dragging ? 'border-[#3d3192] bg-[#f0eef9]' : 'border-gray-200 bg-white hover:border-[#3d3192]/50'
                }`}
              >
                {uploading ? (
                  <div className="text-sm text-[#3d3192]">正在解析订单 ZIP…</div>
                ) : (
                  <>
                    <UploadCloud className={`h-9 w-9 ${dragging ? 'text-[#3d3192]' : 'text-gray-300'}`} />
                    <div className="mt-2 text-sm text-gray-600">拖拽领星订单 ZIP 到这里，或点击选择</div>
                    <div className="mt-1 flex items-center gap-1 text-xs text-gray-400">
                      <ClipboardPaste className="h-3 w-3" /> 支持外层 Excel + Files/image/*.zip 的领星导出包
                    </div>
                  </>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  accept=".zip"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) void upload(f);
                    e.target.value = '';
                  }}
                />
              </div>

              <div className="mt-3 flex items-center gap-2 text-xs">
                <span className="text-gray-500">品类</span>
                <select
                  value={categoryCode}
                  onChange={(e) => setCategoryCode(e.target.value)}
                  className="rounded border border-gray-300 bg-white px-2 py-1 text-xs"
                >
                  <option value="BLADE_SHOES">BLADE_SHOES（刀锋鞋）</option>
                  <option value="UNKNOWN">UNKNOWN</option>
                </select>
                <span className="text-gray-400">品类从 SKU / ASIN / 导入指定确定，不靠图片判断</span>
              </div>
            </div>
          ) : tab === 'records' ? (
            /* ---------- 导入记录 ---------- */
            <div className="space-y-2">
              {batches.length === 0 ? (
                <div className="py-10 text-center text-[13px] text-gray-400">还没有导入记录，先「上传订单」</div>
              ) : (
                batches.map((b) => (
                  <button
                    key={b.id}
                    onClick={() => void openBatch(b)}
                    className="flex w-full items-center gap-3 rounded-md border border-gray-100 bg-white px-3 py-2 text-left hover:border-[#3d3192]/40"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-medium text-gray-800">{b.originalFilename}</div>
                      <div className="mt-0.5 text-[11px] text-gray-400">
                        {b.createdAt.replace('T', ' ').slice(0, 19)} · JSON {b.totalJsonCount} · 订单 {b.totalItemCount}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 text-[11px] text-gray-400">
                      <span className="rounded bg-gray-50 px-1.5 py-0.5">{b.categoryCode}</span>
                      <span className={`rounded px-1.5 py-0.5 ${STATUS_TONE[b.status] ?? 'bg-gray-100 text-gray-600'}`}>
                        {b.status}
                      </span>
                      {b.duplicateOfBatchId && <span className="text-[10px] text-gray-300">重复</span>}
                    </div>
                  </button>
                ))
              )}
            </div>
          ) : tab === 'match' ? (
            /* ---------- 素材匹配 ---------- */
            <div className="space-y-3">
              <div className="rounded-md border border-gray-100 bg-white p-3">
                <div className="mb-2 text-[13px] font-medium text-gray-800">按批次自动匹配</div>
                <div className="space-y-1.5">
                  {batches.map((b) => (
                    <div key={b.id} className="flex items-center gap-3 text-xs">
                      <span className="min-w-0 flex-1 truncate text-gray-600">{b.originalFilename}</span>
                      <span className="text-gray-400">{b.totalItemCount} 单</span>
                      <button
                        onClick={async () => {
                          try {
                            const r = await materialApi.autoMatchBatch(b.id);
                            toast.success(`自动匹配完成：确认 ${r.confirmed} / 待审核 ${r.reviewRequired} / 失败 ${r.failed}`);
                          } catch (error) {
                            toast.error(error instanceof Error ? error.message : '匹配失败');
                          }
                        }}
                        className="rounded border border-[#3d3192]/30 px-2 py-0.5 text-[#3d3192] hover:bg-[#f0eef9]"
                      >
                        自动匹配
                      </button>
                    </div>
                  ))}
                  {batches.length === 0 && <div className="py-6 text-center text-[13px] text-gray-400">先上传订单再匹配</div>}
                </div>
              </div>
              <div className="rounded-md border border-gray-100 bg-white p-3 text-xs text-gray-500">
                <b className="text-gray-700">匹配规则</b>：MATERIAL_SOURCE 优先走 URL 绑定（已绑定直接命中）；未绑定走
                Child ASIN 候选内图片匹配。FINAL_EFFECT 走 ASIN 候选内效果图匹配。所有匹配强制品类隔离
                （Order.category_code = Candidate.category_code），失败进入异常审核。
              </div>
            </div>
          ) : tab === 'review' ? (
            /* ---------- 异常审核 ---------- */
            <ReviewList />
          ) : (
            /* ---------- 销量归因 ---------- */
            <SalesView />
          )}
        </main>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 异常审核 */

const MATCH_LABEL: Record<string, string> = {
  CONFIRMED: '已确认',
  REVIEW_REQUIRED: '待审核',
  UNMATCHED: '未匹配',
  FAILED: '无法识别',
};
const MATCH_BADGE: Record<string, string> = {
  CONFIRMED: 'bg-emerald-50 text-emerald-600',
  REVIEW_REQUIRED: 'bg-amber-50 text-amber-600',
  UNMATCHED: 'bg-gray-100 text-gray-500',
  FAILED: 'bg-red-50 text-red-600',
};

function ReviewList() {
  const [items, setItems] = useState<OrderItemDto[]>([]);
  const [scope, setScope] = useState('all');
  const [variantOptions, setVariantOptions] = useState<Record<string, { id: string; label: string }[]>>({});
  const [contracts, setContracts] = useState<Record<string, MaterialVariantContract | null>>({});
  const [pendingVariant, setPendingVariant] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (s = scope) => {
    try {
      const list = await materialApi.matchReview({ scope: s });
      setItems(list);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '加载异常审核列表失败');
    }
  }, [scope]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await materialApi.matchReview({ scope });
        const contractRows = await Promise.all(list.map(async (item) => {
          if (!item.childAsin) return [item.id, null, [] as { id: string; label: string }[]] as const;
          try {
            const contract = await materialApi.materialPlatformVariants(item.childAsin);
            return [item.id, contract, contract.variants
              .filter((v) => v.categoryCode === item.categoryCode)
              .map((v) => ({ id: v.variantId, label: `${contract.batch?.batchCode ?? 'Batch'} / ${v.displayCode ?? v.variantId} / ${v.materialId ?? 'MAT—' }` }))] as const;
          } catch {
            return [item.id, null, [] as { id: string; label: string }[]] as const;
          }
        }));
        if (cancelled) return;
        setItems(list);
        setVariantOptions(Object.fromEntries(contractRows.map(([id, _contract, options]) => [id, options])));
        setContracts(Object.fromEntries(contractRows.map(([id, contract]) => [id, contract])));
      } catch (error) {
        if (!cancelled) toast.error(error instanceof Error ? error.message : '加载异常审核列表失败');
      }
    })();
    return () => { cancelled = true; };
  }, [scope]);

  const act = async (item: OrderItemDto, action: 'confirm' | 'change' | 'unmatch') => {
    const variantId = pendingVariant[item.id];
    if (action !== 'unmatch' && !variantId) {
      toast.error('请先选择要确认的 Variant');
      return;
    }
    setBusy(true);
    try {
      await materialApi.reviewMatch(item.id, { action, variantId });
      toast.success(action === 'unmatch' ? '已标记无法识别' : '已确认素材');
      void load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '审核操作失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-gray-500">筛选</span>
        {(['all', 'review', 'unmatched', 'failed'] as const).map((s) => (
          <button
            key={s}
            onClick={() => setScope(s)}
            className={`rounded px-2 py-0.5 ${scope === s ? 'bg-[#3d3192] text-white' : 'bg-white text-gray-600'}`}
          >
            {{ all: '全部异常', review: '待审核', unmatched: '未匹配', failed: '无法识别' }[s]}
          </button>
        ))}
      </div>

      {items.length === 0 ? (
        <div className="py-10 text-center text-[13px] text-gray-400">没有需要审核的订单</div>
      ) : (
        items.map((item) => {
          const p = item.normalized ?? {};
          return (
            <div key={item.id} className="rounded-md border border-gray-100 bg-white p-3">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="font-mono text-[11px] text-gray-800">{item.orderId}</span>
                <span className="font-mono text-[10px] text-gray-400">ASIN {item.childAsin ?? '—'}</span>
                <span className={`rounded px-1.5 py-0.5 ${MATCH_BADGE[item.matchStatus] ?? 'bg-gray-100 text-gray-500'}`}>
                  {MATCH_LABEL[item.matchStatus] ?? item.matchStatus}
                </span>
                <span className="text-gray-400">
                  类型：{String(p.imageType ?? '—')} · 定制：{[p.customName, p.customNumber].filter(Boolean).join('/') || '—'}
                </span>
              </div>
              <div className="mt-1 truncate font-mono text-[10px] text-gray-400" title={String(p.materialUrl ?? p.finalEffectUrl ?? '')}>
                图：{String(p.materialUrl ?? p.finalEffectUrl ?? '—').slice(0, 90)}
              </div>
              {Boolean(p.hasBuyerLogo) && (
                <div className="mt-1 text-[10px] text-blue-600">
                  买家 Logo：原图 {Array.isArray(p.buyerLogoOriginal) ? p.buyerLogoOriginal.length : 0} 个
                  · SVG {Array.isArray(p.buyerLogoSvg) ? p.buyerLogoSvg.length : 0} 个
                  · 原图为主附件
                </div>
              )}
              {item.matchMethod && (
                <div className="mt-0.5 text-[10px] text-gray-400">
                  判定依据：{item.matchMethod}（best {item.matchScore ?? '—'} · second {item.secondMatchScore ?? '—'} · gap {item.scoreGap ?? '—'}）
                </div>
              )}
              {item.matchReason && <div className="mt-0.5 text-[10px] text-amber-600">原因：{item.matchReason}</div>}
              {contracts[item.id]?.batch && (
                <div className="mt-1 text-[10px] text-gray-500">
                  Batch：{contracts[item.id]?.batch?.batchCode ?? contracts[item.id]?.batch?.batchId} · 候选 {contracts[item.id]?.variants.length ?? 0} 个 · 品类 {contracts[item.id]?.categoryCode ?? '—'}
                </div>
              )}
              {contracts[item.id] && (contracts[item.id]?.variants ?? []).slice(0, 6).map((v) => (
                <div key={v.variantId} className="mt-0.5 truncate text-[10px] text-gray-400" title={(v.images ?? []).map((image) => image.uri ?? image.assetId ?? '').join(' · ')}>
                  候选 {v.displayCode ?? v.variantId} · MAT {v.materialId ?? '—'} · 图片 {(v.images ?? []).map((image) => `${image.imageRole}${image.soleColor ? `/${image.soleColor}` : ''}`).join(', ') || '—'}
                </div>
              ))}
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                <select
                  value={pendingVariant[item.id] ?? ''}
                  onChange={(e) => setPendingVariant((prev) => ({ ...prev, [item.id]: e.target.value }))}
                  className="max-w-[280px] rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs"
                >
                  <option value="">选择 Variant…</option>
                  {(variantOptions[item.id] ?? []).map((v) => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </select>
                <button
                  disabled={busy}
                  onClick={() => act(item, 'confirm')}
                  className="rounded bg-emerald-600 px-2 py-0.5 text-white hover:bg-emerald-700 disabled:opacity-50"
                >
                  确认
                </button>
                <button
                  disabled={busy}
                  onClick={() => act(item, 'change')}
                  className="rounded border border-[#3d3192]/30 px-2 py-0.5 text-[#3d3192] hover:bg-[#f0eef9] disabled:opacity-50"
                >
                  更换 Variant
                </button>
                <button
                  disabled={busy}
                  onClick={() => act(item, 'unmatch')}
                  className="rounded border border-red-200 px-2 py-0.5 text-red-500 hover:bg-red-50 disabled:opacity-50"
                >
                  无法识别
                </button>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- 销量归因 */

function SalesView() {
  const [sales, setSales] = useState<{
    variantSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    materialSales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    categorySales: Record<string, { quantity: number; orders: number; categoryCode: string }>
    totalQuantity: number
    totalOrders: number
  } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const s = await materialApi.orderSales();
        if (!cancelled) setSales(s);
      } catch (error) {
        if (!cancelled) toast.error(error instanceof Error ? error.message : '加载销量失败');
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const rows = (map: Record<string, { quantity: number; orders: number; categoryCode: string }>) =>
    Object.entries(map).map(([id, v]) => ({ id, ...v })).sort((a, b) => b.quantity - a.quantity);

  return (
    <div className="space-y-3">
      <div className="rounded-md border border-gray-100 bg-white p-3">
        <div className="text-[13px] font-medium text-gray-800">
          正式销量（仅 CONFIRMED 计入）
          <span className="ml-3 text-xs font-normal text-gray-500">
            合计 {sales?.totalQuantity ?? 0} 件 / {sales?.totalOrders ?? 0} 单
          </span>
        </div>
        <p className="mt-1 text-[11px] text-gray-400">
          销量最小单位 = OrderItem.quantity；REVIEW_REQUIRED / UNMATCHED / FAILED 不计入。
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <SalesCard title="Variant 销量" rows={rows(sales?.variantSales ?? {})} />
        <SalesCard title="MAT 销量" rows={rows(sales?.materialSales ?? {})} />
        <SalesCard title="品类销量" rows={rows(sales?.categorySales ?? {})} />
      </div>
    </div>
  );
}

function SalesCard({ title, rows }: { title: string; rows: { id: string; quantity: number; orders: number; categoryCode: string }[] }) {
  return (
    <div className="rounded-md border border-gray-100 bg-white p-3">
      <div className="mb-2 text-[13px] font-medium text-gray-800">{title}</div>
      {rows.length === 0 ? (
        <div className="py-6 text-center text-xs text-gray-400">暂无已确认销量</div>
      ) : (
        <div className="space-y-1">
          {rows.slice(0, 12).map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-gray-600">{r.id}</span>
              <span className="text-gray-400">{r.categoryCode}</span>
              <span className="font-medium text-gray-800">{r.quantity} 件</span>
              <span className="text-[10px] text-gray-300">{r.orders} 单</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
