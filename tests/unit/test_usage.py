"""usage 捕获与计费收口。

这里盯的是两件容易写错的事：
- 上游字节是**分块**到达的，SSE 的一行可能被拦腰切成两个 chunk；漏了半行缓存，
  行首的 "data:" 就再也匹配不上，用量静默变成未知。
- None 与 0 的语义不同：上游没报 usage ≠ 这一单没花钱。计费必须是 None（不记账），
  不能顺手补 0——补 0 会让「统计里有 100 个免费请求」这种假象永远查不出来。
"""

from __future__ import annotations

import pytest

from app import pricing, usage


def _sse_message_start(usage_json: str) -> str:
    return f'event: message_start\ndata: {{"type":"message_start","message":{{"usage":{usage_json}}}}}\n\n'


def _sniff(pieces: list[bytes]) -> usage.UsageSniffer:
    sniffer = usage.UsageSniffer()
    for piece in pieces:
        sniffer.feed(piece)
    sniffer.finalize()
    return sniffer


# ── SSE 捕获 ─────────────────────────────────────────────────────────────────
def test_sse_usage_across_chunk_boundaries():
    body = (_sse_message_start('{"input_tokens": 1000, "cache_read_input_tokens": 200}')
            + 'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":7}}\n\n')
    encoded = body.encode("utf-8")
    # 切在行中间（"da|ta:" 与 usage 数字中间）也要能认出来
    cuts = [1, 6, len(encoded) // 3, len(encoded) - 3]
    for cut in cuts:
        sniffer = _sniff([encoded[:cut], encoded[cut:]])
        assert sniffer.snapshot() == {"input_tokens": 1000, "output_tokens": 7,
                                      "cache_read_tokens": 200, "cache_write_tokens": None}, cut


def test_sse_message_delta_overwrites_start_output():
    sniffer = _sniff([
        (_sse_message_start('{"input_tokens": 3, "output_tokens": 0}')).encode(),
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":42}}\n\n',
    ])
    assert sniffer.snapshot()["input_tokens"] == 3
    assert sniffer.snapshot()["output_tokens"] == 42


def test_last_line_without_newline_still_counted():
    # message_stop 后上游可能不再补 \n：不 finalize 尾行的话 output_tokens 会丢
    sniffer = _sniff([
        (_sse_message_start('{"input_tokens": 1}')).encode(),
        b'data: {"type":"message_delta","usage":{"output_tokens":2}}',
    ])
    assert sniffer.snapshot()["output_tokens"] == 2


def test_garbage_and_done_are_ignored():
    sniffer = _sniff([b"data: [DONE]\n\n", b"data: {not json\n\n",
                      (_sse_message_start('{"input_tokens": 5}')).encode()])
    assert sniffer.snapshot()["input_tokens"] == 5


def test_bool_is_not_a_token_count():
    sniffer = _sniff([(_sse_message_start('{"input_tokens": true}')).encode()])
    assert sniffer.snapshot()["input_tokens"] is None


def test_cache_write_zero_kept_as_zero_not_none():
    # cache_creation_input_tokens 可以是合法的 0，但缺失才是 None
    sniffer = _sniff([(_sse_message_start('{"input_tokens": 1, "cache_creation_input_tokens": 0}')).encode()])
    assert sniffer.snapshot()["cache_write_tokens"] == 0


# ── 非流式整包 ───────────────────────────────────────────────────────────────
def test_json_body_usage_read_at_finalize():
    sniffer = _sniff([b'{"type":"message","usage":{"input_tokens":8,',
                      b'"output_tokens":2},"content":[]}'])
    assert sniffer.snapshot() == {"input_tokens": 8, "output_tokens": 2,
                                  "cache_read_tokens": None, "cache_write_tokens": None}


def test_huge_body_gives_up_without_poisoning_accounting():
    sniffer = usage.UsageSniffer()
    sniffer.feed(b"x" * (usage.MAX_OBSERVE_BYTES + 1))
    sniffer.feed((_sse_message_start('{"input_tokens": 1}')).encode())
    sniffer.finalize()
    assert sniffer.snapshot()["input_tokens"] is None


# ── 计费收口 ─────────────────────────────────────────────────────────────────
def _override(fresh_app, model: str, units: dict) -> None:
    tier = {"tier_id": "standard", "min_input": 0, **units}
    fresh_app.set_pricing_overrides(pricing.normalize_admin_table(
        {model: {"currency": "CNY", "as_of": "2026-10-01", "tiers": [tier]}}))


def test_cost_for_known_model(fresh_app):
    _override(fresh_app, "GLM-Cost-Test",
              {"uncached_input": 1_000_000, "cached_input": 100_000,
               "cache_write": 200_000, "output": 3_000_000})
    snap = {"input_tokens": 2, "output_tokens": 2,
            "cache_read_tokens": 10, "cache_write_tokens": 5}
    b = usage.cost_for("GLM-Cost-Test", snap)
    assert b is not None
    # 2*1e6 + 10*1e5 + 5*2e5 + 2*3e6，再各除 1e6
    assert b.total_ticks == 2 + 1 + 1 + 6


def test_cost_for_unknown_model_is_none_not_zero(fresh_app):
    snap = {"input_tokens": 10, "output_tokens": 10,
            "cache_read_tokens": None, "cache_write_tokens": None}
    assert usage.cost_for("GLM-No-Price-Here", snap) is None


def test_cost_for_without_any_tokens_is_none(fresh_app):
    assert usage.cost_for("GLM-5.3", dict.fromkeys(
        ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"))) is None


def test_cost_for_free_model_reports_zero(fresh_app):
    _override(fresh_app, "GLM-Free",
              {"uncached_input": 0, "cached_input": 0, "cache_write": 0, "output": 0})
    b = usage.cost_for("GLM-Free", {"input_tokens": 100, "output_tokens": 5,
                                    "cache_read_tokens": None, "cache_write_tokens": None})
    assert b is not None and b.total_ticks == 0


def test_snapshot_of_tolerates_non_dict():
    assert usage.snapshot_of(None) == {"input_tokens": None, "output_tokens": None,
                                       "cache_read_tokens": None, "cache_write_tokens": None}


@pytest.mark.parametrize("key", ["input_tokens", "output_tokens"])
def test_as_int_accepts_integral_floats_only(key):
    assert usage._as_int(3.0) == 3
    assert usage._as_int(3.5) is None
    assert usage._as_int(-1) is None
    assert usage._as_int("7") is None
