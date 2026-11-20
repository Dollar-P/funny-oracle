"""Web tools for providers without built-in search (LOCAL_LLM.md lesson 9): `web_search` and `fetch_url`.

web_search: news RSS (Bing News, then Google News) → DuckDuckGo HTML fallback. SearXNG / Tavily can slot in
            later as further providers.
fetch_url:  page text via trafilatura, ≤ 1,500 chars. URLs come from the model, so only public http(s)
            hosts are fetched (private, loopback and link-local addresses are refused at every redirect).

Both return compact dicts (the MCP server wraps them with `as_of`) and are cached for 45 minutes.
"""
from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

import config
from core import cache

log = logging.getLogger(__name__)

TTL_WEB = 45 * 60
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FunnyOracle/1.0 (+research; educational)"}
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_PAGE_BYTES = 2_000_000
FETCH_MAX_CHARS = 1500
SNIPPET = 180


def _clean(s: str | None, n: int) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _iso(date: str | None) -> str | None:
    if not date:
        return None
    try:
        return parsedate_to_datetime(date).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return date[:25]


# --- search providers ----------------------------------------------------------------------

def _rss_items(xml_bytes: bytes) -> list[ET.Element]:
    root = ET.fromstring(xml_bytes)
    return root.findall("channel/item")


def search_bing_news(query: str, limit: int) -> list[dict]:
    r = httpx.get(f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss", headers=UA,
                  timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    out = []
    for it in _rss_items(r.content)[:limit]:
        link = it.findtext("link") or ""
        real = parse_qs(urlparse(link).query).get("url", [link])[0]  # apiclick.aspx?…&url=<article>
        source = next((c.text for c in it if c.tag.endswith("Source")), None)
        out.append({"title": _clean(it.findtext("title"), 140), "url": real, "source": source,
                    "date": _iso(it.findtext("pubDate")), "snippet": _clean(it.findtext("description"), SNIPPET)})
    return out


def search_google_news(query: str, limit: int) -> list[dict]:
    r = httpx.get(f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en",
                  headers=UA, timeout=TIMEOUT, follow_redirects=True)
    r.raise_for_status()
    out = []
    for it in _rss_items(r.content)[:limit]:
        src = it.find("source")
        out.append({"title": _clean(it.findtext("title"), 140), "url": it.findtext("link"),
                    "source": src.text if src is not None else None, "date": _iso(it.findtext("pubDate")),
                    "snippet": ""})
    return out


_DDG_RESULT = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)


def search_duckduckgo(query: str, limit: int) -> list[dict]:
    r = httpx.get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", headers=UA, timeout=TIMEOUT,
                  follow_redirects=True)
    r.raise_for_status()
    links = _DDG_RESULT.findall(r.text)
    snippets = _DDG_SNIPPET.findall(r.text)
    out = []
    for i, (href, title) in enumerate(links[:limit]):
        q = parse_qs(urlparse(html.unescape(href)).query)
        url = unquote(q["uddg"][0]) if "uddg" in q else html.unescape(href)
        if url.startswith("//"):
            url = "https:" + url
        out.append({"title": _clean(title, 140), "url": url, "source": urlparse(url).hostname, "date": None,
                    "snippet": _clean(snippets[i] if i < len(snippets) else "", SNIPPET)})
    return out


PROVIDERS = [("bing_news_rss", search_bing_news), ("google_news_rss", search_google_news),
             ("duckduckgo", search_duckduckgo)]


def web_search(query: str, limit: int = 6) -> dict:
    query = (query or "").strip()[:200]
    if not query:
        raise ValueError("empty query")
    limit = max(1, min(int(limit), 8))

    def load():
        tried = []
        for name, fn in PROVIDERS:
            try:
                results = [x for x in fn(query, limit) if x.get("url") and x.get("title")]
            except Exception as e:  # provider down / changed markup → next one
                tried.append(f"{name}: {type(e).__name__}")
                log.info("web_search %s failed: %s", name, e)
                continue
            if results:
                return {"query": query, "provider": name, "results": results}
            tried.append(f"{name}: no results")
        return {"error": "no search provider returned results (" + "; ".join(tried) + ")"}

    res = cache.cached(f"web_search:{query.lower()}:{limit}", TTL_WEB, load)
    if res.get("error"):
        raise RuntimeError(res["error"])
    return _fit({**res, "results": list(res["results"])})


def _fit(out: dict) -> dict:
    """Trim to the tool-size budget (≤ ~2 KB): drop snippets of later results, then results."""
    import json

    def size():
        return len(json.dumps(out, separators=(",", ":"), ensure_ascii=False))

    budget = config.MAX_TOOL_BYTES - 80  # room for the as_of envelope
    i = len(out["results"]) - 1
    while size() > budget and i >= 0:
        if out["results"][i].get("snippet"):
            out["results"][i] = {**out["results"][i], "snippet": ""}
        i -= 1
    while size() > budget and len(out["results"]) > 1:
        out["results"].pop()
    out["count"] = len(out["results"])
    return out


# --- fetch_url ----------------------------------------------------------------------------------

def assert_public_url(url: str) -> None:
    """Refuse anything but http(s) to public addresses (the URL is chosen by the model)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"cannot resolve host {p.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError("refusing to fetch a private, loopback or reserved address")


def _download(url: str) -> tuple[str, str]:
    """GET with manual redirects (each hop re-checked), size-capped. Returns (final_url, html)."""
    with httpx.Client(headers=UA, timeout=TIMEOUT, follow_redirects=False) as client:
        for _hop in range(4):
            assert_public_url(url)
            with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
                    raise ValueError(f"not a text page ({ctype or 'unknown type'})")
                buf = bytearray()
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > MAX_PAGE_BYTES:
                        break
                return url, buf.decode(r.encoding or "utf-8", errors="replace")
    raise ValueError("too many redirects")


def fetch_url(url: str, max_chars: int = FETCH_MAX_CHARS) -> dict:
    max_chars = max(200, min(int(max_chars), FETCH_MAX_CHARS))
    url = (url or "").strip()

    def load():
        import trafilatura

        final, page = _download(url)
        text = trafilatura.extract(page, include_comments=False, include_tables=False, favor_precision=True) or ""
        meta = trafilatura.extract_metadata(page)
        if not text:  # fall back to visible text
            text = _clean(re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page), 20_000)
        return {"url": final, "title": (meta.title if meta and meta.title else None),
                "date": (meta.date if meta and meta.date else None), "site": urlparse(final).hostname,
                "text": re.sub(r"\s+", " ", text).strip()}

    res = cache.cached(f"fetch_url:{url}", TTL_WEB, load)
    text = res["text"]
    out = {**res, "text": text[:max_chars] + ("…" if len(text) > max_chars else ""), "chars_total": len(text)}
    return out
