"""计价内核守护：tick 整数数学、阶梯边界、cache 切分、三层合并、坏数据回落。

钱的口径一旦漂成浮点，落库和展示都会差一分；这里的断言就是那条「不许用 float
算钱」的锁。阶梯边界（32768）与 meter 噪音项一样，是实测出来的坑位。
"""

from __future__ import annotations

import pytest

from app import constants, pricing


def _tier(**kw):
    base = {"tier_id": "standard", "min_input": 0, "max_input": None,
            "uncached_input": 8_000_000, "cached_input": 2_000_000,
            "cache_write": 0, "output": 28_000_000}
    base.update(kw)
    return base


def _table_raw(**kw):
    base = {"currency": "CNY", "as_of": "2026-10-05", "tiers": [_tier(**kw)]}
    return base


# ── 内置表 ───────────────────────────────────────────────────────────────────
def test_builtin_table_covers_official_models():
    table = pricing.builtin_table()
    assert set(table) == set(constants.DEFAULT_MODEL_PRICING)
    assert all(p.source == pricing.SOURCE_BUILTIN for p in table.values())
    assert all(p.currency == "CNY" and p.as_of == constants.PRICING_AS_OF for p in table.values())


def test_builtin_price_matches_official_page():
    # 与 docs.bigmodel.cn 定价页 <table> 逐格核对（2026-10-05 抓取金样）
    table = pricing.builtin_table()
    glm53 = table["GLM-5.3"].tiers[0].units
    assert glm53 == {"uncached_input": 8_000_000, "cached_input": 2_000_000,
                     "cache_write": 0, "output": 28_000_000}
    flash = table["GLM-5.3-Flash"].tiers[0].units
    assert flash["uncached_input"] == 800_000 and flash["cached_input"] == 230_000
    assert flash["output"] == 2_800_000 and flash["cache_write"] == 0
    # 阶梯模型两档，且 GLM-4.7 只保留输入维（第二档封顶 200K）
    assert [t.tier_id for t in table["GLM-5.1"].tiers] == ["lt32k", "ge32k"]
    assert table["GLM-4.7"].tiers[1].max_input == 204_800


# ── 名称归一 ─────────────────────────────────────────────────────────────────
def test_normalize_model_alias_and_provider_prefix():
    assert pricing.normalize_model("glm-5.3-flash") == "GLM-5.3-Flash"
    assert pricing.normalize_model("glm-turbo") == "GLM-5-Turbo"
    assert pricing.normalize_model("zai/glm-5.2") == "GLM-5.2"
    assert pricing.normalize_model("") == ""


def test_price_for_is_case_insensitive_for_unmapped_names():
    # MODEL_NAME_MAP 未收录 glm-5.3-flashx，若大小写不敏感兜底缺失会「有价查不到」
    table = pricing.builtin_table()
    assert pricing.price_for(table, "glm-5.3-flashx") is table["GLM-5.3-FlashX"]
    assert pricing.price_for(table, "GLM-5.3-FLASHX") is table["GLM-5.3-FlashX"]
    assert pricing.price_for(table, "nope") is None
    assert pricing.price_for(table, "") is None


# ── tick 数学 ────────────────────────────────────────────────────────────────
def test_breakdown_is_all_integer_ticks():
    b = pricing.resolve_breakdown(pricing.builtin_table(), "glm-5.3",
                                  input_tokens=1000, output_tokens=500)
    assert b is not None
    assert b.total_ticks == 1000 * 8_000_000 // 1_000_000 + 500 * 28_000_000 // 1_000_000
    assert b.total_ticks == 22_000
    assert [(c.kind, c.tokens, c.ticks) for c in b.components] == [
        ("uncached_input", 1000, 8_000), ("output", 500, 14_000)]
    assert b.amount() == "¥0.022"


def test_amount_formatting_avoids_float():
    table = {"GLM-X": pricing.parse_model_pricing("GLM-X", _table_raw(
        uncached_input=1, cached_input=1, cache_write=0, output=1), pricing.SOURCE_BUILTIN)}
    b = pricing.resolve_breakdown(table, "GLM-X", input_tokens=1, output_tokens=0)
    assert b is not None and b.total_ticks == 0
    assert b.amount() == "¥0.0"


def test_no_tokens_is_unknown_not_zero():
    table = pricing.builtin_table()
    assert pricing.resolve_breakdown(table, "glm-5.3",
                                     input_tokens=None, output_tokens=None) is None
    assert pricing.resolve_breakdown(table, "glm-5.3",
                                     input_tokens=0, output_tokens=0) is None
    assert pricing.resolve_breakdown(table, "no-such-model",
                                     input_tokens=10, output_tokens=10) is None


def test_negative_tokens_are_clamped():
    b = pricing.resolve_breakdown(pricing.builtin_table(), "glm-5.3",
                                  input_tokens=-5, output_tokens=1000)
    assert b is not None
    assert [(c.kind, c.tokens) for c in b.components] == [("output", 1000)]


# ── 阶梯边界 ─────────────────────────────────────────────────────────────────
def test_tier_boundary_at_32768():
    table = pricing.builtin_table()
    low = pricing.resolve_breakdown(table, "glm-5.1", input_tokens=32_767, output_tokens=0)
    high = pricing.resolve_breakdown(table, "glm-5.1", input_tokens=32_768, output_tokens=0)
    assert low.tier_id == "lt32k" and high.tier_id == "ge32k"
    assert low.components[0].ticks == 32_767 * 6_000_000 // 1_000_000
    assert high.components[0].ticks == 32_768 * 8_000_000 // 1_000_000


def test_tier_hit_uses_total_input_including_cache():
    # 档位按「总输入」= uncached + cache_read + cache_write，不是只看不命中部分
    table = pricing.builtin_table()
    b = pricing.resolve_breakdown(table, "glm-5.1", input_tokens=1_000, output_tokens=0,
                                  cache_read=31_000)
    assert b.tier_id == "lt32k"
    b2 = pricing.resolve_breakdown(table, "glm-5.1", input_tokens=1_000, output_tokens=0,
                                   cache_read=31_768)
    assert b2.tier_id == "ge32k"


def test_missing_tier_cover_falls_back_to_last():
    p = pricing.parse_model_pricing("GLM-Y", {"currency": "CNY",
                                              "tiers": [_tier(min_input=100_000, max_input=None)]},
                                    pricing.SOURCE_BUILTIN)
    assert p.tier_for(1) is p.tiers[0]


# ── cache 切分 ───────────────────────────────────────────────────────────────
def test_cache_read_and_write_components():
    table = pricing.builtin_table()
    b = pricing.resolve_breakdown(table, "glm-5.3-flash", input_tokens=1_000,
                                  output_tokens=1_000, cache_read=500_000, cache_write=10_000)
    got = {c.kind: c.ticks for c in b.components}
    assert got["cached_input"] == 500_000 * 230_000 // 1_000_000 == 115_000
    assert got["cache_write"] == 0  # 官方「限时免费」
    assert got["uncached_input"] == 800
    assert b.total_ticks == sum(got.values())


# ── 校验与回落 ───────────────────────────────────────────────────────────────
def test_parse_rejects_bad_prices_and_intervals():
    assert pricing.parse_model_pricing("M", _table_raw(uncached_input=-1), "builtin") is None
    assert pricing.parse_model_pricing("M", _table_raw(uncached_input=1001 * 1_000_000),
                                       "builtin") is None
    assert pricing.parse_model_pricing("M", _table_raw(max_input=0), "builtin") is None
    assert pricing.parse_model_pricing("M", {"tiers": []}, "builtin") is None
    assert pricing.parse_model_pricing("", _table_raw(), "builtin") is None


def test_parse_accepts_free_pricing():
    # 「限时免费」是真价格，不是缺失：拒绝 0 会让该模型从拉取层静默消失
    assert pricing.parse_model_pricing("M", _table_raw(uncached_input=0), "builtin") is not None
    assert pricing.parse_model_pricing("M", _table_raw(), "builtin") is not None


def test_parse_rejects_overlapping_tiers():
    raw = {"currency": "CNY", "tiers": [
        _tier(tier_id="a", min_input=0, max_input=1000),
        _tier(tier_id="b", min_input=500, max_input=None)]}
    assert pricing.parse_model_pricing("M", raw, "builtin") is None


def test_parse_table_bad_json_returns_empty():
    assert pricing.parse_table(None, "admin") == {}
    assert pricing.parse_table("[]", "admin") == {}
    assert pricing.parse_table({"v": 99, "models": {"GLM-5.3": _table_raw()}}, "admin") == {}
    assert pricing.parse_table({"v": 1, "models": []}, "admin") == {}
    # 单模型坏不影响同层其它模型
    table = pricing.parse_table({"v": 1, "models": {"GLM-5.3": _table_raw(),
                                                    "GLM-Bad": {"tiers": []}}}, "admin")
    assert set(table) == {"GLM-5.3"}


def test_effective_table_layer_priority_builtin_pull_admin():
    pulled = {"models": {"GLM-5.3": {"currency": "CNY", "as_of": "2026-11-01", "tiers": [
        {"tier_id": "standard", "min_input": 0, "uncached_input": 5_000_000}]}}}
    admin = {"v": 1, "models": {"GLM-5.3": {"currency": "CNY", "as_of": "2026-12-01", "tiers": [
        {"tier_id": "standard", "min_input": 0, "output": 9_000_000}]}}}
    table = pricing.effective_table(admin, pulled)
    glm = table["GLM-5.3"]
    assert glm.source == pricing.SOURCE_ADMIN
    # admin 只覆盖 output，pulled 的输入价保留（逐字段覆盖而非整表替换）
    assert glm.tiers[0].units["uncached_input"] == 5_000_000
    assert glm.tiers[0].units["output"] == 9_000_000
    assert glm.tiers[0].units["cached_input"] == 2_000_000  # 内置价未被触碰
    assert glm.as_of == "2026-12-01"
    # 未覆盖的模型仍是内置价
    assert table["GLM-5.3-Flash"].source == pricing.SOURCE_BUILTIN


def test_merge_matches_tier_by_interval_not_label():
    # 内置表把阶梯叫 lt32k/ge32k，官方页解析出来叫 0-32768/32768-inf。按名字匹配
    # 会让同一档追加成两套价并先到先赢，一个模型就同时挂着两份钱
    base = pricing.parse_table({"models": {"GLM-Ladder": {"currency": "CNY", "tiers": [
        {"tier_id": "lt32k", "min_input": 0, "max_input": 32768,
         "uncached_input": 6_000_000, "cached_input": 1_300_000},
        {"tier_id": "ge32k", "min_input": 32768, "uncached_input": 8_000_000,
         "output": 28_000_000}]}}}, pricing.SOURCE_BUILTIN)
    pulled = pricing.parse_table({"models": {"GLM-Ladder": {"currency": "CNY", "tiers": [
        {"tier_id": "0-32768", "min_input": 0, "max_input": 32768, "uncached_input": 1_000},
        {"tier_id": "32768-inf", "min_input": 32768, "uncached_input": 2_000}]}}},
        pricing.SOURCE_PULLED)
    merged = pricing.merge_tables(base, pulled)["GLM-Ladder"]
    assert [(t.min_input, t.max_input) for t in merged.tiers] == [(0, 32768), (32768, None)]
    assert merged.tiers[0].units["uncached_input"] == 1_000
    assert merged.tiers[0].units["cached_input"] == 1_300_000  # 后层没给的kind沿用前层
    assert merged.tiers[1].units["output"] == 28_000_000


def test_flat_override_replaces_a_laddered_model():
    base = pricing.parse_table({"models": {"GLM-Ladder": {"currency": "CNY", "tiers": [
        {"tier_id": "lt32k", "min_input": 0, "max_input": 32768, "uncached_input": 6_000_000},
        {"tier_id": "ge32k", "min_input": 32768, "uncached_input": 8_000_000}]}}},
        pricing.SOURCE_BUILTIN)
    flat = pricing.parse_table({"models": {"GLM-Ladder": {"currency": "CNY", "tiers": [
        {"tier_id": "standard", "min_input": 0, "uncached_input": 5}]}}}, pricing.SOURCE_ADMIN)
    merged = pricing.merge_tables(base, flat)["GLM-Ladder"]
    assert len(merged.tiers) == 1 and merged.tiers[0].min_input == 0
    # 不相交的新区间是新增一档，不是覆盖
    extra = pricing.parse_table({"models": {"GLM-Ladder": {"currency": "CNY", "tiers": [
        {"tier_id": "128k", "min_input": 131072, "uncached_input": 9}]}}}, pricing.SOURCE_ADMIN)
    assert len(pricing.merge_tables(base, extra)["GLM-Ladder"].tiers) == 3


def test_effective_table_survives_corrupt_layers():
    table = pricing.effective_table({"v": 1, "models": "not-a-dict"}, "broken")
    assert table == pricing.builtin_table()


def test_dump_parse_roundtrip_preserves_structure():
    # 后台覆盖要落 meta，落库格式必须读得回来，否则重启后价格静默回落内置表
    table = pricing.builtin_table()
    assert pricing.parse_table(pricing.dump_table(table), "builtin") == table


def test_parse_tier_accepts_nested_units():
    nested = {"tier_id": "t", "min_input": 0, "max_input": None,
              "units": {"uncached_input": 1, "cached_input": 1, "output": 1}}
    flat = {"tier_id": "t", "min_input": 0, "max_input": None,
            "uncached_input": 1, "cached_input": 1, "output": 1}
    assert pricing.parse_tier(nested) == pricing.parse_tier(flat)


def test_normalize_admin_table_accepts_bare_and_wrapped():
    wrapped = pricing.normalize_admin_table({"v": 1, "models": {"GLM-5.3": _table_raw()}})
    bare = pricing.normalize_admin_table({"GLM-5.3": _table_raw()})
    assert wrapped == bare
    assert wrapped["v"] == 1
    assert pricing.parse_table(wrapped, "admin")["GLM-5.3"].source == pricing.SOURCE_ADMIN


def test_normalize_admin_table_rejects_garbage():
    with pytest.raises(ValueError):
        pricing.normalize_admin_table([])
    with pytest.raises(ValueError):
        pricing.normalize_admin_table({"GLM-5.3": {"tiers": []}})
    with pytest.raises(ValueError):
        pricing.normalize_admin_table({})


# ── 官方页文本解析原语（P4 拉取的解析边界）───────────────────────────────────
def test_yuan_per_million_to_ticks():
    assert pricing.yuan_per_million_to_ticks("8") == 8_000_000
    assert pricing.yuan_per_million_to_ticks("0.8") == 800_000
    assert pricing.yuan_per_million_to_ticks("0.23") == 230_000
    assert pricing.yuan_per_million_to_ticks("1,024") == 1_024_000_000
    assert pricing.yuan_per_million_to_ticks("限时免费") == 0
    assert pricing.yuan_per_million_to_ticks("免费") == 0
    assert pricing.yuan_per_million_to_ticks("元/百万 Tokens") is None
    assert pricing.yuan_per_million_to_ticks("") is None
    assert pricing.yuan_per_million_to_ticks(None) is None


def test_tier_bounds_from_official_cells():
    assert pricing.tier_bounds("输入长度 [0, 32K)") == (0, 32_768)
    assert pricing.tier_bounds("输入长度 ≥32K") == (32_768, None)
    assert pricing.tier_bounds("输入 [32K, 200K)") == (32_768, 204_800)
    assert pricing.tier_bounds("1M") == (0, None)
    assert pricing.tier_bounds("") == (0, None)
