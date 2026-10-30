# -*- coding: utf-8 -*-
"""Hacker News — front page, search and threads through the public Algolia HN API."""

import html
import json
import re
import urllib.request
from typing import Any, List
from urllib.parse import urlencode, urlsplit

from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError, make_item, unix_to_iso

from .base import Action, Channel, Param

_UA = "agent-reach/1.0"
_API_BASE = "https://hn.algolia.com/api/v1"
_ITEM_URL = "https://news.ycombinator.com/item?id="
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_COMMENTS = 200

_ITEM_URL_RE = re.compile(r"^(?:https?://)?news\.ycombinator\.com/item\?(?:.*&)?id=(\d+)", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _get_json(path: str, timeout: float, **params: Any) -> Any:
    """Fetch one bounded JSON document from the Algolia HN API."""
    url = f"{_API_BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Hacker News API response exceeds the 4 MiB safety limit")
    return json.loads(raw.decode("utf-8"))


def _plain(text: Any) -> str:
    """Turn HN comment HTML into plain text."""
    if not text:
        return ""
    text = re.sub(r"<p>", "\n\n", str(text), flags=re.IGNORECASE)
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _parse_item_id(target: str) -> int:
    """Accept an item id or a https://news.ycombinator.com/item?id=<id> link."""
    t = str(target).strip()
    if t.isdigit():
        return int(t)
    match = _ITEM_URL_RE.match(t)
    if match:
        return int(match.group(1))
    raise ReachError(BAD_INPUT, "target must be a Hacker News item link (https://news.ycombinator.com/item?id=<id>) or id")


def _hit_item(hit: dict) -> dict:
    """One story from a search hit; `url` is the HN discussion, the linked page stays in raw."""
    story_id = hit.get("objectID") or hit.get("story_id")
    return make_item(
        title=hit.get("title") or hit.get("story_title"),
        url=f"{_ITEM_URL}{story_id}" if story_id else None,
        author=hit.get("author"),
        published_at=unix_to_iso(hit.get("created_at_i")) or hit.get("created_at"),
        text=_plain(hit.get("story_text") or hit.get("comment_text")) or hit.get("url"),
        raw=hit,
    )


def _flatten_comments(children: list, depth: int, out: List[str]) -> None:
    """Depth-first comment lines, indented by reply depth, capped at _MAX_COMMENTS."""
    for child in children or []:
        if len(out) >= _MAX_COMMENTS:
            return
        if not isinstance(child, dict):
            continue
        body = _plain(child.get("text"))
        if body:
            out.append(f"{'  ' * depth}@{child.get('author') or '[deleted]'}: {body}")
        _flatten_comments(child.get("children") or [], depth + 1, out)


class HackerNewsChannel(Channel):
    name = "hackernews"
    description = "Hacker News 首页、搜索与讨论串"

    level = "default"
    actions = (
        Action(
            "hot",
            "Stories on the front page now",
            (Param("limit", "max items", required=False, type=int, default=10),),
            (("hn-algolia", "_api_hot"),),
            live_test={"limit": 3},
        ),
        Action(
            "search",
            "Search stories",
            (
                Param("query", "what to search for"),
                Param("limit", "max items", required=False, type=int, default=10),
            ),
            (("hn-algolia", "_api_search"),),
            live_test={"query": "python", "limit": 3},
        ),
        Action(
            "read",
            "Read a story and its comment thread",
            (Param("target", "item link or id"),),
            (("hn-algolia", "_api_read"),),
            live_test={"target": "https://news.ycombinator.com/item?id=1"},
        ),
    )

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "news.ycombinator.com")

    def validate_params(self, action: str, params: dict) -> None:
        if action == "read":
            _parse_item_id(params.get("target", ""))
        limit = params.get("limit")
        if limit is not None and not 1 <= int(limit) <= 100:
            raise ReachError(BAD_INPUT, "limit must be between 1 and 100")

    # ------------------------------------------------------------------ #
    # Unified entry backends
    # ------------------------------------------------------------------ #

    def _api_hot(self, *, limit: int, timeout: float) -> list:
        data = _get_json("/search", timeout, tags="front_page", hitsPerPage=limit)
        return [_hit_item(h) for h in (data.get("hits") or [])[:limit]]

    def _api_search(self, *, query: str, limit: int, timeout: float) -> list:
        data = _get_json("/search", timeout, query=query, tags="story", hitsPerPage=limit)
        return [_hit_item(h) for h in (data.get("hits") or [])[:limit]]

    def _api_read(self, *, target: str, timeout: float) -> list:
        item_id = _parse_item_id(target)
        data = _get_json(f"/items/{item_id}", timeout)
        if not isinstance(data, dict) or not data.get("id"):
            raise ReachError(NOT_FOUND, f"Hacker News item not found: {item_id}")
        lines: List[str] = []
        link = data.get("url")
        if link and urlsplit(link).scheme in ("http", "https"):
            lines.append(f"Link: {link}")
        body = _plain(data.get("text"))
        if body:
            lines.append(body)
        comments: List[str] = []
        _flatten_comments(data.get("children") or [], 0, comments)
        if comments:
            lines.append("")
            lines.extend(comments)
        return [
            make_item(
                title=data.get("title"),
                url=f"{_ITEM_URL}{item_id}",
                author=data.get("author"),
                published_at=unix_to_iso(data.get("created_at_i")) or data.get("created_at"),
                text="\n".join(lines).strip(),
                raw=data,
            )
        ]
