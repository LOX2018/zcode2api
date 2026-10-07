"""网关 API Key 生成制：列表永久脱敏、明文只回一次、删除即失效、旧 Key 自动导入。

Key 存在 api_keys 表里，verify_gateway_key 只认这张表；meta.gateway_key 是历史
遗留字段，升级时落一条「导入的旧 Key」并被清空。
"""

from __future__ import annotations

import json

import pytest

from app.store import Store
from tests.conftest import seed_account

_ADMIN = {"Authorization": "Bearer zcode"}
_MESSAGES_BODY = {"model": "GLM-5.2",
                  "messages": [{"role": "user", "content": "hi"}]}
# 独占 JWT 前缀：mock 上游按 bind 键控 counters（session 级共享），
# 撞前缀会污染其他用例的首次请求判定。
_KEYS_JWT = "h9.eyJzdWIiOiJrZXlzIn0.sig"


@pytest.mark.integration
class TestKeyEndpoints:
    async def test_create_returns_plaintext_once(self, gateway_client, fresh_app):
        client, _ = gateway_client
        res = await client.post("/admin/api/keys", json={"label": "生产"}, headers=_ADMIN)
        assert res.status_code == 200
        full = res.json()["key"]["key"]
        assert full.startswith("sk-") and len(full) > 20
        assert res.json()["key"]["label"] == "生产"

        listed = (await client.get("/admin/api/keys", headers=_ADMIN)).json()["keys"]
        assert len(listed) == 1
        assert full not in json.dumps(listed)
        assert listed[0]["masked"] == f"{full[:6]}…{full[-4:]}"

    async def test_unnamed_key_gets_placeholder_label(self, gateway_client, fresh_app):
        client, _ = gateway_client
        res = await client.post("/admin/api/keys", json={}, headers=_ADMIN)
        assert res.json()["key"]["label"] == "未命名"

    async def test_delete_removes_key_and_404s_on_unknown(self, gateway_client, fresh_app):
        client, _ = gateway_client
        row = (await client.post("/admin/api/keys", json={"label": "临时"}, headers=_ADMIN)).json()["key"]
        res = await client.delete(f"/admin/api/keys/{row['id']}", headers=_ADMIN)
        assert res.status_code == 200
        assert (await client.get("/admin/api/keys", headers=_ADMIN)).json()["keys"] == []
        assert (await client.delete("/admin/api/keys/nope", headers=_ADMIN)).status_code == 404

    async def test_keys_endpoints_require_admin(self, gateway_client, fresh_app):
        client, _ = gateway_client
        assert (await client.get("/admin/api/keys")).status_code == 401


@pytest.fixture(autouse=True)
def _clean_reqlog():
    """reqlog 是进程级环形日志，不按用例隔离；不清会让下面的断言读到别人的条目。"""
    from app import reqlog

    reqlog.clear()
    yield
    reqlog.clear()


@pytest.mark.integration
class TestKeyGatewayEnforcement:
    async def test_deleted_key_is_rejected_while_others_remain(self, gateway_client, fresh_app):
        """删掉的这把是「无效凭证」→ 403；表全空才回到匿名放行（下条用例）。"""
        client, _ = gateway_client
        seed_account(fresh_app, _KEYS_JWT, name="keys-a")
        doomed = fresh_app.add_api_key("要删的")
        keeper = fresh_app.add_api_key("留下")
        assert (await client.post("/v1/messages", json=_MESSAGES_BODY,
                                  headers={"x-api-key": doomed["key"]})).status_code == 200
        fresh_app.delete_api_key(doomed["id"])
        assert (await client.post("/v1/messages", json=_MESSAGES_BODY,
                                  headers={"x-api-key": doomed["key"]})).status_code == 403
        assert (await client.post("/v1/messages", json=_MESSAGES_BODY,
                                  headers={"x-api-key": keeper["key"]})).status_code == 200

    async def test_deleting_every_key_reopens_anonymous(self, gateway_client, fresh_app):
        client, _ = gateway_client
        seed_account(fresh_app, _KEYS_JWT, name="keys-e")
        row = fresh_app.add_api_key("唯一一把")
        fresh_app.delete_api_key(row["id"])
        assert (await client.post("/v1/messages", json=_MESSAGES_BODY,
                                  headers={"x-api-key": row["key"]})).status_code == 200

    async def test_empty_table_allows_anonymous(self, gateway_client, fresh_app):
        client, _ = gateway_client
        seed_account(fresh_app, _KEYS_JWT, name="keys-b")
        assert fresh_app.api_keys() == []
        assert (await client.post("/v1/messages", json=_MESSAGES_BODY)).status_code == 200

    async def test_monitoring_tags_entries_by_key(self, gateway_client, fresh_app):
        """用量页「按 Key」聚合的数据来源：命中的 Key 打进 reqlog，匿名归到空串档。"""
        client, _ = gateway_client
        seed_account(fresh_app, _KEYS_JWT, name="keys-c")
        await client.post("/v1/messages", json=_MESSAGES_BODY)  # 空表：匿名通过
        row = fresh_app.add_api_key("甲")
        await client.post("/v1/messages", json=_MESSAGES_BODY,
                          headers={"x-api-key": row["key"]})
        entries = (await client.get("/admin/api/monitoring", headers=_ADMIN)).json()["entries"]
        assert len(entries) == 2
        assert {e["key_id"] for e in entries} == {"", row["id"]}
        assert next(e for e in entries if e["key_id"] == row["id"])["key_label"] == "甲"


@pytest.mark.integration
class TestLegacyKeyImport:
    def test_legacy_meta_key_becomes_one_row(self, fresh_app, monkeypatch):
        fresh_app.set_setting("gateway_key", "sk-legacy-token")
        first = Store()
        assert [k["label"] for k in first.api_keys()] == ["导入的旧 Key"]
        assert first.match_api_key("sk-legacy-token") is not None
        # 再开一次不复活、不重复：老 Key 删干净后重启仍然是干净
        fresh_app.delete_api_key(first.api_keys()[0]["id"])
        assert [k["label"] for k in Store().api_keys()] == []
