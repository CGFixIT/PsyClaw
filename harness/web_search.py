"""Allowlist-only web fetch for the harness console (``/web``).

Default-off. When enabled, the console may GET only operator-allowlisted
http(s) URLs. There is no search engine, no browser, no JS, no crawl.

Offline mode is the point: the local 27b still cannot invent a host. A
fetch is attempted only if (1) ``web_enabled`` is true, (2) the URL's
host+path matches the allowlist, and (3) DNS resolves to a public
(non-loopback, non-private, non-link-local) address *before* the GET.
httpx re-resolves on connect, so a residual rebinding window remains
(see ``assert_public_host``). Empty allowlist is fail-closed.

I6: this module is harness-local. It never imports ``gate``, ``graph``,
``mcp_hybrid_server``, or ``agentic``. It never starts ``/api/agent/*``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from html import parser, unescape
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import httpx

from harness.config import HarnessConfig, _atomic_write_json
from utils.errors import AgenticError

log = logging.getLogger("cyclaw.harness.web_search")

_UTF8: Final = "utf-8"
_SCHEMES: Final = frozenset(("http", "https"))
_MAX_ALLOW: Final = 32
_MAX_BYTES: Final = 262_144
_TIMEOUT_SEC: Final = 8.0
_MAX_QUERY: Final = 200
_MAX_SNIPPET: Final = 160
_MAX_HITS_PER_URL: Final = 3
_MAX_SEARCH_URLS: Final = 8
_MAX_CONTEXT: Final = 4000
_MAX_RAW: Final = 500
_SNIP_BEFORE: Final = 40
_SNIP_AFTER: Final = 120
_HTTP_OK_BELOW: Final = 400
_BLOCKED_HOSTS: Final = frozenset((
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
))
_SKIP_TAGS: Final = frozenset(("script", "style", "noscript", "template"))
_PATH_CHARS: Final = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~/=+-"
)
_BREAK_TAGS: Final = frozenset(("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre"))
_TEXT_TYPES: Final = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/javascript",
)


class WebToolError(AgenticError):
    """Allowlist / SSRF / fetch failure for ``/web``."""

    def __init__(self, message: str, code: str = "WEB_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


class _TextExtractor(parser.HTMLParser):
    """Pull visible text; drop script/style. Stdlib only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, chunk: str) -> None:
        if not self._skip:
            self.parts.append(chunk)


def _allow_path(cfg: HarnessConfig) -> Path:
    return cfg.tools_dir / "web_allowlist.json"


def _last_path(cfg: HarnessConfig) -> Path:
    return cfg.tools_dir / "web_last.json"


def _context_path(cfg: HarnessConfig) -> Path:
    return cfg.tools_dir / "web_context.txt"


def _atomic_write_text(path: Path, text: str) -> None:
    """Staged file + Path.replace (os.replace). Matches allowlist JSON writes."""
    staged = path.with_name(f".staged.{path.name}.tmp")
    try:
        _stage_and_replace(staged, path, text)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _stage_and_replace(staged: Path, path: Path, text: str) -> None:
    staged.write_text(text, encoding=_UTF8)
    staged.replace(path)


def _host_of(raw: str) -> str:
    return (raw or "").strip().lower().rstrip(".")


def parse_allow_entry(raw: str) -> dict[str, str]:
    """Normalise a host or URL into an allowlist row. No DNS (offline-safe)."""
    text = (raw or "").strip()
    if not text or len(text) > _MAX_RAW:
        raise WebToolError("allowlist entry must be a non-empty URL or host", code="WEB_BAD_URL")
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in _SCHEMES:
        raise WebToolError("only http and https URLs are allowed", code="WEB_BAD_URL")
    if parsed.username or parsed.password:
        raise WebToolError("URLs with userinfo are refused", code="WEB_BAD_URL")
    host = _host_of(parsed.hostname or "")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise WebToolError("that host cannot be allowlisted", code="WEB_SSRF_DENIED")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise WebToolError("private or loopback IPs cannot be allowlisted", code="WEB_SSRF_DENIED")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return {"scheme": parsed.scheme, "host": host, "path": path, "raw": f"{parsed.scheme}://{host}{path}"}


def _load_entries(path: Path) -> list[dict[str, str]]:
    try:
        parsed = json.loads(path.read_text(encoding=_UTF8))
    except (OSError, json.JSONDecodeError):
        return []
    rows = parsed.get("entries", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("host"):
            out.append({
                "scheme": str(row.get("scheme") or "https"),
                "host": _host_of(str(row.get("host") or "")),
                "path": str(row.get("path") or "/"),
                "raw": str(row.get("raw") or ""),
            })
    return out


def _save_entries(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {"entries": entries})


def url_is_allowed(url: str, entries: list[dict[str, str]]) -> dict[str, str] | None:
    """Return the matching allowlist row, or None."""
    try:
        wanted = parse_allow_entry(url)
    except WebToolError:
        return None
    host = wanted["host"]
    path = wanted["path"] or "/"
    aliases = {host}
    if host.startswith("www."):
        aliases.add(host[4:])
    else:
        aliases.add(f"www.{host}")
    for entry in entries:
        if entry["host"] not in aliases:
            continue
        prefix = entry["path"] or "/"
        if prefix == "/":
            return entry
        trimmed = prefix.rstrip("/")
        if path == trimmed or path.startswith(trimmed + "/"):
            return entry
    return None


def assert_public_host(host: str) -> None:
    """Resolve ``host`` and refuse any non-global address (SSRF pre-check).

    Residual DNS TOCTOU: this snapshot is not a pinned connect. httpx
    re-resolves on the GET, so a rebinding window remains between this
    check and the socket. A pinned-IP transport is the future fix; this
    module does not add one. See docs/THREAT_MODEL.md tenth amendment.
    """
    # Pre-connect IP check only — httpx will DNS again. Not a pin.
    clean = _host_of(host)
    if not clean or clean in _BLOCKED_HOSTS:
        raise WebToolError("host is not fetchable", code="WEB_SSRF_DENIED")
    try:
        infos = socket.getaddrinfo(clean, None)
    except socket.gaierror as exc:
        raise WebToolError(f"DNS failed for {clean}", code="WEB_DNS") from exc
    if not infos:
        raise WebToolError(f"DNS returned no addresses for {clean}", code="WEB_DNS")
    for addrinfo in infos:
        raw_ip = addrinfo[4][0]
        ip = ipaddress.ip_address(raw_ip)
        if not ip.is_global:
            raise WebToolError(
                "resolved address is not public; refused",
                code="WEB_SSRF_DENIED",
                details={"host": clean},
            )


def extract_text(body: str, content_type: str) -> str:
    """Visible text from HTML; otherwise a clipped plaintext body."""
    lowered = (content_type or "").split(";", 1)[0].strip().lower()
    if "html" in lowered:
        extractor = _TextExtractor()
        extractor.feed(body)
        extractor.close()
        text = " ".join("".join(extractor.parts).split())
    else:
        text = " ".join(body.split())
    return unescape(text)[:_MAX_BYTES]


def _snippets(text: str, query: str) -> list[str]:
    needle = query.casefold()
    hay = text
    found: list[str] = []
    start = 0
    folded = hay.casefold()
    while len(found) < _MAX_HITS_PER_URL:
        idx = folded.find(needle, start)
        if idx < 0:
            break
        lo = max(0, idx - _SNIP_BEFORE)
        hi = min(len(hay), idx + len(query) + _SNIP_AFTER)
        chunk = hay[lo:hi].strip()
        if lo:
            chunk = f"…{chunk}"
        if hi < len(hay):
            chunk = f"{chunk}…"
        found.append(chunk[:_MAX_SNIPPET])
        start = idx + len(query) or idx + 1
    return found


def _allowlist_target(match: dict[str, str]) -> str:
    """GET URL from the persisted allowlist row only — no user-URL pieces."""
    scheme = match["scheme"] if match.get("scheme") in _SCHEMES else "https"
    host = match["host"]
    path = match["path"] or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if any(ch not in _PATH_CHARS for ch in path):
        raise WebToolError("allowlist path is invalid", code="WEB_BAD_URL")
    return f"{scheme}://{host}{path}"


class WebTool:
    """Persist allowlist + last extract; perform gated GETs."""

    def __init__(
        self,
        cfg: HarnessConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver=assert_public_host,
    ) -> None:
        self._cfg = cfg
        self._resolver = resolver
        self._transport = transport

    def status(self) -> dict:
        entries = _load_entries(_allow_path(self._cfg))
        ctx = _context_path(self._cfg)
        last = _last_path(self._cfg)
        return {
            "enabled": bool(self._cfg.web_enabled),
            "allowlist": [row["raw"] or f"{row['scheme']}://{row['host']}{row['path']}" for row in entries],
            "injected": ctx.is_file() and bool(ctx.stat().st_size),
            "has_last": last.is_file(),
            "max_allow": _MAX_ALLOW,
        }

    def set_enabled(self, enabled: bool) -> dict:
        self._cfg.web_enabled = bool(enabled)
        self._cfg.save()
        return self.status()

    def allow(self, raw: str) -> dict:
        entry = parse_allow_entry(raw)
        path = _allow_path(self._cfg)
        entries = _load_entries(path)
        key = (entry["host"], entry["path"])
        if any((row["host"], row["path"]) == key for row in entries):
            return self.status()
        if len(entries) >= _MAX_ALLOW:
            raise WebToolError(f"allowlist cap is {_MAX_ALLOW}", code="WEB_ALLOWLIST_FULL")
        entries.append(entry)
        _save_entries(path, entries)
        return self.status()

    def deny(self, raw: str) -> dict:
        entry = parse_allow_entry(raw)
        path = _allow_path(self._cfg)
        entries = [
            row for row in _load_entries(path)
            if (row["host"], row["path"]) != (entry["host"], entry["path"])
        ]
        _save_entries(path, entries)
        return self.status()

    def context_text(self) -> str:
        try:
            return _context_path(self._cfg).read_text(encoding=_UTF8)[:_MAX_CONTEXT]
        except OSError:
            return ""

    def forget(self) -> dict:
        for path in (_context_path(self._cfg), _last_path(self._cfg)):
            try:
                path.unlink()
            except OSError:
                continue
        return self.status()

    def inject(self) -> dict:
        last = _last_path(self._cfg)
        try:
            payload = json.loads(last.read_text(encoding=_UTF8))
        except (OSError, json.JSONDecodeError) as exc:
            raise WebToolError("nothing to inject — /web fetch or /web search first", code="WEB_NO_LAST") from exc
        text = str(payload.get("text") or "").strip()
        source = str(payload.get("url") or "")
        if not text:
            raise WebToolError("last extract is empty", code="WEB_NO_LAST")
        body = f"Source: {source}\n\n{text}"[:_MAX_CONTEXT]
        dest = _context_path(self._cfg)
        _atomic_write_text(dest, body)
        return self.status() | {"chars": len(body)}

    def _require_enabled(self) -> list[dict[str, str]]:
        if not self._cfg.web_enabled:
            raise WebToolError(
                "web fetch is off — /web on after allowlisting hosts",
                code="WEB_DISABLED",
            )
        entries = _load_entries(_allow_path(self._cfg))
        if not entries:
            raise WebToolError(
                "allowlist is empty — /web allow <url> first (fail-closed)",
                code="WEB_ALLOWLIST_EMPTY",
            )
        return entries

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=_TIMEOUT_SEC,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "CyClaw-harness-web/0.1 (+allowlist-only; no-browser)"},
        )

    def _get(self, url: str, entries: list[dict[str, str]]) -> dict:
        match = url_is_allowed(url, entries)
        if match is None:
            raise WebToolError("URL is not on the allowlist", code="WEB_HOST_DENIED", details={"url": url})
        parsed = urlparse(url if "://" in url else f"https://{url}")
        if parsed.query or parsed.params or parsed.fragment:
            raise WebToolError("query or fragment is not allowed", code="WEB_BAD_URL")
        self._resolver(match["host"])
        target = _allowlist_target(match)
        with self._client() as client:
            try:
                resp = client.get(target)
            except httpx.HTTPError:
                raise WebToolError("fetch failed", code="WEB_FETCH_FAILED") from None
        if resp.status_code >= _HTTP_OK_BELOW:
            raise WebToolError(
                f"upstream HTTP {resp.status_code}",
                code="WEB_FETCH_FAILED",
                details={"status": resp.status_code},
            )
        ctype = resp.headers.get("content-type", "text/plain")
        if not any(ctype.lower().startswith(prefix) for prefix in _TEXT_TYPES) and "html" not in ctype.lower():
            raise WebToolError("content-type is not text; refused", code="WEB_NOT_TEXT")
        body = resp.content[: _MAX_BYTES + 1]
        if len(body) > _MAX_BYTES:
            body = body[:_MAX_BYTES]
        text = extract_text(body.decode("utf-8", errors="replace"), ctype)
        record = {"url": target, "status": resp.status_code, "content_type": ctype, "text": text, "chars": len(text)}
        _atomic_write_json(_last_path(self._cfg), record)
        return record

    def fetch(self, url: str) -> dict:
        return self._get((url or "").strip(), self._require_enabled())

    def search(self, query: str) -> dict:
        needle = (query or "").strip()
        if not needle or len(needle) > _MAX_QUERY:
            raise WebToolError("search query must be 1–200 characters", code="WEB_BAD_QUERY")
        entries = self._require_enabled()[:_MAX_SEARCH_URLS]
        hits: list[dict] = []
        errors: list[dict] = []
        for entry in entries:
            url = entry["raw"] or f"{entry['scheme']}://{entry['host']}{entry['path']}"
            try:
                page = self._get(url, entries)
            except WebToolError as exc:
                # Code-only record in the HTTP body: str(exc) can carry hosts,
                # DNS detail, or upstream status text; server.py's _web_err
                # already follows the same code-only contract for raise paths.
                log.info("web search skipped %s: %s (%s)", url, exc, exc.code)
                errors.append({"url": url, "code": exc.code})
                continue
            snippets = _snippets(page["text"], needle)
            if snippets:
                hits.append({"url": page["url"], "snippets": snippets})
        return {"query": needle, "hits": hits, "errors": errors, "scanned": len(entries)}
