import ipaddress
import re
import socket
import posixpath
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, unquote

class UnsafeURL(ValueError):
    pass

def normalize_url(url: str, base: str | None = None) -> str:
    url = urljoin(base, url) if base else url
    if any(ord(c) < 32 for c in url) or "\\" in url:
        raise UnsafeURL("Invalid URL characters")
    try:
        p = urlsplit(url)
        if p.scheme.lower() not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise UnsafeURL("Use an HTTP(S) URL without credentials")
        host = p.hostname.encode("idna").decode().lower().rstrip(".")
        port = p.port
        if port not in (None, 80, 443):
            raise UnsafeURL("Only standard HTTP(S) ports are supported")
        authority = f"[{host}]" if ":" in host else host
        if port and port != {"http":80,"https":443}[p.scheme.lower()]:
            authority += f":{port}"
        path = quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
        path = re.sub(r"/{2,}", "/", path)
        return urlunsplit((p.scheme.lower(), authority, path, p.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise UnsafeURL(str(exc)) from exc

def in_scope(url: str, start: str) -> bool:
    a,b = urlsplit(url),urlsplit(start)
    return a.hostname == b.hostname and a.port == b.port and (a.scheme == b.scheme or (b.scheme == "http" and a.scheme == "https"))

def public_addresses(url: str) -> list[str]:
    p = urlsplit(normalize_url(url))
    host = p.hostname
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL("Local destinations are prohibited")
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(ip).is_global or getattr(ipaddress.ip_address(ip), "ipv4_mapped", None) is not None for ip in addresses):
        raise UnsafeURL("Destination resolves to a non-public IP")
    return addresses

def in_crawl_scope(url: str, start: str, scope: str = "directory") -> bool:
    """Keep pages in the submitted directory, or optionally the whole origin.

    Trailing slashes and extensionless URLs denote directories; filenames use
    their containing directory. Decode and normalize before checking boundaries.
    Robots and sitemap documents themselves still use origin-level scope.
    """
    if not in_scope(url, start):
        return False
    if scope == "site":
        return True
    original_path = unquote(urlsplit(start).path)
    path = posixpath.normpath(original_path)
    if not original_path.endswith("/") and "." in path.rsplit("/", 1)[-1]:
        path = posixpath.dirname(path)
    candidate = posixpath.normpath(unquote(urlsplit(url).path))
    return candidate == path or candidate.startswith(path.rstrip("/") + "/")

SKIP = re.compile(r"(?:^|/)(?:login|logout|signin|signup|search|admin|cart|checkout|wp-admin)(?:/|$)|\.(?:pdf|zip|gz|bz2|xz|tar|tgz|tbz2|7z|epub|png|jpg|jpeg|gif|svg|webp|mp4|css|js|xml|txt|ico|woff2?)$", re.I)
def relevant(url: str) -> bool:
    p=urlsplit(url)
    artifact = re.search(r"/(?:tarball|checksum)/?$", p.path, re.I)
    return not p.query and not SKIP.search(p.path) and not artifact and not re.search(r"/page/\d+/?$", p.path)
