"""页面路由：登录、账号管理、设置。

前端文件来自独立部署目录（settings.FRONTEND_DIR，前后端分离）——
改前端只需 rsync 该目录，无需重启后端。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import settings

router = APIRouter()

_TOKEN = "{{APP_VERSION}}"

# 旧的多页入口 → 控制台页签。旧「设置」一页拆成了三处，落在「系统参数」（口令在那儿）；
# 旧「监控」就是现在的「用量与明细」，页签名换了但内容同源。
_LEGACY_TABS = {"accounts": "accounts", "monitoring": "usage", "settings": "system"}


def _html(name: str) -> HTMLResponse:
    path = settings.FRONTEND_DIR / "admin" / name
    if not path.exists():
        raise HTTPException(404, "页面不存在")
    body = path.read_text(encoding="utf-8").replace(_TOKEN, settings.frontend_version())
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


def _legacy_view(tab: str):
    async def view() -> RedirectResponse:
        return RedirectResponse(f"/admin/console#{tab}", status_code=307)

    view.__name__ = f"admin_{tab}"
    return view


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/admin")


@router.get("/admin", include_in_schema=False)
async def admin_root():
    return RedirectResponse("/admin/login")


@router.get("/admin/login", include_in_schema=False)
async def admin_login():
    return _html("login.html")


@router.get("/admin/console", include_in_schema=False)
async def admin_console():
    return _html("console.html")


for _page, _tab in _LEGACY_TABS.items():
    router.add_api_route(
        f"/admin/{_page}", _legacy_view(_tab), methods=["GET"], include_in_schema=False
    )


@router.get("/meta", include_in_schema=False)
async def meta():
    # 角标报的是「你正在看的这套界面」的版本；报 APP_VERSION 会和页面的 ?v= 对不上号。
    return {"version": settings.frontend_version()}
