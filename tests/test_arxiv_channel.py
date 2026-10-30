# -*- coding: utf-8 -*-
"""Offline tests for the ``arxiv`` channel (export API stubbed)."""

import urllib.error
from unittest.mock import patch

import pytest

from agent_reach.channels import arxiv as ax
from agent_reach.channels import get_channel
from agent_reach.channels.arxiv import ArxivChannel
from agent_reach.runtime.dispatch import validate_action
from agent_reach.runtime.result import BAD_INPUT, NOT_FOUND, ReachError

FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <updated>2023-08-02T00:41:18Z</updated>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence
      transduction models ...</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:doi>10.1000/example</arxiv:doi>
    <link href="http://arxiv.org/abs/1706.03762v7" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/1706.03762v7" rel="related" type="application/pdf"/>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""

ERROR_FEED = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_9999.99999</id><title>Error</title></entry>
</feed>"""


def test_registered_as_zero_config_default_channel():
    ch = get_channel("arxiv")
    assert isinstance(ch, ArxivChannel)
    assert ch.level == "default"
    assert ch.tools == () and ch.login_url == "" and ch.needs_key == ""
    assert {a.name: a.timeout for a in ch.actions} == {"search": 60, "read": 60}


def test_can_handle_arxiv_hosts_only():
    ch = ArxivChannel()
    assert ch.can_handle("https://arxiv.org/abs/1706.03762")
    assert ch.can_handle("https://export.arxiv.org/abs/1706.03762")
    assert not ch.can_handle("https://arxiv.org.evil.test/abs/1")
    assert not ch.can_handle("https://example.com")


@pytest.mark.parametrize(
    "target, expected",
    [
        ("1706.03762", "1706.03762"),
        ("arXiv:1706.03762v7", "1706.03762v7"),
        ("https://arxiv.org/abs/1706.03762", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v2.pdf", "1706.03762v2"),
        ("arxiv.org/html/2401.12345v1", "2401.12345v1"),
        ("https://arxiv.org/abs/cs/0101001", "cs/0101001"),
        ("https://arxiv.org/abs/math.GT/0309136", "math.GT/0309136"),
    ],
)
def test_parse_paper_id_forms(target, expected):
    assert ax._parse_paper_id(target) == expected


@pytest.mark.parametrize("target", ["", "attention", "https://example.com/abs/1706.03762", "1706.03762&x=1"])
def test_parse_paper_id_rejects_others(target):
    with pytest.raises(ReachError) as err:
        ax._parse_paper_id(target)
    assert err.value.code == BAD_INPUT


def test_validate_search_sort_and_limit():
    ch = ArxivChannel()
    validate_action(ch, "search", {"query": "llm", "sort": "date"})
    with pytest.raises(ReachError):
        validate_action(ch, "search", {"query": "llm", "sort": "citations"})
    with pytest.raises(ReachError):
        validate_action(ch, "search", {"query": "llm", "limit": 1000})


def test_entries_normalise_whitespace_and_fields():
    [item] = ax._entries(FEED)
    assert item["title"] == "Attention Is All You Need"
    assert item["url"] == "https://arxiv.org/abs/1706.03762v7"
    assert item["author"] == "Ashish Vaswani, Noam Shazeer"
    assert item["published_at"] == "2017-06-12T17:57:34Z"
    assert item["text"] == "The dominant sequence transduction models ..."
    assert item["raw"]["id"] == "1706.03762v7"
    assert item["raw"]["pdf_url"] == "http://arxiv.org/pdf/1706.03762v7"
    assert item["raw"]["primary_category"] == "cs.CL"
    assert item["raw"]["categories"] == ["cs.CL", "cs.LG"]
    assert item["raw"]["doi"] == "10.1000/example"


def test_entries_rejects_bad_xml():
    with pytest.raises(ValueError, match="unreadable XML"):
        ax._entries(b"<feed>")


def test_search_wraps_plain_keywords_and_keeps_field_queries():
    ch = ArxivChannel()
    with patch.object(ax, "_fetch", return_value=FEED) as fetch:
        ch._api_search(query="graph agents", limit=5, sort="relevance", timeout=10)
        ch._api_search(query="cat:cs.AI AND ti:agent", limit=5, sort="date", timeout=10)
    first, second = (c.args[0] for c in fetch.call_args_list)
    assert first["search_query"] == "all:graph agents" and first["sortBy"] == "relevance"
    assert second["search_query"] == "cat:cs.AI AND ti:agent" and second["sortBy"] == "submittedDate"
    assert first["max_results"] == 5


def test_read_unknown_id_is_not_found():
    with patch.object(ax, "_fetch", return_value=ERROR_FEED):
        with pytest.raises(ReachError) as err:
            ArxivChannel()._api_read(target="1706.03762", timeout=10)
    assert err.value.code == NOT_FOUND


def _http_error(code):
    return urllib.error.HTTPError("https://export.arxiv.org/api/query", code, "x", {}, None)


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return self.body[:n]


@pytest.mark.parametrize("code", [429, 503])
def test_fetch_retries_once_after_transient_limit(code):
    with patch.object(ax.urllib.request, "urlopen", side_effect=[_http_error(code), _Resp(FEED)]) as urlopen, \
            patch.object(ax.time, "sleep") as sleep:
        assert ax._fetch({"id_list": "1"}, timeout=30) == FEED
    assert urlopen.call_count == 2
    sleep.assert_called_once_with(ax._RETRY_DELAY)


def test_fetch_reports_second_rate_limit_and_skips_other_errors():
    with patch.object(ax.urllib.request, "urlopen", side_effect=[_http_error(429), _http_error(429)]), \
            patch.object(ax.time, "sleep"):
        with pytest.raises(urllib.error.HTTPError) as err:
            ax._fetch({"id_list": "1"}, timeout=30)
    assert err.value.code == 429
    with patch.object(ax.urllib.request, "urlopen", side_effect=[_http_error(500)]) as urlopen, \
            patch.object(ax.time, "sleep") as sleep:
        with pytest.raises(urllib.error.HTTPError):
            ax._fetch({"id_list": "1"}, timeout=30)
    assert urlopen.call_count == 1
    sleep.assert_not_called()


def test_fetch_does_not_retry_without_time_left():
    with patch.object(ax.urllib.request, "urlopen", side_effect=[_http_error(503)]) as urlopen, \
            patch.object(ax.time, "sleep") as sleep:
        with pytest.raises(urllib.error.HTTPError):
            ax._fetch({"id_list": "1"}, timeout=2)
    assert urlopen.call_count == 1
    sleep.assert_not_called()


def test_fetch_rejects_oversized_responses():
    with patch.object(ax.urllib.request, "urlopen", return_value=_Resp(b"x" * (ax._MAX_RESPONSE_BYTES + 10))):
        with pytest.raises(ValueError, match="safety limit"):
            ax._fetch({"id_list": "1"}, timeout=30)
