# -*- mode: python ; coding: utf-8 -*-
"""ZCode Hub → Windows exe 冻结配置（onedir，双 exe 共享一份 _internal）。

由 packaging/build.ps1 驱动，不要手工调 pyinstaller：路径、版本号、暂存目录都在脚本里统一。

产出：
- ZCodeHub.exe      控制台版：CLI 子命令 + 网关本体（serve）
- ZCodeHubTray.exe  无控制台版：托盘守护，拉起并看护 ZCodeHub.exe serve

随包数据的取舍：
- app/zcode_system.json  必须进包（body_transform.py 用 Path(__file__).with_name() 读它）。
- frontend/             进两份：安装目录里一份（用户可改），_internal/app/statics 一份兜底
                        —— settings.py 在 exe 同级找不到 frontend/ 时回落到 statics。
- captcha_node/         只随安装目录，不进包：Node 必须从真实磁盘读 node_modules。
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

repo_root = Path(SPECPATH).resolve().parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

datas = [
    (str(repo_root / "app" / "zcode_system.json"), "app"),
    (str(repo_root / "frontend"), "app/statics"),
]
icon = str(Path(SPECPATH) / "ZCodeHub.ico")

# ── 控制台 exe：CLI + 网关本体 ────────────────────────────────────────────────
a = Analysis(
    [str(repo_root / "cli.py")],
    pathex=[str(repo_root)],
    binaries=[],
    datas=datas,
    # uvicorn.run("app.main:app") 是按字符串动态 import，冻结态必须显式收全 app 包
    hiddenimports=["app.main", *collect_submodules("app")],
    hookspath=[],
    runtime_hooks=[],
    # venv 里装了 dev 依赖，显式挡掉，避免测试工具被牵连进产物
    excludes=[
        "pytest", "pytest_asyncio", "pytest_cov", "coverage",
        "mypy", "ruff", "tests", "captcha_node",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ZCodeHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=icon,
)

# ── 无控制台 exe：托盘守护（只依赖 app.settings，不拖进 fastapi 全家桶）────────
t = Analysis(
    [str(Path(SPECPATH) / "tray.py")],
    pathex=[str(repo_root), str(SPECPATH)],
    binaries=[],
    datas=[],
    hiddenimports=["app.settings", "app.constants"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "pytest", "pytest_asyncio", "pytest_cov", "coverage", "mypy", "ruff", "tests",
        "tkinter", "unittest", "pydoc_data",
    ],
    noarchive=False,
)
pyz_t = PYZ(t.pure)
exe_tray = EXE(
    pyz_t,
    t.scripts,
    [],
    exclude_binaries=True,
    name="ZCodeHubTray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)

coll = COLLECT(
    exe, exe_tray,
    a.binaries, a.datas, t.binaries, t.datas,
    strip=False, upx=False, name="ZCodeHub",
)
