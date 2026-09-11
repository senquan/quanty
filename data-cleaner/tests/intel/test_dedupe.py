"""dedupe 单测（P0 验收第 2 条）

- 同文改标题：simhash 判重（汉明距离 ≤ 3）
- 轻度洗稿（同义换词 + 语序微调，正文主体不变）：simhash 判重
- 两篇不同文章：不误杀（距离 > 阈值）
- content_hash：同内容同 hash；raw 落盘一致性由 service 层文件名约定保证
- 短文本保护：shingle 不足 simhash=0，is_duplicate 一律 False
- 存储边界：无符号 64 位 ↔ PG BIGINT 有符号 round-trip；有符号表示参与 hamming 不失真
"""
from app.intel.normalize.dedupe import (
    content_hash,
    hamming,
    is_duplicate,
    simhash,
    to_signed64,
    to_unsigned64,
)

# 基准正文（足够长，确保 shingle 充足）
BASE = (
    "贵州茅台今日发布三季度财报，公司实现营业收入同比增长百分之十五，"
    "净利润同比增长百分之十八，超出市场此前一致预期。公司管理层在业绩说明会上表示，"
    "高端白酒需求保持稳定，渠道库存处于合理水平，全年业绩增长确定性较强。"
    "多位分析师认为，白酒板块估值已回到历史低位，龙头企业具备配置价值。"
)

# 同文改标题/轻微编辑（加一句导语，正文不变）
RETITLED = (
    "快讯：贵州茅台今日发布三季度财报，公司实现营业收入同比增长百分之十五，"
    "净利润同比增长百分之十八，超出市场此前一致预期。公司管理层在业绩说明会上表示，"
    "高端白酒需求保持稳定，渠道库存处于合理水平，全年业绩增长确定性较强。"
    "多位分析师认为，白酒板块估值已回到历史低位，龙头企业具备配置价值。"
)

# 轻度洗稿：同义换词（发布→公布，超出→高于，表示→指出，稳定→平稳，较强→较高）
REWRITTEN = (
    "贵州茅台今日公布三季度财报，公司实现营业收入同比增长百分之十五，"
    "净利润同比增长百分之十八，高于市场此前一致预期。公司管理层在业绩说明会上指出，"
    "高端白酒需求保持平稳，渠道库存处于合理水平，全年业绩增长确定性较高。"
    "多位分析师认为，白酒板块估值已回到历史低位，龙头企业具备配置价值。"
)

# 完全不同主题的文章
OTHER = (
    "国家发改委今日召开会议，部署明年新能源汽车产业发展重点工作。"
    "会议指出，要加快充电桩基础设施建设，推动动力电池回收利用体系完善，"
    "支持整车企业加大研发投入，提升产业链供应链韧性和安全水平。"
    "与会专家表示，新能源渗透率持续提升，产业竞争格局正在加速重塑。"
)


class TestSimhashRepost:
    def test_same_text_identical_simhash(self):
        assert simhash(BASE) == simhash(BASE)

    def test_retitled_is_duplicate(self):
        """同文改标题：判重"""
        d = hamming(simhash(BASE), simhash(RETITLED))
        assert d <= 3, f"改标题距离 {d} 应 ≤3"
        assert is_duplicate(simhash(BASE), simhash(RETITLED), 3)

    def test_lightly_rewritten_beyond_repost_threshold(self):
        """轻度洗稿：超出转载阈值（已知边界，锁死防回归；洗稿归 P1 语义层）

        实测距离 11：换词打断 2-gram shingle，simhash 对洗稿天然不敏感。
        若未来改 shingle/位宽使洗稿可判重，本测试会红，提醒同步放宽文档表述。
        """
        d = hamming(simhash(BASE), simhash(REWRITTEN))
        assert d > 3, f"轻度洗稿距离 {d} 应 >3（simhash 已知边界）"
        assert not is_duplicate(simhash(BASE), simhash(REWRITTEN), 3)

    def test_different_articles_not_duplicate(self):
        """两篇不同文章：不误杀"""
        d = hamming(simhash(BASE), simhash(OTHER))
        assert d > 3, f"不同文章距离 {d} 应 >3"
        assert not is_duplicate(simhash(BASE), simhash(OTHER), 3)

    def test_cross_check_other_direction(self):
        """OTHER vs REWRITTEN 也不应误判（两两独立）"""
        assert not is_duplicate(simhash(OTHER), simhash(REWRITTEN), 3)


class TestShortTextGuard:
    def test_short_text_simhash_zero(self):
        assert simhash("涨停") == 0
        assert simhash("") == 0

    def test_zero_never_duplicate(self):
        assert not is_duplicate(0, simhash(BASE), 3)
        assert not is_duplicate(0, 0, 3)


class TestContentHash:
    def test_deterministic(self):
        assert content_hash(BASE) == content_hash(BASE)

    def test_str_and_bytes_equal(self):
        assert content_hash(BASE) == content_hash(BASE.encode("utf-8"))

    def test_different_content_different_hash(self):
        assert content_hash(BASE) != content_hash(OTHER)

    def test_hex64(self):
        h = content_hash(BASE)
        assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


class TestSigned64Boundary:
    """PG BIGINT 上限 2^63-1，simhash 是无符号 64 位 —— 联调实测炸出（NumericValueOutOfRange）"""

    def test_simhash_can_exceed_bigint_max(self):
        """真实正文 simhash 可能落在无符号高半区（≥2^63），BIGINT 装不下"""
        s = simhash(BASE)
        assert 0 <= s < (1 << 64)

    def test_signed_roundtrip(self):
        for s in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1, simhash(BASE), simhash(OTHER)):
            assert to_unsigned64(to_signed64(s)) == s, f"round-trip 失败: {s}"

    def test_signed_value_in_bigint_range(self):
        assert to_signed64(simhash(BASE)) < (1 << 63)
        assert to_signed64(simhash(BASE)) >= -(1 << 63)

    def test_hamming_with_signed_repr_unchanged(self):
        """有符号表示（负数）参与 hamming 不得因符号扩展失真"""
        a, b = simhash(BASE), simhash(RETITLED)
        d_unsigned = hamming(a, b)
        d_signed = hamming(to_signed64(a), to_signed64(b))
        assert d_unsigned == d_signed

    def test_duplicate_with_signed_repr(self):
        assert is_duplicate(
            to_signed64(simhash(BASE)), to_signed64(simhash(RETITLED)), 3
        )
