"""The CLI and worker share this deterministic generation pipeline."""
from dataclasses import dataclass

from .analysis.ranking import select_pages
from .crawler.crawler import Crawler
from .generator.renderer import render
from .generator.validator import validate


class GenerationError(ValueError):
    """A generation failure with a message safe to show to API clients."""


@dataclass
class Generation:
    markdown: str
    title: str
    pages: list
    pages_selected: int
    warnings: list[str]


def build_generation(result):
    if not result.pages:
        raise GenerationError("No accessible HTML pages were extracted.")
    selected = select_pages(result)
    markdown = render(result.start, result.pages, selected)
    if validate(markdown, result.start, result.pages, selected):
        raise GenerationError("Generated Markdown failed validation.")
    home = next((page for page in result.pages if page.url == result.start), result.pages[0])
    return Generation(markdown, home.title, result.pages, len(selected), result.errors)


def generate(url, config):
    return build_generation(Crawler(config).crawl(url))
