import { ChevronRight } from 'lucide-react';
import type { MaterialDesignRef } from '@/types/material';

interface Props {
  designs: MaterialDesignRef[];
  onViewDesign: (d: MaterialDesignRef) => void;
}

/**
 * 素材详情「关联设计」。
 *
 * 显示的是**真实关联**：设计编码（上传设计包时填写）+ 它引用了这个素材的位置/设计包。
 * 没有关联就是空列表 —— 绝不显示编造的条数。
 */
export function MaterialDesignList({ designs, onViewDesign }: Props) {
  if (designs.length === 0) {
    return (
      <div className="px-5 py-16 text-center text-[13px] text-gray-400">
        该素材暂未关联任何设计
        <div className="mt-1 text-[11px] text-gray-400">
          上传设计包时填写「设计编码」，这个素材就会关联到那个设计
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2.5 px-5 py-4">
      {designs.map((d) => (
        <div
          key={d.id}
          className="rounded-md border border-gray-100 bg-white p-2.5 transition-shadow hover:shadow-sm"
        >
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1 text-xs leading-5">
              <div className="text-[13px] font-medium text-gray-800">
                设计编码 <span className="font-mono">{d.designCode}</span>
              </div>
              <div className="text-gray-500">
                设计美工 <span className="text-gray-700">{d.designerName || '未填写'}</span>
                <span className="mx-1.5 text-gray-300">|</span>
                引用位置 <span className="text-gray-700">{d.positionCount}</span> 个
                <span className="mx-1.5 text-gray-300">|</span>
                副素材 <span className="text-gray-700">{d.variantCount}</span> 个
              </div>
              <div className="mt-0.5 space-y-0.5">
                {d.packages.map((p) => (
                  <div key={`${p.packageId}-${p.position}`} className="text-gray-500">
                    设计包 <span className="text-gray-700">{p.packageName}</span>
                    <span className="mx-1 text-gray-300">·</span>
                    位置 <span className="text-gray-700">{p.position}</span>
                  </div>
                ))}
              </div>
            </div>
            <button
              onClick={() => onViewDesign(d)}
              className="flex shrink-0 items-center gap-0.5 rounded border border-[#3d3192]/40 px-2.5 py-1 text-xs text-[#3d3192] transition-colors hover:bg-[#3d3192] hover:text-white"
            >
              查看副素材
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
