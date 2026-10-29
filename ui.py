# -*- coding: utf-8 -*-
"""StudyNudge 界面层：主窗口 / 贴边条 / 提醒引擎 / 各类对话框。"""
from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import time
import traceback
import winsound
from datetime import date, datetime, timedelta
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox

import startup as su
import store as st
import tray as tray_mod
import theme as th
import paths
import progress as pr_mod
import winpos as wp

# ---------------------------------------------------------------- 主题
BG = "#17181C"
BG2 = "#1F2127"
BG3 = "#2A2E36"
FG = "#E8E8E8"
DIM = "#8A9099"
ACCENT = "#4A90D9"
DANGER = "#E05252"
OK = "#4CAF7D"
FONT = "Microsoft YaHei UI"
SZ = 1
VERSION = "1.0"                      # 全局字号偏移（用户要求大一号）
WK = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PYW = su.pythonw()
LOG_PATH = paths.data_dir() / "studynudge.log"

def log(msg: str) -> None:
    """写一行日志（data\studynudge.log），失败也不报错。"""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def btn(parent, text, cmd, bg=BG3, fg=FG, size=9, width=None, pad=6):
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, bd=0, relief="flat",
                  activebackground=ACCENT, activeforeground="#FFFFFF",
                  highlightthickness=0, font=(FONT, size), cursor="hand2",
                  padx=pad, pady=3)
    if width:
        b.configure(width=width)
    return b


def _rr(x0, y0, x1, y1, r):
    """圆角矩形的点集（配 smooth=True 用）。"""
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]



class App:
    # ------------------------------------------------------------ 初始化
    def __init__(self, root: tk.Tk, store: st.Store):
        self.root = root
        self.store = store
        self.s = store.settings
        self.view_day: date = st.today()
        self.timer_task: dict | None = None
        self.timer_start: datetime | None = None
        self.flash_job = None
        self.hide_job = None
        self.geo_job = None
        self.pause_until: datetime | None = None
        self.last_remind = datetime.now()
        self._last_top = datetime.now()
        self._today_ref = st.today()
        self.strip: tk.Toplevel | None = None
        self.normal_geo = None
        self._q: queue.Queue = queue.Queue()
        self.tray: tray_mod.Tray | None = None
        self.tray_on = bool(self.s.get("tray", True))
        self._tray_notified = bool(self.s.get("collapsed"))   # 开机自启时不再气泡骚扰
        self._last_sync_chk = datetime.now()
        self._dnd_log = None          # 上次记录“全屏勿扰”日志的时间
        self._q = queue.Queue()
        self._sy = 0.0                # 列表滚动位置（px）
        self._vy = 0.0                # 滚动速度（惯性用）
        self._anim_job = None
        self._target = 0.0
        self._wheel_ts = 0.0

        self._apply_theme(rebuild=False)   # 先上色，再建界面
        self._init_root()
        self._build_main()
        self._paint_bg()
        self._build_strip()
        self._start_tray()
        self._sync_progress(quiet=True)   # 任务来自进度.md
        self.refresh()
        self._place_initial()
        self.root.after(1000, self._tick)
        self.root.after(120, self._drain_queue)
        if self.s.get("startup_panel", True) and not self.s.get("collapsed"):
            self.root.after(400, self.show_overview)
        if self.s.get("collapsed"):
            self.root.after(150, self.on_close)

    def _screen(self) -> tuple[int, int]:
        """逻辑屏幕尺寸（winfo_screenwidth 是物理像素，geometry 是逻辑像素，需换算）。"""
        r = self.root
        try:
            scale = r.winfo_fpixels("1i") / 96.0
        except Exception:
            scale = 1.0
        if not scale or scale <= 0:
            scale = 1.0
        return int(r.winfo_screenwidth() / scale), int(r.winfo_screenheight() / scale)

    def _init_root(self) -> None:
        r = self.root
        r.title("StudyNudge")
        ico = paths.res_dir() / "assets" / "icon.ico"
        if ico.exists():
            try:
                r.iconbitmap(default=str(ico))
            except Exception:
                pass
        png = paths.res_dir() / "assets" / "icon.png"
        if png.exists():
            try:
                self._icon_img = tk.PhotoImage(file=str(png))
                r.iconphoto(True, self._icon_img)      # 任务栏 / 窗口都用小人图标
            except Exception:
                pass
        w, h = (self.s.get("size") or [420, 580])[:2]
        pos = self.s.get("pos")
        if pos:
            r.geometry(f"{int(w)}x{int(h)}+{int(pos[0])}+{int(pos[1])}")
        else:
            r.geometry(f"{int(w)}x{int(h)}")
        r.minsize(380, 320)
        r.configure(bg=BG)
        r.attributes("-topmost", bool(self.s.get("topmost", True)))
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        r.bind("<Configure>", self._on_configure)
        r.bind("<Escape>", lambda e: self.collapse())
        r.bind("<Control-n>", lambda e: self.edit_task(None))
        r.bind("<Control-t>", lambda e: self.toggle_view())
        r.bind_all("<MouseWheel>", self._on_wheel)

    def _place_initial(self) -> None:
        """用 Win32 物理坐标定位；窗口映射前尺寸不准，带重试。"""
        self._placed = False
        self._place_try(24)

    def _place_try(self, n: int) -> None:
        try:
            l, t, r, b = wp.rect_of(self.root)
            if (r - l) >= 80 and (b - t) >= 80:
                if not self.s.get("pos") or not wp.inside_work_area(self.root):
                    wp.place_top_right(self.root)
                self._placed = True
        except Exception:
            pass
        if not self._placed and n > 0:
            self.root.after(80, self._place_try, n - 1)

    # ------------------------------------------------------------ 主窗口
    def _build_main(self) -> None:
        r = self.root
        # 底层画布：背景照片 + 主题色渐变（透明度由设置控制）
        self.bg_canvas = tk.Canvas(r, highlightthickness=0, bd=0, bg=BG)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        try:
            self.root.tk.call("lower", str(self.bg_canvas))   # 压到最底层
        except Exception:
            pass
        if not getattr(self, "_resize_bound", False):
            r.bind("<Configure>", self._on_root_resize, add="+")
            self._resize_bound = True
        # 闪烁条（提醒时变红）
        self.flash_bar = tk.Frame(r, bg=BG, height=4)
        self.flash_bar.pack(fill="x")

        head = self.head = tk.Frame(r, bg=BG2)
        head.pack(fill="x")
        self.lbl_date = tk.Label(head, bg=BG2, fg=FG, font=(FONT, 13 + SZ, "bold"),
                                 anchor="w", cursor="hand2")
        self.lbl_date.pack(side="left", padx=(14, 8), pady=(10, 0))
        self.lbl_date.bind("<Button-1>", lambda e: self.week_dialog())
        self.badge = tk.Canvas(head, width=70, height=28, bg=BG2,
                               highlightthickness=0, bd=0)
        self.badge.pack(side="right", padx=(0, 14), pady=(10, 0))
        self.lbl_overdue = tk.Label(head, bg=BG2, fg=DIM, font=(FONT, 9 + SZ))

        sub = tk.Frame(r, bg=BG2)
        sub.pack(fill="x")
        self.lbl_sum = tk.Label(sub, bg=BG2, fg=DIM, font=(FONT, 8 + SZ), anchor="w")
        self.lbl_sum.pack(side="left", padx=(14, 6), pady=(2, 8))
        self.lbl_timer = tk.Label(sub, bg=BG2, fg=ACCENT, font=(FONT, 8 + SZ, "bold"),
                                  cursor="hand2")
        self.lbl_timer.pack(side="right", padx=(0, 12), pady=(2, 8))
        self.lbl_timer.bind("<Button-1>", lambda e: self.stop_timer())
        self.lbl_sync = tk.Label(sub, bg=BG2, fg=DIM, font=(FONT, 8 + SZ), cursor="hand2")
        self.lbl_sync.pack(side="right", padx=(0, 10), pady=(2, 8))
        self.lbl_sync.bind("<Button-1>", lambda e: self._sync_progress(quiet=False))

        # 提醒横幅
        self.lbl_banner = tk.Label(r, text="", bg=DANGER, fg="#FFFFFF",
                                   font=(FONT, 9 + SZ, "bold"), wraplength=360, justify="left",
                                   anchor="w", padx=10, pady=5, cursor="hand2")
        self.lbl_banner.bind("<Button-1>", lambda e: self._hide_banner())

        # 工具条（顶栏下）：新增 / 明日 / 本周 / 统计
        bar = self.bar = tk.Frame(r, bg=BG2)
        bar.pack(fill="x")
        self.pill(bar, "＋ 新增", lambda: self.edit_task(None), kind="primary").pack(
            side="left", padx=(12, 6), pady=(0, 10))
        self.btn_day = self.pill(bar, "明日", self.toggle_view)
        self.btn_day.pack(side="left", padx=4, pady=(0, 10))
        self.pill(bar, "本周", self.week_dialog).pack(side="left", padx=4, pady=(0, 10))
        self.pill(bar, "统计", self.stats_dialog).pack(side="left", padx=4, pady=(0, 10))

        # 任务列表：自己算滚动（惯性 + 越界回弹），右侧只留一个小胶囊滑块
        wrap = tk.Frame(r, bg=BG)
        wrap.pack(fill="both", expand=True, padx=(6, 0), pady=(4, 0))
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sbar = tk.Canvas(wrap, width=7, bg=BG, highlightthickness=0, bd=0)
        self.sbar.pack(side="right", fill="y", padx=(0, 1))
        self.inner = tk.Frame(self.canvas, bg=BG)
        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self._apply_scroll())
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self.inner_id, width=e.width))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<MouseWheel>", self._on_wheel)
        if not getattr(self, "_wheel_bound", False):   # 重建界面时别重复绑定，否则滚一格滚 N 倍
            self.root.bind("<MouseWheel>", self._on_wheel, add="+")
            self._wheel_bound = True

        # 底栏：只剩 设置 / 收起（右下角）
        foot = self.foot = tk.Frame(r, bg=BG2)
        foot.pack(fill="x", side="bottom")
        self.pill(foot, "收起", self.collapse).pack(side="right", padx=(2, 12), pady=8)
        self.pill(foot, "设置", self.settings_dialog).pack(side="right", padx=4, pady=8)

    def _on_configure(self, ev=None) -> None:
        """窗口移动/缩放后记住位置（防抖 600ms）。"""
        if getattr(self, "_cfg_job", None):
            try:
                self.root.after_cancel(self._cfg_job)
            except Exception:
                pass
        self._cfg_job = self.root.after(600, self._save_geom)

    def _save_geom(self) -> None:
        try:
            self.s["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.s["size"] = [self.root.winfo_width(), self.root.winfo_height()]
            self.store.save()
        except Exception:
            pass

    # ---------------- 小控件：圆角 pill / 徐章 ----------------
    def pill(self, parent, text: str, cmd, kind: str = "ghost"):
        """圆角按钮（Canvas 自绘，带 hover）。kind: primary / ghost"""
        f = tkfont.Font(font=(FONT, 9 + SZ, "bold" if kind == "primary" else "normal"))
        h = 28
        st = {"t": text, "s": "normal"}
        cv = tk.Canvas(parent, height=h, bg=BG2, highlightthickness=0, bd=0, cursor="hand2")

        def draw():
            w = f.measure(st["t"]) + 28
            cv.configure(width=w)
            cv.delete("all")
            if kind == "primary":
                fill = ACCENT if st["s"] == "normal" else th.mix(ACCENT, "#FFFFFF", 0.18)
                fg, outline = "#FFFFFF", ""
            else:
                fill = (th.mix(BG2, BG3, 0.55) if st["s"] == "normal"
                        else th.mix(BG2, BG3, 0.95))
                fg, outline = FG, th.mix(BG2, FG, 0.14)
            cv.create_polygon(_rr(1, 1, w - 1, h - 1, 13), smooth=True,
                              fill=fill, outline=outline)
            cv.create_text(w / 2, h / 2 - 1, text=st["t"], fill=fg, font=f)

        def set_text(s: str) -> None:
            st["t"] = s
            draw()

        cv.bind("<Enter>", lambda e: (st.__setitem__("s", "hover"), draw()))
        cv.bind("<Leave>", lambda e: (st.__setitem__("s", "normal"), draw()))
        cv.bind("<Button-1>", lambda e: cmd())
        cv._set = set_text
        cv._get = lambda: st["t"]
        draw()
        return cv

    def _set_badge(self, text: str, color: str) -> None:
        try:
            c = self.badge
            f = tkfont.Font(font=(FONT, 9 + SZ, "bold"))
            w = f.measure(text) + 24
            c.configure(width=w, height=28)
            c.delete("all")
            c.create_polygon(_rr(1, 2, w - 1, 26, 12), smooth=True,
                             fill=th.mix(BG2, color, 0.22),
                             outline=th.mix(BG2, color, 0.6))
            c.create_text(w / 2, 14, text=text,
                          fill=th.mix(color, "#FFFFFF", 0.35), font=f)
        except Exception:
            pass

    # ---------------- 线程 → UI 队列 / 托盘回调 ----------------
    def post(self, fn) -> None:
        """从别的线程（托盘菜单）安全地投递到主线程。"""
        try:
            self._q.put(fn)
        except Exception:
            pass

    def _drain_queue(self) -> None:
        try:
            while True:
                fn = self._q.get_nowait()
                try:
                    fn()
                except Exception:
                    traceback.print_exc()
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(120, self._drain_queue)
        except Exception:
            pass

    def window_visible(self) -> bool:
        try:
            return self.root.state() != "withdrawn" and bool(self.root.winfo_viewable())
        except Exception:
            return False

    def toggle_window(self) -> None:
        if self.window_visible() and self.root.state() != "iconic":
            self.collapse()
        else:
            self.expand()

    def pause(self, minutes: int = 30) -> None:
        """暂停提醒 N 分钟。"""
        self.last_remind = datetime.now() + timedelta(minutes=minutes)
        self.show_banner(f"提醒已暂停 {minutes} 分钟", strong=False)

    def toggle_startup(self) -> None:
        try:
            if su.is_enabled():
                su.disable()
            else:
                su.enable()
        except Exception:
            pass

    # ---------------- 刷新 ----------------
    def refresh(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        day = self.view_day
        pend = st.pending(day, self.store.tasks)
        done = st.completed(day, self.store.tasks)
        self._row_seq = 0
        self._row_cvs = []
        self._row_total = max(1, len(pend) + len(done))

        # 顶栏
        wd = "一二三四五六日"[day.isoweekday() - 1]
        try:
            self.lbl_date.configure(
                text=f"{day.month}月{day.day}日 周{wd} · W{st.week_no(day)}")
            od = [t for t in pend if st.overdue_days(t) > 0]
            if od:
                self._set_badge(f"逾期 {len(od)} · 待办 {len(pend)}", DANGER)
            elif pend:
                self._set_badge(f"待办 {len(pend)}", ACCENT)
            else:
                self._set_badge("已清空 ✓", OK)
            total_h = sum(float(t.get("est_hours") or 0) for t in pend)
            try:
                tail = " · " + st.module_of(day)
            except Exception:
                tail = ""
            self.lbl_sum.configure(
                text=f"完成 {len(done)}/{len(pend) + len(done)} · 剩 {total_h:g}h{tail}")
            if getattr(self, "btn_day", None) is not None:
                self.btn_day._set("今日" if st.iso(day) != st.iso(st.today()) else "明日")
        except Exception:
            pass

        for t in pend:
            self._row(t, done=False)
        if done:
            tk.Label(self.inner, text=f"已完成 {len(done)} 项 · 点击可撤销",
                     bg=BG, fg=DIM, anchor="w",
                     font=(FONT, 8 + SZ)).pack(fill="x", padx=8, pady=(10, 2))
            for t in done:
                self._row(t, done=True)

        # 今日清空 → 把明天的任务挂出来（学有余力可提前做）
        if not pend and st.iso(day) == st.iso(st.today()):
            tmr = day + timedelta(days=1)
            tp = st.pending(tmr, self.store.tasks)
            if tp:
                tk.Label(self.inner,
                         text=f"明日（{st.iso(tmr)[5:]}）· 可提前做 {len(tp)} 项",
                         bg=BG, fg=OK, anchor="w",
                         font=(FONT, 9 + SZ)).pack(fill="x", padx=8, pady=(10, 2))
                for t in tp:
                    self._row(t, done=False, day=tmr)

        self._refresh_strip()
        self._refresh_tray()
        self.root.after(50, self._repaint_rows)

    def _refresh_strip(self) -> None:
        try:
            if getattr(self, "strip_lbl", None) is None:
                return
            day = st.today()
            pend = st.pending(day, self.store.tasks)
            od = sum(1 for t in pend if st.overdue_days(t) > 0)
            if not pend:
                txt = "今日已清空 ✓"
            elif od:
                txt = f"逾期 {od} · 待办 {len(pend)}"
            else:
                txt = f"待办 {len(pend)}"
            self.strip_lbl.configure(text=txt)
        except Exception:
            pass

    def _refresh_tray(self) -> None:
        try:
            if not getattr(self, "tray", None) or not self.tray_on:
                return
            day = st.today()
            pend = st.pending(day, self.store.tasks)
            done = st.completed(day, self.store.tasks)
            od = sum(1 for t in pend if st.overdue_days(t) > 0)
            tip = f"StudyNudge · 待办 {len(pend)}"
            self.tray.update(len(done), len(done) + len(pend), od, tip,
                             self.s.get("theme_accent") or "")
        except Exception:
            pass

    # ---------------- 收起 / 展开 / 托盘 ----------------
    def collapse(self) -> None:
        """「收起」/ 点 ×：进系统托盘；没有托盘就贴边成小条。"""
        self.s["collapsed"] = True
        self.store.save()
        if self.tray_on and getattr(self, "tray", None):
            self.root.withdraw()
            if not self._tray_notified:
                self._tray_notified = True
                try:
                    self.tray.notify("StudyNudge", "已收到托盘，点图标可再打开")
                except Exception:
                    pass
            return
        self.root.withdraw()
        if getattr(self, "strip", None) is None:
            self._build_strip()
        try:
            self.strip.deiconify()
            self._refresh_strip()
        except Exception:
            pass

    def expand(self) -> None:
        self.s["collapsed"] = False
        self.store.save()
        try:
            if getattr(self, "strip", None) is not None:
                self.strip.withdraw()
        except Exception:
            pass
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass
        self.refresh()

    def hide_to_tray(self) -> None:
        self.collapse()

    def on_close(self) -> None:
        """点 ×：按设置收到托盘或贴边条。"""
        self.collapse()

    def quit_app(self) -> None:
        """退出：先收托盘再销毁窗口。"""
        try:
            if getattr(self, "tray", None):
                self.tray.stop()
        except Exception:
            pass
        try:
            self.store.save()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _start_tray(self) -> None:
        if not self.tray_on or not tray_mod.AVAILABLE:
            return
        try:
            self.tray = tray_mod.Tray(self)
            self.tray.start()
            self._refresh_tray()
        except Exception:
            self.tray = None

    def _build_strip(self) -> None:
        s = tk.Toplevel(self.root)
        s.withdraw()
        s.overrideredirect(True)
        s.attributes("-topmost", True)
        s.geometry("240x34")
        s.configure(bg=DANGER)
        inner = tk.Frame(s, bg=BG2)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.strip_lbl = tk.Label(inner, text="StudyNudge", bg=BG2, fg=FG,
                                  font=(FONT, 9 + SZ, "bold"), padx=8, pady=5, cursor="hand2")
        self.strip_lbl.pack(fill="both", expand=True)
        for w in (s, inner, self.strip_lbl):
            w.bind("<Double-Button-1>", lambda e: self.expand())
            w.bind("<Button-3>", lambda e: self._strip_menu(e))
        self.strip = s

    def _strip_menu(self, ev) -> None:
        m = tk.Menu(self.root, tearoff=0, bg=BG2, fg=FG, activebackground=ACCENT,
                    activeforeground="#FFFFFF", bd=0)
        m.add_command(label="展开", command=self.expand)
        m.add_command(label="今日总览", command=self.show_overview)
        m.add_command(label="暂停提醒 1 小时", command=lambda: self.pause(60))
        m.add_separator()
        m.add_command(label="退出", command=self.quit_app)
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------------ 窗口状态
    # ---------- 列表滚动：目标位置 + 缓动（顺滑加减速）+ 越界回弹 ----------
    OVER = 84                 # 最多越过内容 84px（约 2 个任务格）
    WHEEL_STEP = 96           # 滚轮一格 = 目标位移 96px

    def _max_scroll(self) -> int:
        try:
            return max(0, self.inner.winfo_reqheight() - self.canvas.winfo_height())
        except Exception:
            return 0

    def _on_wheel(self, ev) -> None:
        try:
            if ev.widget.winfo_toplevel() is not self.root:
                return                     # 对话框里的滚轮归对话框
        except Exception:
            pass
        step = -1 if ev.delta > 0 else 1
        self._target += step * self.WHEEL_STEP
        self._wheel_ts = time.time()
        self._start_anim()

    def _start_anim(self) -> None:
        if getattr(self, "_anim_job", None) is None:
            self._anim_job = self.root.after(16, self._anim_tick)

    def _anim_tick(self) -> None:
        self._anim_job = None
        mx = self._max_scroll()
        idle = (time.time() - getattr(self, "_wheel_ts", 0.0)) > 0.10
        if idle:
            # 没有新输入 → 目标回弹到合法范围（越界部分被吃掉）
            if self._target < 0:
                self._target += (0 - self._target) * 0.20
            elif self._target > mx:
                self._target += (mx - self._target) * 0.20
        else:
            self._target = max(-self.OVER, min(mx + self.OVER, self._target))
        diff = self._target - self._sy
        self._sy += diff * 0.24            # 指数缓动：先快后慢，就是加减速的感觉
        if abs(diff) < 0.4:
            self._sy = self._target
        self._apply_scroll()
        moving = abs(self._target - self._sy) > 0.4
        out = self._target < -0.5 or self._target > mx + 0.5
        if moving or out:
            self._anim_job = self.root.after(16, self._anim_tick)
        else:
            self._sy = self._target = min(max(0.0, self._sy), float(mx))
            self._apply_scroll()

    def _apply_scroll(self) -> None:
        try:
            self.canvas.coords(self.inner_id, 0, -self._sy)
            self._draw_thumb()
            self._repaint_rows()
        except Exception:
            pass

    def _draw_thumb(self) -> None:
        """右侧只留一个胶囊滑块，没有滚动条槽。"""
        try:
            self.sbar.delete("all")
            vh = self.canvas.winfo_height()
            ch = self.inner.winfo_reqheight()
            if ch <= vh + 2 or vh < 30:
                return
            h = max(26, int(vh * vh / ch))
            mx = max(1, ch - vh)
            pos = max(0.0, min(1.0, self._sy / mx))
            y = int((vh - h) * pos)
            self.sbar.create_line(3.5, y + 5, 3.5, y + h - 5,
                                  width=4, capstyle="round", fill=ACCENT)
        except Exception:
            pass

    def _repaint_rows(self, only=None) -> None:
        """每帧按卡片当前屏幕位置重贴：底层=背景原图，上层=圆角半透明卡片。"""
        cvs = [only] if only is not None else getattr(self, "_row_cvs", [])
        if not cvs:
            return
        bg = getattr(self, "_bg_pil", None)
        cache = getattr(self, "_tint_cache", None)
        a = getattr(self, "_bg_alpha", 0) or 0
        if not bg or not cache or a <= 0:
            for cv in cvs:
                try:
                    base = getattr(cv, "_base", BG2)
                    if getattr(cv, "_hover", False):
                        base = th.mix(base, "#FFFFFF", 0.08)
                    cv.configure(bg=base)
                    cv.itemconfigure(cv._bg0, image="")
                    cv.itemconfigure(cv._bg1, image="")
                except Exception:
                    pass
            return
        from PIL import Image, ImageDraw, ImageTk
        try:
            ry = self.root.winfo_rooty()
            vh = max(1, self.canvas.winfo_height())
            vy = self.canvas.winfo_rooty() - ry
        except Exception:
            return
        masks = self.__dict__.setdefault("_mask_cache", {})
        for cv in cvs:
            try:
                if not cv.winfo_exists():
                    continue
                y = cv.winfo_rooty() - ry
                hh = cv.winfo_height() or int(cv.cget("height"))
                if y > vy + vh + 40 or y + hh < vy - 40:
                    continue
                base = getattr(cv, "_base", BG2)
                hov = bool(getattr(cv, "_hover", False))
                srcs = cache
                if hov:
                    srcs = getattr(self, "_tint_cache_hl", None) or cache
                src = srcs.get(base) or srcs.get(BG2)
                ww = cv.winfo_width()
                if ww <= 4:
                    ww = max(120, (self.canvas.winfo_width() or 360) - 12)
                y0 = max(0, min(src.height - 1, int(y)))
                y1 = min(src.height, y0 + hh)
                key = (y0, hh, ww, hov)
                if getattr(cv, "_bg_key", None) == key:
                    continue
                under = bg.crop((0, y0, min(ww, bg.width), y1))
                if under.size != (ww, hh):
                    under = under.resize((ww, hh))
                ph0 = ImageTk.PhotoImage(under)
                cv._ph0 = ph0
                cv.itemconfigure(cv._bg0, image=ph0)

                card = src.crop((0, y0, min(ww, src.width), y1))
                if card.size != (ww, hh):
                    card = card.resize((ww, hh))
                mask = masks.get((ww, hh))
                if mask is None:
                    mask = Image.new("L", (ww, hh), 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        [0, 0, ww - 1, hh - 1], radius=10, fill=255)
                    masks[(ww, hh)] = mask
                card = card.convert("RGBA")
                card.putalpha(mask)
                ph1 = ImageTk.PhotoImage(card)
                cv._ph = ph1
                cv._bg_key = key
                cv.itemconfigure(cv._bg1, image=ph1)
                cv.configure(bg=th.mix(BG2, "#000000", 0.15))
            except Exception:
                pass
        self._tint_bars()

    def _tint_bars(self) -> None:
        """顶栏/工具条/底栏保持整块同色（不按位置取色，避免上下拆成几段）。"""
        for w in (getattr(self, "head", None), getattr(self, "bar", None),
                  getattr(self, "foot", None)):
            if w is None:
                continue
            try:
                w.configure(bg=BG2)
            except Exception:
                pass

    def _row(self, t: dict, done: bool, day=None) -> None:
        """一行 = 一块 Canvas：底层贴该区域背景，上层贴圆角半透明卡片。"""
        day = day or self.view_day
        name, color = st.LINES.get(t.get("line"), st.LINES["other"])
        step = int(t.get("step") or 0)
        od = st.overdue_days(t)
        deferred = (not done) and st.is_deferred(t, day)
        weekly = t.get("kind") == "weekly"
        if deferred:
            base, edge = "#3A2A22", DANGER
        elif step > 0:
            base, edge = BG2, "#5B9BD5"
        elif weekly:
            base, edge = BG2, "#6FA36F"
        else:
            base, edge = BG2, th.mix(BG2, FG, 0.16)

        title = self._title_for(t, day)
        bits = [name]
        if od:
            bits.append(f"逾期 {od} 天")
        if deferred:
            bits.append("延期")
        if step > 0:
            bits.append(f"推进 +{step}")
        if t.get("deadline"):
            bits.append(f"截止 {str(t['deadline'])[5:]}")
        bits.append(f"{float(t.get('est_hours') or 0):g}h")
        if weekly:
            wd = [d for d in (t.get("weekdays") or []) if 1 <= d <= 7]
            bits.append("每周 " + "".join("一二三四五六日"[d - 1] for d in wd))
        else:
            bits.append("每日")
        if int(t.get("defer_count") or 0) > 0:
            bits.append(f"延期 {t['defer_count']} 次")
        if float(t.get("spent_hours") or 0) > 0:
            bits.append(f"实际 {float(t['spent_hours']):g}h")
        meta = "  ·  ".join(bits)

        ft = tkfont.Font(font=(FONT, 11 + SZ, "overstrike" if done else "normal"))
        fm = tkfont.Font(font=(FONT, 8 + SZ))
        W = max(240, (self.canvas.winfo_width() or 380) - 20)
        tw = max(90, W - 58 - 140)
        n = max(1, min(3, -(-ft.measure(title) // tw)))
        mn = max(1, min(2, -(-fm.measure(meta) // tw)))
        th_ = n * ft.metrics("linespace")
        H = 16 + th_ + 4 + mn * fm.metrics("linespace") + 10

        cv = tk.Canvas(self.inner, height=H, bg=BG2, highlightthickness=0, bd=0, takefocus=0)
        cv.pack(fill="x", pady=5, padx=(6, 10))
        cv._base, cv._edge, cv._deferred = base, edge, deferred
        self._row_cvs.append(cv)

        cv._bg0 = cv.create_image(0, 0, anchor="nw", tags="bgbg")
        cv._bg1 = cv.create_image(0, 0, anchor="nw", tags="card")
        cv.create_polygon(_rr(1, 1, W - 1, H - 1, 10), smooth=True, fill="",
                          outline=edge, width=1)
        cv.create_line(12, 12, 12, H - 12, fill=color, width=3, capstyle="round")

        cx, cy = 32, H // 2
        cv._cb = cv.create_oval(cx - 8, cy - 8, cx + 8, cy + 8,
                                outline=(OK if done else (DANGER if deferred
                                                          else th.mix(FG, BG2, 0.5))),
                                fill=(OK if done else ""), width=1)
        if done:
            cv.create_line(cx - 4, cy, cx - 1, cy + 4, cx + 5, cy - 4,
                           fill="#FFFFFF", width=2)
        ti = cv.create_text(54, 13, anchor="nw", text=title, width=tw,
                            fill=(th.mix(FG, BG2, 0.45) if done else FG),
                            font=ft, justify="left")
        cv.create_text(54, 13 + th_ + 5, anchor="nw", text=meta, width=tw,
                       fill=(DANGER if (od or deferred) else th.mix(FG, BG2, 0.42)),
                       font=fm, justify="left")

        def chip(x, cy, label, fg, cmd, size=15):
            rect = cv.create_polygon(_rr(x - size, cy - size, x + size, cy + size, 9),
                                     smooth=True, fill=th.mix(BG2, fg, 0.16), outline="")
            txt = cv.create_text(x, cy - 1, text=label, fill=fg,
                                 font=(FONT, 10 + SZ, "bold"))
            def _on(_e, _r=rect):
                try:
                    cv.itemconfigure(_r, fill=th.mix(BG2, fg, 0.34))
                except Exception:
                    pass
            def _off(_e, _r=rect):
                try:
                    cv.itemconfigure(_r, fill=th.mix(BG2, fg, 0.16))
                except Exception:
                    pass
            for i in (rect, txt):
                cv.tag_bind(i, "<Button-1>", lambda e: cmd())
                cv.tag_bind(i, "<Enter>", _on)
                cv.tag_bind(i, "<Leave>", _off)

        cy = H // 2
        if not done:
            chip(W - 24, cy, "✎", th.mix(FG, BG2, 0.25), lambda: self.edit_task(t))
            if self._is_timing(t):
                chip(W - 62, cy, "■", DANGER, lambda: self.toggle_timer(t))
            else:
                chip(W - 62, cy, "▶", th.mix(FG, BG2, 0.25), lambda: self.toggle_timer(t))
            chip(W - 100, cy, "延", "#E8A33D", lambda: self.defer(t))
        else:
            chip(W - 24, cy, "✎", th.mix(FG, BG2, 0.35), lambda: self.edit_task(t))

        for i in (cv._cb, ti):
            cv.tag_bind(i, "<Button-1>", lambda e, tt=t: self.toggle(tt))

        def on_enter(_e):
            cv._hover = True
            cv._bg_key = None
            self._repaint_rows(only=cv)

        def on_leave(_e):
            cv._hover = False
            cv._bg_key = None
            self._repaint_rows(only=cv)

        cv.bind("<Enter>", on_enter)
        cv.bind("<Leave>", on_leave)

    def _title_for(self, t: dict, day) -> str:
        """明日视图显示「下一条」：推进型按 step 往后推一段（17–20 → 21–24）。"""
        if st.iso(day) > st.iso(st.today()):
            if t.get("next_title"):
                return t["next_title"]
            step = int(t.get("step") or 0)
            if step > 0:
                return st.bump_title(t["title"], step)
        return t["title"]

    # ------------------------------------------------------------ 任务操作
    def toggle(self, t: dict) -> None:
        st.set_done(t, self.view_day, not st.completed_on(t, self.view_day))
        self.store.save()
        pr_mod.apply_advance(self.store, path=self.s.get("progress_md"))   # 补勾过去的日子 → 可能触发课次推进
        pr_mod.flush_dirty(self.store, path=self.s.get("progress_md"))      # 取消完成时把标题退回文档
        self.refresh()

    def defer(self, t: dict) -> None:
        st.defer_task(t, 1, self.view_day)
        self.store.save()
        self.refresh()
        n = int(t.get("defer_count") or 0)
        if n >= 3:
            self.show_banner(f"「{t['title'][:14]}」已延期 {n} 次，考虑拆分或砍掉", strong=True)
        else:
            self.show_banner(f"「{t['title'][:14]}」延到明天，明天置顶提醒")

    def toggle_view(self) -> None:
        """今日 ↔ 明日；按钮文字随当前视图变（明日 / 今日）。"""
        tdy = st.today()
        self.view_day = tdy + timedelta(days=1) if self.view_day == tdy else tdy
        self.refresh()

    # -- 计时
    def _is_timing(self, t: dict) -> bool:
        return bool(self.timer_task and self.timer_task.get("id") == t.get("id"))

    def toggle_timer(self, t: dict) -> None:
        if self._is_timing(t):
            self.stop_timer()
        else:
            self.stop_timer()
            self.timer_task = t
            self.timer_start = datetime.now()
            self.refresh()

    def stop_timer(self) -> None:
        if not self.timer_task or not self.timer_start:
            return
        hours = (datetime.now() - self.timer_start).total_seconds() / 3600.0
        tid = self.timer_task.get("id")
        self.timer_task = None
        self.timer_start = None
        self.lbl_timer.configure(text="")
        if hours >= 1 / 60:
            self.store.add_spent(tid, hours)
        self.refresh()

    def _update_timer_label(self) -> None:
        if not self.timer_start:
            return
        sec = int((datetime.now() - self.timer_start).total_seconds())
        self.lbl_timer.configure(text=f"计时 {sec // 60:02d}:{sec % 60:02d} ■")

    # ------------------------------------------------------------ 提醒引擎
    def _in_quiet(self, now: datetime) -> bool:
        try:
            qs, qe = (self.s.get("quiet_hours") or ["23:30", "07:00"])[:2]
            t1 = datetime.strptime(qs, "%H:%M").time()
            t2 = datetime.strptime(qe, "%H:%M").time()
        except Exception:
            return False
        t = now.time()
        return (t >= t1 or t <= t2) if t1 > t2 else (t1 <= t <= t2)

    def _tick(self) -> None:
        try:
            self._rollover()
            self._update_timer_label()
            self._keep_topmost()
            self._maybe_sync()
            self._check_remind()
        except Exception:
            traceback.print_exc()
        finally:
            self.root.after(1000, self._tick)

    # ------------------------------------------------------------ 进度.md 联动
    def _sync_progress(self, quiet: bool = True) -> None:
        """按进度.md 重建任务清单；quiet=False 时给出可见反馈。"""
        try:
            pr_mod.ensure_file(self.s.get("progress_md"))   # 首次运行生成任务列表模板
            rep = pr_mod.sync(self.store, self.s.get("progress_md"))
            pr_mod.apply_advance(self.store, path=self.s.get("progress_md"))    # 昨天勾完的课次今天生效
            pr_mod.flush_dirty(self.store, path=self.s.get("progress_md"))
        except Exception:
            traceback.print_exc()
            return
        if not rep.get("ok"):
            if not quiet:
                self.show_banner("进度同步失败：" + (rep.get("msg") or "?"), strong=True)
        elif not quiet:
            self.show_banner(f"已按进度.md 同步：{rep['total']} 条任务"
                             + (f"（新增 {rep['added']} / 更新 {rep['updated']}）"
                                if rep.get("added") or rep.get("updated") else ""))
        self._check_stale()
        try:
            self.refresh()
        except Exception:
            pass

    def _pick_task_file(self, entry) -> None:
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择任务列表文件",
            filetypes=[("Markdown / 文本", "*.md *.txt"), ("全部文件", "*.*")])
        if not p:
            return
        try:
            entry.delete(0, "end")
            entry.insert(0, p)
        except Exception:
            pass
        self._sync_from(p)

    def _sync_from(self, path: str) -> None:
        """设置里改了进度文件路径，保存并立即同步。"""
        p = (path or "").strip()
        if p:
            self.s["progress_md"] = p
            self.store.save()
        self._sync_progress(quiet=False)

    def _maybe_sync(self) -> None:
        """进度.md 变了就自动重建（每分钟最多看一次文件时间戳）。"""
        now = datetime.now()
        if (now - self._last_sync_chk).total_seconds() < 60:
            return
        self._last_sync_chk = now
        try:
            if pr_mod.changed_on_disk(self.store, self.s.get("progress_md")):
                self._sync_progress(quiet=True)
        except Exception:
            traceback.print_exc()

    def _check_stale(self) -> None:
        txt, col = pr_mod.status_text(self.store)
        try:
            self.lbl_sync.configure(text="↻ " + txt,
                                    fg={"ok": OK, "dim": DIM, "warn": DANGER}.get(col, DIM))
        except Exception:
            return
        parsed = pr_mod.parse_file(self.s.get("progress_md") or "")
        n = pr_mod.stale_days(parsed)
        tdy = st.iso(st.today())
        if n >= 5 and self.s.get("stale_warned") != tdy:
            self.s["stale_warned"] = tdy
            try:
                self.store.save()
            except Exception:
                pass
            self.show_banner(f"进度.md 已 {n} 天没更新，任务可能已过期——改完点 ↻ 同步",
                             strong=True)

    def _rollover(self) -> None:
        """跨过零点自动切到新的一天，否则后台挂着会一直显示昨天。"""
        now_day = st.today()
        if now_day == self._today_ref:
            return
        was_today = self.view_day == self._today_ref
        self._today_ref = now_day
        if was_today:
            self.view_day = now_day
        self.last_remind = datetime.now()
        self._sync_progress(quiet=True)
        self.refresh()

    def _keep_topmost(self) -> None:
        """其他置顶窗口（浏览器全屏等）会把我们压下去，定期重申一次置顶。"""
        now = datetime.now()
        if (now - self._last_top).total_seconds() < 60:
            return
        self._last_top = now
        if self.s.get("topmost", True) and self.root.winfo_viewable():
            try:
                self.root.attributes("-topmost", True)
            except Exception:
                pass

    def _check_remind(self) -> None:
        now = datetime.now()
        if self.pause_until and now < self.pause_until:
            return
        interval = int(self.s.get("remind_interval_min") or 30)
        if (now - self.last_remind).total_seconds() < interval * 60:
            return
        pend = st.pending(st.today(), self.store.tasks)
        if not pend:
            self.last_remind = now
            return
        if self.remind(pend) is False:     # 全屏勿扰拦下了：不计入间隔，退出全屏后马上补提醒
            return
        self.last_remind = now

    def remind(self, pend: list[dict]) -> bool:
        """弹提醒。返回 False = 因为全屏勿扰被跳过（不计入提醒间隔）。"""
        if self.s.get("dnd_fullscreen", True):
            fs = wp.fullscreen_window()
            if fs:
                now = datetime.now()
                if not self._dnd_log or (now - self._dnd_log).total_seconds() > 600:
                    self._dnd_log = now
                    log(f"全屏勿扰：{fs[1][:40]} 全屏中，本次提醒跳过")
                return False
        od = [t for t in pend if st.overdue_days(t) > 0]
        msg = f"还有 {len(pend)} 项没做完"
        if od:
            msg += f"，其中 {len(od)} 项已逾期"
            oldest = max(od, key=st.overdue_days)
            msg += f"（{oldest['title']} 逾期 {st.overdue_days(oldest)} 天）"
        msg += " —— 点这里关掉"
        if self.root.state() == "withdrawn":
            self.expand()
        else:
            self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.show_banner(msg, strong=bool(od))
        self._flash(8)
        if self.s.get("sound_on", True):
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            except Exception:
                pass
        if not od:
            self._schedule_autohide(60)
        self._refresh_strip()
        return True

    def _flash(self, steps: int = 8) -> None:
        if self.flash_job:
            try:
                self.root.after_cancel(self.flash_job)
            except Exception:
                pass
        self._flash_step(steps)

    def _flash_step(self, n: int) -> None:
        if n <= 0:
            self.flash_bar.configure(bg=BG)
            self.flash_job = None
            return
        self.flash_bar.configure(bg=DANGER if n % 2 == 0 else BG)
        self.flash_job = self.root.after(300, self._flash_step, n - 1)

    def show_banner(self, text: str, strong: bool = False) -> None:
        self.lbl_banner.configure(text=text, bg=(DANGER if strong else ACCENT))
        self.lbl_banner.pack(fill="x", after=self.head)
        if self.hide_job:
            try:
                self.root.after_cancel(self.hide_job)
            except Exception:
                pass
            self.hide_job = None

    def _hide_banner(self) -> None:
        if self.hide_job:
            try:
                self.root.after_cancel(self.hide_job)
            except Exception:
                pass
            self.hide_job = None
        try:
            self.lbl_banner.pack_forget()
        except Exception:
            pass

    def _schedule_autohide(self, sec: int) -> None:
        if self.hide_job:
            try:
                self.root.after_cancel(self.hide_job)
            except Exception:
                pass
        self.hide_job = self.root.after(sec * 1000, self._hide_banner)

    def pause(self, minutes: int) -> None:
        self.pause_until = datetime.now() + timedelta(minutes=minutes)
        self.show_banner(f"提醒已暂停 {minutes} 分钟", strong=False)
        self._schedule_autohide(8)

    # ------------------------------------------------------------ 对话框
    def _dlg(self, title: str, w: int, h: int) -> tk.Toplevel:
        d = tk.Toplevel(self.root)
        d.title(title)
        d.configure(bg=BG)
        d.attributes("-topmost", True)
        d.resizable(True, True)
        d.geometry(f"{w}x{h}")
        d.minsize(300, 200)
        d.update_idletasks()
        d.update()
        wp.center(d)
        # 内容排完后按“实际需要的大小”自适应（不超过屏幕）
        d.after(40, lambda: self._fit_dialog(d, w, h))
        return d

    def _fit_dialog(self, d, w0: int, h0: int) -> None:
        try:
            if not d.winfo_exists():
                return
            d.update_idletasks()
            need_w = max(w0, d.winfo_reqwidth())
            need_h = max(h0, d.winfo_reqheight())
            sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
            need_w = min(need_w, int(sw * 0.92))
            need_h = min(need_h, int(sh * 0.86))
            x = max(0, (sw - need_w) // 2)
            y = max(0, (sh - need_h) // 3)
            d.geometry(f"{need_w}x{need_h}+{x}+{y}")
        except Exception:
            pass

    def _lab(self, parent, text, r=0, c=0, span=1, dim=True):
        lb = tk.Label(parent, text=text, bg=BG, fg=(DIM if dim else FG), font=(FONT, 9 + SZ), anchor="w")
        lb.grid(row=r, column=c, columnspan=span, sticky="w", padx=(0, 8), pady=3)
        return lb

    def edit_task(self, task: dict | None = None) -> None:
        d = self._dlg("编辑任务" if task else "新增任务", 420, 370)
        pad = tk.Frame(d, bg=BG, padx=14, pady=12)
        pad.pack(fill="both", expand=True)
        names = {k: v[0] for k, v in st.LINES.items()}
        keys = {v: k for k, v in names.items()}

        e_title = tk.Entry(pad, bg=BG3, fg=FG, insertbackground=FG, bd=0,
                           font=(FONT, 10 + SZ), highlightthickness=0)
        v_line = tk.StringVar(value=names.get((task or {}).get("line", "other"), "其他"))
        v_kind = tk.StringVar(value=(task or {}).get("kind", "once"))
        e_date = tk.Entry(pad, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                          highlightthickness=0)
        e_hours = tk.Entry(pad, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                           highlightthickness=0)
        e_note = tk.Entry(pad, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                          highlightthickness=0)

        self._lab(pad, "任务标题", 0)
        e_title.grid(row=0, column=1, sticky="ew", pady=3, ipady=4)
        self._lab(pad, "学科线", 1)
        om = tk.OptionMenu(pad, v_line, *names.values())
        om.configure(bg=BG3, fg=FG, bd=0, highlightthickness=0, activebackground=ACCENT,
                     font=(FONT, 9 + SZ), width=10)
        om["menu"].configure(bg=BG3, fg=FG, activebackground=ACCENT, font=(FONT, 9 + SZ))
        om.grid(row=1, column=1, sticky="w", pady=3)
        self._lab(pad, "类型", 2)
        kf = tk.Frame(pad, bg=BG)
        kf.grid(row=2, column=1, sticky="w", pady=3)
        for val, txt in (("once", "一次性"), ("daily", "每日"), ("weekly", "每周")):
            tk.Radiobutton(kf, text=txt, value=val, variable=v_kind, bg=BG, fg=FG,
                           selectcolor=BG3, activebackground=BG, activeforeground=ACCENT,
                           font=(FONT, 9 + SZ), bd=0, highlightthickness=0).pack(side="left", padx=(0, 10))
        self._lab(pad, "执行日", 3)
        e_date.grid(row=3, column=1, sticky="ew", pady=3, ipady=4)
        self._lab(pad, "星期(每周)", 4)
        wf = tk.Frame(pad, bg=BG)
        wf.grid(row=4, column=1, sticky="w", pady=3)
        wvars = []
        for i in range(7):
            v = tk.IntVar(value=1 if (i + 1) in ((task or {}).get("weekdays") or []) else 0)
            wvars.append(v)
            tk.Checkbutton(wf, text="一二三四五六日"[i], variable=v, bg=BG, fg=FG, selectcolor=BG3,
                           activebackground=BG, bd=0, highlightthickness=0, padx=1,
                           font=(FONT, 8 + SZ)).pack(side="left", padx=1)
        self._lab(pad, "预估小时", 5)
        e_hours.grid(row=5, column=1, sticky="ew", pady=3, ipady=4)
        self._lab(pad, "备注", 6)
        e_note.grid(row=6, column=1, sticky="ew", pady=3, ipady=4)
        pad.columnconfigure(1, weight=1)

        e_title.insert(0, (task or {}).get("title", ""))
        e_date.insert(0, (task or {}).get("date") or self.view_day.isoformat())
        e_hours.insert(0, str((task or {}).get("est_hours") or ""))
        e_note.insert(0, (task or {}).get("note", ""))
        tk.Label(pad, text="日期格式 YYYY-MM-DD；每周任务从执行日起生效", bg=BG, fg=DIM,
                 font=(FONT, 8 + SZ), anchor="w").grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def save():
            title = e_title.get().strip()
            if not title:
                messagebox.showwarning("缺少标题", "任务标题不能为空", parent=d)
                return
            try:
                hours = float(e_hours.get().strip() or 0)
            except Exception:
                messagebox.showwarning("小时数", "预估小时请填数字，例如 1.5", parent=d)
                return
            day = st.parse_date(e_date.get().strip(), self.view_day)
            wd = [i + 1 for i, v in enumerate(wvars) if v.get()]
            kind = v_kind.get()
            if kind == "weekly" and not wd:
                messagebox.showwarning("星期", "每周任务至少要勾一个星期", parent=d)
                return
            if task is None:
                self.store.add(st.new_task(title, keys.get(v_line.get(), "other"), kind,
                                           day, wd, hours, e_note.get()))
            else:
                task.update({"title": title, "line": keys.get(v_line.get(), "other"),
                             "kind": kind, "date": st.iso(day), "weekdays": wd,
                             "est_hours": hours, "note": e_note.get()})
                self.store.touch(task)
            d.destroy()
            self.refresh()

        def delete():
            if task and messagebox.askyesno("删除任务", f"删除「{task['title']}」？", parent=d):
                self.store.remove(task["id"])
                d.destroy()
                self.refresh()

        bf = tk.Frame(pad, bg=BG)
        bf.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        btn(bf, "保存", save, bg=ACCENT, fg="#FFFFFF", width=8).pack(side="right", padx=(6, 0))
        btn(bf, "取消", d.destroy, width=6).pack(side="right")
        if task:
            btn(bf, "删除", delete, bg="#3A2226", fg=DANGER, width=6).pack(side="left")
        e_title.focus_set()
        d.bind("<Return>", lambda e: save())
        d.bind("<Escape>", lambda e: d.destroy())

    # ---------------- 外观 / 主题 ----------------
    def _tint_rows(self) -> None:
        # 兼容旧调用：现在统一走 _repaint_rows（每帧按屏幕位置重裁背景）
        self._repaint_rows()

    def _on_root_resize(self, _e=None) -> None:
        w, h = self.root.winfo_width(), self.root.winfo_height()
        if (w, h) == getattr(self, "_last_wh", None):
            return                      # 尺寸没变就不重绘，避免“刷新→触发→再刷新”的循环
        self._last_wh = (w, h)
        if getattr(self, "_bg_job", None):
            try:
                self.root.after_cancel(self._bg_job)
            except Exception:
                pass
        self._bg_job = self.root.after(350, lambda: (self._paint_bg(), self.refresh()))

    def _apply_theme(self, rebuild: bool = True) -> None:
        accent = self.s.get("theme_accent") or th.DEFAULT_ACCENT
        photo = self.s.get("bg_photo") or ""
        try:
            alpha = max(0.0, min(1.0, float(self.s.get("bg_alpha", 0.0) or 0.0)))
        except Exception:
            alpha = 0.0
        tint = None
        if photo and os.path.exists(photo) and alpha > 0:
            tint = th.average_color(photo)
        globals().update(th.palette(accent, tint, alpha))
        if rebuild:
            self._rebuild_ui()

    def _rebuild_ui(self) -> None:
        try:
            for w in list(self.root.winfo_children()):
                if isinstance(w, tk.Toplevel):
                    continue                      # 不动已打开的对话框
                w.destroy()
        except Exception:
            pass
        self._row_cvs = []
        self._sy = 0.0                # 重建后回到顶部，否则新列表会整体上移、顶上留白
        self._target = 0.0
        self._vy = 0.0
        self._build_main()
        self._last_wh = None          # 重建后尺寸判据作废，下一次 Configure 会重绘背景
        self._mask_cache = {}
        self.root.after(60, self._paint_bg)   # 等布局完成再合成（否则拿到 1x1 尺寸）
        try:
            self.refresh()
        except Exception:
            pass

    def _paint_bg(self) -> None:
        """底层画一张「背景照片 + 主题色渐变」，强度由 bg_alpha 控制。"""
        cv = getattr(self, "bg_canvas", None)
        if cv is None:
            return
        try:
            cv.configure(bg=BG)
            cv.delete("all")
        except Exception:
            return
        photo = self.s.get("bg_photo") or ""
        try:
            alpha = max(0.0, min(1.0, float(self.s.get("bg_alpha", 0.0) or 0.0)))
        except Exception:
            alpha = 0.0
        if not (photo and os.path.exists(photo) and alpha > 0):
            self._bg_pil = None
            self._tint_cache = None
            for c in (cv, getattr(self, "canvas", None)):
                try:
                    c.delete("bgimg")
                except Exception:
                    pass
            return
        try:
            from PIL import ImageTk
            w = max(240, self.root.winfo_width())
            h = max(240, self.root.winfo_height())
            accent = self.s.get("theme_accent") or th.DEFAULT_ACCENT
            img = th.compose_bg(photo, (w, h), accent, alpha)
            self._bg_pil = img
            self._bg_alpha = alpha
            self._tint_cache = {b: th.blend_over(img, b, alpha)
                                for b in (BG2, "#3A2A22")}
            from PIL import ImageEnhance
            self._tint_cache_hl = {
                b: ImageEnhance.Brightness(v).enhance(1.14)
                for b, v in self._tint_cache.items()}
            self._bg_img = ImageTk.PhotoImage(img)
            cv.create_image(0, 0, anchor="nw", image=self._bg_img, tags="bgimg")
            # 列表滚动区也铺一层：行间空隙 / 下方空白处能看到照片
            c2 = getattr(self, "canvas", None)
            if c2 is not None:
                cw = max(200, c2.winfo_width() or w)
                ch = max(200, c2.winfo_height() or h)
                self._bg_img2 = ImageTk.PhotoImage(th.compose_bg(photo, (cw, ch), accent, alpha))
                c2.delete("bgimg")
                c2.create_image(0, 0, anchor="nw", image=self._bg_img2, tags="bgimg")
                c2.tag_lower("bgimg")
        except Exception:
            self._bg_img = None
        try:
            self._repaint_rows()      # 背景换了尺寸/颜色后，卡片跟着重贴
        except Exception:
            pass

    def _set_accent(self, hexv: str) -> None:
        self.s["theme_accent"] = (hexv or "").upper()
        self.store.save()
        self._apply_theme()
        try:
            self._refresh_tray()
        except Exception:
            pass

    def _pick_accent(self) -> None:
        from tkinter import colorchooser
        _rgb, hexv = colorchooser.askcolor(
            color=self.s.get("theme_accent") or th.DEFAULT_ACCENT, title="选主题色")
        if hexv:
            self._set_accent(hexv)

    def _pick_photo(self) -> None:
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title="选择背景照片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.webp"), ("全部文件", "*.*")])
        if not p:
            return
        self.s["bg_photo"] = p
        if float(self.s.get("bg_alpha", 0) or 0) <= 0:
            self.s["bg_alpha"] = 0.55
        self.store.save()
        try:
            self.lbl_photo.configure(text=p)
            self.v_alpha.set(int(round(float(self.s["bg_alpha"]) * 100)))
        except Exception:
            pass
        self._apply_theme()

    def _clear_photo(self) -> None:
        self.s["bg_photo"] = ""
        self.store.save()
        try:
            self.lbl_photo.configure(text="（未选择）")
        except Exception:
            pass
        self._apply_theme()

    def _set_alpha(self, a: float) -> None:
        self.s["bg_alpha"] = round(float(a), 3)
        self.store.save()
        if getattr(self, "_alpha_job", None):
            try:
                self.root.after_cancel(self._alpha_job)
            except Exception:
                pass
        self._alpha_job = self.root.after(300, self._apply_theme)

    def appearance_dialog(self) -> None:
        d = self._dlg("外观与背景", 470, 430)
        pad = tk.Frame(d, bg=BG)
        pad.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(pad, text="主题色", bg=BG, fg=FG, anchor="w",
                 font=(FONT, 10 + SZ, "bold")).pack(fill="x")
        sw = tk.Frame(pad, bg=BG)
        sw.pack(fill="x", pady=(8, 12))
        cur = (self.s.get("theme_accent") or th.DEFAULT_ACCENT).lower()
        for _name, hexv in th.PRESETS:
            tk.Button(sw, text="", bg=hexv, activebackground=hexv, bd=0, width=3, height=1,
                      relief=("sunken" if hexv.lower() == cur else "flat"),
                      cursor="hand2", command=lambda h=hexv: self._set_accent(h)
                      ).pack(side="left", padx=3)
        tk.Button(sw, text="自定义…", bg=BG3, fg=FG, bd=0, padx=10, pady=3, cursor="hand2",
                  command=self._pick_accent).pack(side="left", padx=(10, 0))

        tk.Label(pad, text="背景照片", bg=BG, fg=FG, anchor="w",
                 font=(FONT, 10 + SZ, "bold")).pack(fill="x", pady=(6, 4))
        self.lbl_photo = tk.Label(pad, text=(self.s.get("bg_photo") or "（未选择）"),
                                  bg=BG, fg=DIM, anchor="w", justify="left", wraplength=410,
                                  font=(FONT, 8 + SZ))
        self.lbl_photo.pack(fill="x")
        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", pady=(6, 12))
        tk.Button(bf, text="选择图片…", bg=BG3, fg=FG, bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._pick_photo).pack(side="left")
        tk.Button(bf, text="清除", bg=BG3, fg=FG, bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._clear_photo).pack(side="left", padx=6)

        tk.Label(pad, text="透明度（越高照片越明显；0 = 纯主题色渐变）", bg=BG, fg=DIM,
                 anchor="w", font=(FONT, 8 + SZ)).pack(fill="x")
        self.v_alpha = tk.IntVar(value=int(round(float(self.s.get("bg_alpha", 0) or 0) * 100)))
        tk.Scale(pad, from_=0, to=100, orient="horizontal", variable=self.v_alpha,
                 bg=BG, fg=FG, troughcolor=BG3, highlightthickness=0, bd=0,
                 command=lambda _v: self._set_alpha(self.v_alpha.get() / 100.0)
                 ).pack(fill="x", pady=(2, 12))

        tk.Label(pad, text="主题色会同时影响：强调色、按钮、提醒条、进度环和托盘图标。",
                 bg=BG, fg=DIM, anchor="w", justify="left", wraplength=410,
                 font=(FONT, 8 + SZ)).pack(fill="x")
        tk.Button(pad, text="关闭", bg=ACCENT, fg="#FFFFFF", bd=0, padx=18, pady=6,
                  cursor="hand2", command=d.destroy).pack(side="right", pady=(14, 0))

    def settings_dialog(self) -> None:
        d = self._dlg("设置", 440, 440)
        pad = tk.Frame(d, bg=BG, padx=14, pady=12)
        pad.pack(fill="both", expand=True)
        e_int = tk.Entry(pad, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                         highlightthickness=0, width=8)
        self._lab(pad, "提醒间隔(分钟)", 0)
        e_int.grid(row=0, column=1, sticky="w", pady=3, ipady=4)
        e_int.insert(0, str(self.s.get("remind_interval_min", 30)))
        self._lab(pad, "免打扰 起/止", 1)
        qf = tk.Frame(pad, bg=BG)
        qf.grid(row=1, column=1, sticky="w", pady=3)
        e_q1 = tk.Entry(qf, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                        highlightthickness=0, width=8)
        e_q1.pack(side="left")
        tk.Label(qf, text=" ~ ", bg=BG, fg=DIM, font=(FONT, 9 + SZ)).pack(side="left")
        e_q2 = tk.Entry(qf, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 10 + SZ),
                        highlightthickness=0, width=8)
        e_q2.pack(side="left")
        qh = self.s.get("quiet_hours") or ["23:30", "07:00"]
        e_q1.insert(0, qh[0])
        e_q2.insert(0, qh[1])

        v_sound = tk.IntVar(value=1 if self.s.get("sound_on", True) else 0)
        v_panel = tk.IntVar(value=1 if self.s.get("startup_panel", True) else 0)
        v_top = tk.IntVar(value=1 if self.s.get("topmost", True) else 0)
        v_tray = tk.IntVar(value=1 if self.s.get("tray", True) else 0)
        v_auto = tk.IntVar(value=1 if su.is_enabled() else 0)
        v_dnd = tk.IntVar(value=1 if self.s.get("dnd_fullscreen", True) else 0)
        row = 2
        for v, txt in ((v_sound, "提醒时播放系统提示音"),
                       (v_panel, "启动时显示今日总览"),
                       (v_top, "窗口始终置顶"),
                       (v_tray, "点 × 收到系统托盘（否则贴边条）"),
                       (v_dnd, "全屏（游戏/视频）时勿扰，退出后补提醒"),
                       (v_auto, "开机自启动（登录后自动运行）")):
            tk.Checkbutton(pad, text=txt, variable=v, bg=BG, fg=FG, selectcolor=BG3,
                           activebackground=BG, activeforeground=ACCENT, bd=0,
                           highlightthickness=0, font=(FONT, 9 + SZ), anchor="w"
                           ).grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
            row += 1

        self._lab(pad, "进度文件", row)
        pf = tk.Frame(pad, bg=BG)
        pf.grid(row=row, column=1, sticky="w", pady=3)
        e_pmd = tk.Entry(pf, bg=BG3, fg=FG, insertbackground=FG, bd=0, font=(FONT, 8 + SZ),
                         highlightthickness=0, width=28)
        e_pmd.pack(side="left", ipady=3)
        e_pmd.insert(0, str(self.s.get("progress_md") or ""))
        btn(pf, "↻ 同步", lambda: self._sync_from(e_pmd.get()), width=6, pad=3).pack(
            side="left", padx=(4, 0))
        btn(pf, "…", lambda: self._pick_task_file(e_pmd), width=2, pad=3).pack(
            side="left", padx=(2, 0))
        row += 1
        btn(pad, "外观与背景…", self.appearance_dialog, bg=BG3, fg=FG, width=12,
            pad=4).grid(row=row, column=1, sticky="w", pady=(8, 0))
        row += 1

        info = tk.Label(pad, text=f"StudyNudge v{VERSION} · 数据：{self.store.path}", bg=BG, fg=DIM,
                        font=(FONT, 8 + SZ), anchor="w", wraplength=380, justify="left")
        info.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row += 1
        note = tk.Label(pad, text="", bg=BG, fg=OK, font=(FONT, 8 + SZ), anchor="w",
                        wraplength=380, justify="left")
        note.grid(row=row, column=0, columnspan=2, sticky="w")
        pad.columnconfigure(1, weight=1)

        def save():
            try:
                iv = max(1, int(e_int.get().strip()))
            except Exception:
                messagebox.showwarning("提醒间隔", "请填整数分钟", parent=d)
                return
            q1, q2 = e_q1.get().strip(), e_q2.get().strip()
            for q in (q1, q2):
                try:
                    datetime.strptime(q, "%H:%M")
                except Exception:
                    messagebox.showwarning("免打扰", "时间格式 HH:MM，例如 23:30", parent=d)
                    return
            self.s.update({
                "remind_interval_min": iv,
                "quiet_hours": [q1, q2],
                "sound_on": bool(v_sound.get()),
                "startup_panel": bool(v_panel.get()),
                "topmost": bool(v_top.get()),
                "tray": bool(v_tray.get()),
                "dnd_fullscreen": bool(v_dnd.get()),
                "progress_md": e_pmd.get().strip() or self.s.get("progress_md"),
            })
            self.store.save()
            self.root.attributes("-topmost", bool(v_top.get()))
            msgs = []
            if v_tray.get() and self.tray is None:
                self.tray_on = True
                self._start_tray()
                msgs.append("已启用托盘" if self.tray else
                            ("托盘不可用（未装 pystray）：" + tray_mod.IMPORT_ERR
                             if not tray_mod.AVAILABLE else "托盘启动失败"))
            elif not v_tray.get() and self.tray is not None:
                try:
                    self.tray.stop()
                except Exception:
                    pass
                self.tray = None
                self.tray_on = False
                msgs.append("已关闭托盘，收起时用贴边条")
            if v_auto.get() and not su.is_enabled():
                ok, m = su.enable()
                msgs.append(m)
            elif not v_auto.get() and su.is_enabled():
                ok, m = su.disable()
                msgs.append(m)
            note.configure(text="；".join(msgs) or "已保存")
            self.last_remind = datetime.now()

        bf = tk.Frame(pad, bg=BG)
        bf.grid(row=row + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        btn(bf, "保存", save, bg=ACCENT, fg="#FFFFFF", width=8).pack(side="right", padx=(6, 0))
        btn(bf, "关闭", d.destroy, width=6).pack(side="right")
        btn(bf, "打开数据文件夹", lambda: subprocess.Popen(
            ["explorer", str(self.store.path.parent)]), width=14).pack(side="left")
        btn(bf, "退出程序", lambda: (d.destroy(), self.quit_app()), bg="#3A2226",
            fg=DANGER, width=8).pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------ 本周俯瞰
    def _week_sub(self, t: dict, tdy: date) -> str:
        """推进型任务在本周俯瞰里的副标题：本周还能推到哪。"""
        step = int(t.get("step") or 0)
        if step <= 0:
            return ""
        mon = st.week_monday(tdy)
        n = sum(1 for i in range(7)
                if (mon + timedelta(days=i)) >= tdy
                and st.active_on(t, mon + timedelta(days=i)))
        if n <= 0:
            return ""
        parts = []
        if st.active_on(t, tdy):
            wd = "一二三四五六日"[tdy.isoweekday() - 1]
            parts.append(f"本段截止 周{wd}（今天）")
        parts.append(f"每天 {step} 讲")
        if n > 1:
            end = st.bump_title(t.get("title") or "", step * (n - 1))
            m = re.search(r"\d+\s*[–—\-~～]\s*[A-Za-z]{0,2}\d+", end)
            parts.append(f"本周到 {m.group(0) if m else end.split('·')[-1].strip()}")
        dl = str(t.get("deadline") or "")
        if dl:
            try:
                left = (st.parse_date(dl) - tdy).days
            except Exception:
                left = 0
            head = f"全课程 {t['total_units']} 讲 · " if t.get("total_units") else "全课程 · "
            parts.append(f"{head}{dl[5:]} 截止（剩 {left} 天）" if left >= 0
                         else f"{head}已超截止 {dl[5:]} {abs(left)} 天")
        line1 = " · ".join(parts[:3])
        return line1 + (("\n   " + " · ".join(parts[3:])) if len(parts) > 3 else "")

    def week_dialog(self) -> None:
        tdy = st.today()
        monday = st.week_monday(tdy)
        days = [monday + timedelta(days=i) for i in range(7)]
        d = self._dlg(f"本周俯瞰 · W{st.week_no(tdy)}", 620, 520)
        d.resizable(True, True)
        d.minsize(560, 400)   # 可拉伸：任务名一长就拉宽
        pad = tk.Frame(d, bg=BG, padx=14, pady=12)
        pad.pack(fill="both", expand=True)
        tk.Label(pad, text=f"W{st.week_no(tdy)} · {days[0].month}/{days[0].day} – "
                           f"{days[-1].month}/{days[-1].day} · {st.module_of(tdy)}",
                 bg=BG, fg=FG, font=(FONT, 12 + SZ, "bold"), anchor="w").pack(fill="x")
        summary = tk.Label(pad, text="", bg=BG, fg=DIM, font=(FONT, 9 + SZ), anchor="w",
                           justify="left", wraplength=550)
        summary.pack(fill="x", pady=(2, 8))

        wrap = tk.Frame(pad, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        sbar = tk.Canvas(wrap, width=7, bg=BG, highlightthickness=0, bd=0)
        body = tk.Frame(canvas, bg=BG)
        bid = canvas.create_window((0, 0), window=body, anchor="nw")
        def _sync(first, last):
            try:
                sbar.delete("all")
                vh = sbar.winfo_height()
                f, l = float(first), float(last)
                if l - f >= 0.999 or vh < 30:
                    return
                h = max(24, int(vh * (l - f)))
                y = int((vh - h) * (f / max(1e-6, 1 - (l - f))))
                sbar.create_line(3.5, y + 4, 3.5, y + h - 4, width=4,
                                 capstyle="round", fill=ACCENT)
            except Exception:
                pass
        canvas.configure(yscrollcommand=_sync)
        canvas.pack(side="left", fill="both", expand=True)
        sbar.pack(side="right", fill="y")
        d.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(bid, width=e.width))
        d.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        def toggle_cell(t: dict, day: date) -> None:
            if day > tdy:
                return
            st.set_done(t, day, not st.completed_on(t, day))
            self.store.save()
            pr_mod.apply_advance(self.store, path=self.s.get("progress_md"))
            pr_mod.flush_dirty(self.store, path=self.s.get("progress_md"))
            self.refresh()
            render()

        def render() -> None:
            for w in body.winfo_children():
                w.destroy()
            tk.Label(body, text="任务", bg=BG, fg=DIM, font=(FONT, 8 + SZ, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", padx=(4, 8), pady=(0, 4))
            for i, day in enumerate(days):
                is_td = day == tdy
                fg = ACCENT if is_td else (DIM if day > tdy else FG)
                tk.Label(body, text=f"{'一二三四五六日'[i]}\n{day.day}", bg=BG, fg=fg,
                         font=(FONT, 8 + SZ, "bold" if is_td else "normal"),
                         justify="center").grid(row=0, column=i + 1, padx=2, pady=(0, 4))

            tasks = sorted(enumerate(self.store.tasks),
                           key=lambda p: (st.LINE_ORDER.get(p[1].get("line"), 9), p[0]))
            tasks = [t for _i, t in tasks if not t.get("archived")]
            try:
                body.columnconfigure(0, weight=1)   # 拉宽时多出来的宽度给任务名
            except Exception:
                pass
            def group_of(t: dict) -> int:
                if t.get("kind") == "weekly":
                    return 2        # 周进度任务（哪怕带课次推进）
                if t.get("kind") == "once":
                    return 3
                if int(t.get("step") or 0) > 0:
                    return 1        # 每日推进型（看课、教材章）
                return 0            # 每日任务

            groups = [(0, "每日任务", "#8B93A1"),
                      (1, "推进任务（按课次往前走）", "#5B9BD5"),
                      (2, "周进度任务（每周固定几次）", "#6FA36F"),
                      (3, "一次性任务", DIM)]
            plan = done_n = 0
            r = 1
            shown = 0
            for gid, gname, gcolor in groups:
                rows = [t for t in tasks if group_of(t) == gid
                        and any(st.active_on(t, d2) or st.completed_on(t, d2) for d2 in days)]
                if not rows:
                    continue
                tk.Label(body, text=gname, bg=BG, fg=gcolor, anchor="w",
                         font=(FONT, 9 + SZ, "bold")).grid(row=r, column=0, columnspan=8,
                                                           sticky="w", padx=(4, 0), pady=(9, 2))
                r += 1
                for t in rows:
                    active = [st.active_on(t, day) or st.completed_on(t, day) for day in days]
                    txt0 = "● " + (t["title"][:34] + ("…" if len(t["title"]) > 34 else ""))
                    sub = self._week_sub(t, tdy)
                    if sub:
                        txt0 += "\n   " + sub
                    tk.Label(body, text=txt0, bg=BG, fg=FG, font=(FONT, 9 + SZ), anchor="w",
                             justify="left", wraplength=470).grid(row=r, column=0, sticky="w",
                                                                 padx=(14, 8), pady=1)
                    for i, day in enumerate(days):
                        is_done = st.completed_on(t, day)
                        if active[i]:
                            plan += 1
                            if is_done:
                                done_n += 1
                        if is_done:
                            txt, fg, cur = "✓", OK, "hand2"
                        elif not active[i]:
                            txt, fg, cur = "·", "#3A3E46", ""
                        elif day < tdy:
                            txt, fg, cur = "✗", DANGER, "hand2"
                        elif day == tdy:
                            txt, fg, cur = "○", ACCENT, "hand2"
                        else:
                            txt, fg, cur = "○", DIM, ""
                        cell = tk.Label(body, text=txt, bg=(BG3 if day == tdy else BG), fg=fg,
                                        font=(FONT, 12 + SZ, "bold" if day == tdy else "normal"),
                                        width=2, cursor=cur or "arrow")
                        cell.grid(row=r, column=i + 1, padx=1)
                        if cur:
                            cell.bind("<Button-1>", lambda e, t=t, day=day: toggle_cell(t, day))
                    r += 1
                    shown += 1
            if shown == 0:
                tk.Label(body, text="本周没有任务", bg=BG, fg=DIM, font=(FONT, 9 + SZ)).grid(
                    row=1, column=0, columnspan=8, pady=20)

            pct = 0 if not plan else int(round(done_n * 100.0 / plan))
            per, _days = st.week_stats(self.store.tasks, tdy)
            bits = [f"完成 {done_n}/{plan} 项（{pct}%）"]
            for k, (nm, _c) in st.LINES.items():
                tgt = st.WEEK_TEMPLATE.get(k)
                if tgt is None:
                    continue
                got = per[k]["done"]
                mark = "✓" if got >= tgt else ""
                bits.append(f"{nm} {got:g}/{tgt:g}h{mark}")
            od = [t for t in st.pending(tdy, self.store.tasks) if st.overdue_days(t) > 0]
            if od:
                bits.append(f"逾期 {len(od)} 项")
            summary.configure(text=" · ".join(bits))

        render()

        def do_csv():
            p = self.store.path.parent / "exports" / f"W{st.week_no(tdy)}_{days[0]}.csv"
            st.export_week_csv(p, self.store.tasks, tdy)
            summary.configure(text=f"已导出 {p}")

        canvas.update_idletasks()
        _need = body.winfo_reqheight() + 6
        _maxh = int(d.winfo_screenheight() * 0.58)
        canvas.configure(height=min(max(220, _need), _maxh),
                         scrollregion=(0, 0, 0, body.winfo_reqheight()))

        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", pady=(10, 0))
        btn(bf, "导出本周 CSV", do_csv, width=12).pack(side="left")
        tk.Label(bf, text="点格子可补勾 / 撤销", bg=BG, fg=DIM, font=(FONT, 8 + SZ)).pack(side="left", padx=8)
        btn(bf, "关闭", d.destroy, bg=ACCENT, fg="#FFFFFF", width=6).pack(side="right")
        d.bind("<Escape>", lambda e: d.destroy())

    def stats_dialog(self) -> None:
        d = self._dlg(f"统计 · W{st.week_no(st.today())}", 460, 520)
        pad = tk.Frame(d, bg=BG, padx=14, pady=12)
        pad.pack(fill="both", expand=True)
        tdy = st.today()
        per, days = st.week_stats(self.store.tasks, tdy)
        tk.Label(pad, text=f"本周 {days[0]} ~ {days[-1]} · {st.module_of(tdy)}", bg=BG, fg=DIM,
                 font=(FONT, 9 + SZ), anchor="w", wraplength=420, justify="left").pack(fill="x")
        grid = tk.Frame(pad, bg=BG)
        grid.pack(fill="x", pady=8)
        heads = ["学科", "已完成", "周目标", "差距", "条目"]
        for c, h in enumerate(heads):
            tk.Label(grid, text=h, bg=BG, fg=DIM, font=(FONT, 8 + SZ, "bold"),
                     anchor="w").grid(row=0, column=c, sticky="w", padx=(0, 10))
        r = 1
        for k, (name, color) in st.LINES.items():
            done = per[k]["done"]
            target = st.WEEK_TEMPLATE.get(k)
            gap = (done - target) if target is not None else None
            tk.Label(grid, text="● " + name, bg=BG, fg=color, font=(FONT, 9 + SZ),
                     anchor="w").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=1)
            tk.Label(grid, text=f"{done:g}h", bg=BG, fg=FG, font=(FONT, 9 + SZ),
                     anchor="w").grid(row=r, column=1, sticky="w", padx=(0, 10))
            tk.Label(grid, text=(f"{target:g}h" if target is not None else "—"), bg=BG,
                     fg=DIM, font=(FONT, 9 + SZ), anchor="w").grid(row=r, column=2, sticky="w", padx=(0, 10))
            tk.Label(grid, text=(f"{gap:+g}h" if gap is not None else "—"), bg=BG,
                     fg=(OK if (gap or 0) >= 0 else DANGER), font=(FONT, 9 + SZ),
                     anchor="w").grid(row=r, column=3, sticky="w", padx=(0, 10))
            tk.Label(grid, text=str(per[k]["count"]), bg=BG, fg=DIM, font=(FONT, 9 + SZ),
                     anchor="w").grid(row=r, column=4, sticky="w")
            r += 1

        pend = st.pending(tdy, self.store.tasks)
        od = [t for t in pend if st.overdue_days(t) > 0]
        tk.Label(pad, text=f"今日待办 {len(pend)} 项 · 逾期 {len(od)} 项", bg=BG, fg=FG,
                 font=(FONT, 9 + SZ, "bold"), anchor="w").pack(fill="x", pady=(6, 2))
        if od:
            hint = "滞后 ≤1 周：直接吸收；1–2 周：砍可弃项；>2 周：保数学 + 作业2·3·5·7 + M6。"
            tk.Label(pad, text=hint, bg=BG, fg=DANGER, font=(FONT, 8 + SZ), anchor="w",
                     wraplength=410, justify="left").pack(fill="x")

        def do_csv():
            p = self.store.path.parent / "exports" / f"W{st.week_no(tdy)}_{days[0]}.csv"
            st.export_week_csv(p, self.store.tasks, tdy)
            msg.configure(text=f"已导出 {p}")

        def do_md():
            ok, m = self.export_progress()
            msg.configure(text=m, fg=(OK if ok else DANGER))

        msg = tk.Label(pad, text="", bg=BG, fg=OK, font=(FONT, 8 + SZ), anchor="w")
        msg.pack(fill="x", side="bottom")
        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", side="bottom", pady=(10, 0))
        btn(bf, "导出本周 CSV", do_csv, width=12).pack(side="left")
        btn(bf, "追加今日到 进度.md", do_md, width=16).pack(side="left", padx=(6, 0))
        btn(bf, "关闭", d.destroy, width=6).pack(side="right")

        box = tk.Frame(pad, bg=BG2)
        box.pack(fill="both", expand=True, pady=(8, 0))
        txt = tk.Text(box, bg=BG2, fg=FG, bd=0, font=(FONT, 8 + SZ), height=8,
                      highlightthickness=0, wrap="word")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        lines = []
        for t in pend:
            tag = f"  [逾期{st.overdue_days(t)}天]" if st.overdue_days(t) > 0 else ""
            lines.append(f"□ {t['title']}{tag}")
        for t in st.completed(tdy, self.store.tasks):
            lines.append(f"☑ {t['title']}")
        txt.insert("1.0", "\n".join(lines) or "今日无记录")
        txt.configure(state="disabled")

    def export_progress(self) -> tuple[bool, str]:
        p = Path(self.s.get("progress_md") or "")
        tdy = st.today()
        text = st.progress_text(tdy, self.store.tasks)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                import shutil
                try:
                    shutil.copy2(p, p.with_name(p.name + ".bak"))
                except Exception:
                    pass
                sep = "" if p.read_text(encoding="utf-8", errors="ignore").endswith("\n\n") else "\n\n"
                with open(p, "a", encoding="utf-8") as f:
                    f.write(sep + text)
            else:
                with open(p, "w", encoding="utf-8") as f:
                    f.write("# 进度记录\n\n" + text)
            return True, f"已追加到 {p}"
        except Exception as e:
            return False, f"写入失败（文件是否被 WPS 占用？）：{e}"

    def show_overview(self) -> None:
        tdy = st.today()
        d = self._dlg("今日总览", 440, 400)
        pad = tk.Frame(d, bg=BG, padx=16, pady=14)
        pad.pack(fill="both", expand=True)
        pend = st.pending(tdy, self.store.tasks)
        done = st.completed(tdy, self.store.tasks)
        od = [t for t in pend if st.overdue_days(t) > 0]
        tk.Label(pad, text=f"{tdy.month} 月 {tdy.day} 日 {WK[tdy.weekday()]} · W{st.week_no(tdy)}",
                 bg=BG, fg=FG, font=(FONT, 14 + SZ, "bold"), anchor="w").pack(fill="x")
        tk.Label(pad, text=st.module_of(tdy), bg=BG, fg=DIM, font=(FONT, 9 + SZ),
                 anchor="w").pack(fill="x", pady=(0, 10))
        head_txt = "今日已清空 ✓" if not pend else f"今日 {len(pend)} 项未完成"
        tk.Label(pad, text=head_txt, bg=BG, fg=(OK if not pend else DANGER),
                 font=(FONT, 12 + SZ, "bold"), anchor="w").pack(fill="x")
        if od:
            tk.Label(pad, text=f"其中 {len(od)} 项已逾期，最久 {max(st.overdue_days(t) for t in od)} 天",
                     bg=BG, fg=DANGER, font=(FONT, 9 + SZ), anchor="w").pack(fill="x", pady=(2, 0))
        hours = sum(float(t.get("est_hours") or 0) for t in pend)
        tk.Label(pad, text=f"已完成 {len(done)} 项 · 剩余约 {hours:g}h", bg=BG, fg=DIM,
                 font=(FONT, 9 + SZ), anchor="w").pack(fill="x", pady=(2, 8))

        box = tk.Frame(pad, bg=BG2)
        box.pack(fill="both", expand=True)
        for t in pend[:9]:
            tag = f"  [逾期{st.overdue_days(t)}天]" if st.overdue_days(t) > 0 else ""
            tk.Label(box, text=f"□ {t['title']}{tag}", bg=BG2,
                     fg=(DANGER if tag else FG), font=(FONT, 9 + SZ), anchor="w",
                     wraplength=380, justify="left").pack(fill="x", padx=8, pady=1)
        if len(pend) > 9:
            tk.Label(box, text=f"… 还有 {len(pend) - 9} 项", bg=BG2, fg=DIM,
                     font=(FONT, 8 + SZ), anchor="w").pack(fill="x", padx=8)

        per, _days = st.week_stats(self.store.tasks, tdy)
        gap = per["graphics"]["done"] + per["math"]["done"] + per["english"]["done"] + per["reading"]["done"]
        tk.Label(pad, text=f"本周累计 {gap:g}h（目标 28h）", bg=BG, fg=DIM, font=(FONT, 8 + SZ),
                 anchor="w").pack(fill="x", pady=(8, 0))
        stxt, scol = pr_mod.status_text(self.store)
        tk.Label(pad, text="↻ " + stxt, bg=BG,
                 fg={"ok": OK, "dim": DIM, "warn": DANGER}.get(scol, DIM),
                 font=(FONT, 8 + SZ), anchor="w").pack(fill="x")

        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", pady=(10, 0))
        btn(bf, "开始", d.destroy, bg=ACCENT, fg="#FFFFFF", width=8).pack(side="right")
        btn(bf, "看明日", lambda: (d.destroy(), self._goto_tomorrow()), width=8).pack(side="right", padx=(0, 6))
        btn(bf, "新增任务", lambda: (d.destroy(), self.edit_task(None)), width=9).pack(side="left")
        d.bind("<Escape>", lambda e: d.destroy())

    def _goto_tomorrow(self) -> None:
        self.view_day = st.today() + timedelta(days=1)
        self.expand()
        self.refresh()


def run(store_path: Path) -> None:
    root = tk.Tk()
    store = st.Store(store_path)
    App(root, store)
    root.mainloop()
