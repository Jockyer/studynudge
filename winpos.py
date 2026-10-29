# -*- coding: utf-8 -*-
"""窗口定位：直接用 Win32 物理坐标，绕开 Tk 在 DPI 缩放下的坐标换算问题。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

_u = ctypes.windll.user32
_SPI_GETWORKAREA = 0x0030
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def work_area() -> tuple[int, int, int, int]:
    """主显示器可用区域（不含任务栏），物理像素。"""
    r = _RECT()
    try:
        _u.SystemParametersInfoW(_SPI_GETWORKAREA, 0, ctypes.byref(r), 0)
    except Exception:
        return 0, 0, 1920, 1080
    if r.right <= r.left or r.bottom <= r.top:
        return 0, 0, 1920, 1080
    return r.left, r.top, r.right, r.bottom


def hwnd_of(widget) -> int:
    """取 Tk 窗口的真实顶层 HWND。"""
    try:
        return int(widget.wm_frame(), 16)
    except Exception:
        try:
            return int(widget.winfo_id())
        except Exception:
            return 0


def rect_of(widget) -> tuple[int, int, int, int]:
    h = hwnd_of(widget)
    r = _RECT()
    try:
        _u.GetWindowRect(h, ctypes.byref(r))
    except Exception:
        return 0, 0, 0, 0
    return r.left, r.top, r.right, r.bottom


def move(widget, x: int, y: int) -> None:
    h = hwnd_of(widget)
    try:
        _u.SetWindowPos(h, 0, int(x), int(y), 0, 0,
                        _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE)
    except Exception:
        pass


def place_top_right(widget, margin_x: int = 16, margin_y: int = 44) -> None:
    l, t, r, b = work_area()
    wl, wt, wr, wb = rect_of(widget)
    w, h = wr - wl, wb - wt
    if w < 80 or h < 60:          # 窗口还没映射，尺寸不可信，不动
        return
    move(widget, max(l, r - w - margin_x), t + margin_y)


def center(widget) -> None:
    l, t, r, b = work_area()
    wl, wt, wr, wb = rect_of(widget)
    w, h = wr - wl, wb - wt
    if w <= 0 or h <= 0:
        return
    move(widget, l + (r - l - w) // 2, t + max(20, (b - t - h) // 3))


def inside_work_area(widget, tol: int = 8) -> bool:
    l, t, r, b = work_area()
    wl, wt, wr, wb = rect_of(widget)
    if wr <= wl or wb <= wt:
        return False
    vis_w = min(wr, r) - max(wl, l)
    vis_h = min(wb, b) - max(wt, t)
    return vis_w >= 120 and vis_h >= 60


def clamp(widget) -> None:
    if not inside_work_area(widget):
        place_top_right(widget)


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", wintypes.DWORD)]


def _title_of(hwnd) -> str:
    n = _u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _covers_monitor(hwnd) -> bool:
    """窗口矩形是否盖满它所在的那块屏（全屏游戏/视频的特征）。"""
    r = _RECT()
    if not _u.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    _u.MonitorFromWindow.restype = ctypes.c_void_p
    _u.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    _u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
    mon = _u.MonitorFromWindow(hwnd, 2)          # MONITOR_DEFAULTTONEAREST
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _u.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    w, h = r.right - r.left, r.bottom - r.top
    mw, mh = m.right - m.left, m.bottom - m.top
    return (w >= mw - 2 and h >= mh - 2
            and r.left <= m.left + 2 and r.top <= m.top + 2)


def fullscreen_window(exclude_own: bool = True):
    """前台窗口是否整块屏占满（游戏 / 视频全屏）。返回 (hwnd, 标题) 或 None。"""
    try:
        _u.GetForegroundWindow.restype = ctypes.c_void_p
        h = _u.GetForegroundWindow()
        if not h:
            return None
        if not _u.IsWindowVisible(h):
            return None
        if exclude_own:
            import os
            pid = wintypes.DWORD()
            _u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value == os.getpid():
                return None                       # 自己的窗口不算
        title = _title_of(h)
        if not title or title in ("Program Manager", "Windows 输入体验"):
            return None                           # 桌面 / 输入法候选不算
        if _covers_monitor(h):
            return (int(h), title)
    except Exception:
        return None
    return None