"""L1 规范化层：正文抽取 / 精确去重 / 转载识别 / 红线标记

- text.py:     HTML → 规范化纯文本；canonical_url 规范化（转载去重键）
- dedupe.py:   content_hash 精确去重 + simhash 转载/洗稿识别（阈值可配）
- redline.py:  红线关键词标记（只标记不删除，保留审计可见性）
"""
