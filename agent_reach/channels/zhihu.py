# -*- coding: utf-8 -*-
"""Zhihu hot list, search, questions and answers through OpenCLI in the shared Agent Reach browser."""

import re
from typing import Tuple

from agent_reach.runtime.result import BAD_INPUT, ReachError

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param

_HOST = r"(?:https?://)?(?:www\.)?zhihu\.com"
_ANSWER_RE = re.compile(rf"^{_HOST}/question/(\d+)/answer/(\d+)(?:[/?#].*)?$", re.IGNORECASE)
_QUESTION_RE = re.compile(rf"^{_HOST}/question/(\d+)(?:[/?#].*)?$", re.IGNORECASE)
_SEARCH_TYPES = ("all", "answer", "article", "question")


def _parse_target(target: str) -> Tuple[str, str]:
    """Return ("answer", "<question>:<answer>") or ("question", "<id>") for a link or question id."""
    t = str(target).strip()
    match = _ANSWER_RE.match(t)
    if match:
        return "answer", f"{match.group(1)}:{match.group(2)}"
    match = _QUESTION_RE.match(t)
    if match:
        return "question", match.group(1)
    if t.isdigit():
        return "question", t
    raise ReachError(
        BAD_INPUT,
        "target must be a Zhihu question or answer link (https://www.zhihu.com/question/<id>[/answer/<id>]) "
        "or a question id; Zhuanlan articles are not supported",
    )


class ZhihuChannel(OpenCLISiteChannel):
    name = "zhihu"
    description = "知乎热榜、搜索、问题与回答"
    site = "zhihu"
    domains = ("zhihu.com",)
    usage = "opencli zhihu hot/search/question/answer-detail -f yaml"
    login_hint = "zhihu.com"
    login_cookie = "z_c0"

    level = "optional"
    login_url = "https://www.zhihu.com/signin"
    actions = (
        Action(
            "hot",
            "Zhihu hot list",
            (Param("limit", "max items", required=False, type=int, default=10),),
            (("opencli", "_opencli_hot"),),
            live_test={"limit": 3},
        ),
        Action(
            "search",
            "Search Zhihu",
            (
                Param("query", "what to search for"),
                Param("limit", "max results", required=False, type=int, default=10),
                Param("type", "all, answer, article or question", required=False, default="all"),
            ),
            (("opencli", "_opencli_search"),),
            live_test={"query": "Python", "limit": 2},
            public_web=("zhihu.com/",),
        ),
        Action(
            "read",
            "A question with its top answers, or one full answer",
            (
                Param("target", "question or answer link, or question id"),
                Param("limit", "answers to return for a question", required=False, type=int, default=5),
            ),
            (("opencli", "_opencli_read"),),
            live_test={"target": "https://www.zhihu.com/question/19550225", "limit": 2},
        ),
    )

    def validate_params(self, action: str, params: dict) -> None:
        if action == "read":
            _parse_target(params.get("target", ""))
        if action == "search" and params.get("type") not in _SEARCH_TYPES:
            raise ReachError(BAD_INPUT, f"type must be one of: {', '.join(_SEARCH_TYPES)}")
        limit = params.get("limit")
        if limit is not None and not 1 <= limit <= 50:
            raise ReachError(BAD_INPUT, "limit must be between 1 and 50")

    def _opencli_hot(self, *, limit: int, timeout: float) -> list:
        return self._opencli(["hot", "--limit", str(limit)], timeout)[:limit]

    def _opencli_search(self, *, query: str, limit: int, type: str, timeout: float) -> list:
        return self._opencli(["search", query, "--limit", str(limit), "--type", type], timeout)[:limit]

    def _opencli_read(self, *, target: str, limit: int, timeout: float) -> list:
        kind, ref = _parse_target(target)
        if kind == "answer":
            items = self._opencli(["answer-detail", f"answer:{ref}"], timeout)[:1]
            for item in items:
                raw = item.get("raw") or {}
                item["title"] = item["title"] or raw.get("question_title") or None
            return items
        return self._opencli(["question", ref, "--limit", str(limit)], timeout)[:limit]
