"""去重：content_hash 精确去重 + simhash 转载/洗稿识别

- content_hash: sha256(原文 HTML bytes)，精确去重 + 变更检测；
  同时是原文落盘文件名（验收：raw_path 文件内容与本 hash 一致）
- simhash: 64 位，字符/词混合 2-gram shingle，自实现零依赖（不引 simhash/jieba 库）。
  中文按相邻字对、英文按词——"同文改标题"与"洗稿换词"shingle 仍高度重叠，
  两篇不同文章重叠度天然低。汉明距离 ≤ 阈值（默认 3）判转载。

短文本保护：shingle 过少时 simhash 返回 0，is_duplicate 对 0 一律不判重——
短摘要类条目（如快讯）不做转载判定，避免误杀。
"""
from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"[一-鿿]|[a-zA-Z0-9]+")
_ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")

# 低于此 shingle 数，simhash 不可靠（返回 0，不参与转载判定）
_MIN_SHINGLES = 8


def content_hash(raw: str | bytes) -> str:
    """sha256 十六进制，精确去重键 + 原文落盘文件名"""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    return hashlib.sha256(data).hexdigest()


def _shingles(text: str, n: int = 2) -> list[str]:
    """字符/词混合 n-gram shingle：中文相邻字对，英文整词"""
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return []
    units = [tok.lower() if _ASCII_WORD_RE.fullmatch(tok) else tok for tok in tokens]
    if len(units) < n:
        return units
    return ["|".join(units[i:i + n]) for i in range(len(units) - n + 1)]


def _hash64(s: str) -> int:
    return int.from_bytes(hashlib.md5(s.encode("utf-8")).digest()[:8], "big")


def simhash(text: str) -> int:
    """64 位 simhash。空/过短文本返回 0（不参与转载判定）"""
    shingles = _shingles(text)
    if len(shingles) < _MIN_SHINGLES:
        return 0
    v = [0] * 64
    for sh in shingles:
        h = _hash64(sh)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def hamming(a: int, b: int) -> int:
    """64 位汉明距离。mask 住 64 位：即使传入有符号表示（负数）也不受符号扩展干扰"""
    return bin((a ^ b) & _U64_MASK).count("1")


def is_duplicate(sim_a: int, sim_b: int, threshold: int = 3) -> bool:
    """汉明距离 ≤ 阈值判转载/洗稿；任一 simhash 为 0（空/过短文本）不判重"""
    if sim_a == 0 or sim_b == 0:
        return False
    return hamming(sim_a, sim_b) <= threshold


# ---- 64 位无符号 ↔ PG BIGINT 有符号 的存储边界转换 ----
# simhash 是无符号 64 位（0 .. 2^64-1），而 PG BIGINT 上限 2^63-1；
# Python 侧一律无符号，仅在 store 读写数据库时转换（hamming 等比较全部在无符号域）。
_U64_MASK = (1 << 64) - 1


def to_signed64(v: int) -> int:
    """无符号 64 位 → 有符号 64 位（PG BIGINT 可存）"""
    return v - (1 << 64) if v >= (1 << 63) else v


def to_unsigned64(v: int) -> int:
    """有符号 64 位（PG BIGINT 读出）→ 无符号 64 位"""
    return v + (1 << 64) if v < 0 else v
