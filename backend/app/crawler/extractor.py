from dataclasses import dataclass, field
import re
from bs4 import BeautifulSoup
try:
    import trafilatura
except ImportError:
    trafilatura = None
from .urls import normalize_url, site_origin_allowed, site_url, relevant

def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()

def concise(text, limit=220):
    text=clean(text)
    if len(text)<=limit: return text
    return text[:limit-1].rsplit(" ",1)[0]+"…"

@dataclass
class Page:
    url: str
    title: str
    description: str
    headings: list[str]
    content: str
    depth: int
    links: list[str] = field(default_factory=list)
    nav_links: list[str] = field(default_factory=list)
    last_modified: str | None = None
    canonical: str | None = None
    link_labels: dict[str, str] = field(default_factory=dict)

def extract(html, url, depth, start):
    soup=BeautifulSoup(html,"html.parser")
    tag=soup.find("link",rel="canonical")
    canonical=None
    if tag and tag.get("href"):
        try:
            candidate=site_url(tag["href"],start,url)
            if site_origin_allowed(candidate,start): canonical=candidate
        except ValueError: pass
    # Canonicals are hints only. Keep the fetched URL until a canonical target
    # has itself been fetched; an unverified tag must never fabricate a link.
    links=[]; nav=[]; labels={}
    for a in soup.find_all(["a", "area"],href=True):
        try: link=site_url(a["href"],start,url)
        except ValueError: continue
        if site_origin_allowed(link,start) and relevant(link):
            links.append(link)
            image = a.find("img", alt=True)
            labels[link] = clean(a.get("aria-label") or a.get("alt") or a.get_text(" ")
                                 or (image.get("alt") if image else "") or a.get("title"))[:200]
            if a.name == "area" or a.find_parent(["nav","header"]) or a.find_parent(attrs={"role":"navigation"}): nav.append(link)
    title=clean(soup.title.get_text(" ")) if soup.title else ""
    headings=[clean(h.get_text(" ")) for h in soup.find_all(["h1","h2","h3"])][:30]
    meta=soup.find("meta",attrs={"name":re.compile("^description$",re.I)}) or soup.find("meta",attrs={"property":"og:description"})
    # Strip confirmed navigation before extraction so differing menus do not
    # prevent duplicate detection. Semantic headers may contain the introduction.
    for node in soup.select("script,style,nav,[role=navigation],.advertisement,.ads"):
        node.decompose()
    extracted=trafilatura.extract(str(soup),include_comments=False,include_tables=False) if trafilatura else None
    # Apply broader pruning only to the fallback text and description source.
    # Keep headers here too, including on pages with no article/main wrapper.
    for node in soup.select("footer,aside,form"):
        node.decompose()
    content=clean(extracted or soup.get_text(" "))
    description=concise(meta.get("content", "")) if meta else ""
    if not description:
        paragraph=next((clean(p.get_text(" ")) for p in soup.find_all("p") if len(clean(p.get_text(" ")))>40),"")
        description=concise(paragraph or content)
    return Page(url,title or (headings[0] if headings else url),description,headings,content,depth,list(dict.fromkeys(links)),list(dict.fromkeys(nav)),canonical=canonical,link_labels=labels)
