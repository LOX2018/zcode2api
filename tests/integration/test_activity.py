"""活动日志：落库、按分类过滤、环形截断，以及各处的打点。

日志落在 SQLite 的 activity 表里（不在文件里），所以重启后仍在；
条数由 ACTIVITY_KEEP 截断，接口给的是「最近 N 条」而不是全量。
"""

from __future__ import annotations

import time

import pytest

from app import activity
from app.store import ACTIVITY_KEEP

_ADMIN = {"Authorization": "Bearer zcode"}


@pytest.mark.integration
class TestStoreActivity:
    def test_record_and_list_newest_first(self, fresh_app):
        activity.record("config", "第一条")
        activity.record("config", "第二条")
        entries = fresh_app.activity_list("config")
        assert [e["text"] for e in entries] == ["第二条", "第一条"]
        assert all(e["ts"] <= time.time() + 1 for e in entries)

    def test_kind_filter_and_total(self, fresh_app):
        activity.record("auth", "生成网关 Key「甲」")
        activity.record("account", "新增 1 个账号：zai-1")
        assert len(fresh_app.activity_list()) == 2
        assert fresh_app.activity_total() == 2
        assert [e["kind"] for e in fresh_app.activity_list("auth")] == ["auth"]

    def test_unknown_kind_falls_back_to_error(self, fresh_app):
        activity.record("nonsense", "分类外的写入")
        assert fresh_app.activity_list("error")[0]["kind"] == "error"

    def test_trim_keeps_last_n(self, fresh_app, monkeypatch):
        monkeypatch.setattr("app.store.ACTIVITY_KEEP", 5)
        for i in range(9):
            fresh_app.activity_add("boot", f"第 {i} 次")
        assert fresh_app.activity_total() == 5
        assert fresh_app.activity_list()[0]["text"] == "第 8 次"


@pytest.mark.integration
class TestActivityEndpoint:
    async def test_shape(self, gateway_client, fresh_app):
        client, _ = gateway_client
        activity.record("boot", "服务启动")
        res = await client.get("/admin/api/activity", headers=_ADMIN)
        assert res.status_code == 200
        data = res.json()
        assert data["keep"] == ACTIVITY_KEEP
        assert [k["kind"] for k in data["kinds"]] == list(activity.KINDS)
        assert data["entries"][0]["text"] == "服务启动"

    async def test_kind_and_limit_params(self, gateway_client, fresh_app):
        client, _ = gateway_client
        activity.record("auth", "a")
        activity.record("account", "b")
        data = (await client.get("/admin/api/activity?kind=auth", headers=_ADMIN)).json()
        assert [e["kind"] for e in data["entries"]] == ["auth"]
        assert data["total"] == 2
        assert len((await client.get("/admin/api/activity?limit=1",
                                     headers=_ADMIN)).json()["entries"]) == 1

    async def test_key_crud_is_logged(self, gateway_client, fresh_app):
        client, _ = gateway_client
        row = (await client.post("/admin/api/keys", json={"label": "甲"},
                                 headers=_ADMIN)).json()["key"]
        await client.delete(f"/admin/api/keys/{row['id']}", headers=_ADMIN)
        texts = [e["text"] for e in fresh_app.activity_list("auth")]
        assert texts[:2] == ["删除网关 Key「甲」", "生成网关 Key「甲」"]

    async def test_account_ops_are_logged(self, gateway_client, fresh_app):
        client, _ = gateway_client
        res = await client.post("/admin/api/accounts",
                                json={"provider": "zai", "tokens": "hA.eyJzdWIiOiJhIn0.sig"},
                                headers=_ADMIN)
        aid = res.json()["ids"][0]
        await client.post(f"/admin/api/accounts/{aid}/enabled", json={"enabled": False},
                          headers=_ADMIN)
        await client.put(f"/admin/api/accounts/{aid}", json={"name": "改名"}, headers=_ADMIN)
        await client.request("DELETE", "/admin/api/accounts", json=[aid], headers=_ADMIN)
        # 安装/领取等后台任务也会往 account 分类里写，这里只钉住自己这四条的顺序
        mine = {"删除 1 个账号：改名", "编辑账号「改名」：名称",
                "停用账号「zai-1」", "新增 1 个账号：zai-1"}
        texts = [e["text"] for e in fresh_app.activity_list("account") if e["text"] in mine]
        assert texts == ["删除 1 个账号：改名", "编辑账号「改名」：名称",
                         "停用账号「zai-1」", "新增 1 个账号：zai-1"]

    async def test_settings_change_is_logged(self, gateway_client, fresh_app):
        client, _ = gateway_client
        await client.put("/admin/api/settings", json={"account_concurrency": 3}, headers=_ADMIN)
        texts = [e["text"] for e in fresh_app.activity_list("config")]
        assert "修改系统参数：账号并发" in texts

    async def test_mask_echo_admin_key_is_not_logged(self, gateway_client, fresh_app):
        """回填掩码等于没改，不该在日志里留下「改过口令」的假痕迹。"""
        client, _ = gateway_client
        await client.put("/admin/api/settings", json={"admin_key": "zd…zcode"}, headers=_ADMIN)
        assert fresh_app.activity_list("config") == []

    async def test_requires_admin(self, gateway_client, fresh_app):
        client, _ = gateway_client
        assert (await client.get("/admin/api/activity")).status_code == 401
