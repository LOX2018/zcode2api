"""运行活动日志 —— 服务端事件的持久化流水（管理端「活动日志」页数据源）。

和 reqlog 的分工：reqlog 是网关请求的内存环形缓冲（重启清零、只观测流量），
这里记的是「系统做了什么」——启停、账号增删、配置改动、鉴权事件，
落在 SQLite 里所以重启后仍然看得到，否则「服务生命周期」这个分类就没有意义。

写入失败不吞：活动日志和触发它的操作同生共死，宁可让管理端看到 500，
也不要出现「提示成功但没记上」或反过来的假象。
"""

from __future__ import annotations

from .store import store

#: 分类固定这几档，前端圆片与之一一对应；新增分类要同时改两边
KINDS = ("boot", "account", "config", "auth", "error")

KIND_LABELS = {
    "boot": "服务生命周期",
    "account": "账号管理",
    "config": "系统与配置",
    "auth": "鉴权与 Key",
    "error": "报错",
}


def record(kind: str, text: str) -> None:
    """记一条活动。kind 不在字典里时归入 error，避免页面上出现认不出的分类。"""
    if kind not in KIND_LABELS:
        kind = "error"
    store.activity_add(kind, text)
