-- 018: 画像版本化（D-9）—— 加 as_of 知识截止日，禁止同版本覆盖
--
-- 背景（2026-09-10 审计 D-9）：
--   intel.author_profiles 的唯一键是 (profile_key, profile_type, profile_version)，
--   而 profile_version 恒为常量 'v1' ⇒ 每日重算永远命中同一行、**原地覆盖**老值。
--   后果：历史截面不可复现 —— 09-07/09-08 的因子行里 INTL_AUTHOR_CONVICTION 会随
--   每一次画像重算而改变（实测 0.9612 → 0.5683 → 0.8881）。回测无法复现。
--
-- 修法：引入 as_of（画像的"知识截止日"，= 参与聚合的最后一天），并把它并入唯一键：
--       (profile_key, profile_type, profile_version, as_of)
--   于是「同一天重算」= 幂等覆盖（相同 as_of，覆盖无副作用），
--        「不同天重算」= 新增一行（历史保留，可回溯）。
--
-- ⚠️ 本文件若经 SQLAlchemy text() 执行，注释里禁止出现 "冒号+紧跟字母/数字"
--    （如 count:8），会被解析成 bind parameter。已统一用 " = " 书写。
--    推荐改用 app.intel.store.run_sql_file()（原生执行，绕开 bind 解析）。

-- 1) 加列：as_of 初始回填为 computed_at 的日期（存量行的"知识截止日"）
ALTER TABLE intel.author_profiles
    ADD COLUMN IF NOT EXISTS as_of DATE;

UPDATE intel.author_profiles
   SET as_of = (computed_at AT TIME ZONE 'Asia/Shanghai')::date
 WHERE as_of IS NULL;

-- 存量行回填后，as_of 必有值；但仍保留 DEFAULT 兜底（新写入若漏传不至于 NULL 破坏唯一键）
ALTER TABLE intel.author_profiles
    ALTER COLUMN as_of SET DEFAULT (now() AT TIME ZONE 'Asia/Shanghai')::date;

-- ⚠️ 唯一键里的日期列若可为 NULL，则 NULL 不参与冲突判定 ⇒ 版本化失效。
--    因此置 NOT NULL。
ALTER TABLE intel.author_profiles
    ALTER COLUMN as_of SET NOT NULL;

-- 2) 唯一索引重建：并入 as_of
DROP INDEX IF EXISTS intel.idx_intel_profiles_key_ver;
CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_profiles_key_ver_asof
    ON intel.author_profiles(profile_key, profile_type, profile_version, as_of);

-- 3) 查询辅助索引：取"某 key 最新 as_of"是最常见读法
CREATE INDEX IF NOT EXISTS idx_intel_profiles_key_asof
    ON intel.author_profiles(profile_key, as_of DESC);
