"""计价/白名单配置的落库口径：meta 里的 JSON 值读写、坏数据回落、后台接口校验。

meta 表历史上只有标量，本次首例存 JSON。热改标量的 getter 模板是
account_concurrency（coerce + clamp + 回落 settings），JSON 版必须同样「读不
出来就回落」——一次手滑写入的坏 JSON 不能把整个计价层抹掉，更不能让服务起不来。
"""

from __future__ import annotations

from app import constants, pricing, settings
from app.store import Store


def _admin_client():
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://admin.test")


def _auth():
    return {"Authorization": f"Bearer {settings.DEFAULT_ADMIN_KEY}"}


# ── 默认值 ───────────────────────────────────────────────────────────────────
def test_defaults(fresh_app):
    assert fresh_app.model_whitelist_mode() == settings.MODEL_WHITELIST_MODE == "manual"
    assert fresh_app.model_whitelist_names_text() == ""
    assert fresh_app.pricing_pull_interval() == settings.PRICING_PULL_INTERVAL == 0
    assert fresh_app.pricing_overrides() == {}
    assert fresh_app.pricing_pulled() == {}
    assert fresh_app.pricing_pull_status() == {}


# ── JSON 往返与持久化 ────────────────────────────────────────────────────────
def test_pricing_overrides_roundtrip_and_reload(fresh_app):
    table = {"GLM-5.3": {"currency": "CNY", "as_of": "2026-12-01", "tiers": [
        {"tier_id": "standard", "min_input": 0, "uncached_input": 1_000}]}}
    fresh_app.set_pricing_overrides(pricing.normalize_admin_table(table))
    stored = fresh_app.pricing_overrides()
    assert stored["v"] == 1
    assert pricing.parse_table(stored, "admin")["GLM-5.3"].tiers[0].units["uncached_input"] == 1_000
    # 重开一个 Store 读同一个库：落库格式必须自洽（dump→parse 可逆）
    assert Store().pricing_overrides() == stored


def test_corrupt_json_falls_back_to_empty_dict(fresh_app):
    fresh_app.set_setting("pricing_overrides", "{not json")
    assert fresh_app.pricing_overrides() == {}
    fresh_app.set_setting("pricing_pulled", "[]")
    assert fresh_app.pricing_pulled() == {}
    fresh_app.set_setting("pricing_pull_status", None)
    assert fresh_app.pricing_pull_status() == {}


def test_effective_table_survives_corrupt_meta(fresh_app):
    fresh_app.set_setting("pricing_overrides", "garbage")
    assert pricing.effective_table(fresh_app.pricing_overrides(),
                                   fresh_app.pricing_pulled()) == pricing.builtin_table()


# ── 标量口径 ─────────────────────────────────────────────────────────────────
def test_bad_scalars_fall_back(fresh_app):
    fresh_app.set_setting("pricing_pull_interval", "abc")
    assert fresh_app.pricing_pull_interval() == settings.PRICING_PULL_INTERVAL
    fresh_app.set_setting("pricing_pull_interval", "-5")
    assert fresh_app.pricing_pull_interval() == 0
    fresh_app.set_setting("model_whitelist_mode", "nonsense")
    assert fresh_app.model_whitelist_mode() == "manual"


def test_mode_switch_persists(fresh_app):
    fresh_app.set_model_whitelist_mode("static")
    assert fresh_app.model_whitelist_mode() == "static"
    assert Store().model_whitelist_mode() == "static"


# ── 后台接口 ─────────────────────────────────────────────────────────────────
async def test_get_pricing_shows_merged_table(fresh_app):
    fresh_app.set_pricing_overrides(pricing.normalize_admin_table(
        {"GLM-5.3-Flash": {"currency": "CNY", "tiers": [
            {"tier_id": "standard", "min_input": 0, "output": 123}]}}))
    async with _admin_client() as client:
        res = await client.get("/admin/api/pricing", headers=_auth())
    assert res.status_code == 200
    body = res.json()
    assert body["ticks_per_yuan"] == pricing.TICKS_PER_YUAN
    assert body["source_url"] == constants.PRICING_SOURCE_URL
    assert body["builtin_as_of"] == constants.PRICING_AS_OF
    assert body["admin_overrides_active"] is True
    flash = body["models"]["GLM-5.3-Flash"]
    assert flash["source"] == "admin"
    # tier 单价摊平在 tier 层（与落库格式同形，前端不必再拼 units）
    assert flash["tiers"][0]["output"] == 123
    assert flash["tiers"][0]["uncached_input"] == 800_000  # 内置价保留
    # 未覆盖的模型仍是内置溯源
    assert body["models"]["GLM-5.3"]["source"] == "builtin"


async def test_pull_endpoint_uses_injected_result_and_never_hits_network(fresh_app, monkeypatch):
    """手动拉取端点：这条路径会出网，测试必须把它钉住，断言的是「写进去了什么」。"""
    from app import pricing_pull

    async def fake_pull(client=None):
        fresh_app.set_pricing_pulled({"v": pricing.SCHEMA_VERSION, "models": {
            "GLM-Pulled": {"currency": "CNY", "as_of": "2026-11-01", "tiers": [
                {"tier_id": "standard", "min_input": 0, "uncached_input": 7}]}}})
        return {"result": "ok", "models": 1, "changed": True}

    monkeypatch.setattr(pricing_pull, "pull_once", fake_pull)
    async with _admin_client() as client:
        res = await client.post("/admin/api/pricing/pull", headers=_auth())
        assert res.status_code == 200
        assert res.json() == {"status": {"result": "ok", "models": 1, "changed": True},
                              "models": 1}
        merged = (await client.get("/admin/api/pricing", headers=_auth())).json()
    assert merged["models"]["GLM-Pulled"]["source"] == "pulled"


async def test_put_settings_validates(fresh_app):
    async with _admin_client() as client:
        res = await client.put("/admin/api/settings", json={"model_whitelist_mode": "STATIC"},
                               headers=_auth())
        assert res.status_code == 200
        assert fresh_app.model_whitelist_mode() == "static"

        res = await client.put("/admin/api/settings", json={"model_whitelist_mode": "all"},
                               headers=_auth())
        assert res.status_code == 400

        res = await client.put("/admin/api/settings", json={"pricing_pull_interval": "x"},
                               headers=_auth())
        assert res.status_code == 400

        res = await client.put("/admin/api/settings",
                               json={"pricing_models": {"GLM-9": {"tiers": []}}}, headers=_auth())
        assert res.status_code == 400
        assert fresh_app.pricing_overrides() == {}

        res = await client.put("/admin/api/settings",
                               json={"pricing_models": {"GLM-5.3": {"tiers": [
                                   {"tier_id": "standard", "min_input": 0,
                                    "uncached_input": 500_000}]}}}, headers=_auth())
        assert res.status_code == 200
        tiers = fresh_app.pricing_overrides()["models"]["GLM-5.3"]["tiers"]
        assert tiers[0]["uncached_input"] == 500_000

        # 清空覆盖
        res = await client.put("/admin/api/settings", json={"pricing_models": {}}, headers=_auth())
        assert res.status_code == 200
        assert fresh_app.pricing_overrides() == {}

        res = await client.get("/admin/api/settings", headers=_auth())
        assert res.status_code == 200
        body = res.json()
        assert body["model_whitelist_mode"] == "static"
        assert body["whitelist_modes"] == ["manual", "static", "dynamic", "hybrid"]
        assert body["pricing_pull_interval"] == 0
        assert body["pricing_overrides"] == {}


async def test_manual_names_round_trip(fresh_app):
    """手填名单：原文回填一字不差，PUT 回显真正生效的名单。"""
    async with _admin_client() as client:
        res = await client.put("/admin/api/settings",
                               json={"model_whitelist_mode": "manual",
                                     "model_whitelist_names": "GLM-5.2\nglm-4.7"}, headers=_auth())
        assert res.status_code == 200
        # 回显的是归一后的生效集，不是原文
        assert res.json()["model_whitelist_effective"] == ["GLM-5.2", "GLM-4.7"]

        res = await client.get("/admin/api/settings", headers=_auth())
        body = res.json()
        assert body["model_whitelist_names"] == "GLM-5.2\nglm-4.7"
        assert body["model_whitelist_effective"] == ["GLM-5.2", "GLM-4.7"]

        # 非字符串非列表必须报错，静默吞掉会把用户填了半年的名单抹平
        res = await client.put("/admin/api/settings",
                               json={"model_whitelist_names": 42}, headers=_auth())
        assert res.status_code == 400
