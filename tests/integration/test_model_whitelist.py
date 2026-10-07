"""白名单三处在网关里的实际效果：早退拦截、按号跳过、3006 纳入容灾。

三条路各自守护一个不变量：
  E1 白名单外的模型不得产生任何上游流量（不占号、不烧额度）；
  E2 权益不含该模型的账号被跳过，且不计入 attempts（否则健康池会被误判 503）；
  E3 上游 3006 只钉「号 × 模型」，账号状态与 fail_count 都不动。
"""

from __future__ import annotations

import pytest

ADMIN_AUTH = {"Authorization": "Bearer zcode"}

_JWT_A = "hA.eyJzdWIiOiJhIn0.sig"
_JWT_B = "hB.eyJzdWIiOiJiIn0.sig"


def _body(model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture(autouse=True)
def _deterministic_upstream(gateway_client, monkeypatch):
    """两点确定性前置：

    1. Mock 上游是 session 级、calls 不清零 —— 本文件按「哪个号被打了几次」
       断言，必须从干净的计数开始；
    2. 成功后的 billing 刷新会异步改写账号额度快照，把白名单用例变成时序赛跑
       （刷新去抖另有归属用例，在 test_gateway_risk_cooldown）。
    """
    _client, mock = gateway_client
    mock.state.calls.clear()

    from app.routes import gateway as gateway_module

    async def _noop(account):
        return None

    monkeypatch.setattr(gateway_module, "_safe_refresh", _noop)


def _message_calls(mock):
    return [c for c in mock.state.calls if c[1].endswith("/v1/messages")]


def _served_by(mock, secret: str) -> int:
    token = f"Bearer {secret}"
    return len([c for c in _message_calls(mock) if c[2].get("authorization") == token])


@pytest.mark.integration
class TestEarlyReject:
    async def test_static_mode_rejects_without_upstream_traffic(self, gateway_client, fresh_app):
        client, mock = gateway_client
        fresh_app.set_model_whitelist_mode("static")
        before = len(mock.state.calls)

        res = await client.post("/v1/messages", json=_body("GLM-9.9"))
        assert res.status_code == 400
        assert res.json()["error"]["type"] == "invalid_request_error"
        assert "GLM-9.9" in res.json()["error"]["message"]
        assert len(mock.state.calls) == before  # 一个上游字节都没发

        admin = await client.get("/admin/api/monitoring", headers=ADMIN_AUTH)
        entry = admin.json()["entries"][0]
        assert entry["ok"] is False and entry["status"] == 400
        assert "白名单" in entry["error"]

    async def test_openai_endpoint_rejects_in_openai_shape(self, gateway_client, fresh_app):
        client, mock = gateway_client
        fresh_app.set_model_whitelist_mode("static")
        before = len(mock.state.calls)

        res = await client.post("/v1/chat/completions", json={
            "model": "glm-9.9", "messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 400
        err = res.json()["error"]
        assert err["code"] == "model_not_allowed" and err["type"] == "invalid_request_error"
        assert len(mock.state.calls) == before

    async def test_no_knowledge_passes_through(self, gateway_client, fresh_app):
        """号池非空但额度未刷 → 早退必须放行（改造前语义），交给上游判。

        这是权益推导那两档的性质：manual/static 由名单/常量直接给答案，不存在
        「还没查过」的状态，所以必须显式切到 hybrid 来测。
        """
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("hybrid")
        seed_account(fresh_app, _JWT_A, name="no-quota")
        res = await client.post("/v1/messages", json=_body("GLM-9.9"))
        assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 1

    async def test_known_set_rejects_model_outside_entitlements(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("hybrid")
        acc = seed_account(fresh_app, _JWT_A, name="only-53")
        acc.quota = {"GLM-5.3": {"total": 100, "used": 1, "remaining": 99}}
        fresh_app.update_account(acc)
        before = len(mock.state.calls)

        res = await client.post("/v1/messages", json=_body("GLM-5-Turbo"))
        assert res.status_code == 400
        assert len(mock.state.calls) == before


@pytest.mark.integration
class TestManualList:
    """manual 档（默认）：后台手填的名字名单是唯一依据，账号权益不参与。"""

    async def test_manual_serves_named_model_regardless_of_entitlement(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("manual")
        fresh_app.set_model_whitelist_names("GLM-4.7")
        acc = seed_account(fresh_app, _JWT_A, name="m-53")
        acc.quota = {"GLM-5.3": {"remaining": 10}}  # 权益里没有 GLM-4.7
        fresh_app.update_account(acc)

        res = await client.post("/v1/messages", json=_body("GLM-4.7"))
        assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 1  # 不做按号跳过

    async def test_manual_rejects_unlisted_model(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("manual")
        fresh_app.set_model_whitelist_names("GLM-4.7")
        seed_account(fresh_app, _JWT_A, name="m-53b")
        before = len(mock.state.calls)

        # 常量表里的名字在 manual 档也不外溢 —— 填了名单就以名单为准
        res = await client.post("/v1/messages", json=_body("GLM-5.3-Flash"))
        assert res.status_code == 400
        assert len(mock.state.calls) == before

    async def test_manual_empty_list_gates_nothing(self, gateway_client, fresh_app):
        """名单还没填 → 不在门口拦，名字照旧透传给上游判。"""
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("manual")
        seed_account(fresh_app, _JWT_A, name="m-empty")
        res = await client.post("/v1/messages", json=_body("GLM-5.3"))
        assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 1


@pytest.mark.integration
class TestPerAccountSkip:
    async def test_skips_account_without_entitlement(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("dynamic")
        a = seed_account(fresh_app, _JWT_A, name="a-53")
        a.quota = {"GLM-5.3": {"remaining": 10}}
        fresh_app.update_account(a)
        b = seed_account(fresh_app, _JWT_B, name="b-turbo")
        b.quota = {"GLM-5-Turbo": {"remaining": 10}}
        fresh_app.update_account(b)

        res = await client.post("/v1/messages", json=_body("GLM-5-Turbo"))
        assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 0
        assert _served_by(mock, _JWT_B) == 1

    async def test_skip_does_not_consume_attempts(self, gateway_client, fresh_app):
        """A 权益不含目标模型被跳过，不得因此把 B 的正常调度算成失败。"""
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("dynamic")
        a = seed_account(fresh_app, _JWT_A, name="a-other")
        a.quota = {"GLM-4.7": {"remaining": 10}}
        fresh_app.update_account(a)
        b = seed_account(fresh_app, _JWT_B, name="b-53")
        b.quota = {"GLM-5.3": {"remaining": 10}}
        fresh_app.update_account(b)

        for _ in range(3):
            res = await client.post("/v1/messages", json=_body("GLM-5.3"))
            assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 0

    async def test_all_accounts_lack_model_gives_distinct_503(self, gateway_client, fresh_app):
        """GLM-5.3 在常量表里（hybrid 并集 → 早退放行），但池内账号权益都不含它
        → 只能由按号跳过后凑成 503，且文案要指向套餐而不是额度/并发。"""
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("hybrid")
        a = seed_account(fresh_app, _JWT_A, name="a-flash")
        a.quota = {"GLM-5.3-Flash": {"remaining": 10}}
        fresh_app.update_account(a)
        before = len(mock.state.calls)

        res = await client.post("/v1/messages", json=_body("GLM-5.3"))
        assert res.status_code == 503
        assert "套餐支持" in res.json()["error"]["message"]
        assert len(mock.state.calls) == before


@pytest.mark.integration
class TestUpstream3006Failover:
    async def test_denies_pair_and_switches_account(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("hybrid")
        a = seed_account(fresh_app, _JWT_A, name="a-deny")
        b = seed_account(fresh_app, _JWT_B, name="b-ok")
        mock.state.sequences[_JWT_A[:16]] = ["model_not_allowed"]

        res = await client.post("/v1/messages", json=_body("GLM-5.2"))
        assert res.status_code == 200
        assert _served_by(mock, _JWT_A) == 1
        assert _served_by(mock, _JWT_B) == 1

        a = fresh_app.find("zai", a.id)
        assert a.status == "active"          # 套餐不含 ≠ 账号故障
        assert a.fail_count == 0
        assert a.model_denials.get("GLM-5.2")
        assert b.fail_count == 0

    async def test_denied_pair_is_skipped_next_time(self, gateway_client, fresh_app):
        client, mock = gateway_client
        from tests.conftest import seed_account

        fresh_app.set_model_whitelist_mode("dynamic")
        a = seed_account(fresh_app, _JWT_A, name="a-deny")
        a.quota = {"GLM-5.2": {"remaining": 50}}   # 权益说有，上游说没有
        fresh_app.update_account(a)
        b = seed_account(fresh_app, _JWT_B, name="b-ok")
        b.quota = {"GLM-5.2": {"remaining": 50}}
        fresh_app.update_account(b)
        mock.state.sequences[_JWT_A[:16]] = ["model_not_allowed"]

        assert (await client.post("/v1/messages", json=_body("GLM-5.2"))).status_code == 200
        assert _served_by(mock, _JWT_A) == 1
        assert _served_by(mock, _JWT_B) == 1

        # 再来一次：A 的「号 × 模型」钉格生效，直接跳过，不再为它产生上游流量
        assert (await client.post("/v1/messages", json=_body("GLM-5.2"))).status_code == 200
        assert _served_by(mock, _JWT_A) == 1
        assert _served_by(mock, _JWT_B) == 2
        # 钉的是「号 × 模型」组合：B 的权益仍覆盖此模型，可见集不受影响
        models = [d["id"] for d in (await client.get("/v1/models")).json()["data"]]
        assert "GLM-5.2" in models
