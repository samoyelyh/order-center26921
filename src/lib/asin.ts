// ============================================================================
// ASIN 工具
//
// 第二十一条：Parent / Child ASIN 跳转 URL 不在组件里写死站点，统一走站点映射。
// 第二十二条：ASIN 本身即唯一业务标识 —— 站点/店铺不参与唯一键，
//            站点只用于生成 listingUrl，缺失站点不影响 ASIN 保存。
// 第十八条：批量粘贴需 trim、去空行、去重，并提示识别到的 Child ASIN 数量。
// ============================================================================

import type { MarketplaceSiteCode } from '@/types/material-workflow'

export interface SiteMeta {
  code: MarketplaceSiteCode
  label: string
  domain: string
}

/** 站点映射表：新增站点只改这里，组件不写死域名 */
export const AMAZON_SITES: SiteMeta[] = [
  { code: 'US', label: '美国站', domain: 'amazon.com' },
  { code: 'UK', label: '英国站', domain: 'amazon.co.uk' },
  { code: 'DE', label: '德国站', domain: 'amazon.de' },
  { code: 'FR', label: '法国站', domain: 'amazon.fr' },
  { code: 'IT', label: '意大利站', domain: 'amazon.it' },
  { code: 'ES', label: '西班牙站', domain: 'amazon.es' },
  { code: 'JP', label: '日本站', domain: 'amazon.co.jp' },
  { code: 'CA', label: '加拿大站', domain: 'amazon.ca' },
  { code: 'MX', label: '墨西哥站', domain: 'amazon.com.mx' },
  { code: 'AU', label: '澳洲站', domain: 'amazon.com.au' },
]

export const DEFAULT_SITE: MarketplaceSiteCode = 'US'

export function siteMeta(site?: MarketplaceSiteCode): SiteMeta {
  return AMAZON_SITES.find((s) => s.code === site) ?? AMAZON_SITES[0]
}

/**
 * 由 ASIN + 站点生成可跳转 URL。
 * 站点缺省时回落到默认站点，仅影响链接可读性，不影响 ASIN 业务身份。
 */
export function buildAsinUrl(asin: string, site?: MarketplaceSiteCode): string {
  const code = asin.trim().toUpperCase()
  if (!code) return ''
  return `https://www.${siteMeta(site).domain}/dp/${code}`
}

/** listing 链接优先用已保存的 URL，其次按站点推导 */
export function resolveListingUrl(input: { asin: string; listingUrl?: string; site?: MarketplaceSiteCode }): string {
  if (input.listingUrl) return input.listingUrl
  return buildAsinUrl(input.asin, input.site)
}

export const ASIN_PATTERN = /^B0[A-Z0-9]{8}$/

export function isValidAsin(value: string): boolean {
  return ASIN_PATTERN.test(value.trim().toUpperCase())
}

export interface ParsedAsinList {
  /** 最终有效列表（已 trim / 去空行 / 去重 / 大写） */
  asins: string[]
  /** 原始有效行数 */
  rawCount: number
  /** 被去掉的重复数量 */
  duplicateCount: number
  /** 格式不正确的条目 */
  invalid: string[]
  /** 重复的条目 */
  duplicates: string[]
}

/** 支持一行一个 / Excel 整列粘贴（含制表符、逗号、分号、空格分隔） */
export function parseAsinList(text: string): ParsedAsinList {
  const raw = text
    .split(/[\r\n]+/)
    .flatMap((line) => line.split(/[\t,;]+/))
    .map((value) => value.trim().toUpperCase())
    .filter(Boolean)

  const seen = new Set<string>()
  const asins: string[] = []
  const duplicates: string[] = []
  const invalid: string[] = []

  for (const value of raw) {
    if (!isValidAsin(value)) {
      invalid.push(value)
      continue
    }
    if (seen.has(value)) {
      duplicates.push(value)
      continue
    }
    seen.add(value)
    asins.push(value)
  }

  return {
    asins,
    rawCount: raw.length,
    duplicateCount: duplicates.length,
    invalid,
    duplicates,
  }
}
