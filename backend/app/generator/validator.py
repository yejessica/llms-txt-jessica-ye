"""Validate the generated subset of the llms.txt Markdown specification.

Grounding is enforced by re-rendering trusted extracted Page records, rather
than pretending that a syntax check can prove arbitrary prose is factual.
"""
import re
from ..crawler.urls import normalize_url
from .renderer import render

def validate(markdown,start,pages,selected):
    errors=[]
    if not pages: return ["No extracted pages"]
    if markdown != render(start,pages,selected): errors.append("Output differs from grounded rendering")
    if not markdown.startswith("# ") or len(re.findall(r"^# ",markdown,re.M))!=1: errors.append("Expected exactly one H1")
    discovered={p.url for p in pages}; seen=set()
    for page in selected:
        try:
            if normalize_url(page.url)!=page.url: errors.append("Non-normalized URL")
        except ValueError: errors.append("Invalid URL")
        if page.url not in discovered: errors.append("Undiscovered URL")
        if page.url in seen: errors.append("Duplicate URL")
        seen.add(page.url)
        original=next((p for p in pages if p.url==page.url),None)
        if original and (page.title!=original.title or page.description!=original.description): errors.append("Ungrounded metadata")
    return errors
