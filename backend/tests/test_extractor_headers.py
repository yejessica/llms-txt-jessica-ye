"""Semantic headers can contain the page's substantive introduction."""
from contextlib import nullcontext
from unittest.mock import patch

import pytest

from app.crawler.extractor import extract


@pytest.mark.parametrize("container", ["article", "main", "body"])
@pytest.mark.parametrize("extractor", ["primary", "unavailable", "empty"])
def test_introductory_header_is_preserved(container, extractor):
    introduction = "This introduction explains how to install and configure the project for your website."
    document = f"""<html><head><title>Getting started</title></head>
        <{container}>
          <nav><p>Navigation links to documentation and other pages on this website.</p></nav>
          <header><h1>Getting started</h1><p>{introduction}</p></header>
        </{container}></html>"""
    if extractor == "unavailable":
        context = patch("app.crawler.extractor.trafilatura", None)
    elif extractor == "empty":
        context = patch("app.crawler.extractor.trafilatura.extract", return_value=None)
    else:
        context = nullcontext()
    with context:
        page = extract(document, "https://site.test/", 0, "https://site.test/")

    assert introduction in page.content
    assert page.description == introduction
    assert "Navigation links" not in page.content
