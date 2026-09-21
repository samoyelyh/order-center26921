import { useCallback, useEffect, useRef, useState } from 'react';
import { ClipboardPaste, ImageUp, Loader2, UploadCloud, X } from 'lucide-react';
import { toast } from 'sonner';

interface Props {
  open: boolean;
  onClose: () => void;
  /** 上传/拖拽/粘贴的查询图 → 真实搜索（走 /api/image-search/search） */
  onSearch: (file: File) => void;
}

/** 图片搜索上传面板：支持点击上传 / 拖拽 / Ctrl+V 粘贴，三种方式走同一套搜索接口 */
export function ImageSearchPanel({ open, onClose, onSearch }: Props) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File | undefined | null) => {
      if (!file) return;
      if (!file.type.startsWith('image/')) {
        toast.error('请选择图片文件');
        return;
      }
      setBusy(true);
      try {
        onSearch(file);
        onClose();
      } finally {
        setBusy(false);
      }
    },
    [onSearch, onClose],
  );

  // Ctrl+V 粘贴剪贴板图片（没有图片则忽略，不影响普通文字复制粘贴）
  useEffect(() => {
    if (!open) return;
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith('image/'));
      if (item) {
        e.preventDefault();
        handleFile(item.getAsFile());
      }
    };
    window.addEventListener('paste', onPaste);
    return () => window.removeEventListener('paste', onPaste);
  }, [open, handleFile]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-[520px] max-w-[92vw] rounded-lg bg-white p-6 shadow-2xl">
        <div className="mb-1 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">图片导航 / 以图搜图</h3>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-4 text-xs text-gray-400">
          上传一张图片，在素材库中查找相似的主素材 / 副素材。查询图是临时文件，不会写进素材库；
          图片搜索与主副素材配对完全独立。
        </p>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            handleFile(e.dataTransfer.files?.[0]);
          }}
          onClick={() => fileRef.current?.click()}
          className={`flex h-48 cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed transition-colors ${
            dragging ? 'border-[#3d3192] bg-[#f0eef9]' : 'border-gray-200 bg-gray-50 hover:border-[#3d3192]/50'
          }`}
        >
          {busy ? (
            <Loader2 className="h-8 w-8 animate-spin text-[#3d3192]" />
          ) : (
            <UploadCloud className={`h-8 w-8 ${dragging ? 'text-[#3d3192]' : 'text-gray-300'}`} />
          )}
          <div className="mt-2 text-[13px] text-gray-600">拖拽图片到这里，或点击上传</div>
          <div className="mt-1 flex items-center gap-1 text-xs text-gray-400">
            <ClipboardPaste className="h-3 w-3" />
            也可以直接 Ctrl+V 粘贴图片
          </div>
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </div>

        <button
          onClick={() => {
            onClose();
          }}
          className="mt-3 flex w-full items-center justify-center gap-1.5 rounded border border-gray-200 py-2 text-xs text-gray-500 transition-colors hover:border-[#3d3192] hover:text-[#3d3192]"
        >
          <ImageUp className="h-3.5 w-3.5" />
          返回素材中心
        </button>
      </div>
    </div>
  );
}
