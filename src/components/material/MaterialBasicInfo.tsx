import type { Material } from '@/types/material';
import { TagPicker } from './TagPicker';

interface Props {
  material: Material;
  /** 标签编辑（单个素材） */
  onTagsChange?: (tags: string[]) => void;
  /** 负责人（来自所属设计包） */
  responsibleName?: string;
  /** 当前状态：已生成版本 / 已派发 等 */
  currentStatus?: string;
  /** 最近流转：最近一次维护记录摘要 */
  latestLog?: { action: string; summary: string; createdAt: string; actor: string } | null;
}

export function MaterialBasicInfo({
  material: m,
  onTagsChange,
  responsibleName,
  currentStatus,
  latestLog,
}: Props) {
  const rows: [string, React.ReactNode][] = [
    ['素材ID', m.id],
    ['文件名', m.fileName],
    ['尺寸', m.size],
    ['格式', m.format],
    ['负责人', responsibleName ?? m.uploader],
    ['上传时间', m.uploadTime],
    ['分类', `${m.category} / ${m.subCategory}${m.league ? ` / ${m.league}` : ''}`],
    ['适用场景', m.scenes.join('、')],
    ['风格', m.styles.join('、')],
  ];

  return (
    <div className="px-5 py-4">
      {/* 当前状态 + 最近流转（基本信息卡片顶部） */}
      {(currentStatus || latestLog) && (
        <div className="mb-3 rounded-md border border-[#e6e3f5] bg-[#faf9ff] px-3 py-2 text-xs">
          {currentStatus && (
            <div className="flex items-center gap-2">
              <span className="text-gray-400">当前状态</span>
              <span className="font-medium text-[#3d3192]">{currentStatus}</span>
            </div>
          )}
          {latestLog && (
            <div className="mt-1 flex items-center gap-2">
              <span className="text-gray-400">最近流转</span>
              <span className="text-gray-600">
                {latestLog.createdAt.replace('T', ' ').slice(0, 16)} {latestLog.summary}
              </span>
            </div>
          )}
        </div>
      )}

      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="flex border-b border-gray-50 py-2 text-[13px] last:border-0">
            <dt className="w-20 shrink-0 text-gray-400">{k}</dt>
            <dd className="flex-1 text-gray-700">{v}</dd>
          </div>
        ))}
      </dl>

      {/* 标签编辑：单个素材 */}
      <div className="mt-4 border-t border-gray-100 pt-3">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-[13px] font-medium text-gray-600">标签</span>
          {onTagsChange && (
            <span className="text-[11px] text-gray-400">点标签旁的 × 删除，输入后回车新增</span>
          )}
        </div>
        {onTagsChange ? (
          <TagPicker
            value={m.tags ?? []}
            onChange={onTagsChange}
            placeholder="输入标签后回车"
            suggestions={[]}
          />
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {(m.tags ?? []).length ? (
              (m.tags ?? []).map((tag) => (
                <span key={tag} className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                  {tag}
                </span>
              ))
            ) : (
              <span className="text-xs text-gray-400">暂无标签</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
