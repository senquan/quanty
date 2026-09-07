"""L0 摄取层（占位）

后续阶段实现，分别对应设计文档 §9 的摄取源类型：
  - rss.py      RSS 源抓取（参考 Vibe-Research sources/rss.py 的并发 + 单源失败隔离）
  - web.py      单页/列表抓取（东方财富等无 RSS 源）
  - manual.py   人工投喂（URL 列表 / 目录 watch），公众号主路径的入口
  - wechat.py   微信公众号半自动摄取（P4，官方接口/搜狗均不可行，主路径是 manual）
"""
