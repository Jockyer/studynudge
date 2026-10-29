# -*- coding: utf-8 -*-
"""StudyNudge 数据层：任务模型 / 原子读写 / 日期与统计。

任务种类:
    once    一次性任务，date = 计划执行日
    daily   每日重复，date = 起始日，完成记录写入 done_dates
    weekly  每周指定星期，weekdays 里 1=周一 ... 7=周日
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import uuid

import paths
from datetime import date, datetime, timedelta
from pathlib import Path

# 26 周规划锚点：W1 = 2026-09-14（周一）
WEEK1_MONDAY = date(2026, 9, 14)
RANGE_RE = re.compile(r"(\d+)(\s*[–—\-~～]\s*)([A-Za-z]{0,2})(\d+)")

LINES = {
    "graphics": ("图形学", "#4A90D9"),
    "math": ("数学", "#E05252"),
    "english": ("英语", "#4CAF7D"),
    "reading": ("阅读", "#E8A33D"),
    "other": ("其他", "#9AA0A8"),
}
LINE_ORDER = {"math": 0, "graphics": 1, "english": 2, "reading": 3, "other": 4}

# 学期内周模板（rev2）：图形学12h + 数学12h + 英语2h + 阅读2h
WEEK_TEMPLATE = {"graphics": 12.0, "math": 12.0, "english": 2.0, "reading": 2.0}

MODULES = [
    (1, 2, "M1 UE 上手 + Blender→UE 管线"),
    (3, 6, "M2 GAMES101 第5讲 → 作业7"),
    (7, 10, "M3 RTiOW 光追器 + LearnOpenGL PBR/IBL"),
    (11, 14, "M4 UE 材质/光照/Lumen + 资产管线"),
    (15, 16, "个人场景 v1"),
    (17, 19, "期末周（≈4h/周，只维持）"),
    (20, 23, "M5 抓帧 → 定位瓶颈 → 优化"),
    (24, 26, "M6 复现项目 + 材料包"),
]

# 任务清单版本：每次改动 SEED_TASKS 就 +1，老数据会按需补齐
SEED_VERSION = 2

DEFAULT_SETTINGS = {
    "remind_interval_min": 30,
    "quiet_hours": ["23:30", "07:00"],
    "sound_on": True,
    "startup_panel": True,
    "topmost": True,
    "pos": None,
    "size": [420, 580],
    "collapsed": False,
    "tray": True,          # 关闭按钮 → 收到系统托盘
    "seed_version": 1,     # 1 = 旧的 2 条种子；2 = 完整清单
    "progress_md": "",          # 空 = data\任务列表.md（首次运行自动生成模板）
    "progress_mtime": 0.0,   # 上次同步时进度.md 的修改时间
    "progress_sync_at": "",  # 上次同步时间
    "progress_updated": "",  # 进度.md 自称的「最近更新」日期
    "stale_warned": "",      # 最后一天提醒过“进度.md 太久没更新”
    "dnd_fullscreen": True,   # 全屏游戏/视频时勿扰（不弹提醒，只记日志）
    "theme_accent": "#4A90D9",  # 主题色（影响强调色与界面色调）
    "bg_photo": "",            # 背景照片路径（空 = 不用）
    "bg_alpha": 0.0,           # 背景照片透明度 0–1（越高照片越明显）
}


# ---------------------------------------------------------------- 日期工具
def today() -> date:
    return date.today()


def iso(d: date) -> str:
    return d.isoformat()


def parse_date(s: str, default: date | None = None) -> date:
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except Exception:
        return default or today()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def week_no(d: date) -> int:
    monday = d - timedelta(days=d.weekday())
    return (monday - WEEK1_MONDAY).days // 7 + 1


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def module_of(d: date) -> str:
    w = week_no(d)
    for a, b, name in MODULES:
        if a <= w <= b:
            return name
    return "规划区间外"


# ---------------------------------------------------------------- 任务对象
def new_task(title: str, line: str = "other", kind: str = "once",
             day: date | None = None, weekdays: list[int] | None = None,
             est_hours: float = 0.0, note: str = "",
             src: str = "user", pkey: str = "") -> dict:
    """src: user=手动新增 / seed=内置清单 / progress=由进度.md 生成。"""
    ts = now_iso()
    return {
        "id": "t_" + uuid.uuid4().hex[:10],
        "title": (title or "").strip(),
        "src": src if src in ("user", "seed", "progress") else "user",
        "pkey": pkey or "",
        "step": 0,             # >0 时：每次勾完，次日标题里的课次自动 +step
        "next_title": "",      # 勾完后算好的下一条标题，次日生效
        "doc_dirty": False,    # 推进后还没写回进度.md
        "defer_from": "",       # 延期：原定哪一天
        "defer_to": "",         # 延期：改到哪一天
        "prev_title": "",      # 推进前的标题（取消完成时回退用）
        "advance_on": "",      # 哪一天的完成触发了这次推进
        "deadline": "",        # 本板块截止日（进度.md 备注里的「截止 11-04」）
        "total_units": 0,      # 板块总量（如共 46 讲）
        "note": note or "",
        "line": line if line in LINES else "other",
        "kind": kind if kind in ("once", "daily", "weekly") else "once",
        "weekdays": sorted(int(x) for x in (weekdays or [])),
        "date": iso(day or today()),
        "est_hours": float(est_hours or 0),
        "status": "todo",
        "done_on": "",
        "done_dates": [],
        "defer_count": 0,
        "spent_hours": 0.0,
        "created_at": ts,
        "updated_at": ts,
    }


def normalize(t: dict) -> dict:
    base = new_task("?")
    out = dict(base)
    out.update({k: v for k, v in (t or {}).items() if k in base or k in ("weekdays",)})
    out["title"] = str(out.get("title") or "未命名")
    out["line"] = out["line"] if out.get("line") in LINES else "other"
    out["kind"] = out.get("kind") if out.get("kind") in ("once", "daily", "weekly") else "once"
    try:
        out["est_hours"] = float(out.get("est_hours") or 0)
    except Exception:
        out["est_hours"] = 0.0
    if not isinstance(out.get("done_dates"), list):
        out["done_dates"] = []
    if not isinstance(out.get("weekdays"), list):
        out["weekdays"] = []
    if out.get("src") not in ("user", "seed", "progress"):
        out["src"] = "user"
    out["pkey"] = str(out.get("pkey") or "")
    out["archived"] = bool(out.get("archived"))
    try:
        out["step"] = max(0, int(out.get("step") or 0))
    except Exception:
        out["step"] = 0
    out["next_title"] = str(out.get("next_title") or "")
    out["doc_dirty"] = bool(out.get("doc_dirty"))
    out["defer_from"] = str(out.get("defer_from") or "")
    out["defer_to"] = str(out.get("defer_to") or "")
    out["prev_title"] = str(out.get("prev_title") or "")
    out["advance_on"] = str(out.get("advance_on") or "")
    out["deadline"] = str(out.get("deadline") or "")
    try:
        out["total_units"] = max(0, int(out.get("total_units") or 0))
    except Exception:
        out["total_units"] = 0
    return out


def active_on(t: dict, d: date) -> bool:
    """该任务在 d 这天是否还需要做。"""
    ds = iso(d)
    if t.get("date", "") > ds:          # 还没到开始日
        return False
    if t.get("kind") == "once":
        return t.get("status") == "todo"
    if t.get("defer_from") == ds:
        return False                     # 已延期到次日，当天不再出现
    if ds in (t.get("done_dates") or []):
        return False
    if t.get("kind") == "daily":
        return True
    return (d.weekday() + 1) in (t.get("weekdays") or [])


def completed_on(t: dict, d: date) -> bool:
    ds = iso(d)
    if t.get("kind") == "once":
        return t.get("status") == "done" and t.get("done_on") == ds
    return ds in (t.get("done_dates") or [])


def overdue_days(t: dict, ref: date | None = None) -> int:
    """仅一次性任务判逾期。"""
    if t.get("kind") != "once" or t.get("status") == "done":
        return 0
    ref = ref or today()
    try:
        due = date.fromisoformat(t.get("date") or "")
    except Exception:
        return 0
    return max(0, (ref - due).days)


def set_done(t: dict, d: date, done: bool = True) -> None:
    ds = iso(d)
    if t.get("kind") == "once":
        t["status"] = "done" if done else "todo"
        t["done_on"] = ds if done else ""
    else:
        dd = t.setdefault("done_dates", [])
        if done:
            if ds not in dd:
                dd.append(ds)
                dd.sort()
        elif ds in dd:
            dd.remove(ds)
    # 课次推进：勾完先算好下一条标题，次日生效（今天仍显示已完成的那一条）
    if done and int(t.get("step") or 0) > 0:
        t["next_title"] = bump_title(t.get("title") or "", int(t["step"]))
    elif not done:
        t["next_title"] = ""
        # 回退：取消的正是当初触发推进的那天 → 把标题退回上一档，并标记需要写回进度.md
        if t.get("prev_title") and ds <= (t.get("advance_on") or ""):
            t["title"] = t["prev_title"]
            t["prev_title"] = ""
            t["advance_on"] = ""
            t["doc_dirty"] = True
    t["updated_at"] = now_iso()


def bump_title(title: str, step: int) -> str:
    """把标题里的课次往后推：L13–L16 → L17–L20；第 14 讲 → 第 15 讲。"""
    if step <= 0 or not title:
        return title
    m = RANGE_RE.search(title)
    if m:
        # 支持 L13–L16 / 第 13–16 讲 这种两边都带前缀的写法
        return (title[:m.start()] + f"{int(m.group(1)) + step}{m.group(2)}"
                f"{m.group(3)}{int(m.group(4)) + step}" + title[m.end():])
    ms = list(re.finditer(r"\d+", title))
    if ms:
        m = ms[-1]      # 取最后一个数字，避开「0 基础课」的 0
        return title[:m.start()] + str(int(m.group(0)) + step) + title[m.end():]
    return title


def defer_task(t: dict, days: int = 1, day: date | None = None) -> None:
    """主动延期：一次性任务改执行日；每日/周任务只跳过当天，次日带「延期」标记出现。"""
    base = day or today()
    to = base + timedelta(days=days)
    t["defer_from"] = iso(base)
    t["defer_to"] = iso(to)
    t["defer_count"] = int(t.get("defer_count") or 0) + 1
    if t.get("kind") == "once":
        t["date"] = iso(to)
    t["updated_at"] = now_iso()


def is_deferred(t: dict, day: date) -> bool:
    """这一天看到的是不是「延期过来」的任务。"""
    ds = iso(day)
    return t.get("defer_to") == ds and (t.get("defer_from") or "") < ds


def sort_tasks(tasks: list[dict], day: date | None = None) -> list[dict]:
    """延期的排最前（要优先解决），其余按学科线顺序。"""
    day = day or today()
    idx = {id(t): i for i, t in enumerate(tasks)}
    return sorted(tasks, key=lambda t: (
        0 if is_deferred(t, day) else 1,
        LINE_ORDER.get(t.get("line"), 9),
        idx.get(id(t), 0)))


def pending(day: date, tasks: list[dict]) -> list[dict]:
    out = [t for t in tasks if not t.get("archived") and active_on(t, day)]
    out.sort(key=lambda t: (
        0 if is_deferred(t, day) else 1,
        0 if overdue_days(t) > 0 else 1,
        t.get("date") or "",
        LINE_ORDER.get(t.get("line"), 9),
        t.get("created_at") or "",
    ))
    return out


def completed(day: date, tasks: list[dict]) -> list[dict]:
    out = [t for t in tasks if not t.get("archived") and completed_on(t, day)]
    out.sort(key=lambda t: (LINE_ORDER.get(t.get("line"), 9), t.get("updated_at") or ""))
    return out


def week_stats(tasks: list[dict], ref: date) -> tuple[dict, list[date]]:
    monday = week_monday(ref)
    days = [monday + timedelta(days=i) for i in range(7)]
    per = {k: {"done": 0.0, "count": 0} for k in LINES}
    for t in tasks:
        for d in days:
            if completed_on(t, d):
                row = per[t.get("line", "other")]
                row["done"] += float(t.get("est_hours") or 0)
                row["count"] += 1
    return per, days


# (标题, 学科线, 类型, 星期, 预估h, 备注)
SEED_TASKS = [
    ("数学 · 基础篇（武忠祥）例题", "math", "daily", [], 1.0,
     "读基础篇 → 例题先自己做 → 再看解答"),
    ("数学 · 严选题 / 错题回顾", "math", "daily", [], 0.7, "错题打标记，周末回看"),
    ("图形学 · 看课 / 读书", "graphics", "daily", [], 0.8, "GAMES101 等"),
    ("图形学 · 动手实现 / 作业", "graphics", "daily", [], 0.9, "当前：作业 7（路径追踪）"),
    ("英语 · 背单词 20 分钟", "english", "daily", [], 0.3, "考研英语一 5500 词"),
    ("阅读 · 教材 / 长文 1 小时", "reading", "weekly", [3, 6], 1.0, "周三、周六"),
    ("论文轻轨 · 隔周 1 篇", "reading", "weekly", [6], 0.5,
     "只读 Abstract + Intro + 方法，写 3 句话"),
    ("技术笔记（双周 1 篇）", "other", "weekly", [7], 0.5, "双周 1 篇，周日写"),
    ("周复盘 30 分钟", "other", "weekly", [7], 0.5, "学了什么 / 卡在哪 / 下周调整"),
]

# 旧版种子的标题，迁移时若没动过就删掉
OLD_SEEDS = {"数学（高数 / 线代 / 概率）", "GAMES101 / 图形学"}
SEED_TITLES = {row[0] for row in SEED_TASKS} | OLD_SEEDS


def seed_tasks() -> list[dict]:
    d = today()
    return [new_task(t, line, kind, d, wd, h, note, src="seed")
            for t, line, kind, wd, h, note in SEED_TASKS]


# ---------------------------------------------------------------- 存储
class Store:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.data: dict = {"version": 1, "tasks": [], "settings": dict(DEFAULT_SETTINGS)}
        self.load()

    # -- 属性
    @property
    def settings(self) -> dict:
        return self.data.setdefault("settings", {})

    @property
    def tasks(self) -> list[dict]:
        return self.data.setdefault("tasks", [])

    # -- 读写
    def load(self) -> None:
        if not self.path.exists():
            self.data["tasks"] = seed_tasks()
            self.settings["seed_version"] = SEED_VERSION
            if not self.settings.get("progress_md"):
                self.settings["progress_md"] = str(paths.data_dir() / "任务列表.md")
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("root is not object")
        except Exception:
            try:
                shutil.copy2(self.path, self.path.with_suffix(".corrupt.json"))
            except Exception:
                pass
            raw = {}
        s = dict(DEFAULT_SETTINGS)
        s.update(raw.get("settings") or {})
        if not s.get("progress_md"):
            s["progress_md"] = str(paths.data_dir() / "任务列表.md")
        tasks = []
        for t in (raw.get("tasks") or []):
            if not isinstance(t, dict):
                continue
            nt = normalize(t)
            if "src" not in t:      # 旧数据没这字段：内置标题算 seed，其余算用户自建
                nt["src"] = "seed" if nt["title"] in SEED_TITLES else "user"
            tasks.append(nt)
        self.data = {"version": 1, "tasks": tasks, "settings": s}
        self.migrate_seed()

    def migrate_seed(self) -> bool:
        """把旧的 2 条粗任务补成完整清单；只做一次，用户后来删掉的不会被重新加回。"""
        try:
            ver = int(self.settings.get("seed_version") or 1)
        except Exception:
            ver = 1
        if ver >= SEED_VERSION:
            return False
        keep = []
        for t in self.tasks:
            untouched = (not t.get("done_dates")) and t.get("status") != "done" \
                        and not t.get("spent_hours") and not t.get("defer_count")
            if t.get("title") in OLD_SEEDS and untouched:
                continue
            keep.append(t)
        self.tasks[:] = keep
        have = {t.get("title") for t in self.tasks}
        d = today()
        for title, line, kind, wd, h, note in SEED_TASKS:
            if title not in have:
                self.tasks.append(new_task(title, line, kind, d, wd, h, note,
                                           src="seed"))
        self.settings["seed_version"] = SEED_VERSION
        self.save()
        return True

    def save(self) -> None:
        """原子写：临时文件 + os.replace，旧文件留 .bak 兜底。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        txt = json.dumps(self.data, ensure_ascii=False, indent=2)
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
            f.flush()
            os.fsync(f.fileno())
        if self.path.exists():
            try:
                shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
            except Exception:
                pass
        os.replace(tmp, self.path)

    # -- 任务操作
    def get(self, tid: str) -> dict | None:
        for t in self.tasks:
            if t.get("id") == tid:
                return t
        return None

    def add(self, task: dict) -> dict:
        self.tasks.append(task)
        self.save()
        return task

    def remove(self, tid: str) -> None:
        self.tasks[:] = [t for t in self.tasks if t.get("id") != tid]
        self.save()

    def touch(self, t: dict) -> None:
        t["updated_at"] = now_iso()
        self.save()

    def add_spent(self, tid: str, hours: float) -> None:
        t = self.get(tid)
        if not t:
            return
        t["spent_hours"] = round(float(t.get("spent_hours") or 0) + float(hours), 3)
        self.touch(t)


# ---------------------------------------------------------------- 导出
def export_week_csv(path: Path, tasks: list[dict], ref: date) -> Path:
    per, days = week_stats(tasks, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["周次", f"W{week_no(ref)}", f"{days[0]} ~ {days[-1]}"])
        w.writerow([])
        w.writerow(["学科", "本周完成小时", "周模板目标", "差距", "完成条目"])
        for k, (name, _c) in LINES.items():
            done = per[k]["done"]
            target = WEEK_TEMPLATE.get(k)
            w.writerow([name, round(done, 2), target if target is not None else "",
                        round(done - target, 2) if target is not None else "",
                        per[k]["count"]])
        w.writerow([])
        w.writerow(["任务明细"])
        w.writerow(["日期", "标题", "学科", "类型", "预估h", "实际h", "状态"])
        for d in days:
            for t in tasks:
                if completed_on(t, d):
                    w.writerow([iso(d), t["title"], LINES[t["line"]][0], t["kind"],
                                t.get("est_hours", 0), t.get("spent_hours", 0), "done"])
    return path


def progress_text(day: date, tasks: list[dict]) -> str:
    pend = pending(day, tasks)
    done = completed(day, tasks)
    od = [t for t in pend if overdue_days(t) > 0]
    total_h = sum(float(t.get("est_hours") or 0) for t in done)
    lines = [f"## {iso(day)}（{['周一','周二','周三','周四','周五','周六','周日'][day.weekday()]} · W{week_no(day)}）",
             f"完成 {len(done)} 项 / 约 {total_h:g}h" + (f"；未完成 {len(pend)} 项" if pend else "；今日已清空")]
    for t in done:
        lines.append(f"- [x] {t['title']}（{LINES[t['line']][0]}{' · ' + format(float(t.get('est_hours') or 0), 'g') + 'h' if t.get('est_hours') else ''}）")
    for t in pend:
        tag = f"（逾期 {overdue_days(t)} 天）" if overdue_days(t) > 0 else ""
        lines.append(f"- [ ] {t['title']}{tag}")
    if od:
        lines.append(f"> 逾期 {len(od)} 项：滞后 ≤1 周直接吸收；>2 周保数学 + 作业2·3·5·7 + M6。")
    return "\n".join(lines) + "\n\n"
