"""ZCode Hub 托盘守护进程（打包专用，不入库业务代码）。

无控制台窗口：服务本体仍由同目录的 ZCodeHub.exe serve 承载，本进程只做
「拉起 + 看护 + 入口」——崩溃隔离、重启即换进程，误关托盘也不会把服务带走。

日志：子进程 stdout/stderr 直接重定向到 data/hub.log（NO_COLOR 由 cli.py 的
非 tty 检测自动关闭着色，文件里不会满是转义码）。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw, ImageFont
from pystray import MenuItem as Item

from app import settings

APP_DIR = Path(sys.executable).resolve().parent
SERVER_EXE = APP_DIR / "ZCodeHub.exe"
LOG_FILE = settings.DATA_DIR / "hub.log"
MAX_LOG_BYTES = 4 * 1024 * 1024
# 反复起不来就别无限重启，停下来让人看日志
FAIL_WINDOW = 180.0
FAIL_LIMIT = 3

RUNNING, STOPPED, FAILED, EXTERNAL = "running", "stopped", "failed", "external"
_DOT = {
    RUNNING: (46, 204, 113),
    STOPPED: (149, 155, 165),
    FAILED: (231, 76, 60),
    EXTERNAL: (52, 152, 219),
}
_TIP = {
    RUNNING: "运行中",
    STOPPED: "已停止",
    FAILED: "启动失败，请查看日志",
    EXTERNAL: "端口已被外部实例占用",
}

_lock = threading.RLock()
_proc: subprocess.Popen | None = None
_log_handle = None
_state = STOPPED
_icon: pystray.Icon | None = None
# 用户主动停止时不该触发自动重启
_user_stopped = False
_fail_times: list[float] = []


def _tray_log(msg: str) -> None:
    """托盘自身的生命周期事件也写进同一个日志文件。

    必须写进 `_log_handle`：那是子进程继承的那个句柄，父子共用同一个文件偏移。
    另开一个句柄写，Windows 下两个句柄各自记偏移，子进程会从自己的偏移继续覆盖
    托盘刚写入的行（首行「已启动服务」就是这么丢的）。
    """
    line = f"[tray {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
        handle = _log_handle
        if handle is not None:
            handle.write(line)
            handle.flush()
            return
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
    except (OSError, ValueError):
        pass


def _rotate_log() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_BYTES:
            old = LOG_FILE.with_suffix(".log.1")
            if old.exists():
                old.unlink()
            LOG_FILE.rename(old)
    except OSError as err:
        _tray_log(f"日志轮转失败: {err}")


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _set_state(state: str) -> None:
    global _state
    with _lock:
        _state = state
        if _icon is not None:
            _icon.icon = _make_image(state)
            _icon.title = f"ZCode Hub :{settings.PORT} · {_TIP[state]}"


def _spawn() -> bool:
    global _proc, _log_handle
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return True
        if not SERVER_EXE.is_file():
            _tray_log(f"找不到 {SERVER_EXE}")
            _set_state(FAILED)
            return False
        _rotate_log()
        _log_handle = open(LOG_FILE, "a", encoding="utf-8", errors="replace")  # noqa: SIM115
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        _proc = subprocess.Popen(  # noqa: S603
            [str(SERVER_EXE), "serve"],
            cwd=str(APP_DIR),
            stdout=_log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            env={**os.environ, "NO_COLOR": "1"},
        )
    _tray_log(f"已启动服务 pid={_proc.pid} 端口={settings.PORT}")
    return True


def _stop(wait_seconds: float = 12.0) -> None:
    global _proc, _log_handle
    with _lock:
        proc, _proc = _proc, None
        handle, _log_handle = _log_handle, None
    if proc is None:
        if handle is not None:
            handle.close()
        return
    proc.terminate()
    try:
        proc.wait(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        _tray_log(f"pid={proc.pid} 未在 {wait_seconds}s 内退出，强制结束")
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    if handle is not None:
        handle.close()
    _tray_log("服务已停止")


# ── 菜单动作 ──────────────────────────────────────────────────────────────────
def _console_url() -> str:
    return f"http://127.0.0.1:{settings.PORT}/admin/login"


def act_open_console(item=None):
    webbrowser.open(_console_url())


def act_start(item=None):
    global _user_stopped
    _user_stopped = False
    _fail_times.clear()
    if _spawn():
        _set_state(RUNNING)


def act_stop(item=None):
    global _user_stopped
    _user_stopped = True
    _stop()
    _set_state(STOPPED)


def act_restart(item=None):
    global _user_stopped
    _user_stopped = False
    _fail_times.clear()
    _stop()
    time.sleep(0.6)
    if _spawn():
        _set_state(RUNNING)


def act_open_log(item=None):
    if not LOG_FILE.exists():
        LOG_FILE.touch()
    os.startfile(str(LOG_FILE))  # noqa: S606


def act_quit(item=None):
    global _user_stopped
    _user_stopped = True
    _tray_log("托盘退出")
    _stop()
    if _icon is not None:
        _icon.visible = False
        _icon.stop()


def _notify(msg: str, title: str = "ZCode Hub") -> None:
    if _icon is not None:
        try:
            _icon.notify(msg, title)
        except Exception:  # noqa: BLE001  通知失败不该拖垮守护
            pass


# ── 看护循环 ──────────────────────────────────────────────────────────────────
def _watch_loop() -> None:
    global _proc, _log_handle, _user_stopped
    # 端口已被占用（例如手工起了 serve / 另一个托盘）：不重复拉起，也不去杀别人
    if port_open(settings.PORT):
        _tray_log(f"端口 {settings.PORT} 上已有实例，托盘进入观察模式")
        _set_state(EXTERNAL)
        _notify(f"检测到端口 {settings.PORT} 已有实例在运行，托盘不会重复启动它。")
        return

    if not _spawn():
        return
    for _ in range(60):
        if port_open(settings.PORT):
            break
        with _lock:
            dead = _proc is not None and _proc.poll() is not None
        if dead:
            break
        time.sleep(0.5)

    with _lock:
        alive = _proc is not None and _proc.poll() is None
    if alive and port_open(settings.PORT):
        _set_state(RUNNING)
        _notify(f"网关已就绪，管理后台 {_console_url()}", "ZCode Hub 运行中")
    else:
        _set_state(FAILED)
        _notify(f"服务未能就绪，请查看日志：{LOG_FILE}", "ZCode Hub 启动失败")

    while True:
        time.sleep(2.0)
        with _lock:
            proc = _proc
        if proc is None:
            continue  # 已停止 / 观察模式：等用户从菜单里操作
        if proc.poll() is None:
            continue  # 还活着
        code = proc.returncode
        with _lock:
            dead_handle, _log_handle = _log_handle, None
            _proc = None
        if dead_handle is not None:
            dead_handle.close()
        _tray_log(f"服务进程退出 rc={code}")
        if _user_stopped:
            _set_state(STOPPED)
            continue

        now = time.monotonic()
        _fail_times[:] = [t for t in _fail_times if now - t < FAIL_WINDOW]
        _fail_times.append(now)
        if len(_fail_times) >= FAIL_LIMIT:
            _set_state(FAILED)
            _tray_log(f"{int(FAIL_WINDOW)}s 内连续 {FAIL_LIMIT} 次启动失败，停止自动重启（原因见上方日志）")
            _notify(f"{int(FAIL_WINDOW)} 秒内连续 {FAIL_LIMIT} 次启动失败，已停止自动重启。日志：{LOG_FILE}",
                    "ZCode Hub 反复启动失败")
            _user_stopped = True
            continue
        _set_state(STOPPED)
        time.sleep(3.0)
        if _user_stopped or port_open(settings.PORT):
            continue
        _tray_log("自动重启服务")
        _spawn()
        _set_state(RUNNING if _proc else STOPPED)


# ── 图标与菜单 ────────────────────────────────────────────────────────────────
def _make_image(state: str) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill=(30, 34, 46, 255))
    try:
        font = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "Z", font=font)
    draw.text(
        ((size - (bbox[2] - bbox[0])) / 2 - bbox[0], (size - (bbox[3] - bbox[1])) / 2 - bbox[1]),
        "Z", font=font, fill=(240, 242, 246, 255),
    )
    dot = _DOT[state]
    draw.ellipse([size - 27, size - 27, size - 7, size - 7],
                 fill=(*dot, 255), outline=(255, 255, 255, 255), width=3)
    return img


def _menu() -> pystray.Menu:
    def running(_):
        with _lock:
            return _proc is not None and _proc.poll() is None

    def idle(_):
        return not running(None) and _state != EXTERNAL

    SEP = pystray.Menu.SEPARATOR
    return pystray.Menu(
        Item("打开管理后台", act_open_console, default=True),
        SEP,
        Item("启动服务", act_start, enabled=idle),
        Item("重启服务", act_restart, enabled=running),
        Item("停止服务", act_stop, enabled=running),
        SEP,
        Item("打开日志", act_open_log),
        SEP,
        Item("退出（同时停止服务）", act_quit),
    )


def main() -> None:
    global _icon
    if sys.platform != "win32":
        raise SystemExit("托盘模式仅支持 Windows；请直接运行 ZCodeHub.exe serve")
    if not SERVER_EXE.is_file():
        raise SystemExit(f"缺少 {SERVER_EXE}")
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    _icon = pystray.Icon("ZCodeHub", _make_image(STOPPED), f"ZCode Hub :{settings.PORT}", _menu())
    threading.Thread(target=_watch_loop, name="hub-watch", daemon=True).start()
    _icon.run()  # 阻塞；双击图标 = 触发 default 菜单项（打开管理后台）


if __name__ == "__main__":
    main()
