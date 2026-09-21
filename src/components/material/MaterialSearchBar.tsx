import { Camera, Search } from 'lucide-react';

interface Props {
  keyword: string;
  onKeywordChange: (v: string) => void;
  onOpenImageSearch: () => void;
}

export function MaterialSearchBar({ keyword, onKeywordChange, onOpenImageSearch }: Props) {
  return (
    <div className="flex items-center gap-1.5 px-3 pt-3 pb-2">
      <div className="relative flex-1">
        <input
          value={keyword}
          onChange={(e) => onKeywordChange(e.target.value)}
          placeholder="搜索素材 / 素材ID / 美工编码 / ASIN / 标签"
          className="h-8 w-full rounded border border-gray-300 bg-white pl-2.5 pr-7 text-xs text-gray-700 placeholder:text-gray-400 focus:border-[#3d3192] focus:outline-none"
        />
        <Search className="pointer-events-none absolute right-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
      </div>
      <button
        onClick={onOpenImageSearch}
        title="图片导航：以图搜素材"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-gray-300 bg-white text-gray-500 transition-colors hover:border-[#3d3192] hover:text-[#3d3192]"
      >
        <Camera className="h-4 w-4" />
      </button>
    </div>
  );
}
