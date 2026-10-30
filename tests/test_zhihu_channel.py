# -*- coding: utf-8 -*-
"""Offline tests for the ``zhihu`` channel (OpenCLI calls stubbed)."""

from unittest.mock import patch

import pytest

from agent_reach.channels import get_channel
from agent_reach.channels import zhihu as zh
from agent_reach.channels.zhihu import ZhihuChannel
from agent_reach.runtime.dispatch import validate_action
from agent_reach.runtime.result import BAD_INPUT, ReachError, make_item


def test_registered_as_optional_opencli_channel():
    ch = get_channel("zhihu")
    assert isinstance(ch, ZhihuChannel)
    assert ch.level == "optional"
    assert ch.tools == ("opencli",)
    assert ch.login_url == "https://www.zhihu.com/signin"
    assert ch.login_cookie == "z_c0"
    assert [a.name for a in ch.actions] == ["hot", "search", "read"]


def test_can_handle_zhihu_hosts_only():
    ch = ZhihuChannel()
    assert ch.can_handle("https://www.zhihu.com/question/1")
    assert ch.can_handle("https://zhuanlan.zhihu.com/p/1")
    assert not ch.can_handle("https://zhihu.com.evil.test/question/1")
    assert not ch.can_handle("https://example.com")


@pytest.mark.parametrize(
    "target, expected",
    [
        ("2065373304001656222", ("question", "2065373304001656222")),
        ("https://www.zhihu.com/question/2065373304001656222", ("question", "2065373304001656222")),
        ("zhihu.com/question/42?utm=x", ("question", "42")),
        ("https://www.zhihu.com/question/42/answer/7", ("answer", "42:7")),
        ("https://www.zhihu.com/question/42/answer/7?share=1", ("answer", "42:7")),
    ],
)
def test_parse_target_forms(target, expected):
    assert zh._parse_target(target) == expected


@pytest.mark.parametrize("target", ["", "abc", "https://zhuanlan.zhihu.com/p/1", "https://example.com/question/1"])
def test_parse_target_rejects_others(target):
    with pytest.raises(ReachError) as err:
        zh._parse_target(target)
    assert err.value.code == BAD_INPUT


def test_validate_rejects_bad_type_limit_and_target_before_setup():
    ch = ZhihuChannel()
    validate_action(ch, "search", {"query": "python", "type": "answer"})
    for action, params in [
        ("search", {"query": "python", "type": "video"}),
        ("hot", {"limit": 200}),
        ("read", {"target": "https://zhuanlan.zhihu.com/p/1"}),
    ]:
        with pytest.raises(ReachError):
            validate_action(ch, action, params)


@pytest.mark.parametrize(
    "method, kwargs, expected_args",
    [
        ("_opencli_hot", {"limit": 3}, ["hot", "--limit", "3"]),
        ("_opencli_search", {"query": "LangGraph", "limit": 2, "type": "all"},
         ["search", "LangGraph", "--limit", "2", "--type", "all"]),
        ("_opencli_read", {"target": "https://www.zhihu.com/question/42", "limit": 4},
         ["question", "42", "--limit", "4"]),
    ],
)
def test_actions_call_read_only_opencli_commands(method, kwargs, expected_args):
    ch = ZhihuChannel()
    with patch.object(ZhihuChannel, "_opencli", return_value=[make_item(title="t")]) as call:
        getattr(ch, method)(timeout=30, **kwargs)
    assert call.call_args.args[0] == expected_args


def test_read_answer_uses_question_title():
    row = {"id": "7", "author": "a", "question_title": "Q?", "content": "body",
           "url": "https://www.zhihu.com/question/42/answer/7"}
    ch = ZhihuChannel()
    with patch.object(ZhihuChannel, "_opencli", return_value=[make_item(text="body", raw=row)]) as call:
        [item] = ch._opencli_read(target="https://www.zhihu.com/question/42/answer/7", limit=5, timeout=30)
    assert call.call_args.args[0] == ["answer-detail", "answer:42:7"]
    assert item["title"] == "Q?"
