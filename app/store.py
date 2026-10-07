"""账号与设置的持久化存储（SQLite）。

数据保存在项目本目录下的 data/accounts.db，采用 WAL 模式，
与 grok2api 的本地 (local) 账号后端保持一致。

运行期账号对象常驻内存（保证轮询游标与状态实时性），
每次变更同步落库；进程启动时从 SQLite 读取快照。
"""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing

from . import settings
from .models import PROVIDERS, Account, Status

_TBL = "accounts"
_META = "meta"
_KEYS = "api_keys"
_ACTIVITY = "activity"

#: 活动日志只留最近这些条，超出部分在写入时顺手裁掉
ACTIVITY_KEEP = 2000


def _mask_key(value: str) -> str:
    if len(value) <= 8:
        return "••••"
    return f"{value[:6]}…{value[-4:]}"


class Store:
    """线程安全的账号 / 设置存储，含轮询游标。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._accounts: dict[str, list[Account]] = {p: [] for p in PROVIDERS}
        self._settings: dict = {}
        self._rotation: dict[str, int] = {p: 0 for p in PROVIDERS}
        self._init_db()
        self._load()

    # ── SQLite 基础 ──────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self) -> None:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {_META} (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS {_TBL} (
                    id          TEXT PRIMARY KEY,
                    provider    TEXT NOT NULL,
                    name        TEXT,
                    mode        TEXT,
                    status      TEXT,
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    created_at  REAL,
                    data        TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_acc_provider ON {_TBL} (provider);
                CREATE INDEX IF NOT EXISTS idx_acc_status   ON {_TBL} (status);
                CREATE TABLE IF NOT EXISTS {_KEYS} (
                    id          TEXT PRIMARY KEY,
                    label       TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    created_at  REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS {_ACTIVITY} (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts    REAL NOT NULL,
                    kind  TEXT NOT NULL,
                    text  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_act_ts ON {_ACTIVITY} (ts DESC);
                """
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('admin_key', ?)",
                (settings.DEFAULT_ADMIN_KEY,),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('gateway_key', '')"
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('quota_refresh_interval', ?)",
                (str(settings.QUOTA_REFRESH_INTERVAL),),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('account_concurrency', ?)",
                (str(settings.ACCOUNT_CONCURRENCY),),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('claim_round_interval', ?)",
                (str(settings.CLAIM_ROUND_INTERVAL),),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('model_whitelist_mode', ?)",
                (settings.MODEL_WHITELIST_MODE,),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('model_whitelist_names', '')"
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('pricing_pull_interval', ?)",
                (str(settings.PRICING_PULL_INTERVAL),),
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('pricing_overrides', '')"
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('pricing_pulled', '')"
            )
            conn.execute(
                f"INSERT OR IGNORE INTO {_META} (key, value) VALUES ('pricing_pull_status', '')"
            )
            conn.commit()

    def _load(self) -> None:
        with closing(self._connect()) as conn:
            meta_rows = conn.execute(f"SELECT key, value FROM {_META}").fetchall()
            self._settings = {r["key"]: r["value"] for r in meta_rows}
            self._settings.setdefault("admin_key", settings.DEFAULT_ADMIN_KEY)
            self._settings.setdefault("gateway_key", "")
            self._settings.setdefault("quota_refresh_interval", str(settings.QUOTA_REFRESH_INTERVAL))
            self._settings.setdefault("account_concurrency", str(settings.ACCOUNT_CONCURRENCY))
            self._settings.setdefault("claim_round_interval", str(settings.CLAIM_ROUND_INTERVAL))
            self._settings.setdefault("model_whitelist_mode", settings.MODEL_WHITELIST_MODE)
            self._settings.setdefault("pricing_pull_interval", str(settings.PRICING_PULL_INTERVAL))
            self._import_legacy_gateway_key(conn)

            self._accounts = {p: [] for p in PROVIDERS}
            rows = conn.execute(
                f"SELECT data FROM {_TBL} ORDER BY created_at ASC"
            ).fetchall()
            for row in rows:
                try:
                    account = Account.from_dict(json.loads(row["data"]))
                except (json.JSONDecodeError, TypeError):
                    continue
                if account.provider in self._accounts:
                    self._accounts[account.provider].append(account)

    def _import_legacy_gateway_key(self, conn: sqlite3.Connection) -> None:
        """老版本只有一个手填的网关 Key，升级时落成第一条生成的 Key。

        导入后立刻清空 meta 值，所以删光 Key 不会被重启「复活」；
        老客户端手里那串仍然能用，直到它被主动删除。
        """
        legacy = str(self._settings.get("gateway_key") or "")
        if not legacy:
            return
        conn.execute(
            f"INSERT OR IGNORE INTO {_KEYS} (id, label, key, created_at) VALUES (?,?,?,?)",
            (uuid.uuid4().hex, "导入的旧 Key", legacy, time.time()),
        )
        conn.execute(f"INSERT OR REPLACE INTO {_META} (key, value) VALUES ('gateway_key', '')")
        conn.commit()
        self._settings["gateway_key"] = ""

    # ── 网关 API Key ─────────────────────────────────────────────────────────
    def api_keys(self) -> list[dict]:
        """Key 列表，只带掩码 —— 明文只在 add_api_key 的返回值里出现一次。"""
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT id, label, key, created_at FROM {_KEYS} ORDER BY created_at ASC"
            ).fetchall()
        return [{"id": r["id"], "label": r["label"], "created_at": r["created_at"],
                 "masked": _mask_key(r["key"])} for r in rows]

    def add_api_key(self, label: str = "") -> dict:
        """生成一把新 Key，返回含明文的完整行（只在这一刻返回明文）。"""
        row = {
            "id": uuid.uuid4().hex,
            "label": (label or "").strip()[:40] or "未命名",
            "key": "sk-" + secrets.token_hex(20),
            "created_at": time.time(),
        }
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                f"INSERT INTO {_KEYS} (id, label, key, created_at) VALUES (?,?,?,?)",
                (row["id"], row["label"], row["key"], row["created_at"]),
            )
            conn.commit()
        return row

    def delete_api_key(self, key_id: str) -> bool:
        with self._lock, closing(self._connect()) as conn:
            cur = conn.execute(f"DELETE FROM {_KEYS} WHERE id = ?", (key_id,))
            conn.commit()
        return cur.rowcount > 0

    def match_api_key(self, token: str) -> dict | None:
        """按明文找 Key；恒定时间逐条比对，不通过长度提前泄露存在性。"""
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(f"SELECT id, label, key FROM {_KEYS}").fetchall()
        for r in rows:
            if hmac.compare_digest(token, r["key"]):
                return {"id": r["id"], "label": r["label"]}
        return None

    # ── 活动日志 ─────────────────────────────────────────────────────────────
    def activity_add(self, kind: str, text: str) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                f"INSERT INTO {_ACTIVITY} (ts, kind, text) VALUES (?,?,?)",
                (time.time(), kind, (text or "")[:400]),
            )
            # 裁剪按自增主键而不是行数窗口，避免每次写入都全表扫一遍
            conn.execute(
                f"DELETE FROM {_ACTIVITY} WHERE id <= "
                f"(SELECT MAX(id) - ? FROM {_ACTIVITY})",
                (ACTIVITY_KEEP,),
            )
            conn.commit()

    def activity_list(self, kind: str = "", limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit or 200), ACTIVITY_KEEP))
        with self._lock, closing(self._connect()) as conn:
            if kind:
                rows = conn.execute(
                    f"SELECT ts, kind, text FROM {_ACTIVITY} WHERE kind = ? "
                    f"ORDER BY id DESC LIMIT ?",
                    (kind, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT ts, kind, text FROM {_ACTIVITY} ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def activity_total(self) -> int:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {_ACTIVITY}").fetchone()
        return int(row["n"]) if row else 0

    def _persist_account(self, account: Account) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                f"""INSERT OR REPLACE INTO {_TBL}
                    (id, provider, name, mode, status, enabled, created_at, data)
                    VALUES (?,?,?,?,?,?,?,?)""",
                (
                    account.id, account.provider, account.name, account.mode,
                    account.status, 1 if account.enabled else 0, account.created_at,
                    json.dumps(account.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()

    def _delete_account(self, account_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(f"DELETE FROM {_TBL} WHERE id = ?", (account_id,))
            conn.commit()

    def _set_meta(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO {_META} (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def save(self) -> None:
        """全量落库（兜底接口）。"""
        with self._lock:
            for accounts in self._accounts.values():
                for account in accounts:
                    self._persist_account(account)

    # ── 设置 ─────────────────────────────────────────────────────────────────
    def get_setting(self, key: str, default=None):
        with self._lock:
            return self._settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            self._settings[key] = str(value)
            self._set_meta(key, str(value))

    def admin_key(self) -> str:
        return str(self.get_setting("admin_key", settings.DEFAULT_ADMIN_KEY) or "")

    def quota_refresh_interval(self) -> int:
        try:
            return max(0, int(self.get_setting("quota_refresh_interval", settings.QUOTA_REFRESH_INTERVAL)))
        except (TypeError, ValueError):
            return settings.QUOTA_REFRESH_INTERVAL

    def account_concurrency(self) -> int:
        """单账号并发上限（0 = 不限）。运行时可改（meta 表），改后即生效。"""
        try:
            return max(0, int(self.get_setting("account_concurrency", settings.ACCOUNT_CONCURRENCY)))
        except (TypeError, ValueError):
            return settings.ACCOUNT_CONCURRENCY

    def claim_round_interval(self) -> int:
        """套餐自动领取轮间隔（0 = 关闭）。运行时可改（meta 表），改后即生效。"""
        try:
            return max(0, int(self.get_setting("claim_round_interval", settings.CLAIM_ROUND_INTERVAL)))
        except (TypeError, ValueError):
            return settings.CLAIM_ROUND_INTERVAL

    def _json_setting(self, key: str) -> dict:
        """meta 里的 JSON 值。损坏 / 非对象 / 空一律回落 {} —— 计价表 fail-open 的底座。"""
        raw = self.get_setting(key, "")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def pricing_overrides(self) -> dict:
        """后台人工覆盖的计价表（admin 层，合并优先级最高）。"""
        return self._json_setting("pricing_overrides")

    def set_pricing_overrides(self, table: dict) -> None:
        self.set_setting("pricing_overrides", json.dumps(table, ensure_ascii=False))

    def pricing_pulled(self) -> dict:
        """官方定价页最近一次成功拉取的结果 + 条件 GET 凭证（etag / last-modified）。"""
        return self._json_setting("pricing_pulled")

    def set_pricing_pulled(self, payload: dict) -> None:
        self.set_setting("pricing_pulled", json.dumps(payload, ensure_ascii=False))

    def pricing_pull_status(self) -> dict:
        return self._json_setting("pricing_pull_status")

    def set_pricing_pull_status(self, payload: dict) -> None:
        self.set_setting("pricing_pull_status", json.dumps(payload, ensure_ascii=False))

    def pricing_pull_interval(self) -> int:
        """定价页拉取间隔（0 = 关闭）。运行时可改（meta 表），改后即生效。"""
        try:
            return max(0, int(self.get_setting("pricing_pull_interval", settings.PRICING_PULL_INTERVAL)))
        except (TypeError, ValueError):
            return settings.PRICING_PULL_INTERVAL

    def model_whitelist_mode(self) -> str:
        """模型白名单模式（manual / static / dynamic / hybrid）。非法值回落默认。"""
        mode = str(self.get_setting("model_whitelist_mode", settings.MODEL_WHITELIST_MODE) or "")
        return mode if mode in settings.WHITELIST_MODES else settings.MODEL_WHITELIST_MODE

    def set_model_whitelist_mode(self, mode: str) -> None:
        self.set_setting("model_whitelist_mode", mode)

    def model_whitelist_names_text(self) -> str:
        """后台手填名单的原文（不解析、不改写，界面回填时要和输入一字不差）。"""
        return str(self.get_setting("model_whitelist_names", "") or "")

    def set_model_whitelist_names(self, value: str | list[str]) -> None:
        """存原文：换行分隔。传列表就按列表拼，传字符串保持用户输入的形状。"""
        text = "\n".join(str(v).strip() for v in value) if isinstance(value, list) else str(value or "")
        self.set_setting("model_whitelist_names", text)

    # ── 账号读取 ─────────────────────────────────────────────────────────────
    def list_accounts(self, provider: str | None = None) -> list[Account]:
        with self._lock:
            if provider:
                return list(self._accounts.get(provider, []))
            return [a for p in PROVIDERS for a in self._accounts[p]]

    def find(self, provider: str, id_or_name: str) -> Account | None:
        with self._lock:
            return self._find_locked(provider, id_or_name)

    def find_any(self, id_or_name: str) -> Account | None:
        with self._lock:
            for p in PROVIDERS:
                for a in self._accounts[p]:
                    if a.id == id_or_name:
                        return a
        return None

    def _find_locked(self, provider: str, id_or_name: str) -> Account | None:
        for a in self._accounts.get(provider, []):
            if a.id == id_or_name or a.name == id_or_name:
                return a
        return None

    # ── 账号增删改 ───────────────────────────────────────────────────────────
    def add_account(self, provider: str, name: str, secret: str) -> Account:
        if provider not in PROVIDERS:
            raise ValueError(f"不支持的 provider: {provider}")
        account = Account.create(provider, name, secret)
        with self._lock:
            for a in self._accounts[provider]:
                if a.secret and a.secret == account.secret:
                    return a  # 跳过重复 token
            self._assign_fingerprint(account)  # 入池即分配独立设备指纹
            account.install_id = str(uuid.uuid4())  # 安装身份：稳定安装令牌
            self._accounts[provider].append(account)
            self._persist_account(account)
        return account

    @staticmethod
    def _assign_fingerprint(account: Account) -> None:
        """给账号分配客户端指纹并固化为 dict（随 asdict 落库，取用时还原）。"""
        from .fingerprint import profile_for

        profile = profile_for(account)
        account.fingerprint = {
            "platform": profile.platform, "arch": profile.arch,
            "os_version": profile.os_version, "language": profile.language,
            "timezone": profile.timezone, "screen": profile.screen,
            "device_mid": profile.device_mid,
        }

    def remove_account(self, provider: str, id_or_name: str) -> bool:
        with self._lock:
            items = self._accounts.get(provider, [])
            target = next((a for a in items if a.id == id_or_name or a.name == id_or_name), None)
            if not target:
                return False
            self._accounts[provider] = [a for a in items if a.id != target.id]
            self._delete_account(target.id)
            return True

    def update_account(self, account: Account) -> bool:
        """持久化某个账号的当前状态。

        账号已从内存池删除时拒绝写回，避免后台任务 INSERT OR REPLACE 把已删行救活。
        """
        with self._lock:
            if self._find_locked(account.provider, account.id) is None:
                return False
            self._persist_account(account)
            return True

    def set_enabled(self, provider: str, id_or_name: str, enabled: bool) -> bool:
        with self._lock:
            account = self._find_locked(provider, id_or_name)
            if not account:
                return False
            account.enabled = enabled
            if not enabled:
                account.status = Status.DISABLED
            elif account.status in (Status.DISABLED, Status.COOLING):
                # 人工启用 = 确认恢复：禁用洗回 active；冷却中启用视为跳过等待
                #（风控退避本就是自动恢复语义，人工提前放行同样成立）
                account.status = Status.ACTIVE
                account.cooling_until = None
            self._persist_account(account)
            return True

    # ── 轮询选择 ─────────────────────────────────────────────────────────────
    def select(self, provider: str, skip_ids: set[str] | None = None) -> Account | None:
        """按 round-robin 选择下一个可用账号。用完 / 失效的自动跳过。"""
        skip_ids = skip_ids or set()
        now = time.time()
        with self._lock:
            pool = [
                a for a in self._accounts.get(provider, [])
                if a.is_selectable(now) and a.id not in skip_ids
            ]
            if not pool:
                return None
            idx = self._rotation.get(provider, 0) % len(pool)
            account = pool[idx]
            self._rotation[provider] = (idx + 1) % len(pool)
            return account

    # ── 导入 / 导出 ─────────────────────────────────────────────────────────
    def export(self) -> dict:
        with self._lock:
            return {
                "version": 1,
                "exported_at": time.time(),
                "providers": {
                    p: [
                        {"name": a.name, "mode": a.mode, "secret": a.secret}
                        for a in self._accounts[p]
                    ]
                    for p in PROVIDERS
                },
            }

    def import_accounts(self, payload: dict) -> int:
        providers = payload.get("providers", {})
        count = 0
        for provider, items in providers.items():
            if provider not in PROVIDERS or not isinstance(items, list):
                continue
            for it in items:
                secret = it.get("secret") or it.get("token") or it.get("jwtToken") or it.get("apiKey")
                if not secret:
                    continue
                self.add_account(provider, it.get("name", provider), secret)
                count += 1
        return count


# 单例
store = Store()
