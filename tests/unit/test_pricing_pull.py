"""定价页拉取：HTML 解析、条件 GET、退避与 fail-open。

fixtures/pricing_page.html 是按官方页的真实形态写的：表头带 <span> 单位、
单元格有 data-numeric、模型名用 rowspan 跨两档、免费项写「限时免费」——
解析器必须靠这些形态活下来，而不是靠某一次抓取快照。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app import pricing, pricing_pull

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pricing_page.html"


@pytest.fixture
def page_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class _Resp:
    def __init__(self, status_code: int = 200, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        # 真 httpx 的 Headers 大小写不敏感，假对象必须一致，否则测试里 "retry-after"
        # 读不到、代码却在线上正常——这种偏差比没测到更坏
        self.headers = httpx.Headers(headers or {})


class _Client:
    """假 httpx 客户端：按序吐响应，并留下请求头供断言。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[tuple[str, dict]] = []

    async def get(self, url, headers=None):
        self.requests.append((str(url), dict(headers or {})))
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ── 解析 ─────────────────────────────────────────────────────────────────────
def test_parse_fixture_models(page_html):
    table = pricing_pull.parse_pricing_html(page_html)
    assert set(table) == {"GLM-5.2", "GLM-5.1", "GLM-5.3-Flash", "GLM-4.7-Flash",
                          "GLM-4.7", "GLM-4.6V"}


def test_parse_skips_unrelated_tables(page_html):
    # 页面里先有一张「日期/事件」装饰表，抓错表等于把不相干的价格当价目
    assert "日期" not in pricing_pull.parse_pricing_html(page_html)


def test_parse_rowspan_ladder_expands_into_two_tiers(page_html):
    tiers = pricing_pull.parse_pricing_html(page_html)["GLM-5.1"]["tiers"]
    assert [(t["min_input"], t["max_input"]) for t in tiers] == [(0, 32768), (32768, None)]
    assert tiers[0]["uncached_input"] == 6_000_000 and tiers[0]["cached_input"] == 1_300_000
    assert tiers[1]["uncached_input"] == 8_000_000


def test_parse_ignores_hourly_storage_and_modality_columns(page_html):
    """「缓存存储（元/百万 Tokens/小时）」是按小时计的存储费，不是每 token 的缓存
    写入价；「输入模态」带着「输入」二字但装的是「图片、视频」。读错这两列，
    账要么多记一个量级，要么把模态文字当 0 元。"""
    tier = pricing_pull.parse_pricing_html(page_html)["GLM-5.3-Flash"]["tiers"][0]
    assert "cache_write" not in tier
    assert tier["uncached_input"] == 800_000 and tier["cached_input"] == 230_000


def test_parse_two_dimensional_ladder_keeps_the_higher_price(page_html):
    """GLM-4.7 官方按 输入×输出 两维定价，我们只建模输入维：同一区间塌成两行时
    留低价等于少收钱，必须取较高价。"""
    tiers = pricing_pull.parse_pricing_html(page_html)["GLM-4.7"]["tiers"]
    assert [(t["min_input"], t["max_input"]) for t in tiers] == [(0, 32768), (32768, 204800)]
    assert tiers[0]["uncached_input"] == 3_000_000      # 2 与 3 取 3
    assert tiers[0]["output"] == 14_000_000             # 8 与 14 取 14
    assert tiers[0]["cache_write"] == 700_000


def test_parse_merges_the_same_model_across_tables(page_html):
    """官方把价目拆成好几张表，只取首表会漏掉大半模型；同名同档跨表按 kind 取高价。"""
    tier = pricing_pull.parse_pricing_html(page_html)["GLM-5.2"]["tiers"]
    assert len(tier) == 1
    assert tier[0]["uncached_input"] == 9_000_000 and tier[0]["output"] == 30_000_000
    assert tier[0]["cached_input"] == 2_500_000 and tier[0]["cache_write"] == 1_000_000


def test_parse_free_cell_is_zero_and_missing_price_drops_model(page_html):
    table = pricing_pull.parse_pricing_html(page_html)
    assert table["GLM-4.7-Flash"]["tiers"][0]["uncached_input"] == 0
    assert "GLM-No-Numbers" not in table  # 「待定」不是价格，别把它当 0 记账


def test_parse_without_pricing_table_returns_empty():
    assert pricing_pull.parse_pricing_html("<table><tr><td>hi</td></tr></table>") == {}
    assert pricing_pull.parse_pricing_html("<html><body>炸裂的 <table") == {}


# ── 拉取一轮 ─────────────────────────────────────────────────────────────────
async def test_pull_ok_writes_table_and_never_identity_headers(fresh_app, page_html):
    client = _Client(_Resp(200, page_html, {"etag": 'W/"abc"'}))
    status = await pricing_pull.pull_once(client)
    assert status["result"] == "ok" and status["models"] == 6 and status["changed"] is True

    pulled = fresh_app.pricing_pulled()
    assert pulled["v"] == pricing.SCHEMA_VERSION
    assert pulled["etag"] == 'W/"abc"' and pulled["fetched_at"] > 0
    assert pricing.effective_table({}, pulled)["GLM-5.1"].tiers[1].min_input == 32768

    _url, sent = client.requests[0]
    assert sent["User-Agent"] == pricing_pull.USER_AGENT
    # 关联面：docs 域不该看到任何 ZCode 身份头
    assert not [k for k in sent if k.lower().startswith("x-device")]
    assert not [k for k in sent if k.lower() == "authorization"]


async def test_pull_sends_conditional_headers_and_304_keeps_table(fresh_app, page_html):
    fresh_app.set_pricing_pulled({"v": pricing.SCHEMA_VERSION,
                                  "models": {"GLM-Old": {"currency": "CNY", "as_of": "x",
                                                         "tiers": [{"tier_id": "standard",
                                                                    "min_input": 0,
                                                                    "uncached_input": 1}]}},
                                  "etag": 'W/"old"', "last_modified": "Tue, 01 Sep 2026 00:00:00 GMT"})
    client = _Client(_Resp(304))
    status = await pricing_pull.pull_once(client)
    assert status["result"] == "unchanged" and status["changed"] is False

    _url, sent = client.requests[0]
    assert sent["If-None-Match"] == 'W/"old"'
    assert sent["If-Modified-Since"].endswith("GMT")
    assert "GLM-Old" in fresh_app.pricing_pulled()["models"]


async def test_pull_keeps_previous_validator_when_response_omits_it(fresh_app, page_html):
    fresh_app.set_pricing_pulled({"v": pricing.SCHEMA_VERSION, "models": {},
                                  "etag": 'W/"old"', "last_modified": "Tue, 01 Sep 2026 00:00:00 GMT"})
    await pricing_pull.pull_once(_Client(_Resp(200, page_html)))
    pulled = fresh_app.pricing_pulled()
    assert pulled["etag"] == 'W/"old"' and pulled["last_modified"].endswith("GMT")


async def test_parse_failure_keeps_previous_table(fresh_app):
    fresh_app.set_pricing_pulled({"v": pricing.SCHEMA_VERSION,
                                  "models": {"GLM-Old": {"currency": "CNY", "as_of": "x",
                                                         "tiers": [{"tier_id": "standard",
                                                                    "min_input": 0,
                                                                    "uncached_input": 1}]}}})
    status = await pricing_pull.pull_once(_Client(_Resp(200, "<html>改版了</html>")))
    assert status["result"] == "error" and "改版" in status["error"]
    assert "GLM-Old" in fresh_app.pricing_pulled()["models"]


async def test_network_error_is_fail_open(fresh_app):
    status = await pricing_pull.pull_once(_Client(httpx.ConnectError("boom")))
    assert status["result"] == "error" and fresh_app.pricing_pulled() == {}
    assert fresh_app.pricing_overrides() == {}   # 另外两层不受牵连


async def test_server_error_result_is_error(fresh_app, page_html):
    status = await pricing_pull.pull_once(_Client(_Resp(500, "")))
    assert status["result"] == "error" and status["http_status"] == 500


# ── 退避 ─────────────────────────────────────────────────────────────────────
async def test_403_uses_retry_after(fresh_app, page_html):
    status = await pricing_pull.pull_once(_Client(_Resp(403, "blocked", {"Retry-After": "3600"})))
    assert status["result"] == "rejected"
    assert 3500 < status["blocked_until"] - status["last_attempt_at"] <= 3600


async def test_429_doubles_interval_and_caps_at_seven_days(fresh_app):
    fresh_app.set_setting("pricing_pull_interval", "86400")
    status = await pricing_pull.pull_once(_Client(_Resp(429)))
    assert status["interval"] == 86400
    assert status["blocked_until"] - status["last_attempt_at"] == 172800

    fresh_app.set_setting("pricing_pull_interval", str(6 * 86400))
    capped = await pricing_pull.pull_once(_Client(_Resp(403)))
    assert capped["blocked_until"] - capped["last_attempt_at"] == pricing_pull.MAX_BACKOFF


def test_backoff_prefers_retry_after_and_floors_at_sixty():
    assert pricing_pull._backoff_seconds(_Resp(headers={"Retry-After": "5"}), 3600) == 60
    assert pricing_pull._backoff_seconds(_Resp(headers={"Retry-After": "not-a-number"}), 3600) == 7200
    assert pricing_pull._backoff_seconds(_Resp(), 0) == 172800


# ── 后台循环 ─────────────────────────────────────────────────────────────────
def test_disabled_by_default(fresh_app):
    assert fresh_app.pricing_pull_interval() == 0


async def test_second_pull_while_one_is_inflight_is_refused(fresh_app, monkeypatch, page_html):
    """手动按钮双击叠上后台轮：同一时刻只发一次条件 GET，多出来的直接回 busy。"""
    started, release = asyncio.Event(), asyncio.Event()
    real = pricing_pull._pull_once

    async def slow(client=None):
        started.set()
        await release.wait()
        return await real(client)

    monkeypatch.setattr(pricing_pull, "_pull_once", slow)
    task = asyncio.create_task(pricing_pull.pull_once(_Client(_Resp(200, page_html))))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert (await pricing_pull.pull_once(_Client()))["result"] == "busy"
    release.set()
    assert (await task)["result"] == "ok"
