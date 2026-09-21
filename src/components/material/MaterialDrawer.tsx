import { useEffect, useState } from 'react';
import { ChevronLeft, ChevronRight, ExternalLink, ScanSearch, X } from 'lucide-react';
import type { Asin, Material, MaterialDesignRef } from '@/types/material';
import {
  getDesignsOfMaterial,
  getMaterialHistoryFromApi,
  getVariantDetailFromApi,
  getVariantHistoryFromApi,
  getVariantsOfMaterial,
  patchMaterialTagsFromApi,
  patchVariantTagsFromApi,
  reloadAllPackagesFromApi,
  useWorkflowState,
} from '@/store/workflowStore';
import { toast } from 'sonner';
import { resolveMediaUrl } from '@/services/apiClient';
import { MaterialAsinList } from './MaterialAsinList';
import { MaterialBasicInfo } from './MaterialBasicInfo';
import { MaterialDesignList } from './MaterialDesignList';
import { TagPicker } from './TagPicker';

const TABS = ['基本信息', '关联设计', '关联ASIN', '数据表现', '副素材', '流转记录'] as const;
type TabKey = (typeof TABS)[number];

interface Props {
  material: Material | null;
  onClose: () => void;
  /** 点进来就打开这个副素材的详情（素材中心点副素材卡片时用） */
  initialVariantId?: string;
  /** 详情里的「找相似」：用当前素材图片 Asset 直接搜索 */
  onFindSimilar?: (assetId: string) => void;
}

/**
 * 数据表现：订单/销量属于 Phase 3（派发 + ASIN 归因），本阶段没有任何真实数据源。
 * 这里明确说明「暂未开放」，**不再显示按素材编号编出来的假曲线**。
 */
function PerformanceTab() {
  return (
    <div className="px-5 py-16 text-center text-[13px] text-gray-400">
      数据表现（订单 / 销量 / 转化）属 Phase 3，本阶段暂未开放
      <div className="mt-1 text-[11px] text-gray-400">
        需要先完成运营派发与 ASIN 回填，系统才会产生可统计的业务数据
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 流转记录（素材中心统一叫法）

interface HistoryEntry {
  id: string;
  action: string;
  actor: string;
  summary: string;
  before?: string | null;
  after?: string | null;
  createdAt: string;
}

/** 流转记录 Tab：直接用现有 ActivityLog（前端叫「流转记录」，后台仍叫 ActivityLog） */
function HistoryTab({ entries, emptyHint }: { entries: HistoryEntry[]; emptyHint: string }) {
  if (!entries.length) {
    return <div className="py-16 text-center text-[13px] text-gray-400">{emptyHint}</div>;
  }
  return (
    <div className="px-5 py-4">
      <p className="mb-3 text-[11px] text-gray-400">
        流转记录来自统一的维护记录（ActivityLog），按时间顺序排列。
      </p>
      <div className="space-y-2">
        {entries.map((log) => (
          <div key={log.id} className="rounded-md border border-gray-100 bg-white px-3 py-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="rounded bg-[#f0eef9] px-1.5 py-0.5 font-medium text-[#3d3192]">
                {log.action}
              </span>
              <span className="text-gray-400">{log.createdAt.replace('T', ' ').slice(0, 16)}</span>
              <span className="ml-auto text-gray-500">{log.actor}</span>
            </div>
            <div className="mt-1 text-gray-700">{log.summary}</div>
            {(log.before || log.after) && (
              <div className="mt-1 rounded bg-gray-50 px-2 py-1 font-mono text-[11px] text-gray-500">
                {log.before ? `修改前：${log.before}` : ''}
                {log.before && log.after ? ' → ' : ''}
                {log.after ? `修改后：${log.after}` : ''}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 副素材 Tab（点击进详情）

function VariantTab({ material: m, onOpenVariant }: { material: Material; onOpenVariant: (variantId: string) => void }) {
  // 点开的是副素材卡片时，列表仍然给「它所属主素材」的那一整套副素材
  const lookupCode = m.kind === 'VARIANT' ? m.mainMaterialCode ?? '' : m.id;
  const variants = lookupCode ? getVariantsOfMaterial(lookupCode) : [];

  if (!variants.length) {
    return (
      <div className="py-16 text-center text-[13px] text-gray-400">
        该主素材暂无副素材（副素材在提交设计包后生成，命名如 1-1 / 1-2 / 1-3）
      </div>
    );
  }

  return (
    <div className="px-5 py-4">
      <p className="mb-3 text-[11px] text-gray-400">
        副素材 code 规则为「主素材位置-上架版本」，例如 1-2 = 主素材1在第2套上架版本中的副素材。
        点击任意副素材进入详情（可再返回主素材）。
      </p>
      <div className="grid grid-cols-3 gap-2.5">
        {variants.map((row) => {
          const variant = row.variant;
          return (
            <button
              key={variant.id}
              onClick={() => onOpenVariant(variant.id)}
              className="group text-left"
            >
              <div className="aspect-square overflow-hidden rounded border border-gray-100 bg-gray-50">
                <img
                  src={row.imageUri}
                  alt={variant.displayCode}
                  loading="lazy"
                  className="h-full w-full object-cover transition-transform group-hover:scale-105"
                />
              </div>
              <div className="mt-1 flex items-center justify-between text-xs text-gray-700">
                <span className="font-medium">{variant.displayCode}</span>
                <span className="text-[10px] text-gray-400">
                  {row.versionCode}
                  {row.reusedByBatchCode ? ` · ${row.reusedByBatchCode} 复用` : ''}
                </span>
              </div>
              <div className="truncate text-[10px] text-gray-400">
                {row.designPackageName}
                {row.currentRevision > 1 ? ` · Revision ${row.currentRevision}` : ''}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 副素材详情（抽屉内的一个视图）

interface VariantDetailState {
  variantId: string;
  detail: import('@/services/apiClient').VariantDetailDto | null;
  history: HistoryEntry[];
  loading: boolean;
}

const VARIANT_TABS = ['基本信息', '关联ASIN', '流转记录'] as const;
type VariantTabKey = (typeof VARIANT_TABS)[number];

/**
 * 副素材详情。**与主素材详情同款界面结构**：
 * 顶部大图 + 标题 + 计数条 + Tabs（基本信息 / 关联ASIN / 流转记录）。
 * 点击「所属主素材」可回到所属 MAT 详情（返回主素材）。
 */
function VariantDetailView({
  variantId,
  onBack,
  onOpenMaterial,
  onFindSimilar,
}: {
  variantId: string;
  onBack: () => void;
  onOpenMaterial: (materialCode: string) => void;
  onFindSimilar?: (assetId: string) => void;
}) {
  const [state, setState] = useState<VariantDetailState>({
    variantId,
    detail: null,
    history: [],
    loading: true,
  });
  // Tab 按 variantId 派生：换一个副素材时自动回到「基本信息」，不在 effect 里 setState
  const [tabNav, setTabNav] = useState<{ variantId: string; tab: VariantTabKey }>({
    variantId: '',
    tab: '基本信息',
  });
  const tab = tabNav.variantId === variantId ? tabNav.tab : '基本信息';
  const setTab = (next: VariantTabKey) => setTabNav({ variantId, tab: next });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const [detail, history] = await Promise.all([
        getVariantDetailFromApi(variantId),
        getVariantHistoryFromApi(variantId),
      ]);
      if (cancelled) return;
      setState({ variantId, detail, history: history as HistoryEntry[], loading: false });
    })();
    return () => {
      cancelled = true;
    };
  }, [variantId]);

  if (state.loading) {
    return (
      <div className="px-5 py-16 text-center text-[13px] text-gray-400">正在加载副素材详情…</div>
    );
  }

  const detail = state.detail;
  if (!detail) {
    return (
      <div className="px-5 py-16 text-center text-[13px] text-gray-400">
        副素材不存在或已删除
        <div className="mt-3">
          <button onClick={onBack} className="text-[#3d3192] hover:underline">
            返回
          </button>
        </div>
      </div>
    );
  }

  // previewUrl 是相对路径（/api/assets/...），必须转成绝对地址才能从浏览器加载
  const previewSrc = resolveMediaUrl(detail.previewUrl);

  /** 基本信息 Tab：与主素材一致的行式字段 + 标签编辑 */
  const renderBasic = () => {
    const rows: [string, React.ReactNode][] = [
      ['副素材编号', detail.displayCode],
      ['所属主素材', (
        <button onClick={() => onOpenMaterial(detail.materialCode)} className="font-medium text-[#3d3192] hover:underline">
          {detail.materialCode} · {detail.materialName}
        </button>
      )],
      ['所属设计包', detail.designPackageName ?? '—'],
      ['版本 / 批次', `${detail.batchCode ?? '—'}${detail.versionNo ? `（V${detail.versionNo}）` : ''}`],
      ['Revision', detail.currentRevisionNo ?? '—'],
      ['负责人', detail.responsibleName ?? '—'],
      ['创建时间', detail.createdAt.replace('T', ' ').slice(0, 16)],
      ['文件名', detail.originalFilename ?? '—'],
    ];
    return (
      <div className="px-5 py-4">
        <dl>
          {rows.map(([k, v]) => (
            <div key={k} className="flex border-b border-gray-50 py-2 text-[13px] last:border-0">
              <dt className="w-20 shrink-0 text-gray-400">{k}</dt>
              <dd className="flex-1 text-gray-700">{v}</dd>
            </div>
          ))}
        </dl>
        <a
          href={previewSrc || '#'}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1 text-xs text-[#3d3192] hover:underline"
        >
          新窗口查看原图 <ExternalLink className="h-3 w-3" />
        </a>

        {/* 标签编辑（单个副素材） */}
        <div className="mt-4 border-t border-gray-100 pt-3">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[13px] font-medium text-gray-600">标签</span>
            <span className="text-[11px] text-gray-400">点标签旁的 × 删除，输入后回车新增</span>
          </div>
          <TagPicker
            value={detail.tags ?? []}
            onChange={(tags) => {
              void patchVariantTagsFromApi(detail.id, tags, '素材中心')
                .then(() => reloadAllPackagesFromApi())
                .then(() => setState((s) => ({ ...s, detail: s.detail ? { ...s.detail, tags } : s.detail })))
                .then(() => toast.success(`副素材 ${detail.displayCode} 的标签已更新`))
                .catch(() => toast.error('副素材标签保存失败'));
            }}
            placeholder="输入标签后回车"
            suggestions={[]}
          />
        </div>
      </div>
    );
  };

  return (
    <div>
      {/* 顶部大图 + 标题 + 计数条（与主素材详情同款） */}
      <div className="px-5 pt-4">
        <button
          onClick={onBack}
          className="mb-3 flex items-center gap-1 text-xs text-[#3d3192] hover:underline"
        >
          <ChevronLeft className="h-3.5 w-3.5" />
          返回主素材 {detail.materialCode}
        </button>
        <div className="aspect-[16/10] w-full overflow-hidden rounded-md border border-gray-100 bg-gray-50">
          <img src={previewSrc} alt={detail.displayCode} className="h-full w-full object-contain" />
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-base font-semibold text-gray-900">副素材 {detail.displayCode}</span>
          <span className="text-xs text-gray-400">{detail.id}</span>
          {onFindSimilar && detail.assetId && (
            <button
              onClick={() => onFindSimilar(detail.assetId!)}
              className="ml-auto flex items-center gap-1 rounded border border-[#3d3192]/30 px-2 py-0.5 text-[11px] text-[#3d3192] hover:bg-[#f0eef9]"
            >
              <ScanSearch className="h-3 w-3" />
              找相似
            </button>
          )}
        </div>
        <div className="mt-3 grid grid-cols-4 divide-x divide-gray-100 rounded-md border border-gray-100">
          {[
            { label: '所属主素材', value: detail.materialCode },
            { label: '所属设计包', value: detail.designPackageName ?? '—' },
            { label: '版本', value: detail.batchCode ?? '—' },
            { label: 'Revision', value: detail.currentRevisionNo ?? '—' },
          ].map((s) => (
            <div key={s.label} className="px-1 py-2.5 text-center">
              <div className="truncate text-base font-semibold text-gray-800" title={String(s.value)}>{s.value}</div>
              <div className="text-[11px] text-gray-400">{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Tabs（与主素材详情同款 sticky） */}
      <div className="sticky top-0 z-10 mt-3 flex items-center gap-5 border-b border-gray-100 bg-white px-5">
        {VARIANT_TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`relative py-2.5 text-[13px] ${
              tab === t ? 'font-medium text-[#3d3192]' : 'text-gray-500 hover:text-gray-800'
            }`}
          >
            {t}
            {t === '流转记录' && <span className="ml-0.5 text-[11px] text-gray-300">{state.history.length}</span>}
            {tab === t && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-[#3d3192]" />}
          </button>
        ))}
      </div>

      {tab === '基本信息' && renderBasic()}
      {tab === '关联ASIN' && <MaterialAsinList asins={[]} onViewAsin={() => {}} />}
      {tab === '流转记录' && (
        <HistoryTab entries={state.history} emptyHint="这个副素材还没有任何流转记录" />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- 主抽屉

export function MaterialDrawer({ material, onClose, initialVariantId, onFindSimilar }: Props) {
  const materialId = material?.id ?? '';
  /**
   * 导航态（Tab + 面包屑）**按素材 id 派生**：
   * 换一个素材时自动回到「基本信息」，不需要在 effect 里 setState 重置。
   */
  const [nav, setNav] = useState<{
    materialId: string;
    tab: TabKey;
    design: MaterialDesignRef | null;
    asin: Asin | null;
  }>({ materialId: '', tab: '基本信息', design: null, asin: null });
  const current =
    nav.materialId === materialId
      ? nav
      : { materialId, tab: '基本信息' as TabKey, design: null, asin: null };
  const { tab, design: crumbDesign, asin: crumbAsin } = current;
  const setTab = (next: TabKey) => setNav({ ...current, tab: next });
  const setCrumbDesign = (next: MaterialDesignRef | null) => setNav({ ...current, design: next });
  const setCrumbAsin = (next: Asin | null) => setNav({ ...current, asin: next });

  // 抽屉内可以下钻到副素材详情（ variantId 非空时显示副详情，不回到素材列表 ）。
  // 用「按素材 id 派生」而不是 useState(initialVariantId)：
  // 抽屉是常驻挂载的（切素材不卸载），useState 初始值只在第一次生效，
  // 之后点其它副素材/主素材会显示旧的副素材详情。这里每次 material 变化都重新判定：
  //   - 从外部点副素材进来（initialVariantId）→ 直接显示它
  //   - 抽屉内手动下钻（detailNav.variantId）→ 显示它（返回主素材时清空）
  //   - 其它情况 → 显示主素材视图
  const [detailNav, setDetailNav] = useState<{ materialId: string; variantId: string | null }>({
    materialId: '',
    variantId: null,
  });
  const detailVariantId =
    detailNav.materialId === materialId
      ? detailNav.variantId
      : (initialVariantId ?? null);
  const setDetailVariantId = (variantId: string | null) =>
    setDetailNav({ materialId, variantId });

  // 订阅 store，保证上传/补充素材后副素材 Tab 立即刷新
  useWorkflowState();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!material) return null;
  const m = material;
  /**
   * 关联查询用的主素材编码。
   * 点开「副素材」卡片时 m.id 是 variant-xxx，本身不是主素材，
   * 但它的 mainMaterialCode 指向所属主素材 —— 副素材也要能看到自己属于哪个设计。
   */
  const lookupCode = m.kind === 'VARIANT' ? (m.mainMaterialCode ?? '') : m.id;
  // 真实关联：按设计编码聚合（不再是 Mock 设计 / 位置计数）
  const designs = lookupCode ? getDesignsOfMaterial(lookupCode) : [];
  const variants = lookupCode ? getVariantsOfMaterial(lookupCode) : [];
  // 副素材卡片自己也算一个副素材，指向的主素材那套副素材才是列表内容
  const variantCount = variants.length;
  const packageCount = new Set(
    designs.flatMap((design) => design.packages.map((p) => p.packageId)),
  ).size;
  // 关联 ASIN 属 Phase 3：没有真实数据就不显示数字
  const asins: Asin[] = [];

  // 副素材详情视图：点了副素材后整个抽屉切过去
  if (detailVariantId) {
    return (
      <div className="fixed inset-0 z-50">
        <div className="absolute inset-0 bg-black/25" onClick={onClose} />
        <div className="absolute inset-y-0 right-0 flex w-[480px] max-w-[92vw] flex-col bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-gray-100 px-5 py-2.5">
            <div className="flex min-w-0 items-center gap-1 text-xs text-gray-400">
              <span className="shrink-0">素材</span>
              <ChevronRight className="h-3 w-3 shrink-0" />
              <button
                className="shrink-0 text-[#3d3192] hover:underline"
                onClick={() => setDetailVariantId(null)}
              >
                {lookupCode || m.id}
              </button>
              <ChevronRight className="h-3 w-3 shrink-0" />
              <span className="shrink-0 text-gray-500">副素材详情</span>
            </div>
            <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto">
            <VariantDetailView
              variantId={detailVariantId}
              onBack={() => setDetailVariantId(null)}
              onOpenMaterial={() => setDetailVariantId(null)}
              onFindSimilar={onFindSimilar}
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/25" onClick={onClose} />
      <div className="absolute inset-y-0 right-0 flex w-[480px] max-w-[92vw] flex-col bg-white shadow-2xl">
        {/* 面包屑 + 关闭 */}
        <div className="flex items-center justify-between border-b border-gray-100 px-5 py-2.5">
          <div className="flex min-w-0 items-center gap-1 text-xs text-gray-400">
            <span className="shrink-0">素材</span>
            <ChevronRight className="h-3 w-3 shrink-0" />
            <button
              className="shrink-0 text-[#3d3192] hover:underline"
              onClick={() => {
                setCrumbDesign(null);
                setCrumbAsin(null);
                setTab('基本信息');
              }}
            >
              {m.id}
            </button>
            {crumbDesign && (
              <>
                <ChevronRight className="h-3 w-3 shrink-0" />
                <button
                  className="shrink-0 text-[#3d3192] hover:underline"
                  onClick={() => {
                    setCrumbAsin(null);
                    setTab('副素材');
                  }}
                >
                  {crumbDesign.designCode}
                </button>
                <ChevronRight className="h-3 w-3 shrink-0" />
                <span className="shrink-0 text-gray-500">{crumbDesign.designerName || '未填写美工'}</span>
              </>
            )}
            {crumbAsin && (
              <>
                <ChevronRight className="h-3 w-3 shrink-0" />
                <span className="truncate font-mono text-gray-700">{crumbAsin.childAsin}</span>
              </>
            )}
          </div>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* 顶部大图 + 核心数据 */}
          <div className="px-5 pt-4">
            <div className={`mx-auto aspect-[16/10] w-full overflow-hidden rounded-md border border-gray-100 ${m.transparent ? 'checker-bg' : 'bg-gray-50'}`}>
              <img src={m.image} alt={m.name} className="h-full w-full object-contain" />
            </div>
            <div className="mt-3 flex items-baseline gap-2">
              <span className="text-base font-semibold text-gray-900">{m.name}</span>
              <span className="text-xs text-gray-400">{m.id}</span>
              {m.risk && <span className="rounded bg-red-50 px-1.5 py-0.5 text-[11px] text-red-500">风险素材</span>}
              {onFindSimilar && m.assetId && (
                <button
                  onClick={() => onFindSimilar(m.assetId!)}
                  className="ml-auto flex items-center gap-1 rounded border border-[#3d3192]/30 px-2 py-0.5 text-[11px] text-[#3d3192] hover:bg-[#f0eef9]"
                >
                  <ScanSearch className="h-3 w-3" />
                  找相似
                </button>
              )}
            </div>
            <div className="mt-3 grid grid-cols-4 divide-x divide-gray-100 rounded-md border border-gray-100">
              {[
                { label: '关联设计', value: designs.length },
                { label: '设计包', value: packageCount },
                { label: '副素材', value: variantCount },
                { label: '关联 ASIN', value: '—' },
              ].map((s) => (
                <div key={s.label} className="py-2.5 text-center">
                  <div className="text-base font-semibold text-gray-800">{s.value}</div>
                  <div className="text-[11px] text-gray-400">{s.label}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Tabs */}
          <div className="sticky top-0 z-10 mt-3 flex items-center gap-5 border-b border-gray-100 bg-white px-5">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`relative py-2.5 text-[13px] ${
                  tab === t ? 'font-medium text-[#3d3192]' : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {t}
                {t === '关联设计' && <span className="ml-0.5 text-[11px] text-gray-300">{designs.length}</span>}
                {t === '关联ASIN' && <span className="ml-0.5 text-[11px] text-gray-300">{asins.length}</span>}
                {t === '副素材' && <span className="ml-0.5 text-[11px] text-gray-300">{variantCount}</span>}
                {tab === t && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-[#3d3192]" />}
              </button>
            ))}
          </div>

          {tab === '基本信息' && (
            <MaterialBasicInfo
              material={m}
              responsibleName={m.responsibleName}
              currentStatus={
                m.kind === 'VARIANT'
                  ? `已生成 ${m.versionCode ?? 'V?'}（Revision ${m.currentRevision ?? 1}）`
                  : variants.length
                    ? `已生成 ${variants[variants.length - 1]?.versionCode ?? 'V1'}（共 ${variantCount} 个副素材）`
                    : '已入库（还没有副素材）'
              }
              onTagsChange={(tags) => {
                // 单个素材改标签（主素材按 materialCode，副素材按 variant id）
                if (m.kind === 'VARIANT') {
                  void patchVariantTagsFromApi(m.id, tags, '素材中心')
                    .then(() => reloadAllPackagesFromApi())
                    .then(() => toast.success(`副素材 ${m.id} 的标签已更新`))
                    .catch(() => toast.error('副素材标签保存失败'));
                } else {
                  void patchMaterialTagsFromApi(m.id, tags, '素材中心')
                    .then(() => reloadAllPackagesFromApi())
                    .then(() => toast.success(`主素材 ${m.id} 的标签已更新`))
                    .catch(() => toast.error('主素材标签保存失败'));
                }
              }}
            />
          )}
          {tab === '关联设计' && (
            <MaterialDesignList
              designs={designs}
              onViewDesign={(d) => {
                setCrumbDesign(d);
                setCrumbAsin(null);
                setTab('副素材');
              }}
            />
          )}
          {tab === '关联ASIN' && (
            <MaterialAsinList
              asins={crumbDesign ? asins.filter((a) => a.designCode === crumbDesign.designCode) : asins}
              onViewAsin={(a) => {
                const d = designs.find((x) => x.designCode === a.designCode) ?? null;
                if (d) setCrumbDesign(d);
                setCrumbAsin(a);
              }}
            />
          )}
          {tab === '数据表现' && <PerformanceTab />}
          {tab === '副素材' && (
            <VariantTab
              material={m}
              onOpenVariant={(variantId) => setDetailVariantId(variantId)}
            />
          )}
          {tab === '流转记录' && (
            <MaterialHistoryView materialId={lookupCode || m.id} />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 流转记录（MAT 视角）

function MaterialHistoryView({ materialId }: { materialId: string }) {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const rows = await getMaterialHistoryFromApi(materialId);
      if (cancelled) return;
      setEntries(rows as HistoryEntry[]);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [materialId]);

  if (loading) {
    return <div className="px-5 py-16 text-center text-[13px] text-gray-400">正在加载流转记录…</div>;
  }
  return (
    <HistoryTab
      entries={entries}
      emptyHint="这个主素材还没有任何流转记录（上传、配对、生成版本、修改负责人/标签都会记在这里）"
    />
  );
}
