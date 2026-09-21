import { ExternalLink } from 'lucide-react';
import type { Asin } from '@/types/material';

const STATUS_STYLE: Record<Asin['status'], string> = {
  在售: 'bg-green-50 text-green-600',
  停售: 'bg-gray-100 text-gray-500',
  审核中: 'bg-orange-50 text-orange-500',
  跟卖中: 'bg-blue-50 text-blue-500',
};

interface Props {
  asins: Asin[];
  onViewAsin: (a: Asin) => void;
}

export function MaterialAsinList({ asins, onViewAsin }: Props) {
  if (asins.length === 0) {
    return (
      <div className="px-5 py-16 text-center text-[13px] text-gray-400">
        该素材暂未关联 ASIN
        <div className="mt-1 text-[11px] text-gray-400">
          关联 ASIN 属 Phase 3（需要接入 Amazon / 订单 URL 识别后才有数据）
        </div>
      </div>
    );
  }
  return (
    <div className="px-5 py-4">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-gray-100 text-left text-gray-400">
            <th className="pb-2 font-normal">站点</th>
            <th className="pb-2 font-normal">Child ASIN</th>
            <th className="pb-2 font-normal">店铺</th>
            <th className="pb-2 font-normal">状态</th>
            <th className="pb-2 text-right font-normal">30天订单</th>
            <th className="pb-2 text-right font-normal">操作</th>
          </tr>
        </thead>
        <tbody>
          {asins.map((a) => (
            <tr key={a.id} className="border-b border-gray-50 text-gray-700 last:border-0">
              <td className="py-2.5">{a.site}</td>
              <td className="py-2.5 font-mono text-[11px]">{a.childAsin}</td>
              <td className="max-w-[90px] truncate py-2.5">{a.shop}</td>
              <td className="py-2.5">
                <span className={`rounded px-1.5 py-0.5 text-[11px] ${STATUS_STYLE[a.status]}`}>
                  {a.status}
                </span>
              </td>
              <td className="py-2.5 text-right">{a.orders30.toLocaleString()}</td>
              <td className="py-2.5 text-right">
                <button
                  onClick={() => onViewAsin(a)}
                  className="mr-2 text-[#3d3192] hover:underline"
                >
                  查看详情
                </button>
                <a
                  href={`https://www.amazon.com/dp/${a.childAsin}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-0.5 text-[#3d3192] hover:underline"
                >
                  打开Listing
                  <ExternalLink className="h-3 w-3" />
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
