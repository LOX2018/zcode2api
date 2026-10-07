"""模型可见性白名单：四档模式 + 每号可打模型集推导。

背景：/v1/models 过去直吐写死的常量，而 /v1/messages 完全不校验模型名——任何
名字都透传给上游，等上游回 code:3006「model not allowed」。用额度试错的代价是
真金白银的 tokens，且 3006 在容灾分类里没有任何分支，落在兜底 passthrough。

四档：
  manual   —— 只认后台手填的名字名单（默认）：你写什么就放行什么，账号权益不参与
  static   —— 只认 constants.AVAILABLE_MODELS（人工实测钉死的表，行为等同于改造前）
  dynamic  —— 只认账号池实际权益推导出的模型集（billing/balance 的 show_name
              ∪ plans[].entitlements[] 里 meter=="model_usage" 的 show_name）
  hybrid   —— 内置 ∪ 派生，/v1/models 带溯源

三处 fail-open，都必须记住：
  1. 派生集为空（池子里没账号 / 额度还没刷出来）→ 回落 static，否则 dynamic
     模式会把自己锁死成「任何模型都不支持」；
  2. 手填名单为空 → /v1/models 回落 static 显示，但早退拦截整个关掉（「还没填」
     不等于「什么都不许打」，一次误清空绝不能把网关锁死）；
  3. 账号没有任何额度快照 → account_supports 返回 True，未知 ≠ 不支持。
"""

from __future__ import annotations

import re
import time

from . import constants, logs
from .models import Account
from .pricing import canonical_model
from .settings import WHITELIST_MODES
from .store import store

MODE_MANUAL = "manual"
MODE_STATIC = "static"
MODE_DYNAMIC = "dynamic"
MODE_HYBRID = "hybrid"

# entitlement 计量口径：只有 model_usage 是「能打哪个模型」，其余（如 seat/请求
# 次数）是套餐噪音项——mock 上游里就塞了 meter="other" 的反例
MODEL_USAGE_METER = "model_usage"

# 手填名单的分隔符：换行最直观，但也接受逗号/分号/顿号，方便从表格里整行粘进来。
# 不按空格拆——名字里真有空格时宁可当成一个错名被拒，也不要静默拆成两个能打的模型。
_NAME_SEP = re.compile(r"[\n\r,，;；、]+")


def current_mode() -> str:
    mode = store.model_whitelist_mode()
    return mode if mode in WHITELIST_MODES else MODE_MANUAL


def parse_names(text: str) -> list[str]:
    """名单原文 → 归一化后的模型名（保序去重，大小写不敏感）。

    归一走 canonical_model：手填 `glm-5.3` / `GLM 5.3` 这类别名要能和上游写法对上，
    否则「我明明填了」和「放行」之间会差一层 nobody-knows 的大小写。
    """
    out: list[str] = []
    seen: set[str] = set()
    for token in _NAME_SEP.split(str(text or "")):
        name = canonical_model(token.strip())
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


def manual_models() -> list[str]:
    return parse_names(store.model_whitelist_names_text())


def static_models() -> list[str]:
    return list(constants.AVAILABLE_MODELS)


def _entitlement_models(acc: Account) -> set[str]:
    names: set[str] = set()
    for plan in acc.plans or []:
        if not isinstance(plan, dict):
            continue
        for ent in plan.get("entitlements") or []:
            if not isinstance(ent, dict) or ent.get("meter") != MODEL_USAGE_METER:
                continue
            name = ent.get("show_name") or ent.get("model")
            if name:
                names.add(canonical_model(str(name)))
    return names


def _quota_models(acc: Account) -> set[str]:
    names: set[str] = set()
    for name, window in (acc.quota or {}).items():
        if isinstance(window, dict):
            remaining = window.get("remaining")
            if isinstance(remaining, (int, float)) and remaining <= 0:
                continue  # 窗口耗尽的模型不进可见集
        key = canonical_model(str(name))
        if key:
            names.add(key)
    return names


def account_models(acc: Account) -> set[str]:
    """该账号权益内的模型集（不含 3006 已钉项）；空集 = 尚无额度快照。"""
    now = time.time()
    return {m for m in (_quota_models(acc) | _entitlement_models(acc))
            if not acc.is_model_denied(m, now)}


def account_supports(acc: Account, model: str) -> bool:
    """按号筛选：该号当前是否值得用这个模型去打一次。

    static / manual 模式不做按号判断（名单是全局的，按号跳过等于瞎跳）。额度快照
    空的账号一律放行 —— 新入池/未刷额度的账号被当成「不知道」而不是「不支持」。
    """
    name = canonical_model(model)
    if not name:
        return True
    if acc.is_model_denied(name):
        return False
    if current_mode() in (MODE_STATIC, MODE_MANUAL):
        return True
    known = account_models(acc)
    if not known:
        return True
    return name.lower() in {m.lower() for m in known}


def derived_models() -> set[str]:
    """全池（可选中账号）权益推导集。"""
    out: set[str] = set()
    for acc in store.list_accounts():
        if acc.is_selectable():
            out |= account_models(acc)
    return out


def has_knowledge() -> bool:
    """池子里是否真的知道「谁能打什么」。

    早退拦截的前提是有据可依：号池非空但额度还没刷出来时，「不在白名单」只是
    「我还没查过」，此时拒绝等于把网关锁死（改造前是透传，上游回 3006 才换号）。
    所以这种状态一律放行，让 3006 容灾（E3）去学真话。

    manual 档同理但方向相反：名单**填了**才是已知（你写了名字就是「只许这些」）；
    空名单是「还没填」，此时拦截等于拿一份你没做主的默认表去拒真实请求。注意它和
    allowed() 分工不同 —— allowed() 空表回落 static 是为了 /v1/models 不显示成空，
    而这里决定的是「要不要把请求挡在网关门口」。
    """
    mode = current_mode()
    if mode == MODE_MANUAL:
        return bool(manual_models())
    if mode == MODE_STATIC:
        return True
    return bool(derived_models())


def allowed() -> list[str]:
    """对外可见 / 可请求的模型名列表。

    顺序稳定：manual 按填写顺序（你写第一行就是第一个），其余模式常量原序在前、
    派生新增按字母序追加——/v1/models 的返回顺序被回归基线用例钉着，不能让账号池
    的额度刷新把列表顺序搅动。
    """
    mode = current_mode()
    static = static_models()
    if mode == MODE_STATIC:
        return static
    if mode == MODE_MANUAL:
        names = manual_models()
        if not names:
            # 名单空 = 「还没填」，不是「什么都不许打」——回落 static，否则一次误
            # 清空会让所有请求 400，而 400 的文案又只会让人去查名单
            logs.warn("whitelist", "手填名单为空，临时回落 static 内置名单")
            return static
        return names
    derived = derived_models()
    if not derived:
        # 派生集为空 → 临时回落 static：dynamic 模式下「查不到权益」绝不能等于
        # 「所有模型都不支持」，否则一次额度刷新失败就把网关锁死
        logs.warn("whitelist", "账号池未能推导出任何模型（无账号或未刷额度），临时回落 static")
        return static
    if mode == MODE_DYNAMIC:
        return sorted(derived)
    seen = {m.lower() for m in static}
    return static + sorted(m for m in derived if m.lower() not in seen)


def models_with_provenance() -> list[dict]:
    """/v1/models 用：名字 + 来源（manual=后台手填，constant=人工实测钉定，derived=账号权益）。"""
    derived_lower = {m.lower() for m in derived_models()}
    static_lower = {m.lower() for m in static_models()}
    manual_lower = {m.lower() for m in manual_models()}
    mode = current_mode()
    out = []
    for name in allowed():
        sources = []
        if mode == MODE_MANUAL and name.lower() in manual_lower:
            sources.append("manual")
        if mode not in (MODE_DYNAMIC, MODE_MANUAL) and name.lower() in static_lower:
            sources.append("constant")
        if mode != MODE_MANUAL and name.lower() in derived_lower:
            sources.append("derived")
        out.append({"name": name, "sources": sources or ["constant"]})
    return out


def capability_models() -> list[dict]:
    """上游**实际提供**什么模型 —— 与 allowed() 分开的另一个口径。

    allowed() 是「策略层放行哪些」（手填名单能把它收窄到比上游还少），capability_models()
    是「能力层有什么」（人工实测钉定的常量 ∪ 号池权益推导），恒与白名单模式无关。
    后台芯片墙要用后者：否则手填 1 个名字，界面就宣称上游只有 1 个模型，
    而价目表又是另一个产品（开放平台）的，两个口径一混就再也对不上账。
    """
    out: list[dict] = []
    known: set[str] = set()
    for name in static_models():
        known.add(name.lower())
        out.append({"name": name, "sources": ["constant"]})
    for name in sorted(derived_models()):
        if name.lower() in known:
            continue
        known.add(name.lower())
        out.append({"name": name, "sources": ["derived"]})
    return out


def is_allowed(model: str) -> bool:
    name = canonical_model(model)
    if not name:
        return False
    return name.lower() in {m.lower() for m in allowed()}


def reject_message(model: str) -> str:
    return (f"模型 {model} 不在本网关白名单内（模式 {current_mode()}）。"
            f"可用模型见 GET /v1/models")
