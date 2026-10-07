"""官方定价页拉取（默认关闭）。

三条不可让的前提：
1. **绝不代表账号**。请求只带 Accept/Accept-Language 和一个与 ZCode 无关的 UA，
   绝不带 `X-Device-Mid` 等身份头族——docs 域不在账号体系里，带上它就等于把
   「这个 IP 有 ZCode 账号」交给一个第三方风控可见的域名，凭空多出关联面。
2. **Fail-open**。解析不出来、非 2xx、网络异常，一律保留上一份拉取值并记状态；
   builtin 与 admin 两层永远不受影响。定价页改版不该让网关的钱算错。
3. **省请求**。单轮一次条件 GET（If-None-Match / If-Modified-Since），304 直接跳过，
   不做重试；被 403/429 挡住时把间隔翻倍、封顶 7 天，别把对方的静态站打成靶子。
"""

from __future__ import annotations

import asyncio
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from . import constants, logs, pricing
from .store import store

# 与 ZCode 身份头族完全无关的 UA：不出现 zcode / 设备号 / 本机名
USER_AGENT = "zcode-hub-pricing/1.0 (read-only; local gateway admin)"
MAX_BACKOFF = 7 * 86400
FETCH_TIMEOUT = 20.0

_lock = asyncio.Lock()


class _GridParser(HTMLParser):
    """把页面里的表格收成 raw rows：每格 (文本, rowspan, colspan)。

    不用正则：单元格有 data-numeric 属性、内嵌 <span>/<br> 和中文「元」，
    正则对列错位毫无防御，一旦官方加一列就会静默串列。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[tuple[str, int, int]]]] = []
        self._depth = 0
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: list[str] | None = None
        self._cell_attrs = (1, 1)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append([])
            return
        if self._depth == 0:
            return
        if tag == "tr":
            self._row = []
            if self.tables:
                self.tables[-1].append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            attr = dict(attrs)
            self._cell = []
            self._cell_attrs = (_positive_int(attr.get("rowspan"), 1),
                                _positive_int(attr.get("colspan"), 1))
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._depth = max(0, self._depth - 1)
        elif tag == "tr":
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                text = " ".join("".join(self._cell).split())
                self._row.append((text, self._cell_attrs[0], self._cell_attrs[1]))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def expand_rows(raw_rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    """rowspan/colspan 摊平成等宽网格——官方用 rowspan 合并模型名与区间列，
    不摊平的话续行的每一格都会往左错一列。"""
    placed: dict[tuple[int, int], str] = {}
    for r, cells in enumerate(raw_rows):
        col = 0
        for text, rowspan, colspan in cells:
            while (r, col) in placed:
                col += 1
            for dr in range(rowspan):
                for dc in range(colspan):
                    placed.setdefault((r + dr, col + dc), text)
            col += colspan
    if not placed:
        return []
    n_rows = max(k[0] for k in placed) + 1
    n_cols = max(k[1] for k in placed) + 1
    return [[placed.get((r, c), "") for c in range(n_cols)] for r in range(n_rows)]


def _kind_of(header: str) -> str | None:
    """表头文本 → 价格种类。判断顺序即优先级：缓存写入要在「缓存」之前认。

    阶梯列官方叫「上下文」（值才是 "输入长度 [0, 32K)"），认不到它就把整条阶梯
    读成一档，>32K 的输入按低价算钱。「缓存存储（元/百万 Tokens/小时）」是按小时
    计的存储费、不是每 token 的缓存写入价，必须不认；「输入模态」带「输入」二字
    但装的是「图片、视频、文本」，同样必须不认。
    """
    t = header.replace(" ", "").replace("\u3000", "")
    if not t or "模态" in t:
        return None
    if "模型" in t:
        return "model"
    if "上下文" in t or "输入长度" in t or "长度区间" in t:
        return "tier"
    if "小时" in t:
        return None
    if "缓存写入" in t or "缓存创建" in t:
        return pricing.CACHE_WRITE
    if "缓存" in t and ("输入" in t or "命中" in t):
        return pricing.CACHED_INPUT
    if "输出" in t:
        return pricing.OUTPUT
    if "输入" in t:
        return pricing.UNCACHED_INPUT
    return None


def add_tier(models: dict[str, list[dict[str, Any]]], name: str, tier: dict[str, Any]) -> None:
    """同档位多行时取较高价。

    GLM-4.7 官方是「输入 × 输出」两维阶梯，我们只建模输入维（见 constants 注释）。
    两维塌成一档时若留第一行，就等于按最便宜的那档报价——宁可多算，不可静默少算。
    """
    span = (tier["min_input"], tier["max_input"])
    for existing in models.setdefault(name, []):
        if (existing["min_input"], existing["max_input"]) != span:
            continue
        for kind, ticks in tier.items():
            if isinstance(ticks, int) and ticks > existing.get(kind, 0):
                existing[kind] = ticks
        return
    models[name].append(tier)


def _cells_to_model(row: list[str], kinds: dict[int, str]) -> tuple[str, dict[str, Any]] | None:
    model = ""
    tier_text = ""
    units: dict[str, int] = {}
    for idx, text in enumerate(row):
        kind = kinds.get(idx)
        if kind == "model":
            model = text
        elif kind == "tier":
            tier_text = text
        elif kind:
            ticks = pricing.yuan_per_million_to_ticks(text)
            if ticks is not None:
                units[kind] = ticks
    if not model or not units:
        return None
    low, high = pricing.tier_bounds(tier_text)
    tier = {"tier_id": f"{low}-{high or 'inf'}", "min_input": low, "max_input": high, **units}
    return model, tier


def _models_from_grid(grid: list[list[str]]) -> dict[str, list[dict[str, Any]]]:
    """一张表 → {模型名: [档位…]}；表头不像价目表就返回空。"""
    for header_idx, header in enumerate(grid):
        mapped = {idx: kind for idx, cell in enumerate(header) if (kind := _kind_of(cell))}
        if "model" not in mapped.values() or pricing.UNCACHED_INPUT not in mapped.values():
            continue
        models: dict[str, list[dict[str, Any]]] = {}
        for row in grid[header_idx + 1:]:
            hit = _cells_to_model(row, mapped)
            if hit is None:
                continue
            name, tier = hit
            add_tier(models, name, tier)
        if models:
            return models
    return {}


def parse_pricing_html(html_text: str) -> dict[str, Any]:
    """定价页 HTML → 裸计价表（{"GLM-X": {currency, as_of, tiers:[…]}}）；认不出就 {}。

    官方页把价目按「文本模型 / 多模态 / …」拆成好几张表，只取首表会漏掉大半；
    跨表同名模型按档位区间合并（同档取较高价），别让重复档位在校验环节把整个模型判死。
    """
    parser = _GridParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001 - 畸形 HTML 一律当作解析失败（fail-open）
        return {}

    collected: dict[str, list[dict[str, Any]]] = {}
    for raw_rows in parser.tables:
        for name, tiers in _models_from_grid(expand_rows(raw_rows)).items():
            for tier in tiers:
                add_tier(collected, name, tier)
    if not collected:
        return {}

    table = {name: {"currency": "CNY", "as_of": _today(), "tiers": tiers}
             for name, tiers in collected.items()}
    checked = pricing.parse_table(
        {"v": pricing.SCHEMA_VERSION, "models": table}, pricing.SOURCE_PULLED)
    return pricing.dump_table(checked)["models"] if checked else {}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


async def pull_once(client: httpx.AsyncClient | None = None) -> dict:
    """一轮拉取。后台轮和后台「立即拉取」按钮会撞车，同一时刻只允许一个在飞——
    第二个直接回 busy，不去叠第二个请求（这层的全部意义是少打对方的站）。"""
    if _lock.locked():
        return {"result": "busy", "changed": False}
    async with _lock:
        return await _pull_once(client)


async def _pull_once(client: httpx.AsyncClient | None = None) -> dict:
    """跑一轮拉取，返回状态字典（同时落进 pricing_pull_status）。

    返回的 result：ok（拿到新表）/ unchanged(304) / rejected(403/429) / error。
    """
    started = time.time()
    previous = store.pricing_pulled()
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.8",
        "User-Agent": USER_AGENT,
    }
    if previous.get("etag"):
        headers["If-None-Match"] = str(previous["etag"])
    if previous.get("last_modified"):
        headers["If-Modified-Since"] = str(previous["last_modified"])

    status: dict[str, Any] = {"last_attempt_at": started, "interval": store.pricing_pull_interval()}
    url = constants.PRICING_SOURCE_URL
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as own:
                resp = await own.get(url, headers=headers)
        else:
            resp = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as err:
        logs.warn("pricing", f"定价页拉取失败，沿用上一份: {type(err).__name__}")
        status.update(result="error", error=f"{type(err).__name__}: {err}", changed=False)
        store.set_pricing_pull_status(status)
        return status

    status["http_status"] = resp.status_code
    if resp.status_code == 304:
        status.update(result="unchanged", changed=False)
        store.set_pricing_pull_status(status)
        return status
    if resp.status_code in (401, 403, 429):
        wait = min(MAX_BACKOFF, _backoff_seconds(resp, status["interval"]))
        status.update(result="rejected", error=f"HTTP {resp.status_code}",
                      blocked_until=started + wait, changed=False)
        logs.warn("pricing", f"定价页返回 {resp.status_code}，下次拉取退避到 {wait / 3600:.1f} 小时后")
        store.set_pricing_pull_status(status)
        return status
    if not (200 <= resp.status_code < 300):
        status.update(result="error", error=f"HTTP {resp.status_code}", changed=False)
        store.set_pricing_pull_status(status)
        return status

    models = parse_pricing_html(resp.text)
    if not models:
        # 页面改版认不出表格：保留旧值，绝不写空表把拉取层抹掉
        logs.warn("pricing", "定价页解析不出任何模型，沿用上一份拉取值")
        status.update(result="error", error="解析不出模型（页面结构可能已改版）", changed=False)
        store.set_pricing_pull_status(status)
        return status

    payload = {"v": pricing.SCHEMA_VERSION, "models": models, "fetched_at": time.time(),
               "source_url": constants.PRICING_SOURCE_URL,
               "etag": resp.headers.get("etag") or "", "last_modified": resp.headers.get(
                   "last-modified") or ""}
    # 条件 GET 凭证：上游这次没发就不覆盖，否则下次退化成全量拉
    if not payload["etag"]:
        payload["etag"] = previous.get("etag") or ""
    if not payload["last_modified"]:
        payload["last_modified"] = previous.get("last_modified") or ""

    changed = payload["models"] != (previous.get("models") or {})
    store.set_pricing_pulled(payload)
    status.update(result="ok", models=len(models), changed=changed, error="")
    store.set_pricing_pull_status(status)
    if changed:
        logs.ok("pricing", f"计价规则已更新：{len(models)} 个模型")
    return status


def _backoff_seconds(resp: httpx.Response, interval: int) -> int:
    """优先听 Retry-After，否则把当前间隔翻倍，封顶 7 天。"""
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            hinted = int(float(raw.strip()))
        except (TypeError, ValueError):
            hinted = 0
        if hinted > 0:
            return min(MAX_BACKOFF, max(hinted, 60))
    base = interval if interval > 0 else 86400
    return min(MAX_BACKOFF, base * 2)


class PricingPuller:
    """后台定时拉取。间隔 0 = 关闭，但仍周期性回看设置，随时可以打开。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=10)
            return
        except TimeoutError:
            pass

        while not self._stop.is_set():
            interval = store.pricing_pull_interval()
            if interval > 0:
                try:
                    await pull_once()
                except Exception as err:  # noqa: BLE001 - 后台任务吞掉一切异常继续跑
                    logs.err("pricing", f"拉取任务出错: {err}")
                wait = self._next_wait(interval)
            else:
                wait = 300
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except TimeoutError:
                continue

    def _next_wait(self, interval: int) -> int:
        blocked_until = store.pricing_pull_status().get("blocked_until")
        if isinstance(blocked_until, (int, float)) and blocked_until > time.time():
            return max(60, int(blocked_until - time.time()))
        return interval

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


puller = PricingPuller()
