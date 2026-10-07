"""上游 token 用量的捕获与计费。

网关对 /v1/messages 是原样字节透传，但监控里要看得见「这一单花了多少钱」，
所以在透传通道上挂一个只观察、不拦截的 sniffer：字节照常交给客户端，
顺路把 SSE 事件（或整包 JSON）里的 usage 捞出来。

口径一律用 Anthropic 的：input_tokens 是**不含缓存**的那段，缓存命中与写入各自
单列——这既是上游的原始语义，也是 pricing.resolve_breakdown 的入参语义。
"""

from __future__ import annotations

import json
from typing import Any

from . import pricing
from .store import store

# 观察上限：异常大的响应体不值得占用监控的内存，超过就放弃解析（用量记为未知）
MAX_OBSERVE_BYTES = 1024 * 1024


def _as_int(value: Any) -> int | None:
    """None=上游没给（未知），0=真的是 0，两者语义不同，不能合并。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


class UsageSniffer:
    """边透传边累加 usage；`snapshot()` 给出规范四件套。"""

    def __init__(self) -> None:
        self.usage: dict[str, Any] = {}
        self._body = bytearray()
        self._consumed = 0
        self._sse = False
        self._give_up = False

    def feed(self, chunk: bytes) -> None:
        if self._give_up:
            return
        self._body += chunk
        if len(self._body) > MAX_OBSERVE_BYTES:
            self._give_up = True
            self.usage = {}
            return
        self._scan_lines()

    def _scan_lines(self) -> None:
        """只消费已经完整（有结尾 \\n）的行，半行留给下一次 feed。"""
        start = self._consumed
        while True:
            nl = self._body.find(b"\n", start)
            if nl < 0:
                break
            line = bytes(self._body[start:nl]).strip()
            start = nl + 1
            if line.startswith(b"data:"):
                self._sse = True
                self._absorb_data(line[5:].strip())
        self._consumed = start

    def _absorb_data(self, payload: bytes) -> None:
        if not payload or payload == b"[DONE]":
            return
        evt = _loads(payload)
        if isinstance(evt, dict):
            self.absorb_event(evt)

    def absorb_event(self, evt: dict[str, Any]) -> None:
        """Anthropic SSE 事件 → usage。"""
        etype = evt.get("type")
        if etype == "message_start":
            self._merge((evt.get("message") or {}).get("usage"))
        elif etype == "message_delta":
            self._merge(evt.get("usage"))

    def _merge(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key, value in usage.items():
            if value is not None:
                self.usage[key] = value

    def finalize(self) -> None:
        """收尾：吃掉没有结尾换行的最后一行；整包不是 SSE 时按 JSON 读一次 usage。"""
        if self._give_up or not self._body:
            return
        tail = bytes(self._body[self._consumed:]).strip()
        self._consumed = len(self._body)
        if tail.startswith(b"data:"):
            self._sse = True
            self._absorb_data(tail[5:].strip())
        if not self._sse:
            data = _loads(bytes(self._body))
            if isinstance(data, dict):
                self._merge(data.get("usage"))

    def snapshot(self) -> dict[str, int | None]:
        return snapshot_of(self.usage)


def snapshot_of(raw: Any) -> dict[str, int | None]:
    """上游 usage（Anthropic 口径）→ 规范四件套；缺失的键是 None 而不是 0。"""
    u = raw if isinstance(raw, dict) else {}
    return {"input_tokens": _as_int(u.get("input_tokens")),
            "output_tokens": _as_int(u.get("output_tokens")),
            "cache_read_tokens": _as_int(u.get("cache_read_input_tokens")),
            "cache_write_tokens": _as_int(u.get("cache_creation_input_tokens"))}


def _loads(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "ignore"))
    except ValueError:
        return None


def cost_for(model: str, snap: dict[str, int | None]) -> pricing.PricingBreakdown | None:
    """这一单值多少钱；无价目或没有任何 token 数 → None（未知，不按 0 记账）。"""
    if not any(snap.get(k) for k in ("input_tokens", "output_tokens",
                                     "cache_read_tokens", "cache_write_tokens")):
        return None
    table = pricing.effective_table(store.pricing_overrides(), store.pricing_pulled())
    return pricing.resolve_breakdown(
        table, model,
        input_tokens=snap.get("input_tokens"),
        output_tokens=snap.get("output_tokens"),
        cache_read=snap.get("cache_read_tokens"),
        cache_write=snap.get("cache_write_tokens"))
