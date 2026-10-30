# -*- coding: utf-8 -*-
"""arXiv — paper search and abstracts through the official export API (Atom)."""

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import urlencode

from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError, make_item

from .base import Action, Channel, Param

_UA = "agent-reach/1.0"
_API_URL = "https://export.arxiv.org/api/query"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
#: arXiv asks clients to wait 3 seconds between requests. Its 429/503 answers are often
#: momentary on shared exits, so retry once after that pause before reporting a rate limit
#: (which pauses the whole channel for an hour).
_RETRY_DELAY = 3.0
_RETRY_CODES = frozenset({429, 503})

_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_SORTS = {"relevance": "relevance", "date": "submittedDate"}
# New-style ids (2401.00001, optional version) and old-style ids (cs/0101001).
_ID = r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)"
_ID_RE = re.compile(rf"^{_ID}$", re.IGNORECASE)
_URL_RE = re.compile(rf"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/{_ID}(?:\.pdf)?/?(?:[?#].*)?$", re.IGNORECASE)


def _parse_paper_id(target: str) -> str:
    """Accept an arXiv id or an abs/pdf/html link."""
    t = str(target).strip()
    if t.lower().startswith("arxiv:"):
        t = t[6:]
    if _ID_RE.match(t):
        return t
    match = _URL_RE.match(t)
    if match:
        return match.group(1)
    raise ReachError(BAD_INPUT, "target must be an arXiv id (e.g. 2401.00001) or an arxiv.org/abs/<id> link")


def _fetch(params: dict, timeout: float) -> bytes:
    url = f"{_API_URL}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=max(deadline - time.monotonic(), 1)) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise ValueError("arXiv API response exceeds the 4 MiB safety limit")
            return raw
        except urllib.error.HTTPError as exc:
            if exc.code not in _RETRY_CODES or attempt or deadline - time.monotonic() <= _RETRY_DELAY + 1:
                raise
            time.sleep(_RETRY_DELAY)
    raise AssertionError("unreachable")


def _text(node: Optional[ET.Element]) -> str:
    return re.sub(r"\s+", " ", node.text or "").strip() if node is not None else ""


def _entries(raw: bytes) -> list:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"arXiv returned unreadable XML: {exc}") from None
    items = []
    for entry in root.findall("a:entry", _NS):
        entry_id = _text(entry.find("a:id", _NS))
        # An unknown id comes back as an entry without a title and an error id.
        if not entry_id or "/api/errors" in entry_id:
            continue
        authors = [_text(a.find("a:name", _NS)) for a in entry.findall("a:author", _NS)]
        pdf = next((link.get("href") for link in entry.findall("a:link", _NS) if link.get("title") == "pdf"), None)
        primary = entry.find("arxiv:primary_category", _NS)
        raw_entry = {
            "id": entry_id.rsplit("/abs/", 1)[-1],
            "authors": authors,
            "categories": [c.get("term") for c in entry.findall("a:category", _NS) if c.get("term")],
            "primary_category": primary.get("term") if primary is not None else None,
            "updated": _text(entry.find("a:updated", _NS)) or None,
            "pdf_url": pdf,
            "doi": _text(entry.find("arxiv:doi", _NS)) or None,
            "journal_ref": _text(entry.find("arxiv:journal_ref", _NS)) or None,
        }
        items.append(
            make_item(
                title=_text(entry.find("a:title", _NS)),
                url=entry_id.replace("http://", "https://", 1),
                author=", ".join(a for a in authors if a),
                published_at=_text(entry.find("a:published", _NS)) or None,
                text=_text(entry.find("a:summary", _NS)),
                raw=raw_entry,
            )
        )
    return items


class ArxivChannel(Channel):
    name = "arxiv"
    description = "arXiv 论文搜索与摘要"

    level = "default"
    actions = (
        Action(
            "search",
            "Search papers (title, abstract, authors); arXiv query syntax such as cat:cs.AI is accepted",
            (
                Param("query", "keywords or arXiv query, e.g. 'langgraph' or 'cat:cs.CL AND ti:agent'"),
                Param("limit", "max items", required=False, type=int, default=10),
                Param("sort", "relevance or date (newest first)", required=False, default="relevance"),
            ),
            (("arxiv-api", "_api_search"),),
            live_test={"query": "large language model", "limit": 2},
            timeout=60,
        ),
        Action(
            "read",
            "A paper's metadata and abstract (not the PDF full text)",
            (Param("target", "arXiv id or abs/pdf link"),),
            (("arxiv-api", "_api_read"),),
            live_test={"target": "https://arxiv.org/abs/1706.03762"},
            timeout=60,
        ),
    )

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "arxiv.org")

    def validate_params(self, action: str, params: dict) -> None:
        if action == "read":
            _parse_paper_id(params.get("target", ""))
        if action == "search":
            if params.get("sort") not in _SORTS:
                raise ReachError(BAD_INPUT, "sort must be relevance or date")
            if not 1 <= params.get("limit", 1) <= 100:
                raise ReachError(BAD_INPUT, "limit must be between 1 and 100")

    # ------------------------------------------------------------------ #
    # Unified entry backends
    # ------------------------------------------------------------------ #

    def _api_search(self, *, query: str, limit: int, sort: str, timeout: float) -> list:
        search = query if re.search(r"\b(?:all|ti|au|abs|cat|co|jr|rn|id):", query) else f"all:{query}"
        raw = _fetch(
            {
                "search_query": search,
                "start": 0,
                "max_results": limit,
                "sortBy": _SORTS[sort],
                "sortOrder": "descending",
            },
            timeout,
        )
        return _entries(raw)[:limit]

    def _api_read(self, *, target: str, timeout: float) -> list:
        paper_id = _parse_paper_id(target)
        items = _entries(_fetch({"id_list": paper_id, "max_results": 1}, timeout))
        if not items:
            raise ReachError(NOT_FOUND, f"arXiv paper not found: {paper_id}")
        return items[:1]
