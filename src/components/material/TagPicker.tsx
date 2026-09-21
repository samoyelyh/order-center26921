import { useMemo, useState } from 'react';
import { X } from 'lucide-react';

export interface TagSuggestion {
  tag: string;
  count: number;
}

interface Props {
  value: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  /** 全库已有标签（搜索用） */
  suggestions?: TagSuggestion[];
  disabled?: boolean;
}

/**
 * 标签编辑器：搜索已有标签 / 多选 / 新增 / 删除。
 *
 * 规则：标签是纯字符串数组，输入「逗号 / 回车 / 失去焦点」就结束当前标签；
 * 已有标签可以从建议列表点选（不重复），也可以直接删掉。
 */
export function TagPicker({ value, onChange, placeholder, suggestions = [], disabled }: Props) {
  const [draft, setDraft] = useState('');

  const normalized = useMemo(() => value.map((t) => t.trim()).filter(Boolean), [value]);

  const add = (tag: string) => {
    const clean = tag.trim();
    if (!clean || normalized.includes(clean)) return;
    onChange([...normalized, clean]);
    setDraft('');
  };

  const remove = (tag: string) => {
    onChange(normalized.filter((t) => t !== tag));
  };

  const commitDraft = () => {
    if (!draft.trim()) return;
    // 逗号 / 中文逗号 分隔多个标签
    const parts = draft.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const next = [...normalized];
    for (const part of parts) {
      if (!next.includes(part)) next.push(part);
    }
    onChange(next);
    setDraft('');
  };

  const filtered = useMemo(() => {
    const kw = draft.trim().toLowerCase();
    return suggestions
      .filter((s) => !normalized.includes(s.tag))
      .filter((s) => !kw || s.tag.toLowerCase().includes(kw))
      .slice(0, 12);
  }, [suggestions, draft, normalized]);

  return (
    <div className="space-y-1.5">
      <div
        className={`flex flex-wrap items-center gap-1.5 rounded-md border bg-white px-2 py-1.5 ${
          disabled ? 'pointer-events-none bg-gray-50 opacity-80' : 'border-gray-300'
        }`}
      >
        {normalized.map((tag) => (
          <span
            key={tag}
            className="flex items-center gap-0.5 rounded-full bg-[#f0eef9] px-2 py-0.5 text-[11px] text-[#3d3192]"
          >
            {tag}
            <button
              type="button"
              onClick={() => remove(tag)}
              className="rounded-full p-0.5 hover:bg-[#3d3192]/20"
              title={`删除标签 ${tag}`}
            >
              <X className="h-2.5 w-2.5" />
            </button>
          </span>
        ))}
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              commitDraft();
            }
          }}
          onBlur={commitDraft}
          placeholder={normalized.length ? '' : placeholder}
          className="min-w-[80px] flex-1 bg-transparent text-xs text-gray-700 placeholder:text-gray-400 focus:outline-none"
        />
      </div>
      {filtered.length > 0 && draft.trim() && (
        <div className="flex flex-wrap gap-1.5">
          {filtered.map((s) => (
            <button
              key={s.tag}
              type="button"
              onClick={() => add(s.tag)}
              className="rounded-full border border-gray-300 bg-white px-2 py-0.5 text-[11px] text-gray-600 hover:border-[#3d3192] hover:text-[#3d3192]"
            >
              {s.tag}
              <span className="ml-1 text-gray-400">{s.count}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
