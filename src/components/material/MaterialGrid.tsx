import type { Material } from '@/types/material';
import { MaterialCard } from './MaterialCard';

interface Props {
  materials: Material[];
  total: number;
  favorites: Set<string>;
  onToggleFavorite: (id: string) => void;
  onOpen: (m: Material) => void;
  /** 点击主素材卡片上的副素材缩略图 → 直接进副素材详情 */
  onOpenVariant?: (variantId: string) => void;
  onImageSearch: (m: Material) => void;
  hasMore: boolean;
  onLoadMore: () => void;
  /** 素材中心多选：选中的素材 id 集合 */
  selectedIds: Set<string>;
  onToggleSelect: (id: string) => void;
}

export function MaterialGrid({
  materials,
  total,
  favorites,
  onToggleFavorite,
  onOpen,
  onOpenVariant,
  onImageSearch,
  hasMore,
  onLoadMore,
  selectedIds,
  onToggleSelect,
}: Props) {
  if (materials.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 text-gray-400">
        <div className="text-sm">素材库暂无素材</div>
        <div className="mt-1 text-xs">上传并提交设计包后，素材会显示在这里</div>
      </div>
    );
  }

  return (
    <div className="px-4 pb-6">
      <div className="grid grid-cols-3 gap-3 md:grid-cols-4 lg:grid-cols-5 2xl:grid-cols-6">
        {materials.map((m) => (
          <MaterialCard
            key={m.id}
            material={m}
            favorite={favorites.has(m.id)}
            selected={selectedIds.has(m.id)}
            onToggleSelect={() => onToggleSelect(m.id)}
            onToggleFavorite={onToggleFavorite}
            onOpen={onOpen}
            onOpenVariant={onOpenVariant}
            onImageSearch={onImageSearch}
          />
        ))}
      </div>
      <div className="mt-5 flex items-center justify-center gap-3">
        <span className="text-xs text-gray-400">
          已显示 {materials.length} / {total} 个素材
        </span>
        {hasMore && (
          <button
            onClick={onLoadMore}
            className="rounded border border-gray-300 px-4 py-1.5 text-xs text-gray-600 transition-colors hover:border-[#3d3192] hover:text-[#3d3192]"
          >
            加载更多
          </button>
        )}
      </div>
    </div>
  );
}
