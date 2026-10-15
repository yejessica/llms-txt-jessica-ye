from dataclasses import dataclass, field
import re
from bs4 import BeautifulSoup
try:
    import trafilatura
except ImportError:
    trafilatura = None
from .urls import normalize_url, in_scope, relevant

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

def extract(html, url, depth, start):
    soup=BeautifulSoup(html,"html.parser")
    tag=soup.find("link",rel="canonical")
    canonical=None
    if tag and tag.get("href"):
        try:
            candidate=normalize_url(tag["href"],url)
            if in_scope(candidate,start): canonical=candidate
        except ValueError: pass
    # Canonicals are hints only. Keep the fetched URL until a canonical target
    # has itself been fetched; an unverified tag must never fabricate a link.
    links=[]; nav=[]
    for a in soup.find_all("a",href=True):
        try: link=normalize_url(a["href"],url)
        except ValueError: continue
        if in_scope(link,start) and relevant(link):
            links.append(link)
            if a.find_parent(["nav","header"]) or a.find_parent(attrs={"role":"navigation"}): nav.append(link)
    title=clean(soup.title.get_text(" ")) if soup.title else ""
    headings=[clean(h.get_text(" ")) for h in soup.find_all(["h1","h2","h3"])][:30]
    meta=soup.find("meta",attrs={"name":re.compile("^description$",re.I)}) or soup.find("meta",attrs={"property":"og:description"})
    for node in soup.select("script,style,nav,header,footer,aside,form,[role=navigation],.advertisement,.ads"):
        node.decompose()
    extracted=trafilatura.extract(html,include_comments=False,include_tables=False) if trafilatura else None
    content=clean(extracted or soup.get_text(" "))
    description=concise(meta.get("content", "")) if meta else ""
    if not description:
        paragraph=next((clean(p.get_text(" ")) for p in soup.find_all("p") if len(clean(p.get_text(" ")))>40),"")
        description=concise(paragraph or content)
    return Page(url,title or (headings[0] if headings else url),description,headings,content,depth,list(dict.fromkeys(links)),list(dict.fromkeys(nav)),canonical=canonical)
