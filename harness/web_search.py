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
``mcp_hybrid_server``, ``agentic``, ``sync``, or ``guardrails``. Tool
name-gating uses ``utils.tool_broker``. It never starts ``/api/agent/*``.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
from html import parser, unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx

from harness.config import HarnessConfig, _atomic_write_json
from utils.errors import AgenticError
from utils.tool_broker import ToolDenied, assert_allowed

log = logging.getLogger("cyclaw.harness.web_search")

_UTF8 = "utf-8"
_SCHEMES = frozenset(("http", "https"))
_MAX_ALLOW = 32
_MAX_BYTES = 262_144
_READ_CHUNK_BYTES = 65_536
_MAX_PORT = 65_535
_TIMEOUT_SEC = 8.0
_MAX_QUERY = 200
_MAX_SNIPPET = 160
_MAX_HITS_PER_URL = 3
_MAX_SEARCH_URLS = 8
_MAX_CONTEXT = 4000
_MAX_RAW = 500
_SNIP_BEFORE = 40
_SNIP_AFTER = 120
_HTTP_OK_BELOW = 400
_BLOCKED_HOSTS = frozenset((
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
))
_SKIP_TAGS = frozenset(("script", "style", "noscript", "template"))
_PATH_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~/=+-"
)
_BREAK_TAGS = frozenset(("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "pre"))
_TEXT_TYPES = (
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


def _port_of(parsed) -> str:
    try:
        port = parsed.port
    except ValueError as exc:
        raise WebToolError("URL port is invalid", code="WEB_BAD_URL") from exc
    if port is None or port == {"http": 80, "https": 443}[parsed.scheme]:
        return ""
    return str(port)


def _authority(host: str, port: str = "") -> str:
    """Render a host and optional port as a URL authority."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return f"{host}:{port}" if port else host
    if ip.version == 6:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


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
    port = _port_of(parsed)
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
    if not path.startswith("/"):  # pragma: no cover -- urlparse http(s)+host path is "" or /-prefixed
        path = f"/{path}"
    return {
        "scheme": parsed.scheme,
        "host": host,
        "port": port,
        "path": path,
        "raw": f"{parsed.scheme}://{_authority(host, port)}{path}",
    }


def _load_entries(path: Path) -> list[dict[str, str]]:
    try:
        parsed = json.loads(path.read_text(encoding=_UTF8))
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Existing corrupt allowlist must not look like "empty" -- allow/deny
        # would then rewrite from [] and wipe recoverable bytes.
        raise WebToolError("allowlist is unreadable", code="WEB_ALLOWLIST_UNREADABLE") from exc
    rows = parsed.get("entries", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict) and row.get("host"):
            out.append({
                "scheme": str(row.get("scheme") or "https"),
                "host": _host_of(str(row.get("host") or "")),
                "port": str(row.get("port") or ""),
                "path": str(row.get("path") or "/"),
                "raw": str(row.get("raw") or ""),
            })
    return out


def _save_entries(path: Path, entries: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {"entries": entries})


def _entry_key(entry: dict[str, str]) -> tuple[str, str, str]:
    return entry["host"], str(entry.get("port") or ""), entry["path"]


def _entry_raw(entry: dict[str, str]) -> str:
    return entry["raw"] or f"{entry['scheme']}://{_authority(entry['host'], entry.get('port', ''))}{entry['path']}"


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
        if str(entry.get("port") or "") != wanted["port"]:
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
    port = str(match.get("port") or "")
    if port and (not port.isdecimal() or not 0 < int(port) <= _MAX_PORT):
        raise WebToolError("allowlist port is invalid", code="WEB_BAD_URL")
    path = match["path"] or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    if any(ch not in _PATH_CHARS for ch in path):
        raise WebToolError("allowlist path is invalid", code="WEB_BAD_URL")
    return f"{scheme}://{_authority(host, port)}{path}"


def _read_text_response(resp: httpx.Response) -> tuple[bytes, str, int]:
    """Validate and consume at most the configured number of response bytes."""
    if resp.status_code >= _HTTP_OK_BELOW:
        raise WebToolError(
            f"upstream HTTP {resp.status_code}",
            code="WEB_FETCH_FAILED",
            details={"status": resp.status_code},
        )
    ctype = resp.headers.get("content-type", "text/plain")
    is_text = any(ctype.lower().startswith(prefix) for prefix in _TEXT_TYPES)
    if not is_text and "html" not in ctype.lower():
        raise WebToolError("content-type is not text; refused", code="WEB_NOT_TEXT")
    body = bytearray()
    for chunk in resp.iter_bytes(chunk_size=_READ_CHUNK_BYTES):
        remaining = _MAX_BYTES - len(body)
        body.extend(chunk[:remaining])
        if len(body) >= _MAX_BYTES:
            break
    return bytes(body), ctype, resp.status_code


class WebTool:
    """Persist allowlist + last extract; perform gated GETs."""

    def __init__(
        self,
        cfg: HarnessConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver=assert_public_host,
        audit_cfg: dict | None = None,
    ) -> None:
        self._cfg = cfg
        self._resolver = resolver
        self._transport = transport
        self._audit_cfg = audit_cfg

    def status(self) -> dict:
        entries = _load_entries(_allow_path(self._cfg))
        ctx = _context_path(self._cfg)
        last = _last_path(self._cfg)
        return {
            "enabled": bool(self._cfg.web_enabled),
            "allowlist": [_entry_raw(row) for row in entries],
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
        key = _entry_key(entry)
        if any(_entry_key(row) == key for row in entries):
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
            if _entry_key(row) != _entry_key(entry)
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
                with client.stream("GET", target) as resp:
                    body, ctype, status = _read_text_response(resp)
            except httpx.HTTPError:
                raise WebToolError("fetch failed", code="WEB_FETCH_FAILED") from None
        text = extract_text(bytes(body).decode("utf-8", errors="replace"), ctype)
        return {"url": target, "status": status, "content_type": ctype, "text": text, "chars": len(text)}

    def _web_tool_allowlist(self) -> frozenset[str]:
        if not self._cfg.web_enabled:
            return frozenset()
        return frozenset(("web_fetch", "web_search"))

    def _gate_tool(self, name: str, argv: tuple[str, ...]) -> None:
        try:
            assert_allowed(name, argv, allowlist=self._web_tool_allowlist(), cfg=self._audit_cfg)
        except ToolDenied as exc:
            raise WebToolError(exc.message, code="WEB_TOOL_DENIED", details=exc.details) from exc

    def fetch(self, url: str) -> dict:
        target = (url or "").strip()
        entries = self._require_enabled()
        self._gate_tool("web_fetch", (target,))
        page = self._get(target, entries)
        _atomic_write_json(_last_path(self._cfg), page)
        return page

    def search(self, query: str) -> dict:
        needle = (query or "").strip()
        if not needle or len(needle) > _MAX_QUERY:
            raise WebToolError("search query must be 1–200 characters", code="WEB_BAD_QUERY")
        entries = self._require_enabled()[:_MAX_SEARCH_URLS]
        self._gate_tool("web_search", (needle,))
        hits: list[dict] = []
        errors: list[dict] = []
        # _get() no longer writes _last_path itself (see fetch()) -- a search
        # spans up to _MAX_SEARCH_URLS entries, and unconditionally overwriting
        # on every successful fetch left web_last.json holding whichever entry
        # happened to be LAST in allowlist order, regardless of whether it
        # matched the query. /web inject reads only that file, so it could
        # inject an unrelated page while the actually-matching hit was
        # discarded. Record only the first hit-bearing page instead.
        recorded_last = False
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
                if not recorded_last:
                    _atomic_write_json(_last_path(self._cfg), page)
                    recorded_last = True
        return {"query": needle, "hits": hits, "errors": errors, "scanned": len(entries)}
