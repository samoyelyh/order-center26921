import { QUICK_TABS } from '@/mock/materials';
import { API_ENABLED } from '@/services/apiClient';

export type MaterialKindFilter = 'all' | 'main' | 'variant';

/**
 * 依赖订单/销量/ASIN 的快捷入口（热门、高增长）属 Phase 3：
 * 接真实后端时没有数据来源，直接隐藏，避免点进去永远是空列表。
 */
const PHASE3_TABS = new Set(['hot', 'growth']);

interface Props {
  active: string;
  onChange: (k: string) => void;
  /** 第二十五条：首页筛选 全部 / 主素材 / 副素材 */
  kind?: MaterialKindFilter;
  onKindChange?: (kind: MaterialKindFilter) => void;
  /** 第二十五条：按设计包折叠 */
  groupByPackage?: boolean;
  onGroupByPackageChange?: (value: boolean) => void;
}

const KIND_OPTIONS: { value: MaterialKindFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'main', label: '主素材' },
  { value: 'variant', label: '副素材' },
];

/** 快捷导航：轻量 Text Tab */
export function MaterialQuickTabs({
  active,
  onChange,
  kind,
  onKindChange,
  groupByPackage,
  onGroupByPackageChange,
}: Props) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 pb-1 pt-2">
      {QUICK_TABS.filter((t) => !(API_ENABLED && PHASE3_TABS.has(t.key))).map((t) => {
        const isActive = active === t.key;
        return (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={`relative pb-1.5 text-[13px] transition-colors ${
              isActive ? 'font-medium text-[#3d3192]' : 'text-gray-500 hover:text-gray-800'
            }`}
          >
            {t.label}
            {isActive && (
              <span className="absolute inset-x-0 -bottom-0.5 h-0.5 rounded-full bg-[#3d3192]" />
            )}
          </button>
        );
      })}

      {onKindChange && (
        <div className="ml-auto flex items-center gap-1.5">
          <div className="flex items-center rounded border border-gray-300 bg-white p-0.5 text-xs">
            {KIND_OPTIONS.map((option) => (
              <button
                key={option.value}
                onClick={() => onKindChange(option.value)}
                className={`rounded px-2.5 py-0.5 transition-colors ${
                  kind === option.value ? 'bg-[#3d3192] font-medium text-white' : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          {onGroupByPackageChange && (
            <label className="flex cursor-pointer items-center gap-1.5 text-xs text-gray-500">
              <input
                type="checkbox"
                checked={Boolean(groupByPackage)}
                onChange={(event) => onGroupByPackageChange(event.target.checked)}
                className="h-3.5 w-3.5 accent-[#3d3192]"
              />
              按设计包折叠
            </label>
          )}
        </div>
      )}
    </div>
  );
}
