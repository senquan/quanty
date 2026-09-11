"""prescreen 单测（P1-2）：标的识别 + 事件关键词 + 否决逻辑"""
from app.intel.understand import keywords as kw
from app.intel.understand.prescreen import AliasIndex, _normalize_code, prescreen

# 离线索引（不依赖 DB）
IDX = AliasIndex([
    ("600519", "600519.SH"), ("贵州茅台", "600519.SH"), ("茅台", "600519.SH"),
    ("五粮液", "000858.SZ"), ("宁德时代", "300750.SZ"), ("宁德", "300750.SZ"),
    ("000858.SZ", "000858.SZ"),
])


class TestCodeRegex:
    def test_bare_code(self):
        syms, by = None, None
        from app.intel.understand.prescreen import find_symbols
        syms, by = find_symbols("600519 今日大涨", IDX)
        assert "600519.SH" in syms and by["600519.SH"] == "600519"

    def test_code_with_suffix(self):
        from app.intel.understand.prescreen import find_symbols
        syms, _ = find_symbols("建议关注 000858.SZ", IDX)
        assert "000858.SZ" in syms

    def test_longer_digits_not_code(self):
        from app.intel.understand.prescreen import find_symbols
        syms, _ = find_symbols("订单编号 1234567890", IDX)
        assert not any(s.startswith("123456") for s in syms)

    def test_exchange_inference(self):
        assert _normalize_code("600519", None) == "600519.SH"
        assert _normalize_code("000858", None) == "000858.SZ"
        assert _normalize_code("300750", None) == "300750.SZ"
        assert _normalize_code("830799", None) == "830799.BJ"

    def test_long_alias_wins(self):
        """'贵州茅台' 不应被短别名 '茅台' 截断出错误归因（两者同 symbol，这里测顺序稳定）"""
        res = prescreen("贵州茅台发布业绩", None, IDX)
        assert "600519.SH" in res.symbols


class TestPrescreen:
    def test_symbol_hit(self):
        res = prescreen("茅台三季度业绩超预期", None, IDX)
        assert res.hit and res.symbols == ["600519.SH"] and res.reason == "symbol"

    def test_event_only_hit(self):
        text = ("工信部发布新能源产业政策征求意见稿，行业格局或将重塑。")
        res = prescreen(text, None, IDX)
        assert res.hit and not res.symbols and res.reason == "event"
        assert "政策监管" in res.events

    def test_miss(self):
        res = prescreen("今天天气不错，适合出门徒步，山间空气清新。", None, IDX)
        assert not res.hit and res.reason == "miss"

    def test_symbol_and_event(self):
        res = prescreen("茅台发布业绩预告，净利润同比增长18%", None, IDX)
        assert res.reason == "symbol+event"
        assert "业绩" in res.events


class TestKeywordClauses:
    def test_negatives_same_clause_veto(self):
        """同一子句：否决词生效 → '终止收购' 不算并购"""
        assert "并购重组" not in kw.match_events("公司宣布终止收购标的股权的事项")

    def test_negatives_other_clause_no_veto(self):
        """不同子句：并列事项互不否决"""
        text = "公司完成收购A公司。此前市场上曾有传言称不会收购。"
        assert "并购重组" in kw.match_events(text)

    def test_english_word_boundary(self):
        assert "机构观点" in kw.match_events("Morgan Stanley raised its price target")

    def test_regex_keyword(self):
        assert "订单合同" in kw.match_events("公司与客户签订重大合同公告")
        assert "订单合同" not in kw.match_events("签订意向后再谈")

    def test_empty(self):
        assert kw.match_events("") == []
