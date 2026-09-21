import type { Asin, Design, Material } from '@/types/material';

// ---------------- 常量字典 ----------------

/** 左侧可折叠分类树 */
export interface CategoryNode {
  name: string;
  children: string[];
}

export const CATEGORY_TREE: CategoryNode[] = [
  { name: '底纹', children: ['球迷', '经典', '通用'] },
  { name: '图案', children: ['球迷', '经典', '通用'] },
  { name: '文案', children: ['球迷', '经典', '通用'] },
  { name: '字体', children: ['球迷', '经典', '通用'] },
];

export const SUB_CATEGORIES = ['球迷', '经典', '通用'];

/** 适用场景（替代原适用品类） */
export const SCENE_OPTIONS = [
  '全部',
  '万圣节',
  '圣诞节',
  '感恩节',
  '情人节',
  '复活节',
  '独立日',
  '母亲节',
  '父亲节',
  '毕业季',
  '生日派对',
  '日常通用',
];

/** 球迷子类下的联盟标签 */
export const LEAGUE_OPTIONS = ['NFL', 'NBA', 'MLB', 'NHL', 'NCAA', '英超', '西甲', '欧冠'];

export const SOURCE_OPTIONS = ['全部', '原创', '采购', 'AI生成', '公共素材', '未知来源'];
export const TYPE_OPTIONS = ['全部', '插画', '纹理', '字体', '图标', '照片', '矢量', '背景', '装饰元素'];
export const STYLE_OPTIONS = ['全部', '复古', '美式', '街头', '卡通', '极简', '手绘', '哥特', 'Y2K', '扎染', '渐变'];
export const CRAFT_OPTIONS = ['全部', '印花', '刺绣', 'DTF', '热转印', '丝印', '激光'];
export const AREA_OPTIONS = ['全部', '全身', '胸前', '背部', '肩部', '袖口', '号码', '姓名', '下摆', '局部装饰'];
export const USAGE_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'unused', label: '未使用' },
  { value: 'used', label: '已使用' },
  { value: 'highReuse', label: '高复用' },
  { value: 'lowReuse', label: '低复用' },
  { value: 'hasAsin', label: '已有在售ASIN' },
  { value: 'noAsin', label: '无在售ASIN' },
  { value: 'risk', label: '风险素材' },
];
export const SORT_OPTIONS: { value: string; label: string }[] = [
  { value: 'latest', label: '最新上传' },
  { value: 'earliest', label: '最早上传' },
  { value: 'usage', label: '使用次数' },
  { value: 'asin', label: 'ASIN数量' },
  { value: 'sales30', label: '30天销量' },
];

export const FIT_CATEGORIES = SCENE_OPTIONS;

export const QUICK_TABS: { key: string; label: string }[] = [
  { key: 'recommend', label: '推荐' },
  { key: 'latest', label: '最新' },
  { key: 'hot', label: '热门' },
  { key: 'growth', label: '高增长' },
  { key: 'highReuse', label: '高复用' },
  { key: 'unused', label: '未使用' },
  { key: 'favorite', label: '我的收藏' },
  { key: 'recent', label: '最近使用' },
];

// ---------------- Mock 素材 ----------------

const UPLOADERS = ['张伟', '李娜', '王强', '陈静', '刘洋', '赵敏'];

/** 上传人筛选选项 */
export const UPLOADER_OPTIONS = ['全部', ...UPLOADERS];

/** 上传时间筛选选项 */
export const UPLOAD_TIME_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: '7d', label: '近七天' },
  { value: '30d', label: '近三十天' },
  { value: '90d', label: '近三个月' },
  { value: '180d', label: '近六个月' },
];

/** 可复现的伪随机 */
function seeded(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

/** 素材目录当前为空；后续上传提交成功后由后端返回正式素材。 */
export const MATERIALS: Material[] = [];

// ---------------- Mock 设计 ----------------

const ARTISTS = ['张三', '李四', '王五', '赵六', '钱七'];

export function getDesigns(materialId: string): Design[] {
  const mat = MATERIALS.find((m) => m.id === materialId);
  if (!mat || mat.designCount === 0) return [];
  const idx = MATERIALS.indexOf(mat);
  const rnd = seeded(idx * 131 + 7);
  const count = Math.min(mat.designCount, 6);
  return Array.from({ length: count }, (_, i) => ({
    id: `${materialId}-D${i}`,
    code: `DES-2026${String(1 + (idx % 8)).padStart(2, '0')}-${String(100 + idx * 6 + i).padStart(4, '0')}`,
    artistCode: `MF-${8800 + ((idx * 3 + i * 7) % 180)}`,
    artist: ARTISTS[Math.floor(rnd() * ARTISTS.length)],
    asinCount: Math.max(1, Math.floor(rnd() * 12)),
    thumb: mat.image,
    materialId,
    updateTime: `2026-0${1 + (i % 8)}-1${i}`,
  }));
}

// ---------------- Mock ASIN ----------------

const SITES = ['美国站', '英国站', '德国站', '日本站', '加拿大站'];
const SHOPS = ['SDS-运动旗舰店', 'SDS-服饰专营店', 'AlphaSports', 'NorthPeak'];
const ASIN_STATUS: Asin['status'][] = ['在售', '在售', '在售', '停售', '审核中', '跟卖中'];

export function getAsins(materialId: string): Asin[] {
  const mat = MATERIALS.find((m) => m.id === materialId);
  if (!mat || mat.asinCount === 0) return [];
  const idx = MATERIALS.indexOf(mat);
  const rnd = seeded(idx * 57 + 3);
  const designs = getDesigns(materialId);
  const count = Math.min(mat.asinCount, 10);
  return Array.from({ length: count }, (_, i) => ({
    id: `${materialId}-A${i}`,
    site: SITES[Math.floor(rnd() * SITES.length)],
    childAsin: `B0${Array.from({ length: 8 }, () => '0123456789ABCDEFGHJKLMNPQRSTUVWXYZ'[Math.floor(rnd() * 34)]).join('')}`,
    shop: SHOPS[Math.floor(rnd() * SHOPS.length)],
    status: ASIN_STATUS[Math.floor(rnd() * ASIN_STATUS.length)],
    orders30: Math.floor(rnd() * 400),
    designCode: designs.length ? designs[i % designs.length].code : '-',
  }));
}
