from collections import defaultdict
import re
from ..analysis.ranking import section

def escape(text):
    text=re.sub(r"\s+"," ",text).strip()
    return re.sub(r"([\\`*{}\[\]<>_#!|])",r"\\\1",text)

def render(start,pages,selected):
    home=next((p for p in pages if p.url==start),pages[0])
    lines=[f"# {escape(home.title)}",""]
    if home.description: lines += [f"> {escape(home.description)}",""]
    groups=defaultdict(list)
    for page in selected: groups[section(page)].append(page)
    for name,group in groups.items():
        lines += [f"## {escape(name)}",""]
        for page in group:
            # Parentheses in a URL must not terminate the Markdown link.
            url=page.url.replace("(","%28").replace(")","%29")
            description=f": {escape(page.description)}" if page.description else ""
            lines.append(f"- [{escape(page.title)}]({url}){description}")
        lines.append("")
    return "\n".join(lines)
