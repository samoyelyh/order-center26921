import { useCallback, useEffect, useMemo, useState } from 'react';
import { FileUp, PackagePlus, X } from 'lucide-react';
import { useNavigate } from 'react-router';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { MaterialCategoryFilter } from '@/components/material/MaterialCategoryFilter';
import { MaterialDrawer } from '@/components/material/MaterialDrawer';
import { MaterialFilterPanel, StyleFilterRow, type FilterValues } from '@/components/material/MaterialFilterPanel';
import { MaterialGrid } from '@/components/material/MaterialGrid';
import { MaterialPackageGroups, type ArchivedPackageView } from '@/components/material/MaterialPackageGroups';
import { MaterialQuickTabs, type MaterialKindFilter } from '@/components/material/MaterialQuickTabs';
import { MaterialSidebar, type CategorySelection } from '@/components/material/MaterialSidebar';
import { ImageSearchPanel } from '@/components/material/ImageSearchPanel';
import { CATEGORY_TREE } from '@/mock/materials';
import {
  batchTagsFromApi,
  deletePackageFromApi,
  getDesignPackageGroups,
  getDesignsOfMaterial,
  getMaterials,
  getVariantMaterials,
  listArchivedPackagesFromApi,
  loadAllPackagesFromApi,
  pruneArchivedPackagesFromStore,
  restorePackageFromApi,
  reloadAllPackagesFromApi,
  useWorkflowState,
} from '@/store/workflowStore';
import { API_ENABLED, materialApi, resolveMediaUrl, type ImageSearchResultDto } from '@/services/apiClient';
import type { Material } from '@/types/material';

const DEFAULT_FILTERS: FilterValues = {
  type: '全部',
  style: '全部',
  uploader: '全部',
  uploadTime: 'all',
  usage: 'all',
  sort: 'latest',
};

const PAGE_SIZE = 18;

export default function MaterialCenterPage() {
  const navigate = useNavigate();
  // 订阅工作流 store：上传/补充素材后首页与抽屉立即刷新
  useWorkflowState();

  const [keyword, setKeyword] = useState('');
  const [catSel, setCatSel] = useState<CategorySelection>({ category: '', subCategory: '' });
  const [filters, setFilters] = useState<FilterValues>(DEFAULT_FILTERS);
  const [scenes, setScenes] = useState<string[]>([]);
  const [quickTab, setQuickTab] = useState('recommend');
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [recentIds, setRecentIds] = useState<string[]>([]);
  const [imageMode, setImageMode] = useState(false);
  const [imageThumb, setImageThumb] = useState<string>('');
  const [imagePanelOpen, setImagePanelOpen] = useState(false);
  // ---- 图片搜索（以图搜图，与配对完全独立）----
  const [searchResults, setSearchResults] = useState<ImageSearchResultDto[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [querySource, setQuerySource] = useState<{ file?: File; assetId?: string } | null>(null);
  const [searchScope, setSearchScope] = useState<'all' | 'main' | 'variant'>('all');
  const [searchTopK, setSearchTopK] = useState(20);
  const [searchTag, setSearchTag] = useState('');
  const [searchResponsible, setSearchResponsible] = useState('');
  const [searchDesignPackage, setSearchDesignPackage] = useState('');
  /** 标签选择器数据（全库去重标签） */
  const [allTags, setAllTags] = useState<{ tag: string; count: number }[]>([]);
  useEffect(() => {
    if (!API_ENABLED) return;
    void materialApi.listTags().then(setAllTags).catch(() => {});
  }, []);
  const [drawerMaterial, setDrawerMaterial] = useState<Material | null>(null);
  /** 打开抽屉时直接进入的副素材详情（副素材卡片 / 主素材卡片上的副素材缩略图） */
  const [drawerInitialVariant, setDrawerInitialVariant] = useState<string | undefined>(undefined);
  /** 素材中心多选：选中的素材 id（主素材用 materialCode，副素材用 variant id） */
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  /** 多选后弹出的批量标签操作 */
  const [batchTagDialog, setBatchTagDialog] = useState<null | 'add' | 'remove' | 'replace'>(null);
  const [batchTagInput, setBatchTagInput] = useState('');
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  /** 第二十五条：全部 / 主素材 / 副素材 */
  const [kind, setKind] = useState<MaterialKindFilter>('all');
  /** 第二十五条：按设计包折叠 */
  const [groupByPackage, setGroupByPackage] = useState(false);
  const [expandedPackages, setExpandedPackages] = useState<Set<string>>(new Set());
  /** 已删除（归档）的设计包：可在折叠视图里恢复 */
  const [archivedPackages, setArchivedPackages] = useState<ArchivedPackageView[]>([]);
  /**
   * 真实后端模式：进页面就把全库设计包拉进 store。
   * 之前素材中心只显示「本次会话里传过的东西」，刷新后真实素材就没了。
   */
  const [loading, setLoading] = useState(API_ENABLED);
  const [loadError, setLoadError] = useState('');

  const refreshArchived = useCallback(async () => {
    try {
      const list = await listArchivedPackagesFromApi();
      setArchivedPackages(
        list.map((pkg) => ({
          id: pkg.id,
          name: pkg.name,
          code: pkg.code,
          designCode: pkg.designCode,
          archivedAt: pkg.archivedAt ?? null,
        })),
      );
    } catch {
      // 归档列表拿不到不影响主流程
    }
  }, []);

  useEffect(() => {
    if (!API_ENABLED) return;
    let cancelled = false;
    // 先清掉「已经被归档但仍残留在 store 里的包」，避免主页显示已删除的副素材
    void pruneArchivedPackagesFromStore();
    void loadAllPackagesFromApi()
      .then((result) => {
        if (cancelled) return;
        if (result.failed > 0) {
          setLoadError(`有 ${result.failed} 个设计包加载失败（其余 ${result.packageCount - result.failed} 个已加载）`);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : '加载设计包失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    // 已删除（归档）的设计包：在折叠视图里可以恢复
    void listArchivedPackagesFromApi()
      .then((list) => {
        if (cancelled) return;
        setArchivedPackages(
          list.map((pkg) => ({            id: pkg.id,
            name: pkg.name,
            code: pkg.code,
            designCode: pkg.designCode,
            archivedAt: pkg.archivedAt ?? null,
          })),
        );
      })
      .catch(() => {
        // 归档列表拿不到不影响主流程
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const mainMaterials = getMaterials();
  const variantMaterials = getVariantMaterials();
  const allMaterials = useMemo(
    () => (kind === 'main' ? mainMaterials : kind === 'variant' ? variantMaterials : [...mainMaterials, ...variantMaterials]),
    [kind, mainMaterials, variantMaterials],
  );

  const patchFilters = (p: Partial<FilterValues>) => {
    setFilters((f) => ({ ...f, ...p }));
    setVisibleCount(PAGE_SIZE);
  };

  const resetFilters = () => {
    setFilters(DEFAULT_FILTERS);
    setScenes([]);
    setKeyword('');
    setCatSel({ category: '', subCategory: '' });
    setQuickTab('recommend');
    setKind('all');
    setVisibleCount(PAGE_SIZE);
  };

  /** 选中「球迷」子类时，适用场景区域切换为联盟标签 */
  const fanMode = catSel.subCategory === '球迷';

  const toggleFavorite = (id: string) => {
    setFavorites((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const openDrawer = (m: Material) => {
    setDrawerMaterial(m);
    // 副素材卡片：直接进副素材详情（主素材卡片：进主素材详情）
    setDrawerInitialVariant(m.kind === 'VARIANT' ? m.id : undefined);
    setRecentIds((prev) => [m.id, ...prev.filter((x) => x !== m.id)].slice(0, 12));
  };

  /** 打开主素材卡片上的某个副素材缩略图 → 直接进副素材详情 */
  const openVariantFromCard = (variantId: string) => {
    // 用该副素材对应的主素材卡片作为「返回主素材」的上下文；无则用一个占位
    const owner = mainMaterials.find((mm) =>
      getVariantMaterials().some((vm) => vm.id === variantId && vm.mainMaterialCode === mm.id),
    );
    if (owner) {
      setDrawerMaterial(owner);
    } else {
      // 兜底：直接打开副素材详情（抽屉需要非空 material）
      const variantView = getVariantMaterials().find((vm) => vm.id === variantId);
      if (variantView) setDrawerMaterial(variantView);
      else return;
    }
    setDrawerInitialVariant(variantId);
    setRecentIds((prev) => [variantId, ...prev.filter((x) => x !== variantId)].slice(0, 12));
  };

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  /**
   * 搜索结果里 store 可能没有的素材（如「已上传但尚未生成版本」的草稿包素材）
   * 用搜索结果字段构建一个只读 MaterialView，保证点击结果总能打开详情。
   */
  const materialFromSearchResult = (r: ImageSearchResultDto): Material => ({
    id: r.kind === 'VARIANT' ? r.assetId : (r.materialCode ?? r.name),
    name: r.kind === 'VARIANT' ? `副素材 ${r.displayCode}` : (r.materialCode ?? r.name),
    image: r.previewUrl,
    transparent: false,
    category: '图案',
    subCategory: '通用',
    scenes: [],
    source: '原创',
    type: '插画',
    styles: [],
    crafts: [],
    areas: [],
    fitCategories: [],
    designCount: 0,
    asinCount: 0,
    orders30: 0,
    prevOrders30: 0,
    uploadTime: '-',
    uploader: '',
    size: '-',
    format: 'JPG',
    fileName: '',
    copyright: '公司自有',
    tags: r.tags ?? [],
    risk: false,
    kind: r.kind,
    designPackageId: r.designPackageId ?? undefined,
    designPackageName: r.designPackageName ?? undefined,
    responsibleName: r.responsibleName ?? undefined,
    mainMaterialCode: r.materialCode ?? undefined,
    assetId: r.assetId,
  });

  /** 搜索结果点击 → 打开正确素材详情 */
  const openSearchResult = (r: ImageSearchResultDto) => {
    if (r.kind === 'VARIANT') {
      // 副素材：优先用 store 里的 variant（能拿到 variant id 直接进副素材详情）
      const vv = variantMaterials.find((vm) => vm.assetId === r.assetId);
      if (vv) {
        const owner = mainMaterials.find((mm) => mm.id === vv.mainMaterialCode);
        setDrawerMaterial(owner ?? vv);
        setDrawerInitialVariant(vv.id);
      } else {
        setDrawerMaterial(materialFromSearchResult(r));
        setDrawerInitialVariant(undefined);
      }
    } else {
      const target = mainMaterials.find((mm) => mm.id === r.materialCode);
      setDrawerMaterial(target ?? materialFromSearchResult(r));
      setDrawerInitialVariant(undefined);
    }
    setRecentIds((prev) => [r.assetId, ...prev.filter((x) => x !== r.assetId)].slice(0, 12));
  };

  /** 批量标签：对当前选中的素材（主素材 + 副素材）执行 add/remove/replace */
  const runBatchTags = async (op: 'add' | 'remove' | 'replace') => {
    const tags = batchTagInput.split(/[,，]/).map((t) => t.trim()).filter(Boolean);
    if (!tags.length) {
      toast.error('请先输入要操作的标签（逗号分隔多个）');
      return;
    }
    const selected = [...selectedIds];
    const materialIds = selected.filter((id) => id.startsWith('MAT-'));
    const variantIds = selected.filter((id) => id.startsWith('variant-'));
    if (!materialIds.length && !variantIds.length) {
      toast.error('请先勾选要批量操作的素材');
      return;
    }
    try {
      const actor = '素材中心';
      let total = 0;
      if (materialIds.length) {
        const r1 = await batchTagsFromApi({ op, tags, targetType: 'MATERIAL', targetIds: materialIds, actor });
        total += r1.updated;
      }
      if (variantIds.length) {
        const r2 = await batchTagsFromApi({ op, tags, targetType: 'MATERIAL_VARIANT', targetIds: variantIds, actor });
        total += r2.updated;
      }
      toast.success(`已${op === 'add' ? '添加' : op === 'remove' ? '删除' : '替换'}标签（${total} 个素材）`);
      setBatchTagDialog(null);
      setBatchTagInput('');
      setSelectedIds(new Set());
      // 刷新列表
      void reloadAllPackagesFromApi();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '批量调整标签失败');
    }
  };

  const openMaterialByCode = (materialCode: string) => {
    const target = mainMaterials.find((m) => m.id === materialCode);
    if (target) openDrawer(target);
  };

  /** 上传查询图搜索（点击上传 / 拖拽 / Ctrl+V 粘贴都走这里） */
  const runImageSearch = useCallback(
    async (file: File) => {
      setImageThumb(URL.createObjectURL(file));
      setQuerySource({ file });
      setImageMode(true);
      setSearchLoading(true);
      setSearchError('');
      try {
        const resp = await materialApi.imageSearch({ file, scope: searchScope, topK: searchTopK });
        setSearchResults(resp.results ?? []);
      } catch (error) {
        setSearchError(error instanceof Error ? error.message : '图片搜索失败');
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    },
    [searchScope, searchTopK],
  );

  /** 用已有 Asset 向量找相似（素材卡片 / 素材详情的「找相似」） */
  const searchByAsset = useCallback(
    async (assetId: string, thumb?: string) => {
      if (thumb) setImageThumb(thumb);
      setQuerySource({ assetId });
      setImageMode(true);
      setSearchLoading(true);
      setSearchError('');
      try {
        const resp = await materialApi.imageSearch({
          assetId,
          scope: searchScope,
          topK: searchTopK,
          tags: searchTag || undefined,
          responsibleName: searchResponsible || undefined,
          designPackageId: searchDesignPackage || undefined,
        });
        setSearchResults(resp.results ?? []);
      } catch (error) {
        setSearchError(error instanceof Error ? error.message : '图片搜索失败');
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    },
    [searchScope, searchTopK, searchTag, searchResponsible, searchDesignPackage],
  );

  /** 筛选变化时用同一个查询源重新搜索（参数直接传入，避免读到旧 state 闭包） */
  const rerunImageSearch = useCallback(
    (overrides?: {
      scope?: 'all' | 'main' | 'variant'
      topK?: number
      tags?: string
      responsible?: string
      designPackageId?: string
    }) => {
      if (!querySource) return;
      const scope = overrides?.scope ?? searchScope;
      const topK = overrides?.topK ?? searchTopK;
      const tags = overrides?.tags !== undefined ? overrides.tags : searchTag;
      const responsible = overrides?.responsible !== undefined ? overrides.responsible : searchResponsible;
      const designPackageId = overrides?.designPackageId !== undefined ? overrides.designPackageId : searchDesignPackage;
      void (async () => {
        setSearchLoading(true);
        setSearchError('');
        try {
          const resp = await materialApi.imageSearch({
            ...querySource,
            scope,
            topK,
            tags: tags || undefined,
            responsibleName: responsible || undefined,
            designPackageId: designPackageId || undefined,
          });
          setSearchResults(resp.results ?? []);
        } catch (error) {
          setSearchError(error instanceof Error ? error.message : '图片搜索失败');
          setSearchResults([]);
        } finally {
          setSearchLoading(false);
        }
      })();
    },
    [querySource, searchScope, searchTopK, searchTag, searchResponsible, searchDesignPackage],
  );

  const exitImageMode = () => {
    setImageMode(false);
    setImageThumb('');
    setQuerySource(null);
    setSearchResults([]);
    setSearchError('');
    setFilters((f) => ({ ...f, sort: 'latest' }));
  };

  // 左侧分类计数：基于当前 全部/主素材/副素材 口径
  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const node of CATEGORY_TREE) {
      counts[node.name] = allMaterials.filter((m) => m.category === node.name).length;
      for (const sub of node.children) {
        counts[`${node.name}/${sub}`] = allMaterials.filter(
          (m) => m.category === node.name && m.subCategory === sub,
        ).length;
      }
    }
    return counts;
  }, [allMaterials]);

  const filtered = useMemo(() => {
    // 图片导航：搜索结果由后端返回（imageMode 时不走本地列表）
    if (imageMode) return [];
    let list = [...allMaterials];

    // 分类 / 子类
    if (catSel.category) {
      list = list.filter((m) => m.category === catSel.category);
      if (catSel.subCategory) {
        list = list.filter((m) => m.subCategory === catSel.subCategory);
      }
    }
    // 关键词：素材名 / 素材ID / 副素材 code / 设计包 / 标签
    const kw = keyword.trim().toLowerCase();
    if (kw) {
      list = list.filter((m) => {
        if (m.name.toLowerCase().includes(kw) || m.id.toLowerCase().includes(kw)) return true;
        if (m.mainMaterialCode?.toLowerCase().includes(kw)) return true;
        if (m.designPackageName?.toLowerCase().includes(kw)) return true;
        if (m.tags.some((t) => t.toLowerCase().includes(kw))) return true;
        if (getDesignsOfMaterial(m.id).some((d) => d.designCode.toLowerCase().includes(kw) || d.designerName.toLowerCase().includes(kw))) return true;
        return false;
      });
    }
    // 下拉筛选
    if (filters.type !== '全部') list = list.filter((m) => m.type === filters.type);
    if (filters.style !== '全部') list = list.filter((m) => m.styles.includes(filters.style));
    if (filters.uploader !== '全部') list = list.filter((m) => m.uploader === filters.uploader);
    if (filters.uploadTime !== 'all') {
      const days = { '7d': 7, '30d': 30, '90d': 90, '180d': 180 }[filters.uploadTime] ?? 0;
      const cutoff = new Date();
      cutoff.setDate(cutoff.getDate() - days);
      list = list.filter((m) => new Date(m.uploadTime.replace(' ', 'T')) >= cutoff);
    }
    switch (filters.usage) {
      case 'unused': list = list.filter((m) => m.designCount === 0); break;
      case 'used': list = list.filter((m) => m.designCount > 0); break;
      case 'highReuse': list = list.filter((m) => m.designCount >= 15); break;
      case 'lowReuse': list = list.filter((m) => m.designCount > 0 && m.designCount < 5); break;
      case 'hasAsin': list = list.filter((m) => m.asinCount > 0); break;
      case 'noAsin': list = list.filter((m) => m.asinCount === 0); break;
      case 'risk': list = list.filter((m) => m.risk); break;
    }
    // 适用场景 / 联盟（多选，任一命中）
    if (scenes.length > 0) {
      if (fanMode) {
        list = list.filter((m) => m.league && scenes.includes(m.league));
      } else {
        list = list.filter((m) => m.scenes.some((s) => scenes.includes(s)));
      }
    }
    // 快捷导航
    switch (quickTab) {
      case 'latest': list.sort((a, b) => b.uploadTime.localeCompare(a.uploadTime)); break;
      case 'hot': list.sort((a, b) => b.orders30 - a.orders30); break;
      case 'growth':
        list = list.filter((m) => m.orders30 > m.prevOrders30);
        list.sort((a, b) => b.orders30 - b.prevOrders30 - (a.orders30 - a.prevOrders30));
        break;
      case 'highReuse': list = list.filter((m) => m.designCount >= 15); break;
      case 'unused': list = list.filter((m) => m.designCount === 0); break;
      case 'favorite': list = list.filter((m) => favorites.has(m.id)); break;
      case 'recent': list = list.filter((m) => recentIds.includes(m.id)); break;
    }
    // 排序
    switch (filters.sort) {
      case 'earliest': list.sort((a, b) => a.uploadTime.localeCompare(b.uploadTime)); break;
      case 'usage': list.sort((a, b) => b.designCount - a.designCount); break;
      case 'asin': list.sort((a, b) => b.asinCount - a.asinCount); break;
      case 'sales30': list.sort((a, b) => b.orders30 - a.orders30); break;
      default:
        if (quickTab === 'recommend') list.sort((a, b) => b.uploadTime.localeCompare(a.uploadTime));
    }
    return list;
  }, [imageMode, allMaterials, keyword, catSel, filters, scenes, fanMode, quickTab, favorites, recentIds]);

  const visible = filtered.slice(0, visibleCount);
  /**
   * 按设计包折叠的分组。
   *
   * 注意：**不能 useMemo(..., [])** —— 那会在首次渲染（后端还没 hydrate 完）时算一次并永久缓存，
   * 于是刚上传/刚生成版本的设计包在折叠视图里根本不出现。
   * 组件已经通过 useWorkflowState() 订阅 store，每次数据变化都会重渲染，直接算即可；
   * 没开折叠时不算，省掉遍历全部设计包的开销。
   */
  const packageGroups = groupByPackage ? getDesignPackageGroups() : [];

  return (
    <div className="flex h-screen flex-col bg-[#f5f6f8]">
      {/* 顶部面包屑 */}
      <header className="flex h-10 shrink-0 items-center px-4 text-xs text-gray-400">
        <span className="font-medium text-gray-600">素材中心</span>
        <span className="mx-1.5">/</span>
        <span>
          {imageMode
            ? '图片导航'
            : keyword.trim()
              ? '搜索结果'
              : catSel.category
                ? `${catSel.category}${catSel.subCategory ? ` / ${catSel.subCategory}` : ''}`
                : '全部素材'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs" onClick={() => navigate('/orders')}><FileUp className="h-3.5 w-3.5" />订单识别</Button>
          <Button size="sm" className="h-7 bg-[#3d3192] px-2.5 text-xs hover:bg-[#32277a]" onClick={() => navigate('/materials/upload')}><PackagePlus className="h-3.5 w-3.5" />上传设计包</Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <MaterialSidebar
          keyword={keyword}
          onKeywordChange={(v) => {
            setKeyword(v);
            setVisibleCount(PAGE_SIZE);
          }}
          onOpenImageSearch={() => setImagePanelOpen(true)}
          selection={catSel}
          onSelect={(sel) => {
            setCatSel(sel);
            setScenes([]);
            setVisibleCount(PAGE_SIZE);
          }}
          counts={categoryCounts}
          totalCount={allMaterials.length}
        />

        <main className="min-w-0 flex-1 overflow-y-auto bg-white">
          <MaterialFilterPanel
            values={filters}
            onChange={patchFilters}
            onReset={resetFilters}
            imageMode={imageMode}
          />
          <MaterialCategoryFilter
            selected={scenes}
            onChange={(v) => {
              setScenes(v);
              setVisibleCount(PAGE_SIZE);
            }}
            fanMode={fanMode}
          />
          {!fanMode && (
            <StyleFilterRow
              value={filters.style}
              onChange={(v) => patchFilters({ style: v })}
            />
          )}

          {imageMode ? (
            <div className="mx-4 mb-2 mt-1 space-y-2 rounded-md border border-[#3d3192]/20 bg-[#f0eef9] px-3 py-2">
              <div className="flex flex-wrap items-center gap-3">
                {imageThumb && (
                  <img src={imageThumb} alt="搜索图片" className="h-11 w-11 rounded border border-gray-200 object-cover" />
                )}
                <div className="text-[13px] text-gray-700">
                  图片搜索：<span className="font-semibold text-[#3d3192]">{searchResults.length}</span> 个相似素材
                  <span className="ml-2 text-xs text-gray-400">按相似度降序；与主副素材配对完全独立</span>
                </div>
                <button
                  onClick={exitImageMode}
                  className="ml-auto flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs text-gray-500 hover:border-[#3d3192] hover:text-[#3d3192]"
                >
                  <X className="h-3 w-3" />
                  退出图片导航
                </button>
              </div>

              {/* 搜索筛选：类型 / TopN / 设计包 / 标签 / 负责人 */}
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="text-gray-500">类型</span>
                {(['all', 'main', 'variant'] as const).map((s) => (
                  <button
                    key={s}
                    data-search-scope={s}
                    onClick={() => {
                      setSearchScope(s);
                      void rerunImageSearch({ scope: s });
                    }}
                    className={`rounded px-2 py-0.5 ${
                      searchScope === s ? 'bg-[#3d3192] text-white' : 'bg-white text-gray-600 hover:text-[#3d3192]'
                    }`}
                  >
                    {{ all: '全部', main: '主素材', variant: '副素材' }[s]}
                  </button>
                ))}
                <span className="ml-2 text-gray-500">Top</span>
                {[20, 50, 100].map((n) => (
                  <button
                    key={n}
                    data-search-topk={n}
                    onClick={() => {
                      setSearchTopK(n);
                      void rerunImageSearch({ topK: n });
                    }}
                    className={`rounded px-2 py-0.5 ${
                      searchTopK === n ? 'bg-[#3d3192] text-white' : 'bg-white text-gray-600 hover:text-[#3d3192]'
                    }`}
                  >
                    {n}
                  </button>
                ))}
                <select
                  value={searchDesignPackage}
                  onChange={(e) => {
                    setSearchDesignPackage(e.target.value);
                    void rerunImageSearch({ designPackageId: e.target.value });
                  }}
                  className="ml-2 rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs"
                >
                  <option value="">全部设计包</option>
                  {packageGroups.map((g) => (
                    <option key={g.pkg.id} value={g.pkg.id}>{g.pkg.name}</option>
                  ))}
                </select>
                <select
                  value={searchTag}
                  onChange={(e) => {
                    setSearchTag(e.target.value);
                    void rerunImageSearch({ tags: e.target.value });
                  }}
                  className="rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs"
                >
                  <option value="">全部标签</option>
                  {allTags.map((t) => (
                    <option key={t.tag} value={t.tag}>{t.tag}（{t.count}）</option>
                  ))}
                </select>
                <select
                  value={searchResponsible}
                  onChange={(e) => {
                    setSearchResponsible(e.target.value);
                    void rerunImageSearch({ responsible: e.target.value });
                  }}
                  className="rounded border border-gray-300 bg-white px-1.5 py-0.5 text-xs"
                >
                  <option value="">全部负责人</option>
                  {Array.from(new Set(packageGroups.map((g) => g.pkg.responsibleName).filter(Boolean))).map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                </select>
              </div>
            </div>
          ) : (
            <>
              <MaterialQuickTabs
                active={quickTab}
                onChange={(k) => {
                  setQuickTab(k);
                  setVisibleCount(PAGE_SIZE);
                }}
                kind={kind}
                onKindChange={(next) => {
                  setKind(next);
                  setVisibleCount(PAGE_SIZE);
                }}
                groupByPackage={groupByPackage}
                onGroupByPackageChange={setGroupByPackage}
              />
              <div className="px-4 pb-1 text-[11px] text-gray-400">
                当前口径：
                {kind === 'all' ? '全部（主素材 + 副素材）' : kind === 'main' ? '主素材' : '副素材'}
                ，共 {allMaterials.length} 条
                {groupByPackage && ' · 已按设计包折叠（折叠时只显示该设计包第一个主素材作为封面）'}
              </div>
              {loading && (
                <div className="mx-4 mb-2 rounded-md bg-[#faf9ff] px-3 py-2 text-[11px] text-[#3d3192]">
                  正在从后端加载设计包与素材…
                </div>
              )}
              {loadError && (
                <div className="mx-4 mb-2 rounded-md bg-amber-50/70 px-3 py-2 text-[11px] text-amber-800">
                  {loadError}
                </div>
              )}
              {!loading && allMaterials.length === 0 && (
                <div className="px-4 py-16 text-center text-[13px] text-gray-400">
                  还没有任何素材
                  <div className="mt-1 text-[11px] text-gray-400">
                    去「上传设计包」上传主素材与同名副素材，这里就会出现真实的 MAT 与副素材
                  </div>
                </div>
              )}
            </>
          )}

          <div className="h-2" />
          {imageMode ? (
            // 图片搜索结果（真实后端搜索）
            <div className="px-4 pb-4">
              {searchLoading ? (
                <div className="py-16 text-center text-[13px] text-gray-400">正在搜索相似素材…</div>
              ) : searchError ? (
                <div className="py-16 text-center text-[13px] text-red-500">{searchError}</div>
              ) : searchResults.length === 0 ? (
                <div className="py-16 text-center text-[13px] text-gray-400">
                  没有找到相似素材
                  <div className="mt-2 text-xs text-gray-300">换个查询图，或调整筛选条件再试</div>
                </div>
              ) : (
                <div
                  data-testid="image-search-results"
                  className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5"
                >
                  {searchResults.map((r, idx) => {
                    const isVariant = r.kind === 'VARIANT';
                    return (
                      <div
                        key={`${r.assetId}-${r.kind}-${idx}`}
                        onClick={() => openSearchResult(r)}
                        className="group cursor-pointer overflow-hidden rounded-md border border-gray-100 bg-white transition-shadow hover:shadow-md"
                      >
                        <div className="relative aspect-square w-full overflow-hidden bg-gray-50">
                          <img
                            src={resolveMediaUrl(r.previewUrl)}
                            alt={r.name}
                            loading="lazy"
                            className="h-full w-full object-cover"
                          />
                          <span
                            className={`absolute left-1 top-1 rounded px-1 py-0.5 text-[10px] ${
                              isVariant ? 'bg-emerald-600 text-white' : 'bg-[#3d3192] text-white'
                            }`}
                          >
                            {isVariant ? '副素材' : '主素材'}
                          </span>
                          <span className="absolute right-1 top-1 rounded bg-black/60 px-1 py-0.5 text-[10px] font-medium text-white">
                            {r.similarity.toFixed(1)}%
                          </span>
                          {r.matchedBlake3 && (
                            <span className="absolute bottom-1 left-1 rounded bg-blue-600/90 px-1 py-0.5 text-[10px] text-white">
                              内容相同
                            </span>
                          )}
                        </div>
                        <div className="px-1.5 pb-1.5 pt-1">
                          <div className="truncate text-[12px] font-medium text-gray-800" title={r.name}>
                            {isVariant ? `副素材 ${r.displayCode}` : (r.materialCode ?? r.name)}
                          </div>
                          <div className="truncate text-[10px] text-gray-400" title={r.designPackageName ?? ''}>
                            {r.designPackageName ?? '—'}
                          </div>
                          {r.responsibleName && (
                            <div className="text-[10px] text-gray-400">负责人：{r.responsibleName}</div>
                          )}
                          {(r.tags ?? []).length > 0 && (
                            <div className="mt-0.5 flex flex-wrap gap-0.5">
                              {(r.tags ?? []).slice(0, 2).map((t) => (
                                <span key={t} className="rounded-full bg-gray-100 px-1 py-0.5 text-[9px] text-gray-500">
                                  {t}
                                </span>
                              ))}
                            </div>
                          )}
                          {(r.matchedTags ?? []).length > 0 && (
                            <div className="mt-0.5 text-[9px] text-[#3d3192]">
                              标签命中：{(r.matchedTags ?? []).join('、')}
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          ) : groupByPackage ? (
            <MaterialPackageGroups
              groups={packageGroups}
              expanded={expandedPackages}
              onToggle={(packageId) => setExpandedPackages((prev) => {
                const next = new Set(prev);
                if (next.has(packageId)) next.delete(packageId);
                else next.add(packageId);
                return next;
              })}
              onOpenMaterial={openMaterialByCode}
              onDeletePackage={async (group) => {
                try {
                  await deletePackageFromApi(group.pkg.id, '素材中心');
                  setExpandedPackages((prev) => {
                    const next = new Set(prev);
                    next.delete(group.pkg.id);
                    return next;
                  });
                  setDrawerMaterial(null);
                  toast.success(
                    `已删除设计包「${group.pkg.name}」（软删除 / 归档，可在「显示已删除的设计包」里恢复）`,
                  );
                  await refreshArchived();
                } catch (error) {
                  toast.error(error instanceof Error ? error.message : '删除设计包失败');
                }
              }}
              archived={archivedPackages}
              onRestorePackage={async (pkg) => {
                try {
                  await restorePackageFromApi(pkg.id);
                  toast.success(`已恢复设计包「${pkg.name}」`);
                  await refreshArchived();
                } catch (error) {
                  toast.error(error instanceof Error ? error.message : '恢复设计包失败');
                }
              }}
            />
          ) : (
            <MaterialGrid
              materials={visible}
              total={filtered.length}
              favorites={favorites}
              onToggleFavorite={toggleFavorite}
              onOpen={openDrawer}
              onOpenVariant={openVariantFromCard}
              onImageSearch={(m) => m.assetId ? searchByAsset(m.assetId, m.image) : undefined}
              hasMore={visibleCount < filtered.length}
              onLoadMore={() => setVisibleCount((c) => c + PAGE_SIZE)}
              selectedIds={selectedIds}
              onToggleSelect={toggleSelect}
            />
          )}
        </main>
      </div>

      <ImageSearchPanel
        open={imagePanelOpen}
        onClose={() => setImagePanelOpen(false)}
        onSearch={(file) => {
          void runImageSearch(file);
        }}
      />
      <MaterialDrawer
        material={drawerMaterial}
        initialVariantId={drawerInitialVariant}
        onClose={() => setDrawerMaterial(null)}
        onFindSimilar={(assetId) => {
          const vm = variantMaterials.find((x) => x.assetId === assetId);
          void searchByAsset(assetId, vm?.image);
        }}
      />

      {/* 多选后的批量操作条 */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-full border border-gray-200 bg-white px-4 py-2.5 shadow-lg">
          <span className="text-xs text-gray-600">已选 {selectedIds.size} 个素材</span>
          <button
            onClick={() => setBatchTagDialog('add')}
            className="rounded-full bg-[#3d3192] px-3 py-1.5 text-xs text-white hover:bg-[#32277a]"
          >
            添加标签
          </button>
          <button
            onClick={() => setBatchTagDialog('remove')}
            className="rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:border-red-400 hover:text-red-500"
          >
            删除标签
          </button>
          <button
            onClick={() => setBatchTagDialog('replace')}
            className="rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:border-[#3d3192] hover:text-[#3d3192]"
          >
            替换标签
          </button>
          <button
            onClick={() => setSelectedIds(new Set())}
            className="rounded-full border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:border-gray-400"
          >
            取消选择
          </button>
        </div>
      )}

      {/* 批量标签对话框 */}
      {batchTagDialog && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center">
          <div className="absolute inset-0 bg-black/30" onClick={() => setBatchTagDialog(null)} />
          <div className="relative w-[420px] max-w-[92vw] rounded-lg bg-white p-5 shadow-2xl">
            <h3 className="text-sm font-semibold text-gray-900">
              {batchTagDialog === 'add' && '批量添加标签'}
              {batchTagDialog === 'remove' && '批量删除标签'}
              {batchTagDialog === 'replace' && '批量替换标签'}
            </h3>
            <p className="mt-1 text-xs text-gray-400">
              已选 {selectedIds.size} 个素材（{[...selectedIds].filter((id) => id.startsWith('MAT-')).length} 个主素材 +{' '}
              {[...selectedIds].filter((id) => id.startsWith('variant-')).length} 个副素材）
            </p>
            <div className="mt-3">
              <input
                value={batchTagInput}
                onChange={(e) => setBatchTagInput(e.target.value)}
                placeholder="输入标签，逗号分隔多个"
                className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-[#3d3192] focus:outline-none"
                autoFocus
              />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setBatchTagDialog(null)}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={() => void runBatchTags(batchTagDialog)}
                className="rounded-md bg-[#3d3192] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#32277a]"
              >
                确认{batchTagDialog === 'add' ? '添加' : batchTagDialog === 'remove' ? '删除' : '替换'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
