# -*- coding: utf-8 -*-
"""系统托盘图标（pystray + Pillow）。

不可用时（没装 pystray / 没有桌面会话）调用方回退到贴边条。
所有菜单回调都在 pystray 自己的线程里跑，必须用 App.post() 抛回 Tk 线程。
"""
from __future__ import annotations

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
    AVAILABLE = True
    IMPORT_ERR = ""
except Exception as exc:          # pragma: no cover
    pystray = None                # type: ignore
    Image = None                  # type: ignore
    AVAILABLE = False
    IMPORT_ERR = f"{type(exc).__name__}: {exc}"

import startup as su

COL_OK = (76, 175, 125, 255)
COL_WARN = (74, 144, 217, 255)
COL_BAD = (224, 82, 82, 255)
COL_TRACK = (58, 62, 70, 255)
COL_BASE = (23, 24, 28, 255)


def _base_icon(size: int = 64):
    """托盘用的小人头像（assets/icon.png）。"""
    try:
        import paths
        p = paths.res_dir() / "assets" / "icon.png"
        if p.exists():
            return Image.open(p).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    return None


def make_image(done: int = 0, total: int = 0, overdue: int = 0, accent: str = ""):
    """托盘图标：小人头像 + 右下角状态点（绿=清空 / 红=有逾期 / 蓝=还有待办）。"""
    if not AVAILABLE:
        return None
    S = 64
    img = _base_icon(S)
    if img is None:
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d0 = ImageDraw.Draw(img)
        d0.ellipse([2, 2, S - 2, S - 2], fill=COL_BASE)
    d = ImageDraw.Draw(img)
    ring = COL_BAD if overdue else (COL_OK if (total and done >= total) else (accent or COL_WARN))
    d.ellipse([42, 42, 63, 63], fill=(20, 22, 26, 255))
    d.ellipse([45, 45, 60, 60], fill=ring)
    return img


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = None
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------- 生命周期
    def start(self) -> bool:
        if not AVAILABLE or self.icon is not None:
            return False
        try:
            menu = pystray.Menu(
                pystray.MenuItem(
                    lambda i: "收起窗口" if self.app.window_visible() else "展开窗口",
                    lambda i: self.app.post(self.app.toggle_window),
                    default=True),
                pystray.MenuItem("今日总览", lambda i: self.app.post(self.app.show_overview)),
                pystray.MenuItem("本周俯瞰", lambda i: self.app.post(self.app.week_dialog)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("暂停提醒 1 小时",
                                 lambda i: self.app.post(lambda: self.app.pause(60))),
                pystray.MenuItem("开机自启动", lambda i: self.app.post(self.app.toggle_startup),
                                 checked=lambda i: su.is_enabled()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出 StudyNudge", lambda i: self.app.post(self.app.quit_app)),
            )
            self.icon = pystray.Icon("StudyNudge", make_image(0, 0, 0), "StudyNudge", menu)
            self._thread = threading.Thread(target=self.icon.run, name="StudyNudgeTray",
                                            daemon=True)
            self._thread.start()
            return True
        except Exception as exc:      # pragma: no cover
            self.icon = None
            print(f"[tray] 启动失败：{exc}")
            return False

    def stop(self) -> None:
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None

    @property
    def running(self) -> bool:
        return self.icon is not None

    # ---------------------------------------------------------- 更新
    def update(self, done: int, total: int, overdue: int, tip: str, accent: str = "") -> None:
        if self.icon is None:
            return
        try:
            self.icon.icon = make_image(done, total, overdue, accent)
            self.icon.title = tip
            self.icon.update_menu()
        except Exception:
            pass

    def notify(self, msg: str, title: str = "StudyNudge") -> None:
        """右下角气泡提示（Win11 可能落在通知中心）。"""
        try:
            if self.icon is not None:
                self.icon.notify(msg, title)
        except Exception:
            pass

    @property
    def visible(self) -> bool:
        try:
            return bool(getattr(self.icon, "visible", False))
        except Exception:
            return False
