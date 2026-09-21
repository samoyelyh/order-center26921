import { Download, Heart, ScanSearch, Link2 } from 'lucide-react';
import type { Material } from '@/types/material';

interface Props {
  material: Material;
  favorite: boolean;
  /** 素材中心多选：该卡片是否被选中 */
  selected?: boolean;
  onToggleSelect?: () => void;
  onToggleFavorite: (id: string) => void;
  onOpen: (m: Material) => void;
  /** 点击主素材卡片上的副素材缩略图 → 直接进副素材详情 */
  onOpenVariant?: (variantId: string) => void;
  onImageSearch: (m: Material) => void;
}

export function MaterialCard({ material: m, favorite, selected, onToggleSelect, onToggleFavorite, onOpen, onOpenVariant, onImageSearch }: Props) {
  const stop = (e: React.MouseEvent) => e.stopPropagation();

  return (
    <div
      onClick={() => onOpen(m)}
      className={`group cursor-pointer rounded-md bg-white transition-shadow hover:shadow-md ${
        selected ? 'ring-2 ring-[#3d3192] ring-offset-1' : ''
      }`}
    >
      <div
        className={`relative aspect-square overflow-hidden rounded-md border border-gray-100 ${
          m.transparent ? 'checker-bg' : 'bg-gray-50'
        }`}
      >
        <img
          src={m.image}
          alt={m.name}
          loading="lazy"
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
        />

        {/* 多选框（左上角） */}
        <button
          onClick={(e) => {
            stop(e);
            onToggleSelect?.();
          }}
          title={selected ? '取消选择' : '选择这个素材'}
          className={`absolute left-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded border transition-opacity ${
            selected
              ? 'border-[#3d3192] bg-[#3d3192] text-white opacity-100'
              : 'border-gray-300 bg-white/90 opacity-0 group-hover:opacity-100'
          }`}
        >
          {selected && <span className="text-[10px]">✓</span>}
        </button>

        {/* 收藏 */}
        <button
          onClick={(e) => {
            stop(e);
            onToggleFavorite(m.id);
          }}
          title="收藏"
          className={`absolute right-1.5 top-1.5 flex h-6 w-6 items-center justify-center rounded-full bg-white/90 shadow-sm transition-opacity ${
            favorite ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
          }`}
        >
          <Heart
            className={`h-3.5 w-3.5 ${favorite ? 'fill-red-500 text-red-500' : 'text-gray-500'}`}
          />
        </button>

        {/* Hover 操作层 */}
        <div className="absolute inset-x-0 bottom-0 flex translate-y-full items-center justify-center gap-1 bg-black/55 py-2 transition-transform duration-200 group-hover:translate-y-0">
          {[
            { icon: ScanSearch, label: '找相似', fn: () => onImageSearch(m) },
            { icon: Link2, label: '查看关联', fn: () => onOpen(m) },
            { icon: Heart, label: '收藏', fn: () => onToggleFavorite(m.id) },
            {
              icon: Download,
              label: '下载',
              fn: () => {
                const a = document.createElement('a');
                a.href = m.image;
                a.download = m.fileName;
                a.click();
              },
            },
          ].map((a) => (
            <button
              key={a.label}
              onClick={(e) => {
                stop(e);
                a.fn();
              }}
              className="flex flex-col items-center gap-0.5 rounded px-2 py-0.5 text-white/90 hover:bg-white/15 hover:text-white"
            >
              <a.icon className="h-3.5 w-3.5" />
              <span className="text-[10px]">{a.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="px-1 pb-1.5 pt-1.5">
        <div className="truncate text-[13px] font-medium text-gray-800">{m.name}</div>
        <div className="text-[11px] text-gray-400">{m.id}</div>
        <div className="mt-0.5 flex items-center gap-2 text-[11px] text-gray-400">
          <span>
            设计 <span className="text-gray-600">{m.designCount}</span>
          </span>
          <span>
            副素材 <span className="text-gray-600">{m.variantCount ?? 0}</span>
          </span>
          {m.responsibleName && (
            <span className="ml-auto text-gray-400">
              {m.responsibleName}
            </span>
          )}
        </div>
        {(m.tags ?? []).length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {(m.tags ?? []).slice(0, 3).map((tag) => (
              <span key={tag} className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
                {tag}
              </span>
            ))}
            {(m.tags ?? []).length > 3 && (
              <span className="text-[10px] text-gray-400">+{(m.tags ?? []).length - 3}</span>
            )}
          </div>
        )}
        {/* 主素材卡片：直接显示关联的副素材缩略图（点击某个缩略图直接进该副素材详情） */}
        {(m.variantPreviews ?? []).length > 0 && (
          <div className="mt-1.5 flex items-center gap-1">
            {(m.variantPreviews ?? []).slice(0, 4).map((vp) => (
              <button
                key={vp.code}
                onClick={(e) => {
                  stop(e);
                  onOpenVariant?.(vp.variantId);
                }}
                title={`副素材 ${vp.code}（点击进入详情）`}
                className="cursor-pointer"
              >
                <img
                  src={vp.imageUri}
                  alt={vp.code}
                  loading="lazy"
                  className="h-7 w-7 rounded border border-gray-100 bg-gray-50 object-cover transition-transform hover:scale-110"
                />
              </button>
            ))}
            {(m.variantPreviews ?? []).length > 4 && (
              <span className="text-[10px] text-gray-400">
                +{(m.variantPreviews ?? []).length - 4}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
