"""normalize/text 单测：HTML 剥离 + markdown 清洗 + canonical_url 规范化"""
from app.intel.normalize.dedupe import hamming, simhash
from app.intel.normalize.text import canonical_url, normalize_text


class TestNormalizeText:
    def test_strips_tags_and_entities(self):
        assert normalize_text("<p>茅台 &amp; 五粮液</p>") == "茅台 & 五粮液"

    def test_collapses_whitespace(self):
        # 块级标签（div）换空格 → 空白压缩；"增长 15%" 之间保留块边界空格
        assert normalize_text("<div>营收\n\n  增长</div>15%") == "营收 增长 15%"

    def test_inline_tags_removed_without_space(self):
        # 行内标签直接删：英文单词不碎裂
        assert normalize_text("<p>net<b>profit</b> rose</p>") == "netprofit rose"

    def test_empty(self):
        assert normalize_text("") == ""
        assert normalize_text(None) == ""


class TestMarkdownResidueCleanup:
    """D-10：微信导出的 .md 副本会让 URL / markdown 标记残渣混进正文污染 simhash。

    同一篇文章的 .html / .md / .mhtml 三副本，因 md 版保留 `![](url)` 图片语法，
    曾产生 9 位汉明距离（> 阈值 3）导致转载识别漏判。以下用例钉死修复。
    """

    def test_drops_markdown_image_url_keeps_alt(self):
        md = "看这张图 ![账户持仓](https://mmbiz.qpic.cn/mmbiz_png/AbCdEf/640?wx_fmt=png) 就知道了"
        out = normalize_text(md)
        assert "mmbiz.qpic.cn" not in out
        assert "wx_fmt" not in out
        assert "账户持仓" in out

    def test_drops_markdown_link_url_keeps_text(self):
        out = normalize_text("[点这里](https://example.com/a/b?from=appmsg) 阅读全文")
        assert "example.com" not in out
        assert "点这里" in out

    def test_drops_bare_url_and_domain(self):
        out = normalize_text("来源 https://res.wx.qq.com/t/wx_fed/a.png 与 mmbiz.qpic.cn/x/640 均无关")
        assert "res.wx.qq.com" not in out
        assert "mmbiz.qpic.cn" not in out
        assert "均无关" in out

    def test_drops_markdown_markup_chars(self):
        # `# ` / `_斜体_` / `**粗**` 会把中文 2-gram 切碎 → 一律清成空格
        out = normalize_text("# 标题\n_2026年09月05日_ __ _ _ _ 上海 走起")
        assert "#" not in out and "_" not in out
        assert "标题" in out and "上海 走起" in out

    def test_strips_wechat_boilerplate_tail(self):
        # 「预览时标签不可点」起的全是客户端 UI 文案，同文的 html/md 副本措辞不一致
        html_v = "正文结束。 预览时标签不可点 微信扫一扫 关注该公众号 知道了 分享 留言 收藏"
        md_v = "正文结束。 预览时标签不可点 微信扫一扫 知道了 图片 ~ 作者头像"
        assert normalize_text(html_v) == normalize_text(md_v) == "正文结束。"

    def test_three_format_copies_converge(self):
        """核心回归：同文三格式（html / md / mhtml）simhash 汉明距离 ≤ 阈值 3

        用真实微信导出结构的缩样：html 副本图在标签里、md 副本图是 markdown 语法。
        文本须足够长（simhash 对 <100 字短文本天然敏感，短样本距离偏大不代表漏判）。
        """
        body = (
            "森哥分红养老周记（26.9.5，调仓，煤价） 原创 散户森 分红养老之路 "
            "本周科技震荡红利继续稳步上涨，部分弱周期红利也开始连续上攻补涨，"
            "总的来说红利整体的性价比在降低了，肉眼可见一眼低估的黄金坑几乎绝迹。"
            "目前阶段我们红利投资更多的就是躺平吃息，坐看市值每天增长，很惬意也很无聊，"
            "遇到涨的多的根据自己的预期收益、风险偏好、仓位情况做一些网格化减仓就行。"
            "本周自己也是继续降低了部分企业的仓位，提升现金比例。本周股权积攒方面，"
            "父母养老账户本周有攒股，本周49左右分批减仓神华，5.45减仓国电，41.6减仓招行，"
            "少量低吸藏格。子女教育基金本周无攒股，港美股账户本周有操作，重新建仓了点海油H吃息。"
            "账户持仓上，父母分红养老账户保持红利打底、适度分散的原则，"
            "个股仓位上限控制在两成以内，避免单一标的波动对净值造成过大冲击。"
        )
        tail = " 预览时标签不可点 微信扫一扫 知道了"

        html_copy = (
            f"<div><p>{body}</p>"
            "<img src='https://mmbiz.qpic.cn/mmbiz_png/AbCdEf/640?wx_fmt=png&from=appmsg'>"
            f"<p>{tail}</p></div>"
        )
        md_copy = (
            f"# {body} "
            "![](https://mmbiz.qpic.cn/mmbiz_png/AbCdEf/640?wx_fmt=png&from=appmsg) "
            f"_2026年09月05日_ {tail}"
        )

        s_html = simhash(normalize_text(html_copy))
        s_md = simhash(normalize_text(md_copy))
        assert s_html != 0 and s_md != 0
        assert hamming(s_html, s_md) <= 3, f"三副本未收敛，汉明距离={hamming(s_html, s_md)}"

    def test_does_not_merge_unrelated_articles(self):
        """反例保护：正文不同的两篇文章，清洗后 simhash 仍应相距远（不误判转载）"""
        a = "贵州茅台发布半年报，营收同比增长百分之十五，净利润创历史新高，直销渠道占比提升。"
        b = "宁德时代宣布新一代电池量产，能量密度提升百分之二十，成本下降，海外工厂投产在即。"
        sa, sb = simhash(normalize_text(a)), simhash(normalize_text(b))
        assert sa != 0 and sb != 0
        assert hamming(sa, sb) > 3


class TestCanonicalUrl:
    def test_strips_utm(self):
        a = canonical_url("https://example.com/news/1?utm_source=feed&utm_medium=rss")
        assert a == "https://example.com/news/1"

    def test_strips_tracking_params_keeps_real(self):
        a = canonical_url("https://example.com/n?id=123&spm=a1b2&t=999")
        assert a == "https://example.com/n?id=123"

    def test_strips_fragment_and_trailing_slash(self):
        a = canonical_url("https://example.com/news/1/#comment")
        assert a == "https://example.com/news/1"

    def test_variant_urls_collapse(self):
        """同一文章的分享变体归并为同一 canonical（转载去重键）"""
        a = canonical_url("https://example.com/news/1?utm_source=a&from=timeline")
        b = canonical_url("https://example.com/news/1?utm_campaign=b#p2")
        assert a == b

    def test_case_insensitive_host(self):
        assert canonical_url("HTTPS://Example.COM/News/1") == "https://example.com/News/1"

    def test_empty(self):
        assert canonical_url("") == ""
