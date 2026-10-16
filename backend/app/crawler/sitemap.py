from xml.etree import ElementTree as ET
import gzip
import io
from urllib.parse import urlsplit, urlunsplit
from .urls import normalize_url, in_scope, in_crawl_scope
from .fetcher import RateLimitError

def parse_sitemap(data: bytes, max_bytes: int = 2_000_000):
    if len(data) > max_bytes:
        raise ValueError("Sitemap exceeds size limit")
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Decompressed sitemap exceeds size limit")
    # Sitemaps use UTF-8. Decode before checking declarations so alternate XML
    # encodings cannot hide entities from the guard ahead of ElementTree.
    text = data.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("Sitemap must use UTF-8")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations with entities are not supported")
    root=ET.fromstring(data)
    kind=root.tag.rsplit("}",1)[-1]
    if kind not in {"urlset","sitemapindex"}: raise ValueError("Not a sitemap")
    entries=[]
    for item in root:
        fields={c.tag.rsplit("}",1)[-1]:(c.text or "").strip() for c in item}
        if fields.get("loc"): entries.append((fields["loc"], fields.get("lastmod")))
    return kind,entries

def discover(fetcher, start, robots_text, config, can_fetch, errors):
    p=urlsplit(start); origin=urlunsplit((p.scheme,p.netloc,"/","",""))
    pending=[normalize_url("/sitemap.xml",origin)]
    for line in robots_text.splitlines():
        if line.lower().startswith("sitemap:"):
            try: pending.append(normalize_url(line.split(":",1)[1].strip(),origin))
            except ValueError: pass
    visited=set(); urls={}
    while pending and len(visited)<config.max_sitemaps and len(urls)<config.max_discovered:
        url=pending.pop(0)
        if url in visited or not in_scope(url,start) or not can_fetch(url): continue
        visited.add(url)
        try:
            response=fetcher.get(url)
            if response.status_code == 404: continue
            response.raise_for_status()
            kind,entries=parse_sitemap(response.content, config.max_bytes)
            for loc,lastmod in entries:
                try: loc=normalize_url(loc,url)
                except ValueError: continue
                if not in_scope(loc,start): continue
                if kind == "sitemapindex":
                    if len(pending)<config.max_sitemaps*10: pending.append(loc)
                elif in_crawl_scope(loc,start,config.crawl_scope) and len(urls)<config.max_discovered:
                    urls[loc]=lastmod
        except RateLimitError:
            raise
        except Exception as exc: errors.append(f"Sitemap {url}: {exc}")
    return urls
