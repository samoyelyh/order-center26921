import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, RotateCcw } from 'lucide-react';
import {
  SORT_OPTIONS,
  STYLE_OPTIONS,
  TYPE_OPTIONS,
  UPLOAD_TIME_OPTIONS,
  UPLOADER_OPTIONS,
  USAGE_OPTIONS,
} from '@/mock/materials';
import { API_ENABLED } from '@/services/apiClient';

/** 依赖 ASIN / 订单数据的使用状态（Phase 3 才有真实来源） */
const PHASE3_USAGE = new Set(['hasAsin', 'noAsin']);
/** 依赖订单/销量的排序（Phase 3 才有真实来源） */
const PHASE3_SORT = new Set(['asin', 'sales30']);

interface Option {
  value: string;
  label: string;
}

export function FilterSelect({
  placeholder,
  value,
  options,
  onChange,
  active,
}: {
  placeholder: string;
  value: string;
  options: Option[];
  onChange: (v: string) => void;
  active: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const current = options.find((o) => o.value === value);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex h-8 min-w-[118px] items-center justify-between gap-2 rounded border px-2.5 text-xs transition-colors ${
          active
            ? 'border-[#3d3192] text-[#3d3192]'
            : 'border-gray-300 text-gray-500 hover:border-gray-400'
        }`}
      >
        <span className="truncate">{active && current ? current.label : placeholder}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 max-h-64 w-40 overflow-y-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {options.map((o) => (
            <button
              key={o.value}
              onClick={() => {
                onChange(o.value);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs hover:bg-gray-50 ${
                o.value === value ? 'font-medium text-[#3d3192]' : 'text-gray-600'
              }`}
            >
              {o.label}
              {o.value === value && <Check className="h-3.5 w-3.5" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export interface FilterValues {
  type: string;
  style: string;
  uploader: string;
  uploadTime: string;
  usage: string;
  sort: string;
}

interface Props {
  values: FilterValues;
  onChange: (patch: Partial<FilterValues>) => void;
  onReset: () => void;
  imageMode: boolean;
}

const toOptions = (arr: string[]): Option[] => arr.map((v) => ({ value: v, label: v }));

export function MaterialFilterPanel({ values, onChange, onReset, imageMode }: Props) {
  const hasActive =
    values.type !== '全部' ||
    values.uploader !== '全部' ||
    values.uploadTime !== 'all' ||
    values.usage !== 'all';

  // ASIN / 订单 / 销量相关筛选与排序属 Phase 3：真实后端下没有数据来源，隐藏掉
  const usageOptions = API_ENABLED
    ? USAGE_OPTIONS.filter((o) => !PHASE3_USAGE.has(o.value))
    : USAGE_OPTIONS;
  const sortOptions = API_ENABLED
    ? SORT_OPTIONS.filter((o) => !PHASE3_SORT.has(o.value))
    : SORT_OPTIONS;

  return (
    <div className="flex flex-wrap items-center justify-end gap-2 px-4 pt-3">
      {hasActive && !imageMode && (
        <button
          onClick={onReset}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-[#3d3192]"
        >
          <RotateCcw className="h-3 w-3" />
          清空筛选
        </button>
      )}
      <FilterSelect placeholder="素材类型" value={values.type} options={toOptions(TYPE_OPTIONS)} onChange={(v) => onChange({ type: v })} active={values.type !== '全部'} />
      <FilterSelect placeholder="上传人" value={values.uploader} options={toOptions(UPLOADER_OPTIONS)} onChange={(v) => onChange({ uploader: v })} active={values.uploader !== '全部'} />
      <FilterSelect placeholder="上传时间" value={values.uploadTime} options={UPLOAD_TIME_OPTIONS} onChange={(v) => onChange({ uploadTime: v })} active={values.uploadTime !== 'all'} />
      <FilterSelect placeholder="使用状态" value={values.usage} options={usageOptions} onChange={(v) => onChange({ usage: v })} active={values.usage !== 'all'} />
      <FilterSelect placeholder="排序" value={values.sort} options={sortOptions} onChange={(v) => onChange({ sort: v })} active={values.sort !== 'latest'} />
    </div>
  );
}

/** 风格筛选：放在适用场景下方 */
export function StyleFilterRow({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 pb-3">
      <span className="shrink-0 text-[13px] text-gray-500">风格：</span>
      {STYLE_OPTIONS.map((s) => {
        const active = s === '全部' ? value === '全部' : value === s;
        return (
          <button
            key={s}
            onClick={() => onChange(s)}
            className={`rounded-full px-3 py-1 text-[13px] transition-colors ${
              active ? 'bg-[#3d3192] font-medium text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {s}
          </button>
        );
      })}
    </div>
  );
}
