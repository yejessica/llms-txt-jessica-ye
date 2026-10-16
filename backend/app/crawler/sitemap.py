"""Incremental XML/gzip parsing with independent byte, entry and URL budgets."""
from contextlib import contextmanager
import codecs
from itertools import chain
from urllib.parse import urlsplit, urlunsplit
from xml.parsers import expat
import zlib
import time

from .urls import site_url, site_origin_allowed, in_crawl_scope, relevant
from .fetcher import RateLimitError
from .frontier import SitemapSample

CHUNK = 65536


class SitemapBudgetExceeded(ValueError):
    pass


def xml_chunks(chunks, max_bytes, max_compressed_bytes):
    """Inflate at most one gzip member, in bounded output chunks, checking CRC."""
    iterator = iter(chunks)
    prefix = b''
    while len(prefix) < 2:
        try:
            prefix += next(iterator)
        except StopIteration:
            break
    compressed = prefix.startswith(b'\x1f\x8b')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if compressed else None
    wire = total = 0
    for raw in chain([prefix], iterator):
        wire += len(raw)
        if compressed and wire > max_compressed_bytes:
            raise ValueError('Compressed sitemap exceeds size limit')
        pending = raw
        while pending:
            if decoder:
                if decoder.eof:
                    raise ValueError('Trailing data after gzip sitemap')
                block = decoder.decompress(pending, min(CHUNK, max_bytes - total + 1))
                pending = decoder.unconsumed_tail
                if decoder.unused_data:
                    raise ValueError('Trailing data after gzip sitemap')
            else:
                block, pending = pending[:CHUNK], pending[CHUNK:]
            total += len(block)
            if total > max_bytes:
                raise ValueError('Decompressed sitemap exceeds size limit' if compressed else 'Sitemap exceeds size limit')
            if block:
                yield block
    if decoder and not decoder.eof:
        raise ValueError('Truncated gzip sitemap')


def parse_sitemap_stream(chunks, on_entry, max_bytes=52_428_800,
                         max_compressed_bytes=10_485_760, max_entries=50_000, on_bytes=None):
    """Keep only one loc/lastmod pair in memory; prohibit DTDs and entities."""
    parser = expat.ParserCreate(namespace_separator='}')
    decoder = codecs.getincrementaldecoder('utf-8-sig')()
    depth = count = 0
    kind = field = None
    entry = None
    text = []
    length = 0

    def reject(*args):
        raise ValueError('XML declarations with entities are not supported')

    def declaration(version, encoding, standalone):
        if encoding and encoding.lower().replace('-', '') != 'utf8':
            raise ValueError('Sitemap must use UTF-8')

    def begin(name, attrs):
        nonlocal depth, kind, entry, field, text, length
        depth += 1
        if depth > 32:
            raise ValueError('Sitemap nesting limit exceeded')
        tag = name.rsplit('}', 1)[-1]
        if depth == 1:
            if tag not in {'urlset', 'sitemapindex'}:
                raise ValueError('Not a sitemap')
            kind = tag
        elif depth == 2:
            entry = {} if tag == ('url' if kind == 'urlset' else 'sitemap') else None
        elif depth == 3 and entry is not None and tag in {'loc', 'lastmod'}:
            field, text, length = tag, [], 0

    def characters(value):
        nonlocal length
        if field and depth == 3:
            length += len(value)
            if length > 8192:
                raise ValueError('Sitemap field length limit exceeded')
            text.append(value)

    def end(name):
        nonlocal depth, entry, field, count
        if depth == 3 and field:
            entry[field] = ''.join(text).strip()
            field = None
        elif depth == 2 and entry is not None:
            count += 1
            if count > max_entries:
                raise ValueError('Sitemap entry limit exceeded')
            if entry.get('loc'):
                on_entry(kind, entry['loc'], entry.get('lastmod'))
            entry = None
        depth -= 1

    parser.StartElementHandler = begin
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.XmlDeclHandler = declaration
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    for block in xml_chunks(chunks, max_bytes, max_compressed_bytes):
        if on_bytes:
            on_bytes(len(block))
        value = decoder.decode(block)
        if '\x00' in value:
            raise ValueError('Sitemap must use UTF-8')
        parser.Parse(value, False)
    parser.Parse(decoder.decode(b'', final=True), True)
    return kind


def parse_sitemap(data: bytes, max_bytes: int = 2_000_000):
    """Small in-memory compatibility helper; discovery uses the streaming API."""
    if len(data) > max_bytes:
        raise ValueError('Sitemap exceeds size limit')
    entries = []
    kind = parse_sitemap_stream((data[i:i + CHUNK] for i in range(0, len(data), CHUNK)),
                                lambda _, loc, lastmod: entries.append((loc, lastmod)),
                                max_bytes=max_bytes, max_compressed_bytes=max_bytes)
    return kind, entries


@contextmanager
def sitemap_response(fetcher, url, allowed):
    if hasattr(fetcher, 'stream'):
        with fetcher.stream(url, allowed=allowed) as response:
            # Do not buffer to a fixed chunk size: progress/deadline checks must
            # also run for servers which trickle small network chunks.
            yield response, response.iter_raw()
    else:  # In-memory fixture fetchers do not need a streaming transport.
        response = fetcher.get(url, allowed=allowed)
        yield response, response.iter_bytes(chunk_size=CHUNK)


def discover(fetcher, start, robots_text, config, can_fetch, errors):
    p = urlsplit(start)
    origin = urlunsplit((p.scheme, p.netloc, '/', '', ''))
    pending = [site_url('/sitemap.xml', start, origin)]
    for line in robots_text.splitlines():
        if line.lower().startswith('sitemap:'):
            try:
                pending.append(site_url(line.split(':', 1)[1].strip(), start, origin))
            except ValueError:
                pass
    visited = set()
    started = time.monotonic()
    consumed = 0

    def budget(size=0):
        nonlocal consumed
        consumed += size
        if consumed > config.max_sitemap_total_bytes:
            raise SitemapBudgetExceeded('Sitemap discovery exceeds total byte budget')
        if time.monotonic() - started > config.sitemap_timeout:
            raise SitemapBudgetExceeded('Sitemap discovery time budget exceeded')

    sample = SitemapSample(config.max_discovered, start, config.max_query_variants)
    while pending and len(visited) < config.max_sitemaps:
        url = pending.pop(0)
        if url in visited or not site_origin_allowed(url, start):
            continue
        visited.add(url)
        try:
            budget()
            if not can_fetch(url):
                continue
            allowed = lambda u: site_origin_allowed(u, start) and can_fetch(u)
            with sitemap_response(fetcher, url, allowed) as (response, chunks):
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                # A document's candidates remain bounded and only enter the
                # global sample after the entire XML/gzip stream validates.
                local = SitemapSample(config.max_discovered, start, config.max_query_variants)
                children = []
                def entry(kind, loc, lastmod):
                    try:
                        loc = site_url(loc, start, str(response.url))
                    except ValueError:
                        return
                    if not site_origin_allowed(loc, start):
                        return
                    if kind == 'sitemapindex':
                        if len(children) < config.max_sitemaps * 10:
                            children.append(loc)
                    elif in_crawl_scope(loc, start, config.crawl_scope, allow_www=True) and relevant(loc):
                        local.add(loc, lastmod)
                def watched_chunks():
                    for chunk in chunks:
                        budget()
                        yield chunk
                parse_sitemap_stream(watched_chunks(), entry, config.max_sitemap_bytes,
                                     config.max_sitemap_compressed_bytes, config.max_sitemap_entries, budget)
                for loc, lastmod in local.urls.items():
                    sample.add(loc, lastmod)
                for child in children:
                    if len(pending) < config.max_sitemaps * 10:
                        pending.append(child)
        except SitemapBudgetExceeded as exc:
            errors.append(f'Sitemap {url}: {exc}')
            break
        except RateLimitError:
            raise
        except Exception as exc:
            errors.append(f'Sitemap {url}: {exc}')
    return sample.urls
