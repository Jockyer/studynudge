# -*- coding: utf-8 -*-
"""StudyNudge 进度联动：把「进度.md」解析成今日任务（唯一真源）。

在 进度.md 里写这样一小节，提示器就照着它生成任务（人读也自然）：

    ## 0.5 当前执行（StudyNudge 读取）
    - [math] 0基础课 第 14 讲 | 1.0 | 下一讲第 15 讲
    - [graphics] GAMES101 L15–L16 | 0.8 | 看完接作业 7
    - [graphics#hw] 作业 7 · 路径追踪 | 0.9 | 前置 L15–L16
    - [english] 背单词 20 分钟 | 0.3 | 5500 词 | 每周 1,2,3,4,5,6,7

字段：- [学科(#子项)] 标题 | 小时 | 备注 | 排期
    学科：math / graphics / english / reading / other（也可写中文）
    排期：缺省 = 每天；「每周 3,6」= 周三、周六；「一次 2026-11-02」= 一次性

同一学科可以写多条，用 #子项 区分（#hw = 作业）；不写 # 时 key 就是学科名。
标题变了 = 换了个东西要做，今天那条完成标记会被清掉，重新出现在待办里。
小节不存在时，退化为读「查缺补漏」里未勾选的 `- [ ]` 条目（每天提醒直到勾掉）。
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import store as st

SECTION_KEYS = ("当前执行", "今日焦点", "NUDGE", "StudyNudge")
LINE_ALIAS = {
    "math": "math", "数学": "math", "高数": "math", "线代": "math", "概率": "math",
    "graphics": "graphics", "图形": "graphics", "图形学": "graphics", "渲染": "graphics",
    "english": "english", "英语": "english",
    "reading": "reading", "阅读": "reading", "论文": "reading",
    "other": "other", "其他": "other", "笔记": "other", "工程": "other", "复盘": "other",
}
ITEM_RE = re.compile(r"^\s*[-*]\s*\[(?P<key>[^\]]+)\]\s*(?P<rest>\S.*)$")
TODO_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s*(?P<rest>\S.*)$")
BOLD_RE = re.compile(r"\*\*(?P<tag>[^*]{1,8})\*\*\s*[:：]?")
UPDATED_RE = re.compile(r"最近更新\**\s*[:：]?\s*(\d{4})-(\d{2})-(\d{2})")
STEP_RE = re.compile(r"(?:每日|每天|每次|步进|推进)\s*\+?\s*(\d+)")
DEADLINE_RE = re.compile(r"截止\s*(\d{4})-(\d{1,2})-(\d{1,2})"
                         r"|截止\s*(\d{1,2})[-/](\d{1,2})"
                         r"|(\d{1,2})/(\d{1,2})\s*前")
TOTAL_RE = re.compile(r"共\s*(\d+)\s*(?:讲|章|节|篇|个)")
START_RE = re.compile(r"从\s*(?:(\d{4})-)?(\d{1,2})-(\d{1,2})")


# ---------------------------------------------------------------- 读文件
def read_doc(path: str | os.PathLike) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
        except Exception:
            return ""
    return ""


def _section(lines: list[str], keys=SECTION_KEYS) -> list[str] | None:
    """取标题里含 keys 的 ## 小节正文（到下一个同级或更高级标题为止）。"""
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s.startswith("#"):
            continue
        if not any(k.lower() in s.lower() for k in keys):
            continue
        level = len(s) - len(s.lstrip("#"))
        body = []
        for l2 in lines[i + 1:]:
            s2 = l2.strip()
            if s2.startswith("#"):
                lv2 = len(s2) - len(s2.lstrip("#"))
                if lv2 <= level:
                    break
            body.append(l2)
        return body
    return None


def _line_of(tag: str) -> str:
    t = tag.strip().lower()
    for k, v in LINE_ALIAS.items():
        if k in t:
            return v
    return "other"


def _schedule(parts: list[str]) -> tuple[str, list[int], str]:
    """返回 (kind, weekdays, date_str)。"""
    if len(parts) < 4 or not parts[3].strip():
        return "daily", [], ""
    s = parts[3].strip()
    m = re.search(r"每?周\s*([0-9,，\s]+)", s)
    if m:
        wd = [int(x) for x in re.findall(r"[1-7]", m.group(1))]
        if wd:
            return "weekly", sorted(set(wd)), ""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return "once", [], d.isoformat()
        except Exception:
            pass
    return "daily", [], ""


def _num(s: str, default: float = 0.0) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", s or "")
    return float(m.group(1)) if m else default


def _step_of(note: str) -> int:
    """备注里写「每日 +4」→ 每次勾完课次往后推 4。"""
    m = STEP_RE.search(note or "")
    return int(m.group(1)) if m else 0


def _deadline_of(note: str) -> str:
    """备注里写「截止 11-04」/「11/04 前」→ 板块截止日。"""
    m = DEADLINE_RE.search(note or "")
    if not m:
        return ""
    if m.group(1):
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    mm = int(m.group(4) or m.group(6))
    dd = int(m.group(5) or m.group(7))
    y = st.today().year
    try:
        d = date(y, mm, dd)
    except ValueError:
        return ""
    if (st.today() - d).days > 180:      # 跨年（12 月里写的 1 月）
        d = date(y + 1, mm, dd)
    return d.isoformat()


def _total_of(note: str) -> int:
    m = TOTAL_RE.search(note or "")
    return int(m.group(1)) if m else 0


def _start_of(sched: str) -> str:
    """排期里写「从 10-29」→ 这条任务从哪天开始出现（之前的日期不显示）。"""
    m = START_RE.search(sched or "")
    if not m:
        return ""
    y = int(m.group(1)) if m.group(1) else st.today().year
    try:
        return date(y, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


# ---------------------------------------------------------------- 解析
def parse(text: str) -> dict:
    """解析进度.md，返回 {ok, source, items, updated, week, note}。"""
    out = {"ok": False, "source": "", "items": [], "updated": None,
           "week": None, "note": ""}
    if not text.strip():
        out["note"] = "进度.md 不存在或为空"
        return out

    m = UPDATED_RE.search(text)
    if m:
        try:
            out["updated"] = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass
    m = re.search(r"当前周\**\s*[:：]?\s*\**\s*W\s*(\d+)", text)
    if m:
        out["week"] = int(m.group(1))

    lines = text.splitlines()
    body = _section(lines)
    if body:
        items = []
        for ln in body:
            mm = ITEM_RE.match(ln)
            if not mm:
                continue
            key_raw = mm.group("key").strip()
            key, _, sub = key_raw.partition("#")
            line = _line_of(key)
            pkey = line + ("#" + sub.strip() if sub.strip() else "")
            parts = [p.strip() for p in mm.group("rest").split("|")]
            title = parts[0]
            if not title:
                continue
            kind, wd, once_day = _schedule(parts)
            note = parts[2] if len(parts) > 2 else ""
            items.append({
                "pkey": pkey, "line": line, "title": title,
                "est_hours": _num(parts[1]) if len(parts) > 1 else 0.0,
                "note": note,
                "step": _step_of(note),
                "deadline": _deadline_of(note),
                "total_units": _total_of(note),
                "start": _start_of(parts[3] if len(parts) > 3 else ""),
                "kind": kind, "weekdays": wd, "date": once_day,
            })
        if items:
            out.update(ok=True, source="block", items=items)
            return out

    # 退化：查缺补漏里未勾选的条目
    items = []
    for i, ln in enumerate(lines):
        mm = TODO_RE.match(ln)
        if not mm:
            continue
        rest = mm.group("rest").strip()
        bm = BOLD_RE.match(rest)
        line = _line_of(bm.group("tag")) if bm else "other"
        title = rest.replace("**", "").strip()
        title = re.sub(r"\s+", " ", title)
        if len(title) > 60:
            title = title[:59] + "…"
        items.append({
            "pkey": f"todo#{i}", "line": line, "title": title,
            "est_hours": 0.8, "note": "来自进度.md · 查缺补漏", "step": 0,
            "kind": "daily", "weekdays": [], "date": "",
        })
    if items:
        out.update(ok=True, source="todo", items=items,
                   note="未找到「当前执行」小节，按查缺补漏清单生成")
    else:
        out["note"] = "进度.md 里既没有「当前执行」小节，也没有未勾选的待补条目"
    return out


def parse_file(path: str | os.PathLike) -> dict:
    p = Path(path) if str(path or "").strip() else (st.paths.data_dir() / "任务列表.md")
    if not p.exists():
        ensure_file(p)          # 首次运行自动生成模板
    r = parse(read_doc(p))
    try:
        r["mtime"] = p.stat().st_mtime
    except Exception:
        r["mtime"] = 0.0
    return r


def stale_days(parsed: dict, day: date | None = None) -> int:
    """进度.md 多久没更新（天）；无日期信息返回 -1。"""
    d = parsed.get("updated")
    if not d:
        return -1
    return max(0, ((day or st.today()) - d).days)


# ---------------------------------------------------------------- 同步
def sync(store: "st.Store", path: str | os.PathLike | None = None,
         day: date | None = None) -> dict:
    """按进度.md 重建任务：更新同名 pkey、删掉消失的、清掉旧静态种子。"""
    day = day or st.today()
    p = Path(path or store.settings.get("progress_md") or "")
    parsed = parse_file(p)
    rep = {"ok": False, "msg": parsed.get("note") or "", "added": 0, "updated": 0,
           "removed": 0, "archived": 0, "total": 0, "source": parsed.get("source"),
           "parsed": parsed}
    if not parsed.get("ok"):
        rep["msg"] = rep["msg"] or "解析失败"
        return rep

    ds = st.iso(day)
    alive = [t for t in store.tasks
             if t.get("src") == "progress" and not t.get("archived")]
    by_key = {t.get("pkey"): t for t in alive}
    seen = set()
    need_write = []

    for it in parsed["items"]:
        pkey = it["pkey"]
        seen.add(pkey)
        t = by_key.get(pkey)
        if t is None:
            nt = st.new_task(it["title"], it["line"], it["kind"], day,
                             it["weekdays"], it["est_hours"], it["note"],
                             src="progress", pkey=pkey)
            if it["kind"] == "once" and it.get("date"):
                nt["date"] = it["date"]
            nt["step"] = int(it.get("step") or 0)
            nt["deadline"] = it.get("deadline") or ""
            nt["total_units"] = int(it.get("total_units") or 0)
            if it.get("start"):
                nt["date"] = it["start"]
            store.tasks.append(nt)
            rep["added"] += 1
            continue
        changed = False
        if t.get("doc_dirty"):
            # 程序已经推进过、但上次没写回文档：以 json 为准，稍后重写文档
            need_write.append(t)
        elif t.get("title") != it["title"]:
            # 换内容了（文档是准）：今天那条完成标记作废，让它重新回到待办
            dd = t.get("done_dates") or []
            if ds in dd:
                dd.remove(ds)
            t["title"] = it["title"]
            t["next_title"] = ""      # 文档改了就丢掉没生效的推进
            changed = True
        if int(t.get("step") or 0) != int(it.get("step") or 0):
            t["step"] = int(it.get("step") or 0)
            changed = True
        for k in ("deadline", "total_units"):
            if t.get(k) != it[k]:
                t[k] = it[k]
                changed = True
        for k in ("note", "est_hours", "line", "kind"):
            if t.get(k) != it[k]:
                t[k] = it[k]
                changed = True
        if list(t.get("weekdays") or []) != list(it["weekdays"]):
            t["weekdays"] = list(it["weekdays"])
            changed = True
        if it["kind"] == "once" and it.get("date") and t.get("date") != it["date"]:
            t["date"] = it["date"]
            changed = True
        if it.get("start") and t.get("date") != it["start"]:
            t["date"] = it["start"]
            changed = True
        if changed:
            t["updated_at"] = st.now_iso()
            rep["updated"] += 1

    # 进度.md 里已经没有的（以及旧的静态种子）
    for t in list(store.tasks):
        if t.get("archived"):
            continue
        src = t.get("src") or "user"
        if src == "progress" and t.get("pkey") in seen:
            continue
        if src == "user":
            continue
        if t.get("done_dates") or float(t.get("spent_hours") or 0) > 0:
            t["archived"] = True          # 留档，周统计还能算到
            rep["archived"] += 1
        else:
            store.tasks.remove(t)
            rep["removed"] += 1

    store.settings["progress_sync_at"] = st.now_iso()
    store.settings["progress_mtime"] = float(parsed.get("mtime") or 0.0)
    store.settings["progress_updated"] = (parsed.get("updated") or "").isoformat() \
        if parsed.get("updated") else ""
    store.save()
    if need_write:
        if write_back(store, [(t.get("pkey") or "", t.get("title") or "")
                              for t in need_write], day, str(p)):
            for t in need_write:
                t["doc_dirty"] = False
            store.save()
    rep.update(ok=True, total=len(parsed["items"]),
               msg=f"{parsed['source']} · {len(parsed['items'])} 条")
    return rep


def changed_on_disk(store: "st.Store", path: str | os.PathLike | None = None) -> bool:
    """进度.md 是否比上次同步时更新了。"""
    p = Path(path or store.settings.get("progress_md") or "")
    try:
        m = p.stat().st_mtime
    except Exception:
        return False
    old = float(store.settings.get("progress_mtime") or 0.0)
    return m > old + 1.0


def _replace_title(text: str, pkey: str, new_title: str) -> str:
    """把 `- [pkey] 标题 | ...` 这一行的标题换掉，其余字段原样保留。"""
    lines = text.splitlines(True)
    for i, ln in enumerate(lines):
        body = ln.rstrip("\r\n")
        m = re.match(r"^(\s*[-*]\s*\[)([^\]]+)\]\s*(.*)$", body)
        if not m or m.group(2).strip() != pkey:
            continue
        parts = m.group(3).split("|")
        rest = " | ".join(x.strip() for x in parts[1:])
        line = m.group(1) + m.group(2) + "] " + new_title.strip()
        if rest:
            line += " | " + rest
        lines[i] = line + ln[len(body):]
        return "".join(lines)
    return text


def write_back(store: "st.Store", edits: list[tuple[str, str]],
               day: date | None = None, path: str | None = None) -> bool:
    """把 [(pkey, 新标题)] 写回进度.md，并顺手把「最近更新」改成今天。"""
    if not edits:
        return False
    p = Path(path or store.settings.get("progress_md") or "")
    if not p.exists():
        return False
    text = read_doc(p)
    for pkey, title in edits:
        text = _replace_title(text, pkey, title)
    if day:
        text = UPDATED_RE.sub(f"最近更新**：{day.isoformat()}", text, count=1)
    try:
        p.write_text(text, encoding="utf-8")
    except Exception:
        return False
    try:
        store.settings["progress_mtime"] = p.stat().st_mtime
    except Exception:
        pass
    return True


def apply_advance(store: "st.Store", day: date | None = None,
                  path: str | None = None) -> int:
    """次日生效：把昨天勾完算好的 next_title 换上，并写回进度.md。"""
    day = day or st.today()
    ds = st.iso(day)
    todo = []
    for t in store.tasks:
        if t.get("src") != "progress" or t.get("archived"):
            continue
        nt = (t.get("next_title") or "").strip()
        if not nt or nt == t.get("title"):
            continue
        if max(t.get("done_dates") or [""]) >= ds:   # 今天刚勾完 → 明天才换
            continue
        todo.append(t)
    if not todo:
        return 0
    edits = []
    for t in todo:
        nt = t["next_title"].strip()
        edits.append((t.get("pkey") or "", nt))
        t["prev_title"] = t.get("title") or ""      # 回退用
        t["advance_on"] = max(t.get("done_dates") or [""])
        t["title"] = nt
        t["next_title"] = ""
        t["updated_at"] = st.now_iso()
    ok = write_back(store, edits, day, path)
    for t in todo:
        t["doc_dirty"] = not ok
    store.save()
    return len(todo)


def flush_dirty(store: "st.Store", day: date | None = None,
                path: str | None = None) -> int:
    """把还没写回文档的推进补上（进度.md 被 WPS 占着时用）。"""
    todo = [t for t in store.tasks if t.get("doc_dirty")]
    if not todo:
        return 0
    ok = write_back(store, [(t.get("pkey") or "", t.get("title") or "")
                            for t in todo], day or st.today(), path)
    if not ok:
        return 0
    for t in todo:
        t["doc_dirty"] = False
    store.save()
    return len(todo)


TEMPLATE = """# 我的任务列表（StudyNudge 提示器读取这个文件）

> 改这里 = 改提示器里的任务，保存后 1 分钟内自动生效（不用重启）。
> 格式：- [学科(#子项)] 标题 | 小时 | 备注 | 排期
>   学科：math / graphics / english / reading / other，也可写中文
>   排期：不写 = 每天；每周 3,6 = 周三周六；一次 2026-11-02 = 一次性；从 10-29 = 那天才开始
>   备注里写「每天 4 讲」→ 勾完这条，次日课次自动 +4（L13–L16 → L17–L20）
>   备注里写「共 46 讲 · 截止 11-04」→ 本周俯瞰会显示板块截止日
> 删掉一行 = 删掉该任务；标题改了 = 换了个东西要做

## 0.5 当前执行（StudyNudge 读取）

- [math] 数学 · 高数 第 1 章 | 1.5 | 每天 1 节 · 共 12 章 · 截止 2026-12-31
- [graphics] 图形学 · GAMES101 L1–L2 看课 | 1.0 | 每天 2 讲
- [english] 英语 · 背单词 20 分钟 | 0.3 | 每天 20 个新词 + 复习
- [reading] 阅读 · 教材 1 章 | 1.0 | 读完写 3 句摘要 | 每周 3,6
- [other#review] 周复盘 30 分钟 | 0.5 | 学了什么 / 卡在哪 / 下周调整 | 每周 7
"""


def ensure_file(path) -> bool:
    """任务文件不存在时生成一份模板，返回是否新建。"""
    p = str(path or "").strip() or os.path.join(str(st.paths.data_dir()), "任务列表.md")
    if os.path.exists(p):
        return False
    try:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
        return True
    except Exception:
        return False


def status_text(store: "st.Store", day: date | None = None) -> tuple[str, str]:
    """返回 (文字, 颜色键)，颜色键 ∈ ok/dim/warn。"""
    day = day or st.today()
    p = Path(store.settings.get("progress_md") or "")
    if not p.exists():
        return "缺失", "warn"
    parsed = parse_file(p)
    n = stale_days(parsed, day)
    if not parsed.get("ok"):
        return "无任务", "warn"
    if n < 0:
        return f"{len(parsed['items'])}条", "dim"
    if n == 0:
        return "今天", "ok"
    if n >= 7:
        return f"{n}天未更新", "warn"
    if n >= 4:
        return f"{n}天前", "dim"
    return f"{n}天前", "ok"
