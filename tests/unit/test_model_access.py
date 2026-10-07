"""白名单四档模式的可测口径：手填名单、集合推导、噪音项过滤、空表回落、按号拒绝与 TTL。

两条防自我锁死的 fail-open（manual 空名单回落 static、dynamic 空池回落 static）
和无快照账号放行，回归里必须各占一条，否则改动很容易把「还没填」实现成「不支持」。
"""

from __future__ import annotations

import time

from app import model_access
from app.models import Account


def _account(store, name: str, quota: dict | None = None, plans: list | None = None) -> Account:
    acc = store.add_account("zai", name, f"jwt.{name}.value")
    acc.quota = quota or {}
    acc.plans = plans or []
    store.update_account(acc)
    return acc


# ── 模式与集合 ───────────────────────────────────────────────────────────────
def test_manual_mode_uses_only_the_typed_list(fresh_app):
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names("GLM-4.7\nglm-5.2")
    assert model_access.allowed() == ["GLM-4.7", "GLM-5.2"]
    # 常量表在 manual 档不外溢：手填就是「只许这些」
    assert model_access.is_allowed("GLM-5.3-Flash") is False
    assert model_access.is_allowed("GLM-4.7") is True


def test_manual_keeps_typed_order_and_normalizes_separators(fresh_app):
    fresh_app.set_model_whitelist_mode("manual")
    # /v1/models 的顺序 = 填写顺序，重复行与逗号/顿号混排都要收成一份
    fresh_app.set_model_whitelist_names("GLM-5.2, GLM-4.7、glm-5.2；GLM-5.2\n")
    assert model_access.allowed() == ["GLM-5.2", "GLM-4.7"]


def test_manual_alias_resolves_to_official_spelling(fresh_app):
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names(["glm-5.3-flash"])
    assert model_access.allowed() == ["GLM-5.3-Flash"]
    assert model_access.is_allowed("zai/glm-5.3-flash") is True


def test_manual_empty_list_shows_static_but_gates_nothing(fresh_app):
    # 空名单 = 「还没填」而不是「什么都不许打」：显示回落 static，但一个都不拦
    fresh_app.set_model_whitelist_mode("manual")
    assert model_access.allowed() == ["GLM-5.3-Flash", "GLM-5.3"]
    assert model_access.has_knowledge() is False
    fresh_app.set_model_whitelist_names("GLM-4.7")
    assert model_access.has_knowledge() is True


def test_manual_mode_does_not_filter_by_account(fresh_app):
    # 名单是全局的，按号跳过只适用于权益推导出来的那两档
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names("GLM-5.3")
    acc = _account(fresh_app, "only-flash", quota={"GLM-5.3-Flash": {"remaining": 10}})
    assert model_access.account_supports(acc, "GLM-5.3") is True


def test_manual_provenance_marks_manual_only(fresh_app):
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names("GLM-5.3-Flash")
    prov = {m["name"]: m["sources"] for m in model_access.models_with_provenance()}
    assert prov == {"GLM-5.3-Flash": ["manual"]}


def test_static_mode_uses_constant_table(fresh_app):
    fresh_app.set_model_whitelist_mode("static")
    assert model_access.allowed() == ["GLM-5.3-Flash", "GLM-5.3"]
    assert model_access.is_allowed("glm-5.3") is True
    assert model_access.is_allowed("glm-4.7") is False


def test_static_mode_does_not_filter_by_account(fresh_app):
    # static 没有可信权益数据，按号跳过等于瞎跳
    fresh_app.set_model_whitelist_mode("static")
    acc = _account(fresh_app, "only-flash", quota={"GLM-5.3-Flash": {"remaining": 10}})
    assert model_access.account_supports(acc, "GLM-5.3") is True


def test_dynamic_mode_derives_from_quota(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    _account(fresh_app, "a", quota={"GLM-4.7": {"remaining": 100},
                                    "GLM-5.3-Flash": {"remaining": 5}})
    _account(fresh_app, "b", quota={"GLM-5.1": {"remaining": 1}})
    assert model_access.allowed() == ["GLM-4.7", "GLM-5.1", "GLM-5.3-Flash"]
    assert model_access.is_allowed("GLM-5.3") is False  # 常量里的名字也不外溢


def test_dynamic_empty_pool_falls_back_to_static(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    assert model_access.allowed() == ["GLM-5.3-Flash", "GLM-5.3"]


def test_derived_models_ignore_non_selectable_accounts(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    acc = _account(fresh_app, "dead", quota={"GLM-4.7": {"remaining": 1}})
    acc.enabled = False
    fresh_app.update_account(acc)
    assert model_access.derived_models() == set()


def test_exhausted_window_excluded(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    _account(fresh_app, "a", quota={"GLM-4.7": {"remaining": 0},
                                    "GLM-5.1": {"remaining": None},
                                    "GLM-5.2": {"total": 10}})
    # remaining=0 排除；remaining 缺失（None / 字段不存在）算「不知道」→ 保留
    assert model_access.derived_models() == {"GLM-5.1", "GLM-5.2"}


def test_entitlement_meter_filter_drops_noise(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    plans = [{"name": "Coding Plan", "entitlements": [
        {"show_name": "GLM-5-Turbo", "meter": "model_usage"},
        {"show_name": "seat-count", "meter": "other"},
        {"show_name": "GLM-5", "meter": "model_usage"},
        {"meter": "model_usage"},
    ]}]
    _account(fresh_app, "a", quota={}, plans=plans)
    assert model_access.derived_models() == {"GLM-5-Turbo", "GLM-5"}


def test_hybrid_unions_and_keeps_constant_order(fresh_app):
    fresh_app.set_model_whitelist_mode("hybrid")
    _account(fresh_app, "a", quota={"GLM-4.7": {"remaining": 9},
                                    "GLM-5.3-Flash": {"remaining": 9}})
    # 常量原序在前，派生新增按字母序追加；重名不重复
    assert model_access.allowed() == ["GLM-5.3-Flash", "GLM-5.3", "GLM-4.7"]
    prov = {p["name"]: p["sources"] for p in model_access.models_with_provenance()}
    assert prov["GLM-5.3-Flash"] == ["constant", "derived"]
    assert prov["GLM-5.3"] == ["constant"]
    assert prov["GLM-4.7"] == ["derived"]


def test_unknown_mode_falls_back_to_manual(fresh_app):
    fresh_app.set_setting("model_whitelist_mode", "wildcard")
    assert model_access.current_mode() == "manual"


# ── 名称归一 ─────────────────────────────────────────────────────────────────
def test_is_allowed_accepts_alias_and_provider_prefix(fresh_app):
    fresh_app.set_model_whitelist_mode("static")
    assert model_access.is_allowed("zai/glm-5.3-flash") is True
    assert model_access.is_allowed("GLM-5.3-FLASH") is True
    assert model_access.is_allowed("") is False


def test_account_supports_matches_show_name_case_insensitively(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    acc = _account(fresh_app, "a", quota={"glm-5.3-flash": {"remaining": 3}})
    assert model_access.account_supports(acc, "GLM-5.3-Flash") is True


def test_account_without_quota_snapshot_is_allowed(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    acc = _account(fresh_app, "fresh")  # 刚入池，额度还没刷
    assert model_access.account_models(acc) == set()
    assert model_access.account_supports(acc, "GLM-5.1") is True


# ── 3006 钉格子 ──────────────────────────────────────────────────────────────
def test_denied_model_skipped_per_account(fresh_app):
    fresh_app.set_model_whitelist_mode("hybrid")
    acc = _account(fresh_app, "a", quota={"GLM-5.3": {"remaining": 10},
                                          "GLM-4.7": {"remaining": 10}})
    assert model_access.account_supports(acc, "GLM-5.3") is True
    acc.deny_model("GLM-5.3", ttl=3600)
    assert acc.is_model_denied("GLM-5.3") is True
    assert model_access.account_supports(acc, "glm-5.3") is False
    # 钉的是组合，不是账号：同号的另一个模型照打，状态也没被动
    assert model_access.account_supports(acc, "GLM-4.7") is True
    assert acc.status == "active" and acc.fail_count == 0
    assert model_access.account_models(acc) == {"GLM-4.7"}


def test_denial_expires(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    acc = _account(fresh_app, "a", quota={"GLM-5.3": {"remaining": 10}})
    acc.deny_model("GLM-5.3", ttl=60, now=time.time() - 120)
    assert acc.is_model_denied("GLM-5.3") is False
    assert model_access.account_supports(acc, "GLM-5.3") is True


def test_denied_models_leave_dynamic_visible_set(fresh_app):
    fresh_app.set_model_whitelist_mode("dynamic")
    acc = _account(fresh_app, "a", quota={"GLM-4.7": {"remaining": 10},
                                          "GLM-5.1": {"remaining": 10}})
    acc.deny_model("GLM-4.7", ttl=3600)
    fresh_app.update_account(acc)
    assert model_access.allowed() == ["GLM-5.1"]


def test_account_roundtrip_keeps_denials(fresh_app):
    acc = _account(fresh_app, "a")
    acc.deny_model("GLM-5.3", ttl=3600)
    revived = Account.from_dict(acc.to_dict())
    assert revived.is_model_denied("GLM-5.3") is True
    assert "model_denials" in revived.public_view()


# ── 能力层（上游实际提供）与策略层（白名单放行）必须是两个口径 ──────────────────
def test_capability_ignores_whitelist_mode(fresh_app):
    """手填 1 个名字不能让「上游有什么模型」也缩成 1 个 —— 后台芯片墙靠这条不撒谎。"""
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names("GLM-5.3-Flash")
    names = [item["name"] for item in model_access.capability_models()]
    assert model_access.allowed() == ["GLM-5.3-Flash"]
    assert set(model_access.static_models()) <= set(names)


def test_capability_unions_derived_and_tags_sources(fresh_app):
    fresh_app.set_model_whitelist_mode("manual")
    fresh_app.set_model_whitelist_names("")
    _account(fresh_app, "a", quota={"GLM-4.7": {"remaining": 10},
                                    "GLM-5.3-Flash": {"remaining": 10}})
    items = model_access.capability_models()
    by_name = {item["name"]: item["sources"] for item in items}
    # 常量在前、派生新增按字母序追加；同名不重复计
    assert by_name["GLM-4.7"] == ["derived"]
    assert by_name["GLM-5.3-Flash"] == ["constant"]
    # 常量原序在前，派生新增追加在后
    assert [item["name"] for item in items][:2] == model_access.static_models()

