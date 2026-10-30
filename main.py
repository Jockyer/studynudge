# -*- coding: utf-8 -*-
"""StudyNudge 入口：高 DPI、单实例、异常日志、自检。"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import traceback
from datetime import timedelta as _td
from pathlib import Path

BASE = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):        # 打包后：exe 所在目录（data/ 就放在它旁边）
    BASE = Path(sys.executable).resolve().parent
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

PORT = 47821
LOG = BASE / "data" / "studynudge.log"


def set_dpi_aware() -> None:
    try:
        import ctypes
        # 让任务栏/托盘用我们自己的图标，而不是 pythonw.exe 的
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Jockyer.StudyNudge")
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except Exception:
        pass


def acquire_single_instance() -> socket.socket | None:
    """拿不到端口说明已有实例在跑，通知它展开窗口后退出。

    刚被强杀时端口可能还在 TIME_WAIT，bind 会失败但连不上——
    这种情况要重试几次，不能当成“已有实例”直接退出。
    """
    deadline = time.time() + 6.0
    while True:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            srv.bind(("127.0.0.1", PORT))
            srv.listen(4)
            return srv
        except OSError:
            try:
                srv.close()
            except Exception:
                pass
            try:
                c = socket.create_connection(("127.0.0.1", PORT), timeout=1.0)
                c.sendall(b"SHOW\n")
                c.close()
                return None
            except Exception:
                pass
            if time.time() > deadline:
                return None
            time.sleep(0.4)


def serve(srv: socket.socket, on_show) -> None:
    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return
            try:
                conn.settimeout(1.0)
                data = conn.recv(64)
                if data.strip().upper() == b"SHOW":
                    on_show()
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    threading.Thread(target=loop, daemon=True).start()


def selftest(data_path: Path) -> int:
    """无交互自检：建窗口 → 渲染 → 勾选 → 折叠/展开 → 提醒 → 各对话框。"""
    import tkinter as tk
    import store as st
    import ui

    # 打包成 exe（--windowed）时没有 stdout，顺手写一份日志便于排查
    try:
        import paths as _paths
        _lf = open(_paths.data_dir() / "selftest.log", "w", encoding="utf-8")
        _out = sys.__stdout__

        class _Tee:
            def write(self, s):
                try:
                    if _out:
                        _out.write(s)
                except Exception:
                    pass
                try:
                    _lf.write(s)
                    _lf.flush()
                except Exception:
                    pass

            def flush(self):
                try:
                    _lf.flush()
                except Exception:
                    pass

        sys.stdout = _Tee()
    except Exception:
        pass

    fails = []
    os.environ["STUDYNUDGE_NOTRAY"] = "1"   # 托盘单独测，别跟着主窗口走
    root = tk.Tk()
    store = st.Store(data_path)
    app = ui.App(root, store)
    try:
        root.update()
        tdy = st.today()
        pend = st.pending(tdy, store.tasks)
        print(f"[ok] 今日任务 {len(pend)} 项，逾期 {sum(1 for t in pend if st.overdue_days(t) > 0)} 项")
        if pend:
            before = len(st.pending(tdy, store.tasks))
            app.toggle(pend[0])
            root.update()
            after = len(st.pending(tdy, store.tasks))
            if after != before - 1:
                fails.append(f"勾选后待办数未减少：{before} -> {after}")
            else:
                print("[ok] 勾选完成 → 未完成列表减少")
            app.toggle(store.tasks[0]) if store.tasks else None
            root.update()
        # 新增 / 删除
        t = st.new_task("自检任务", "math", "once", tdy, [], 1.5)
        store.add(t)
        app.refresh()
        root.update()
        store.remove(t["id"])
        app.refresh()
        root.update()
        print("[ok] 新增/删除任务")
        # 延期
        t2 = st.new_task("自检逾期", "graphics", "once", tdy - __import__("datetime").timedelta(days=2), [], 2)
        store.add(t2)
        if st.overdue_days(t2) != 2:
            fails.append("逾期天数计算错误")
        st.defer_task(t2)
        if t2["date"] != st.iso(tdy + _td(days=1)) or st.overdue_days(t2) != 0 \
                or t2["defer_count"] != 1:
            fails.append("延期逻辑错误")
        else:
            print("[ok] 逾期判定 + 延期")
        store.remove(t2["id"])
        # 折叠 / 展开
        app.collapse()
        root.update()
        app.expand()
        root.update()
        print("[ok] 收起 / 展开")
        # 明日按钮文字随视图变
        app.toggle_view()
        root.update()
        if app.btn_day._get() != "今日":
            fails.append(f"看明日时按钮应显示「今日」，实际 {app.btn_day._get()}")
        app.toggle_view()
        root.update()
        if app.btn_day._get() != "明日":
            fails.append(f"看今日时按钮应显示「明日」，实际 {app.btn_day._get()}")
        else:
            print("[ok] 今日/明日 按钮文字随视图切换")
        # 本周俯瞰
        app.week_dialog()
        root.update()
        print("[ok] 本周俯瞰面板")
        # 提醒
        _pend = st.pending(tdy, store.tasks)
        app.remind(_pend)
        root.update()
        print("[ok] 提醒（横幅 + 闪烁）")
        # 全屏勿扰：检测到全屏时不弹、返回 False（不计入提醒间隔）
        _orig = ui.wp.fullscreen_window
        ui.wp.fullscreen_window = lambda *a, **k: (1, "全屏测试")
        try:
            app.s["dnd_fullscreen"] = True
            r = app.remind(_pend)
            if r is False:
                print("[ok] 全屏勿扰：游戏/视频全屏时跳过提醒")
            else:
                fails.append("全屏时没跳过提醒")
        finally:
            ui.wp.fullscreen_window = _orig
        # 总览
        app.show_overview()
        root.update()
        print("[ok] 今日总览面板")
        # 设置 / 统计 / 编辑
        app.settings_dialog()
        root.update()
        app.stats_dialog()
        root.update()
        app.edit_task(None)
        root.update()
        print("[ok] 设置 / 统计 / 新增对话框可构建")
        # 导出
        ok, m = st.export_week_csv(data_path.parent / "exports" / "t.csv", store.tasks, tdy), "csv"
        print(f"[ok] CSV 导出 {ok}")
        txt = st.progress_text(tdy, store.tasks)
        if "W" not in txt:
            fails.append("进度文本异常")
        # 完整任务清单
        titles = {t["title"] for t in store.tasks}
        lines = {t["line"] for t in store.tasks}
        missing = [k for k in ("math", "graphics", "english", "reading") if k not in lines]
        if missing:
            fails.append(f"任务清单缺学科线：{missing}（现有 {sorted(titles)}）")
        else:
            print(f"[ok] 任务清单 {len(store.tasks)} 条，覆盖数学/图形学/英语/阅读")
        # 进度.md 联动
        import progress as pr
        pp = pr.parse_file(store.settings.get("progress_md"))
        if not pp.get("ok"):
            fails.append("进度.md 解析失败：" + str(pp.get("note")))
        else:
            print(f"[ok] 进度.md 解析 {len(pp['items'])} 条（{pp['source']}），{pr.stale_days(pp)} 天前更新")
            rep = pr.sync(store, store.settings.get("progress_md"), tdy)
            if not rep.get("ok"):
                fails.append("进度同步失败：" + str(rep.get("msg")))
            else:
                print(f"[ok] 进度同步 {rep['total']} 条（新增 {rep['added']} / 更新 {rep['updated']} / 移除 {rep['removed']}）")
                titles = [t["title"] for t in store.tasks if t.get("src") == "progress"]
                want = [it["title"] for it in pp["items"]]
                missing = [w for w in want if w not in titles]
                if missing:
                    fails.append(f"同步后缺任务：{missing[:3]}")
                if any(t.get("src") == "seed" and not t.get("archived")
                       for t in store.tasks):
                    fails.append("同步后仍残留内置静态任务")
        # 改标题 = 换内容：完成标记要作废
        tmpmd = data_path.parent / "_selftest_progress.md"
        try:
            tmpmd.write_text("## 0.5 当前执行\n- [math] 测试A | 1.0 | x\n", encoding="utf-8")
            pr.sync(store, tmpmd, tdy)
            mt = [t for t in store.tasks if t.get("src") == "progress"
                  and t.get("pkey") == "math"]
            if not mt or mt[0]["title"] != "测试A":
                fails.append("进度同步未写入测试任务")
            else:
                st.set_done(mt[0], tdy, True)
                tmpmd.write_text("## 0.5 当前执行\n- [math] 测试B | 1.2 | y\n",
                                 encoding="utf-8")
                pr.sync(store, tmpmd, tdy)
                if st.completed_on(mt[0], tdy):
                    fails.append("进度改标题后今天的完成标记没清掉")
                elif mt[0]["title"] != "测试B" or abs(float(mt[0]["est_hours"]) - 1.2) > 1e-6:
                    fails.append("进度同步未更新标题/小时")
                else:
                    print("[ok] 进度改标题 → 任务跟着换，完成标记作废")
            # 退化：没有「当前执行」小节时读待补清单
            if pr.parse("").get("ok"):
                fails.append("空文档不该解析成功")
            lg = pr.parse("## 4. 查缺补漏\n\n- [ ] **图形学**：看 L15–L16 → 作业 7\n"
                          "- [ ] **数学**：接着走\n")
            if not lg.get("ok") or lg["source"] != "todo" or len(lg["items"]) != 2 \
                    or lg["items"][0]["line"] != "graphics":
                fails.append(f"待补清单退化解析失败：{lg}")
            else:
                print("[ok] 无「当前执行」小节时退化读待补清单")
            # 课次自动推进：勾完 → 次日 +N，并回写文档
            tmpmd.write_text("## 0.5 当前执行\n- [math] 测试 L1–L4 看课 | 1.0 | 每天 4 讲\n",
                             encoding="utf-8")
            pr.sync(store, tmpmd, tdy)
            mt2 = [t for t in store.tasks if t.get("src") == "progress"
                   and t.get("pkey") == "math"]
            if not mt2 or int(mt2[0].get("step") or 0) != 4:
                fails.append(f"「每天 4 讲」没解析成步进：{mt2 and mt2[0].get('step')}")
            else:
                mt2 = mt2[0]
                st.set_done(mt2, tdy, True)
                if mt2.get("next_title") != "测试 L5–L8 看课":
                    fails.append(f"勾完算出的下一条标题不对：{mt2.get('next_title')}")
                elif pr.apply_advance(store, tdy, str(tmpmd)) != 0:
                    fails.append("当天勾完不该立刻换标题")
                else:
                    n = pr.apply_advance(store, tdy + _td(days=1), str(tmpmd))
                    if n != 1 or "L5–L8" not in mt2["title"]:
                        fails.append(f"次日没推进：{n} / {mt2['title']}")
                    elif "L5–L8" not in tmpmd.read_text(encoding="utf-8"):
                        fails.append("推进后没回写进度.md")
                    else:
                        print("[ok] 勾完 → 次日课次自动 +4，并回写进度.md")
            # 回退：取消完成 → 标题退回上一档
            if isinstance(mt2, dict) and "L5–L8" in (mt2.get("title") or ""):
                if not mt2.get("prev_title"):
                    fails.append("推进后没记住上一档标题，无法回退")
                else:
                    st.set_done(mt2, tdy, False)
                    if "L1–L4" not in mt2["title"]:
                        fails.append(f"取消完成后标题没退回：{mt2['title']}")
                    elif not mt2.get("doc_dirty"):
                        fails.append("回退后没标记需要写回进度.md")
                    else:
                        print("[ok] 取消完成 → 课次退回上一档，并标记回写进度.md")

        # 延期：当天消失 → 次日置顶 + 标记
            dft = st.new_task("延期测试", "math", "daily", tdy, [], 0.5, "", src="user")
            store.tasks.append(dft)
            st.defer_task(dft, 1, tdy)
            tmr = tdy + _td(days=1)
            if st.active_on(dft, tdy):
                fails.append("延期后当天不该还在待办里")
            elif not (st.is_deferred(dft, tmr) and st.active_on(dft, tmr)):
                fails.append("延期的任务次日没出现/没标记")
            else:
                pl = st.pending(tmr, store.tasks)
                if not pl or pl[0].get("id") != dft["id"]:
                    fails.append("延期的任务次日没排到最前")
                else:
                    print("[ok] 延期：当天隐藏，次日置顶并标为延期")
            store.tasks.remove(dft)
            for t in store.tasks:            # 自检造的 doc_dirty 不能带去写真实进度.md
                t["doc_dirty"] = False
            pr.sync(store, store.settings.get("progress_md"), tdy)   # 恢复真实进度
        finally:
            try:
                tmpmd.unlink()
            except Exception:
                pass
        # 托盘（真启一次）
        try:
            import tray as tray_mod
            if not tray_mod.AVAILABLE:
                fails.append("pystray 不可用：" + tray_mod.IMPORT_ERR)
            else:
                tr = tray_mod.Tray(app)
                if not tr.start():
                    fails.append("托盘启动失败")
                else:
                    time.sleep(1.2)
                    for _ in range(20):        # pystray 的 visible 是异步置位的，轮询一下
                        if tr.visible:
                            break
                        time.sleep(0.15)
                    if not tr.visible:
                        fails.append("托盘图标不可见（pystray 内部状态为 False）")
                    tr.update(1, 5, 0, "StudyNudge 自检")
                    time.sleep(0.4)
                    tr.notify("自检通知", "StudyNudge")
                    time.sleep(0.3)
                    tr.stop()
                    print("[ok] 系统托盘图标 启动 / 可见 / 更新 / 通知 / 退出")
        except Exception:
            fails.append(traceback.format_exc())
    except Exception:
        fails.append(traceback.format_exc())
    finally:
        try:
            for w in list(root.winfo_children()):
                try:
                    w.destroy()
                except Exception:
                    pass
            root.destroy()
        except Exception:
            pass

        # 数据迁移：旧版（无 version 字段）→ 当前版本
        mig = data_path.parent / "_selftest_v1.json"
        bak = data_path.parent / "_selftest_v1.json.v1.bak"
        for f in (mig, bak):
            try:
                f.unlink()
            except Exception:
                pass
        mig.write_text(json.dumps({
            "tasks": [{"id": "t_old1", "title": "旧任务", "line": "math",
                        "kind": "daily", "weekdays": [], "est_hours": 1.0}],
            "settings": {"remind_interval_min": 45},
        }, ensure_ascii=False), encoding="utf-8")
        s_old = st.Store(mig)
        t_old = s_old.tasks[0] if s_old.tasks else {}
        if s_old.data.get("version") != st.DATA_VERSION:
            fails.append(f"迁移后 version 不对：{s_old.data.get('version')}")
        elif not getattr(s_old, "migrated", None):
            fails.append("迁移没有留下说明")
        elif any(k not in t_old for k in ("src", "pkey", "step", "timer_start")):
            fails.append(f"迁移后任务字段缺失：{t_old}")
        elif "subjects" not in s_old.settings:
            fails.append("迁移后设置缺 subjects")
        else:
            print(f"[ok] 数据迁移 v1 → v{st.DATA_VERSION}，旧文件已备份")
        for f in (mig, bak):
            try:
                f.unlink()
            except Exception:
                pass
    if fails:
        print("\n[FAIL]")
        for f in fails:
            print(" -", f)
        return 1

    print("\n[PASS] 自检全部通过")
    return 0


def main() -> int:
    set_dpi_aware()
    data_path = Path(os.environ.get("STUDYNUDGE_DATA") or (BASE / "data" / "study_tasks.json"))

    if "--selftest" in sys.argv:
        return selftest(data_path)

    srv = acquire_single_instance()
    if srv is None:
        return 0

    try:
        import tkinter as tk
        import store as st
        import ui

        root = tk.Tk()
        store = st.Store(data_path)
        app = ui.App(root, store)
        serve(srv, lambda: root.after(0, app.expand))
        root.mainloop()
        return 0
    except Exception:
        log(traceback.format_exc())
        raise


if __name__ == "__main__":
    sys.exit(main())
