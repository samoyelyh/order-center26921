import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { CATEGORY_TREE } from '@/mock/materials';
import { MaterialSearchBar } from './MaterialSearchBar';

export interface CategorySelection {
  /** 空串 = 全部素材 */
  category: string;
  /** 空串 = 该分类下全部子类 */
  subCategory: string;
}

interface Props {
  keyword: string;
  onKeywordChange: (v: string) => void;
  onOpenImageSearch: () => void;
  selection: CategorySelection;
  onSelect: (sel: CategorySelection) => void;
  /** key 为 `分类` 或 `分类/子类` */
  counts: Record<string, number>;
  totalCount: number;
}

export function MaterialSidebar({
  keyword,
  onKeywordChange,
  onOpenImageSearch,
  selection,
  onSelect,
  counts,
  totalCount,
}: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const isActive = (cat: string, sub = '') =>
    selection.category === cat && selection.subCategory === sub;

  const itemCls = (active: boolean) =>
    `flex w-full items-center justify-between border-l-[3px] pr-4 text-left text-[13px] transition-colors ${
      active
        ? 'border-[#3d3192] bg-[#f0eef9] font-medium text-[#3d3192]'
        : 'border-transparent text-gray-600 hover:bg-gray-50'
    }`;

  return (
    <aside className="flex w-[220px] shrink-0 flex-col border-r border-gray-200 bg-white">
      <MaterialSearchBar
        keyword={keyword}
        onKeywordChange={onKeywordChange}
        onOpenImageSearch={onOpenImageSearch}
      />
      <nav className="flex-1 overflow-y-auto pb-4">
        {/* 全部素材 */}
        <button
          onClick={() => onSelect({ category: '', subCategory: '' })}
          className={`${itemCls(isActive(''))} px-4 py-[9px]`}
        >
          <span>全部素材</span>
          <span className={`text-[11px] ${isActive('') ? 'text-[#3d3192]/70' : 'text-gray-300'}`}>
            {totalCount}
          </span>
        </button>

        {/* 可折叠分类树 */}
        {CATEGORY_TREE.map((node) => {
          const isCollapsed = collapsed[node.name];
          const count = counts[node.name] ?? 0;
          return (
            <div key={node.name}>
              <div className={`group flex items-center ${isActive(node.name) ? 'bg-[#f0eef9]' : ''}`}>
                <button
                  onClick={() => setCollapsed((c) => ({ ...c, [node.name]: !c[node.name] }))}
                  className="flex h-[34px] w-7 shrink-0 items-center justify-center text-gray-400 hover:text-[#3d3192]"
                  title={isCollapsed ? '展开' : '折叠'}
                >
                  {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                </button>
                <button
                  onClick={() => onSelect({ category: node.name, subCategory: '' })}
                  className={`${itemCls(isActive(node.name))} flex-1 py-[7px] pl-0`}
                >
                  <span>{node.name}</span>
                  <span className={`text-[11px] ${isActive(node.name) ? 'text-[#3d3192]/70' : 'text-gray-300'}`}>
                    {count}
                  </span>
                </button>
              </div>
              {!isCollapsed &&
                node.children.map((sub) => (
                  <button
                    key={sub}
                    onClick={() => onSelect({ category: node.name, subCategory: sub })}
                    className={`${itemCls(isActive(node.name, sub))} py-[7px] pl-11`}
                  >
                    <span>{sub}</span>
                    <span className={`text-[11px] ${isActive(node.name, sub) ? 'text-[#3d3192]/70' : 'text-gray-300'}`}>
                      {counts[`${node.name}/${sub}`] ?? 0}
                    </span>
                  </button>
                ))}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
