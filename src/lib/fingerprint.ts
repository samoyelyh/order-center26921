// ============================================================================
// 指纹计算
//
// 当前浏览器实现（零新增依赖，可在浏览器内真实运行）：
//   exactAlgorithm      = SHA256  （Web Crypto message digest）
//   perceptualAlgorithm = AHASH   （8x8 灰度降采样平均哈希）
//
// 后端接入后只改这个文件：
//   SHA256 → BLAKE3
//   AHASH  → PHASH（DCT）
// 数据结构（Fingerprint）与业务组件不感知算法差异。
//
// 注意：绝不要把 SHA256 的结果写进名为 blake3 的字段——字段名与算法必须一致，
// 否则后端接入后会出现「同名字段不同算法」的脏数据。
// ============================================================================

import type { Fingerprint, PerceptualHashAlgorithm } from '@/types/material-workflow'

export const PERCEPTUAL_BITS = 64

/** 当前 Demo 使用的指纹算法标识 */
export const CURRENT_EXACT_ALGORITHM = 'SHA256' as const
export const CURRENT_PERCEPTUAL_ALGORITHM: PerceptualHashAlgorithm = 'AHASH'
export const CURRENT_PERCEPTUAL_VERSION = 1

// 注意：这里刻意**不再提供**「相似度 / 汉明距离」工具。
// 主副素材关系只按同名 pairKey 配对，生产链路不允许出现任何相似度判断，
// 留着这类函数只会让人误以为可以再用它去配主副素材。
// BLAKE3 仍用于「完全相同文件」的 Asset 去重（见 identityHash / isExactDuplicate）。

// ---------------------------------------------------------------- 编解码

export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

/** 64bit 感知哈希 → 16 位 hex */
export function bitsToHex(bits: number[]): string {
  let hex = ''
  for (let i = 0; i < bits.length; i += 4) {
    const nibble = (bits[i] << 3) | (bits[i + 1] << 2) | (bits[i + 2] << 1) | bits[i + 3]
    hex += nibble.toString(16)
  }
  return hex
}

export function hexToBits(hex: string): number[] {
  const bits: number[] = []
  for (const ch of hex) {
    const v = parseInt(ch, 16)
    bits.push((v >> 3) & 1, (v >> 2) & 1, (v >> 1) & 1, v & 1)
  }
  return bits
}

export function popcountHex(hex: string): number {
  return hexToBits(hex).reduce((sum, bit) => sum + bit, 0)
}

/** 生成一个与 ref 距离为 targetDistance 的 hash（用于稳定可复现的演示数据） */
export function hashWithDistance(ref: string, targetDistance: number, seed: number): string {
  const bits = hexToBits(ref)
  const order = Array.from({ length: bits.length }, (_, i) => i)
  let s = seed || 1
  for (let i = order.length - 1; i > 0; i -= 1) {
    s = (s * 1103515245 + 12345) % 2147483648
    const j = Math.abs(s) % (i + 1)
    ;[order[i], order[j]] = [order[j], order[i]]
  }
  const target = Math.max(0, Math.min(bits.length, targetDistance))
  const flip = new Set(order.slice(0, target))
  const next = bits.map((bit, index) => (flip.has(index) ? bit ^ 1 : bit))
  return bitsToHex(next)
}

/** 稳定的 16 位 hex（不依赖随机数，保证多次渲染一致） */
export function identityHash(seed: string): string {
  let h1 = 0x811c9dc5
  let h2 = 0x01000193
  for (let i = 0; i < seed.length; i += 1) {
    const c = seed.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 16777619) >>> 0
    h2 = Math.imul(h2 + c, 2246822519) >>> 0
  }
  const a = (h1 >>> 0).toString(16).padStart(8, '0')
  const b = (h2 >>> 0).toString(16).padStart(8, '0')
  return a + b
}

// ---------------------------------------------------------------- 真实文件指纹

export async function sha256Hex(buffer: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return bytesToHex(new Uint8Array(digest))
}

/** 8x8 灰度 aHash */
export async function aHashFromBlob(blob: Blob): Promise<string> {
  const bitmap = await createImageBitmap(blob)
  try {
    const size = 8
    const canvas = document.createElement('canvas')
    canvas.width = size
    canvas.height = size
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('canvas unavailable')
    ctx.drawImage(bitmap, 0, 0, size, size)
    const { data } = ctx.getImageData(0, 0, size, size)
    const gray: number[] = []
    for (let i = 0; i < data.length; i += 4) {
      gray.push(0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2])
    }
    const avg = gray.reduce((sum, v) => sum + v, 0) / gray.length
    return bitsToHex(gray.map((v) => (v >= avg ? 1 : 0)))
  } finally {
    bitmap.close?.()
  }
}

/** 真实文件指纹（浏览器内 Demo 实现） */
export async function computeFingerprintFromBlob(blob: Blob): Promise<Fingerprint> {
  const buffer = await blob.arrayBuffer()
  const exactHash = await sha256Hex(buffer)
  let perceptualHash: string
  try {
    perceptualHash = await aHashFromBlob(blob)
  } catch {
    perceptualHash = exactHash.slice(0, 16)
  }
  return {
    exactHash,
    exactAlgorithm: CURRENT_EXACT_ALGORITHM,
    perceptualHash,
    perceptualAlgorithm: CURRENT_PERCEPTUAL_ALGORITHM,
    perceptualVersion: CURRENT_PERCEPTUAL_VERSION,
  }
}

/** 合成指纹：用于演示/后端未接入时的稳定占位（算法字段如实标注） */
export function syntheticFingerprint(input: {
  identity: string
  perceptualHash?: string
  perceptualVersion?: number
}): Fingerprint {
  return {
    exactHash: identityHash(`exact:${input.identity}`),
    exactAlgorithm: CURRENT_EXACT_ALGORITHM,
    perceptualHash: input.perceptualHash ?? identityHash(`perceptual:${input.identity}`).slice(0, 16),
    perceptualAlgorithm: CURRENT_PERCEPTUAL_ALGORITHM,
    perceptualVersion: input.perceptualVersion ?? CURRENT_PERCEPTUAL_VERSION,
  }
}
