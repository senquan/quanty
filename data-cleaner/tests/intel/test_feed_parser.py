"""feed_parser 单测：RSS 2.0 / Atom 解析、坏 XML、日期、字段提取"""
from app.intel.ingest.feed_parser import parse_date, parse_feed, strip_html

RSS2_STR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>测试源</title>
    <item>
      <title>贵州茅台三季度营收同比增长</title>
      <link>https://example.com/news/123</link>
      <guid>news-123</guid>
      <pubDate>Mon, 07 Sep 2026 04:00:00 GMT</pubDate>
      <dc:creator>张三</dc:creator>
      <content:encoded><![CDATA[<p>贵州茅台(600519)今日发布三季报，营收同比增长15%。</p>]]></content:encoded>
    </item>
    <item>
      <title></title>
      <link></link>
    </item>
  </channel>
</rss>
"""
RSS2 = RSS2_STR.encode("utf-8")

ATOM_STR = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 源</title>
  <entry>
    <title>宁德时代新产能落地</title>
    <link rel="alternate" href="https://example.com/a/1"/>
    <id>urn:uuid:1</id>
    <published>2026-09-07T06:30:00Z</published>
    <author><name>李四</name></author>
    <content type="html">&lt;p&gt;宁德时代宣布新产能投产。&lt;/p&gt;</content>
  </entry>
</feed>
"""
ATOM = ATOM_STR.encode("utf-8")


class TestParseFeed:
    def test_rss2_item_fields(self):
        items = parse_feed(RSS2)
        assert len(items) == 1
        it = items[0]
        assert it["title"] == "贵州茅台三季度营收同比增长"
        assert it["link"] == "https://example.com/news/123"
        assert it["guid"] == "news-123"
        assert it["author"] == "张三"
        assert "营收同比增长15%" in it["content_html"]
        assert it["published"] is not None and it["published"].tzinfo is not None

    def test_rss2_drops_item_without_title_and_link(self):
        # RSS2 里第二个 item 无 title 无 link，应被丢弃
        items = parse_feed(RSS2)
        assert all(i["title"] or i["link"] for i in items)

    def test_atom_entry_fields(self):
        items = parse_feed(ATOM)
        assert len(items) == 1
        it = items[0]
        assert it["title"] == "宁德时代新产能落地"
        assert it["link"] == "https://example.com/a/1"
        assert it["guid"] == "urn:uuid:1"
        assert it["author"] == "李四"
        assert it["published"] is not None

    def test_bad_xml_returns_empty(self):
        assert parse_feed(b"not xml at all") == []
        assert parse_feed(b"") == []

    def test_limit(self):
        items = parse_feed(RSS2, limit=0)
        assert items == []


class TestParseDate:
    def test_rfc822(self):
        dt = parse_date("Mon, 07 Sep 2026 04:00:00 GMT")
        assert dt is not None and dt.year == 2026 and dt.tzinfo is not None

    def test_iso8601_z(self):
        dt = parse_date("2026-09-07T06:30:00Z")
        assert dt is not None and dt.tzinfo is not None

    def test_naive_gets_utc(self):
        dt = parse_date("2026-09-07T06:30:00")
        assert dt is not None and dt.tzinfo is not None

    def test_garbage_returns_none(self):
        assert parse_date("不是日期") is None
        assert parse_date("") is None


class TestStripHtml:
    def test_strips_tags(self):
        assert strip_html("<p>涨<b>停</b></p>") == "涨停"

    def test_empty(self):
        assert strip_html("") == ""
