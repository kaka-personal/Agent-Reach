# -*- coding: utf-8 -*-
"""Offline tests for the ``hackernews`` channel (Algolia HN API stubbed)."""

from unittest.mock import patch

import pytest

from agent_reach.channels import get_channel
from agent_reach.channels import hackernews as hn
from agent_reach.channels.hackernews import HackerNewsChannel
from agent_reach.runtime.dispatch import validate_action
from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError


def test_registered_as_zero_config_default_channel():
    ch = get_channel("hackernews")
    assert isinstance(ch, HackerNewsChannel)
    assert ch.level == "default"
    assert ch.tools == () and ch.login_url == "" and ch.needs_key == ""
    assert [a.name for a in ch.actions] == ["hot", "search", "read"]


def test_can_handle_only_hn_hosts():
    ch = HackerNewsChannel()
    assert ch.can_handle("https://news.ycombinator.com/item?id=1")
    assert ch.can_handle("https://NEWS.ycombinator.com/news")
    for url in ["https://ycombinator.com", "https://news.ycombinator.com.evil.test/item?id=1", ""]:
        assert not ch.can_handle(url), url


@pytest.mark.parametrize(
    "target, expected",
    [
        ("8863", 8863),
        ("https://news.ycombinator.com/item?id=8863", 8863),
        ("news.ycombinator.com/item?id=8863#c1", 8863),
        ("https://news.ycombinator.com/item?p=2&id=8863", 8863),
    ],
)
def test_parse_item_id_forms(target, expected):
    assert hn._parse_item_id(target) == expected


@pytest.mark.parametrize("target", ["", "abc", "https://example.com/item?id=1", "https://news.ycombinator.com/news"])
def test_parse_item_id_rejects_other_targets(target):
    with pytest.raises(ReachError) as err:
        hn._parse_item_id(target)
    assert err.value.code == BAD_INPUT


def test_validate_rejects_bad_read_target_and_large_limit():
    ch = HackerNewsChannel()
    with pytest.raises(ReachError):
        validate_action(ch, "read", {"target": "https://example.com"})
    with pytest.raises(ReachError):
        validate_action(ch, "hot", {"limit": 500})
    validate_action(ch, "hot", {"limit": 5})


def test_hot_maps_front_page_hits():
    payload = {
        "hits": [
            {"objectID": "42", "title": "Show HN: thing", "author": "pg", "created_at_i": 1700000000,
             "url": "https://example.com/thing", "story_text": None},
            {"objectID": "43", "title": "Ask HN: q", "author": "dang", "created_at_i": 1700000100,
             "story_text": "<p>Body &amp; more"},
        ]
    }
    with patch.object(hn, "_get_json", return_value=payload) as get:
        items = HackerNewsChannel()._api_hot(limit=2, timeout=5)
    path, timeout = get.call_args.args
    assert path == "/search" and get.call_args.kwargs == {"tags": "front_page", "hitsPerPage": 2}
    assert items[0]["url"] == "https://news.ycombinator.com/item?id=42"
    assert items[0]["text"] == "https://example.com/thing"
    assert items[0]["published_at"] == "2023-11-14T22:13:20Z"
    assert items[1]["text"] == "Body & more"


def test_search_limits_to_stories():
    with patch.object(hn, "_get_json", return_value={"hits": []}) as get:
        assert HackerNewsChannel()._api_search(query="rust", limit=3, timeout=5) == []
    assert get.call_args.kwargs == {"query": "rust", "tags": "story", "hitsPerPage": 3}


def test_read_flattens_comment_tree_with_depth():
    payload = {
        "id": 1, "title": "Story", "author": "a", "created_at_i": 1700000000,
        "url": "https://example.com", "text": None,
        "children": [
            {"author": "b", "text": "top <i>level</i>", "children": [
                {"author": "c", "text": "reply", "children": []},
            ]},
            {"author": None, "text": None, "children": []},
        ],
    }
    with patch.object(hn, "_get_json", return_value=payload):
        [item] = HackerNewsChannel()._api_read(target="1", timeout=5)
    assert item["url"] == "https://news.ycombinator.com/item?id=1"
    assert item["text"].splitlines() == [
        "Link: https://example.com",
        "",
        "@b: top level",
        "  @c: reply",
    ]


def test_read_caps_comment_count():
    children = [{"author": f"u{i}", "text": "x", "children": []} for i in range(hn._MAX_COMMENTS + 50)]
    with patch.object(hn, "_get_json", return_value={"id": 1, "title": "t", "children": children}):
        [item] = HackerNewsChannel()._api_read(target="1", timeout=5)
    assert sum(line.startswith("@") for line in item["text"].splitlines()) == hn._MAX_COMMENTS


def test_read_missing_item_is_not_found():
    with patch.object(hn, "_get_json", return_value={"status": 404}):
        with pytest.raises(ReachError) as err:
            HackerNewsChannel()._api_read(target="1", timeout=5)
    assert err.value.code == NOT_FOUND


def test_get_json_rejects_oversized_responses():
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"x" * n

    with patch.object(hn.urllib.request, "urlopen", return_value=Resp()):
        with pytest.raises(ValueError, match="safety limit"):
            hn._get_json("/search", 5, query="x")
