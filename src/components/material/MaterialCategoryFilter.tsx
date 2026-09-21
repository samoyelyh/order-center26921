import { LEAGUE_OPTIONS, SCENE_OPTIONS } from '@/mock/materials';

interface Props {
  selected: string[];
  onChange: (v: string[]) => void;
  /** 当前选中「球迷」子类时切换为联盟标签 */
  fanMode: boolean;
}

/** 适用场景：胶囊多选；球迷子类下切换为联盟标签 */
export function MaterialCategoryFilter({ selected, onChange, fanMode }: Props) {
  const options = fanMode ? LEAGUE_OPTIONS : SCENE_OPTIONS;
  const label = fanMode ? '联盟：' : '适用场景：';

  const toggle = (opt: string) => {
    if (opt === '全部') {
      onChange([]);
      return;
    }
    onChange(selected.includes(opt) ? selected.filter((c) => c !== opt) : [...selected, opt]);
  };

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
      <span className="shrink-0 text-[13px] text-gray-500">{label}</span>
      {(fanMode ? ['全部', ...options] : options).map((opt) => {
        const active = opt === '全部' ? selected.length === 0 : selected.includes(opt);
        return (
          <button
            key={opt}
            onClick={() => toggle(opt)}
            className={`rounded-full px-3 py-1 text-[13px] transition-colors ${
              active ? 'bg-[#3d3192] font-medium text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}
