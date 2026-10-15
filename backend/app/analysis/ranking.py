from collections import defaultdict
import math
from urllib.parse import urlsplit

def section(page):
    parts=[p for p in urlsplit(page.url).path.split("/") if p]
    if not parts: return "Overview"
    first=parts[0]
    if first.isdigit() and len(parts)>1: first=parts[1]
    first=first.removesuffix(".html").removesuffix(".htm")
    if first in {"docs","doc","documentation","tutorial","library","reference","guide","guides","topics","howto"}: return "Documentation"
    if first in {"blog","weblog","essays","articles","news"}: return "Articles and News"
    if "." in parts[0] or len(parts)==1: return "Resources"
    return first.replace("-"," ").replace("_"," ").title()

def score(page,incoming,navigation,start):
    path=urlsplit(page.url).path.lower()
    return (12 if page.url==start else 0)+5/(1+page.depth)+2*math.log1p(incoming[page.url])+3*math.log1p(navigation[page.url])+(2 if page.title else 0)+(2 if page.description else 0)+min(len(page.content)/800,4)+(3 if any(w in path for w in ("overview","intro","about","tutorial","guide","docs")) else 0)

def diversity_bucket(page):
    parts=[p for p in urlsplit(page.url).path.split("/") if p]
    if parts and parts[0].isdigit(): parts=parts[1:]
    if parts and parts[0] in {"docs","documentation","blog"} and len(parts)>2:
        return "/".join(parts[:2])
    return parts[0] if parts and len(parts)>1 else "root"

def select_pages(result,limit=25):
    groups=defaultdict(list)
    for page in result.pages:
        groups[diversity_bucket(page)].append(page)
    for group in groups.values(): group.sort(key=lambda p:(-score(p,result.incoming,result.navigation,result.start),p.url))
    ordered=sorted(groups,key=lambda k:(-score(groups[k][0],result.incoming,result.navigation,result.start),k))
    selected=[]
    while len(selected)<limit and any(groups.values()):
        for key in ordered:
            if groups[key] and len(selected)<limit: selected.append(groups[key].pop(0))
    return selected
