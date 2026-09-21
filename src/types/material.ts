// 素材中心 UI 视图模型（view model）
//
// 说明：这里不是数据库实体。V2 的领域模型（DesignPackage / PackageUpload /
// DerivativeBatch / Material / MaterialVariant …）定义在 src/types/material-workflow.ts，
// 由 src/lib/materialView.ts 适配成本文件的 Material 结构供素材中心组件渲染。

export type MaterialSource = '原创' | '采购' | 'AI生成' | '公共素材' | '未知来源';

export type MaterialType =
  | '插画'
  | '纹理'
  | '字体'
  | '图标'
  | '照片'
  | '矢量'
  | '背景'
  | '装饰元素';

export type SortKey =
  | 'latest'
  | 'earliest'
  | 'usage'
  | 'asin'
  | 'sales30';

export type UsageFilter =
  | 'all'
  | 'unused'
  | 'used'
  | 'highReuse'
  | 'lowReuse'
  | 'hasAsin'
  | 'noAsin'
  | 'risk';

export type QuickTabKey =
  | 'recommend'
  | 'latest'
  | 'hot'
  | 'growth'
  | 'highReuse'
  | 'unused'
  | 'favorite'
  | 'recent';

export type PageMode = 'browse' | 'search' | 'image';

export interface Material {
  /** 素材ID，如 MAT-001928 */
  id: string;
  name: string;
  /** 图片地址 */
  image: string;
  /** 是否透明 PNG（展示棋盘格背景） */
  transparent?: boolean;
  /** 左侧分类：底纹 / 图案 / 文案 / 字体 */
  category: string;
  /** 子类：球迷 / 经典 / 通用 */
  subCategory: string;
  /** 适用场景：万圣节、圣诞节等 */
  scenes: string[];
  /** 球迷子类素材所属联盟：NFL、NBA 等 */
  league?: string;
  source: MaterialSource;
  type: MaterialType;
  styles: string[];
  crafts: string[];
  areas: string[];
  /** 适用品类 */
  fitCategories: string[];
  designCount: number;
  asinCount: number;
  orders30: number;
  /** 30天前订单，用于计算增长 */
  prevOrders30: number;
  uploadTime: string;
  uploader: string;
  size: string;
  format: string;
  fileName: string;
  copyright: string;
  tags: string[];
  /** 风险素材标记 */
  risk?: boolean;
  /** 该主素材名下的副素材数量（真实数据） */
  variantCount?: number;
  /** 主素材卡片上直接显示的副素材缩略图（真实数据，按 displayCode 排序） */
  variantPreviews?: { code: string; imageUri: string; variantId: string }[];
  /** 该素材的图片 Asset id（「找相似」用：主素材=previewAsset，副素材=当前Revision asset） */
  assetId?: string;
  /** 素材种类：主素材 MAT-xxxxxx / 副素材（V2 设计包工作流） */
  kind?: 'MAIN' | 'VARIANT';
  /** 副素材所属设计包与版本，用于副素材 Tab 展示 */
  designPackageId?: string;
  designPackageName?: string;
  versionCode?: string;
  /** 副素材对应的主素材编码 */
  mainMaterialCode?: string;
  /** 副素材当前 Revision */
  currentRevision?: number;
  /** 上架版本号（整套设计包统一，仅展示用） */
  batchVersionNo?: number;
  /** 负责人：来自所属设计包（业务上负责这套设计的人），不复制到素材上 */
  responsibleName?: string;
  /** 副素材标签（建版时从主素材复制，之后独立修改） */
  variantTags?: string[];
  /** 副素材 Revision 历史 */
  revisionHistory?: { revisionNo: number; fileName: string; createdAt: string }[];
}

export interface Design {
  id: string;
  /** 设计编码 DES-202609-0182 */
  code: string;
  /** 美工编码 MF-8821 */
  artistCode: string;
  artist: string;
  asinCount: number;
  thumb: string;
  materialId: string;
  updateTime: string;
}

/**
 * 素材详情的「关联设计」条目（真实数据）。
 *
 * 一个设计编码 = 一个设计，可对应多个设计包（同一设计的新一版）；
 * 关联关系来自「设计包位置 → MAT」，因此这里是按 designCode 聚合后的结果。
 */
export interface MaterialDesignRef {
  id: string;
  /** 设计编码：上传设计包时由用户填写 */
  designCode: string;
  /** 设计美工姓名（设计包上的长期属性） */
  designerName: string;
  /** 该设计下引用这个 MAT 的位置数 */
  positionCount: number;
  /** 该设计下这个 MAT 的副素材数 */
  variantCount: number;
  latestUpdate: string;
  /** 涉及的（设计包, 位置）明细 */
  packages: { packageId: string; packageName: string; position: number }[];
}

export interface Asin {
  id: string;
  site: string;
  childAsin: string;
  shop: string;
  status: '在售' | '停售' | '审核中' | '跟卖中';
  orders30: number;
  designCode: string;
}

export interface MaterialFilterState {
  keyword: string;
  /** 一级分类，如「底纹」；空串表示全部素材 */
  category: string;
  /** 二级子类，如「球迷」；空串表示该分类下全部 */
  subCategory: string;
  source: string;
  type: string;
  style: string;
  craft: string;
  area: string;
  usage: UsageFilter;
  sort: SortKey;
  /** 适用场景多选 */
  scenes: string[];
  /** 球迷模式下的联盟多选 */
  leagues: string[];
  quickTab: QuickTabKey;
}
