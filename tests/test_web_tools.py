"""web_search / fetch_url MCP tools (LOCAL_LLM.md lesson 9) — providers and network mocked."""
from __future__ import annotations

import json
import socket

import pytest

import config
from mcp_server import server
from mcp_server import web_tools as web


def hits(n, prefix="r", snippet="x" * 170):
    return [{"title": f"{prefix} title {i}", "url": f"https://news.example.com/{prefix}/{i}", "source": "Example",
             "date": "2026-11-19T10:00+00:00", "snippet": snippet} for i in range(n)]


@pytest.fixture()
def providers(monkeypatch, fresh_db):
    calls = []

    def make(name, result):
        def fn(q, limit):
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return result[:limit]
        return name, fn

    def set_(*specs):
        monkeypatch.setattr(web, "PROVIDERS", [make(n, r) for n, r in specs])
        return calls

    return set_


def test_rss_first_with_as_of_and_size_budget(providers):
    calls = providers(("bing_news_rss", hits(8)), ("duckduckgo", hits(3, "d")))
    out = server.web_search("NVIDIA earnings guidance", 8)
    assert "as_of" in out and out["data"]["provider"] == "bing_news_rss" and calls == ["bing_news_rss"]
    assert len(json.dumps(out, separators=(",", ":"), ensure_ascii=False)) <= config.MAX_TOOL_BYTES
    assert out["data"]["count"] == len(out["data"]["results"]) >= 4
    assert out["data"]["results"][0]["snippet"]                     # earliest results keep their snippet


def test_falls_back_to_duckduckgo(providers):
    calls = providers(("bing_news_rss", RuntimeError("blocked")), ("google_news_rss", []), ("duckduckgo", hits(3, "d")))
    out = server.web_search("gold real yields", 6)
    assert out["data"]["provider"] == "duckduckgo" and calls == ["bing_news_rss", "google_news_rss", "duckduckgo"]


def test_all_providers_down_is_an_error_envelope(providers):
    providers(("bing_news_rss", RuntimeError("x")), ("duckduckgo", []))
    out = server.web_search("anything")
    assert out["data"] is None and "no search provider" in out["error"] and "as_of" in out


def test_results_are_cached(providers):
    calls = providers(("bing_news_rss", hits(3)))
    server.web_search("btc etf flows")
    server.web_search("BTC ETF flows ")     # same query, different case/whitespace → cache hit
    assert calls == ["bing_news_rss"]


def test_empty_query_rejected(providers):
    providers(("bing_news_rss", hits(1)))
    assert "empty query" in server.web_search("  ")["error"]


def test_bing_link_is_decoded(monkeypatch):
    rss = b"""<?xml version="1.0"?><rss xmlns:news="https://www.bing.com/news/search?q=x&amp;format=rss"><channel>
      <item><title>NVDA &amp; AI</title>
      <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fexample.com%2fa%3fb%3d1&amp;c=2</link>
      <description>Revenue &lt;b&gt;doubled&lt;/b&gt;</description><pubDate>Thu, 19 Nov 2026 02:01:00 GMT</pubDate>
      <news:Source>Example News</news:Source></item></channel></rss>"""

    class R:
        content = rss

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: R())
    (item,) = web.search_bing_news("nvda", 5)
    assert item == {"title": "NVDA & AI", "url": "https://example.com/a?b=1", "source": "Example News",
                    "date": "2026-11-19T02:01+00:00", "snippet": "Revenue doubled"}


def test_duckduckgo_parsing(monkeypatch):
    page = ('<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fx&amp;rut=1">'
            'Gold <b>rallies</b></a> ... <a class="result__snippet" href="#">Real yields fell</a>')

    class R:
        text = page

        def raise_for_status(self):
            pass

    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: R())
    assert web.search_duckduckgo("gold", 5) == [{"title": "Gold rallies", "url": "https://example.org/x",
                                                 "source": "example.org", "date": None, "snippet": "Real yields fell"}]


ARTICLE = ("<html><head><title>Fed cuts</title></head><body><article><h1>Fed cuts rates</h1>"
           + "<p>The Federal Reserve cut its policy rate by 50 basis points on Wednesday, citing credit conditions.</p>"
           + "".join(f"<p>Paragraph {i}: analysts at bank number {i} revised their outlook for {2026 + i % 3} "
                     f"after the decision, expecting yields to move by {i * 3} basis points over {i} weeks.</p>"
                     for i in range(1, 30))
           + "</article></body></html>")


def test_fetch_url_extracts_and_caps_text(monkeypatch, fresh_db):
    monkeypatch.setattr(web, "_download", lambda url: (url, ARTICLE))
    out = server.fetch_url("https://news.example.com/fed", 5000)          # cap stays at 1,500
    d = out["data"]
    assert "as_of" in out and d["site"] == "news.example.com" and d["chars_total"] > 1500
    assert len(d["text"]) <= web.FETCH_MAX_CHARS + 1 and d["text"].endswith("…") and "50 basis points" in d["text"]
    assert len(json.dumps(out, ensure_ascii=False)) <= config.MAX_TOOL_BYTES + 200


def fake_resolve(mapping):
    def getaddrinfo(host, port, *a, **k):
        ip = mapping[host]
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(fam, socket.SOCK_STREAM, 6, "", (ip, port))]
    return getaddrinfo


@pytest.mark.parametrize("url,ip", [
    ("http://router.lan/admin", "192.168.1.1"), ("http://intranet/x", "10.0.0.5"), ("http://localhost:1234/v1", "127.0.0.1"),
    ("http://metadata/latest", "169.254.169.254"), ("http://v6/x", "::1"),
])
def test_fetch_url_refuses_private_addresses(monkeypatch, fresh_db, url, ip):
    host = url.split("/")[2].split(":")[0]
    monkeypatch.setattr(web.socket, "getaddrinfo", fake_resolve({host: ip}))
    out = server.fetch_url(url)
    assert out["data"] is None and "private" in out["error"]


def test_fetch_url_refuses_redirect_to_private(monkeypatch, fresh_db):
    monkeypatch.setattr(web.socket, "getaddrinfo", fake_resolve({"public.example": "93.184.216.34", "inside": "10.1.1.1"}))

    class Stream:
        status_code = 302
        headers = {"location": "http://inside/secret"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url):
            return Stream()

    monkeypatch.setattr(web.httpx, "Client", Client)
    out = server.fetch_url("http://public.example/page")
    assert out["data"] is None and "private" in out["error"]


def test_fetch_url_refuses_other_schemes(fresh_db):
    assert "http(s)" in server.fetch_url("file:///etc/passwd")["error"]
    assert "http(s)" in server.fetch_url("ftp://example.com/x")["error"]


def test_tools_registered_with_mcp():
    assert {"web_search", "fetch_url"} <= set(server.TOOLS)
