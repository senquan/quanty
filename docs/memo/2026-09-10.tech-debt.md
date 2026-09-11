# 技术债务追踪（lab.Quant）

> 仅记录已确认、暂不修复、需后续排期偿还的债务。每条含：现象、根因、风险、建议偿还方式、登记时间。

---

## TD-001 · 后端直读 data-cleaner 的 intel 库

- **登记时间**：2026-09-07
- **现象**：`backend/app/api/api_v1/endpoints/intel.py` 通过 `get_db` 直接 `SELECT ... FROM intel.doc_mentions / documents / sources / author_profiles`。后端 `.env` 与 `data-cleaner/.env` 的 `DATABASE_URL` **完全相同**（`postgresql+asyncpg://...@127.0.0.1:5432/quant`），即后端与 dc 共用同一 Postgres 库的 `intel` schema。
- **根因**：图省事，intel 数据由 dc 落库到共享 `quant` 库，后端未走 HTTP 网关，而是直接读 dc 的内部 schema。
- **风险**：
  1. **强耦合**：后端依赖 dc 的 `intel.*` 表结构与列名；dc 改 schema 会直接 break 后端接口。
  2. **职责边界模糊**：dc 是数据生产方，却无清晰的"对外契约"；后端成了 dc 库的另一个 owner。
  3. **多租户/多实例隐患**：若将来 dc 与 backend 拆库或分实例，接口静默失效。
- **正确架构（建议偿还）**：dc 把 intel 产出通过**自有 HTTP 接口**（或消息）对外暴露；backend 经 HTTP 消费（与 P2-4"经 backend 网关，不直连 intel"的**前端**约束一致，但当前后端这层仍直连了库）。
- **当前决定**：**不改代码**（用户 2026-09-07 明确：先记债务，暂不修）。待 dc 有稳定对外接口后再解耦。
