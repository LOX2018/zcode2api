"""模型计价内核（纯逻辑，无 IO / 无网络）。

钱的表示：一律整数 tick，1 tick = 1e-6 元。单价按「微元 / 百万 token」存
（8 元/1M → 8_000_000），费用 = tokens * 单价 // 1_000_000，全链路不出现浮点，
避免累加误差与 JSON 里 float 落库后的精度漂移。浮点只在两个边界出现一次：
官方页解析（"0.8" → ticks）与展示（ticks → "¥0.0312"）。

计价三层合并，优先级 builtin < pulled < admin（按 model → tier → kind 逐字段覆盖）：
builtin 来自 constants.DEFAULT_MODEL_PRICING，pulled 是官方定价页的定时拉取结果，
admin 是后台人工覆盖表。任一层缺失或 JSON 损坏都回落下一层，绝不因为拉取失败
把价格清空。

tier 档位按「总输入」= uncached + cache_read + cache_write 命中 [min_input, max_input)，
对应官方对 GLM-5.1/5-Turbo/5/4.7 的输入长度阶梯。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from . import constants

# 1 元 = 1_000_000 tick；单价口径为「每百万 token 的微元数」
TICKS_PER_YUAN = 1_000_000
PER_MILLION = 1_000_000

UNCACHED_INPUT = "uncached_input"
CACHED_INPUT = "cached_input"
CACHE_WRITE = "cache_write"
OUTPUT = "output"
PRICE_KINDS = (UNCACHED_INPUT, CACHED_INPUT, CACHE_WRITE, OUTPUT)

SOURCE_BUILTIN = "builtin"
SOURCE_PULLED = "pulled"
SOURCE_ADMIN = "admin"

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PricingTier:
    tier_id: str
    min_input: int
    max_input: int | None
    units: dict[str, int]

    def covers(self, total_input: int) -> bool:
        return total_input >= self.min_input and (self.max_input is None or total_input < self.max_input)

    def to_dict(self) -> dict[str, Any]:
        # 单价摊平在 tier 层（与 parse_tier 的读法对称）；dump→parse 必须可逆，
        # 否则后台覆盖落库后下次启动读不回来，价格静默回落到内置表。
        return {"tier_id": self.tier_id, "min_input": self.min_input,
                "max_input": self.max_input, **self.units}


@dataclass(frozen=True)
class ModelPricing:
    model: str
    currency: str
    tiers: tuple[PricingTier, ...]
    source: str
    as_of: str

    def tier_for(self, total_input: int) -> PricingTier | None:
        for tier in self.tiers:
            if tier.covers(total_input):
                return tier
        return self.tiers[-1] if self.tiers else None

    def to_dict(self) -> dict[str, Any]:
        return {"currency": self.currency, "as_of": self.as_of,
                "tiers": [t.to_dict() for t in self.tiers]}


@dataclass(frozen=True)
class PricingComponent:
    kind: str
    tokens: int
    ticks: int


@dataclass(frozen=True)
class PricingBreakdown:
    model: str
    tier_id: str
    currency: str
    source: str
    as_of: str
    components: tuple[PricingComponent, ...]
    total_ticks: int

    def to_dict(self) -> dict[str, Any]:
        return {"model": self.model, "tier_id": self.tier_id, "currency": self.currency,
                "source": self.source, "as_of": self.as_of, "total_ticks": self.total_ticks,
                "components": [{"kind": c.kind, "tokens": c.tokens, "ticks": c.ticks}
                               for c in self.components]}

    def amount(self) -> str:
        """ticks → 定点金额串（不做浮点格式化，避免 0.1+0.2 类漂移）。"""
        sign = "-" if self.total_ticks < 0 else ""
        whole, frac = divmod(abs(self.total_ticks), TICKS_PER_YUAN)
        return f"{sign}{self._symbol()}{whole}.{str(frac).zfill(6).rstrip('0') or '0'}"

    def _symbol(self) -> str:
        return {"CNY": "¥", "USD": "$"}.get(self.currency, "")


def normalize_model(name: str) -> str:
    """客户端模型名 → 上游规范名（大小写不敏感，别名表与 quota show_name 同域）。"""
    if not name:
        return ""
    stripped = "/".join(name.split("/")[1:]) if "/" in name else name
    return constants.MODEL_NAME_MAP.get(stripped.lower(), stripped)


# 内置表的键大小写即官方口径；别名表未收录的名字（glm-5.3-flashx）归一后仍是大写
# 失配，会让同一模型在合并表里出现两个键、后层覆盖不到前层。
_CANONICAL = {k.lower(): k for k in constants.DEFAULT_MODEL_PRICING}


def canonical_model(name: str) -> str:
    """表键归一：别名映射 + 大小写找回官方写法。"""
    mapped = normalize_model(name)
    return _CANONICAL.get(mapped.lower(), mapped)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def parse_tier(raw: dict[str, Any]) -> PricingTier | None:
    """单个 tier 的结构校验：单价必须非负整数，区间必须左闭右开。"""
    min_input = _int(raw.get("min_input"))
    if min_input is None or min_input < 0:
        return None
    max_input = _int(raw.get("max_input"))
    if max_input is not None and max_input <= min_input:
        return None
    units: dict[str, int] = {}
    flat = dict(raw)
    if isinstance(raw.get("units"), dict):
        # 兼容手写的 {"units": {...}} 嵌套形态（摊平形态是 dump 的输出格式）
        flat.update(raw["units"])
    for kind in PRICE_KINDS:
        tick_price = _int(flat.get(kind))
        if tick_price is not None:
            if tick_price < 0:
                return None
            units[kind] = tick_price
    if not units:
        return None
    return PricingTier(tier_id=str(raw.get("tier_id") or f"t{min_input}"),
                       min_input=min_input, max_input=max_input, units=units)


def parse_model_pricing(model: str, raw: dict[str, Any], source: str) -> ModelPricing | None:
    """校验口径：单价 ∈ [0, 1000 元/1M]。0 合法——官方页确有「限时免费」整行，
    若把它当非法值丢整条，拉取层就静默少一个模型，报表会继续按内置价收钱。"""
    if not isinstance(raw, dict) or not model:
        return None
    tiers: list[PricingTier] = []
    for item in raw.get("tiers") or []:
        if isinstance(item, dict):
            tier = parse_tier(item)
            if tier:
                tiers.append(tier)
    if not tiers:
        return None
    tiers.sort(key=lambda t: t.min_input)
    for prev, nxt in zip(tiers, tiers[1:], strict=False):
        if prev.max_input is not None and nxt.min_input < prev.max_input:
            return None
    limit = 1000 * PER_MILLION
    for tier in tiers:
        for price in tier.units.values():
            if not (0 <= price <= limit):
                return None
    currency = str(raw.get("currency") or "CNY").upper()
    as_of = str(raw.get("as_of") or "")
    return ModelPricing(model=model, currency=currency, tiers=tuple(tiers),
                        source=source, as_of=as_of)


def parse_table(raw: Any, source: str) -> dict[str, ModelPricing]:
    """落库 JSON → 计价表。整体损坏返回空表，由调用方回落下一层。"""
    if not isinstance(raw, dict) or raw.get("v") not in (None, SCHEMA_VERSION):
        return {}
    models = raw.get("models")
    if not isinstance(models, dict):
        return {}
    out: dict[str, ModelPricing] = {}
    for name, item in models.items():
        key = canonical_model(str(name))
        pricing = parse_model_pricing(key, item if isinstance(item, dict) else {}, source)
        if pricing:
            out[key] = pricing
    return out


def dump_table(table: dict[str, ModelPricing]) -> dict[str, Any]:
    return {"v": SCHEMA_VERSION, "models": {k: v.to_dict() for k, v in table.items()}}


def merge_tables(*layers: dict[str, ModelPricing]) -> dict[str, ModelPricing]:
    """按 model → tier → kind 逐字段覆盖，后层价格缺项沿用前层。"""
    merged: dict[str, ModelPricing] = {}
    for layer in layers:
        for model, pricing in layer.items():
            existing = merged.get(model)
            if existing is None:
                merged[model] = pricing
                continue
            merged[model] = _overlay(existing, pricing)
    return merged


def _unbounded(high: int | None) -> float:
    return float("inf") if high is None else high


def _clip(tiers: list[PricingTier], span: PricingTier) -> list[PricingTier]:
    """把落在 span 区间内的部分从旧档里挖掉（旧档可能被切成左右两段）。

    新区间是对某一段的改价，不是对整条阶梯的重写——整段丢掉旧档会留下
    「32K~128K 没有价」的空洞，算钱时那一档直接查不到。
    """
    out: list[PricingTier] = []
    for tier in tiers:
        if _unbounded(tier.max_input) <= span.min_input or tier.min_input >= _unbounded(span.max_input):
            out.append(tier)
            continue
        if tier.min_input < span.min_input:
            out.append(replace(tier, max_input=span.min_input))
        if span.max_input is not None and _unbounded(tier.max_input) > span.max_input:
            out.append(replace(tier, min_input=span.max_input))
    return out


def _overlay(base: ModelPricing, over: ModelPricing) -> ModelPricing:
    """后层覆盖前层：认区间不认标签。

    tier_id 只是名字——内置表叫 lt32k，官方页解析出来叫 0-32768。按 id 匹配会把同
    一档追加成两套价并先到先赢，一个模型同时挂着两份钱。所以：区间完全相同的档
    逐 kind 叠加（后层没写的沿用前层，人工只改输出价的用法全在这里），区间不同的
    新档先挖掉旧档盖住的范围再插入；旧档被整条盖住就是重构（扁平↔阶梯）。
    """
    over_spans = {(tier.min_input, tier.max_input): tier for tier in over.tiers}
    tiers: list[PricingTier] = []
    for base_tier in base.tiers:
        twin = over_spans.get((base_tier.min_input, base_tier.max_input))
        if twin is None:
            tiers.append(base_tier)
            continue
        units = dict(base_tier.units)
        units.update(twin.units)
        tiers.append(replace(base_tier, units=units))
    for new_tier in over.tiers:
        if (new_tier.min_input, new_tier.max_input) in {(t.min_input, t.max_input) for t in tiers}:
            continue
        tiers = _clip(tiers, new_tier)
        tiers.append(new_tier)
    tiers.sort(key=lambda t: t.min_input)
    return ModelPricing(model=base.model, currency=over.currency or base.currency,
                        tiers=tuple(tiers), source=over.source, as_of=over.as_of or base.as_of)


def builtin_table() -> dict[str, ModelPricing]:
    out: dict[str, ModelPricing] = {}
    for name, raw in constants.DEFAULT_MODEL_PRICING.items():
        key = canonical_model(str(name))
        pricing = parse_model_pricing(key, raw, SOURCE_BUILTIN)
        if pricing:
            out[key] = pricing
    return out


def price_for(table: dict[str, ModelPricing], model: str) -> ModelPricing | None:
    """查价：先按归一名精确命中，再大小写不敏感兜底。

    MODEL_NAME_MAP 只钉了当前在售的几个别名，未收录的名字（如 glm-5.3-flashx）
    归一后仍是小写，而表键是官方大小写——不做这一步兜底就会「有价目但查不到」，
    费用静默记空。
    """
    name = canonical_model(model)
    if not name:
        return None
    hit = table.get(name)
    if hit is not None:
        return hit
    lowered = name.lower()
    for key, pricing in table.items():
        if key.lower() == lowered:
            return pricing
    return None


def effective_table(overrides: dict[str, Any], pulled: dict[str, Any]) -> dict[str, ModelPricing]:
    """builtin < pulled < admin 三层合并。overrides / pulled 是 meta 里的原始 JSON。

    任一层损坏（parse_table 回落空表）都只是丢掉该层，不会让价格整体消失。
    """
    pull_models = pulled.get("models") if isinstance(pulled, dict) else None
    return merge_tables(
        builtin_table(),
        parse_table({"v": SCHEMA_VERSION, "models": pull_models or {}}, SOURCE_PULLED),
        parse_table(overrides, SOURCE_ADMIN),
    )


def normalize_admin_table(raw: Any) -> dict[str, Any]:
    """后台提交的覆盖表 → 可落库 JSON；结构非法抛 ValueError（调用方转 400）。"""
    if not isinstance(raw, dict):
        raise ValueError("计价表必须是 JSON 对象")
    models = raw["models"] if isinstance(raw.get("models"), dict) else raw
    table = parse_table({"v": SCHEMA_VERSION, "models": models}, SOURCE_ADMIN)
    if not table:
        raise ValueError("计价表为空或结构非法：每个模型需含 tiers，单价为非负整数")
    return dump_table(table)


def resolve_breakdown(
    table: dict[str, ModelPricing],
    model: str,
    *,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read: int | None = None,
    cache_write: int | None = None,
) -> PricingBreakdown | None:
    """一次请求的费用分解；模型无价目或没有任何 token 数则返回 None（未知 ≠ 0）。"""
    pricing = price_for(table, model)
    if pricing is None:
        return None
    uncached = max(0, _int(input_tokens) or 0)
    cached = max(0, _int(cache_read) or 0)
    writing = max(0, _int(cache_write) or 0)
    out = max(0, _int(output_tokens) or 0)
    if uncached + cached + writing + out == 0:
        return None
    tier = pricing.tier_for(uncached + cached + writing)
    if tier is None:
        return None
    wanted = [(UNCACHED_INPUT, uncached), (CACHED_INPUT, cached),
              (CACHE_WRITE, writing), (OUTPUT, out)]
    components: list[PricingComponent] = []
    total = 0
    for kind, tokens in wanted:
        if tokens <= 0:
            continue
        ticks = tokens * tier.units.get(kind, 0) // PER_MILLION
        components.append(PricingComponent(kind=kind, tokens=tokens, ticks=ticks))
        total += ticks
    return PricingBreakdown(model=pricing.model, tier_id=tier.tier_id, currency=pricing.currency,
                            source=pricing.source, as_of=pricing.as_of,
                            components=tuple(components), total_ticks=total)


_MONEY_RE = re.compile(r"\d+(?:\.\d+)?")


def yuan_per_million_to_ticks(text: Any) -> int | None:
    """官方页单元格文本 → 微元/百万 token。'限时免费'/空 → 0；解析不出数字 → None。"""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if "免费" in raw:
        return 0
    match = _MONEY_RE.search(raw.replace(",", ""))
    if match is None:
        return None
    return round(float(match.group(0)) * PER_MILLION)


def tier_bounds(cell_text: str) -> tuple[int, int | None]:
    """首格里的阶梯描述 → [min, max)。无阶梯信息则全区间。"""
    raw = cell_text or ""
    ge = re.search(r"[≥>=]\s*(\d+)\s*[kK]", raw)
    if ge:
        return int(ge.group(1)) * 1024, None
    rng = re.search(r"\[\s*(\d+)\s*[kK]?\s*,\s*(\d+)\s*[kK]?\s*\)", raw, re.IGNORECASE)
    if rng:
        return int(rng.group(1)) * 1024, int(rng.group(2)) * 1024
    return 0, None
