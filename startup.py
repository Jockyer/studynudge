# -*- coding: utf-8 -*-
"""开机自启动：在用户启动文件夹里放一个指向 pythonw.exe 的快捷方式。

优先创建 .lnk（无控制台闪窗）；若 WScript.Shell COM 不可用，退回 .bat。
关闭自启动 = 删除这两者。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ENTRY = APP_DIR / "main.py"
FROZEN = bool(getattr(sys, "frozen", False))
LNK_NAME = "StudyNudge.lnk"
BAT_NAME = "StudyNudge.bat"


def startup_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def lnk_path() -> Path:
    return startup_dir() / LNK_NAME


def bat_path() -> Path:
    return startup_dir() / BAT_NAME


def pythonw() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        return str(exe)
    cand = exe.with_name("pythonw.exe")
    return str(cand if cand.exists() else exe)


def is_enabled() -> bool:
    return lnk_path().exists() or bat_path().exists()


def _run_hidden(args: list[str]) -> subprocess.CompletedProcess:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(args, capture_output=True, text=True, creationflags=flags, timeout=30)


def _make_lnk() -> bool:
    if FROZEN:
        target, args, workdir = str(Path(sys.executable).resolve()), "", str(Path(sys.executable).resolve().parent)
    else:
        target, args, workdir = pythonw(), f'"{ENTRY}"', str(APP_DIR)
    ps = (
        "$W = New-Object -ComObject WScript.Shell; "
        f"$s = $W.CreateShortcut('{lnk_path()}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{args}'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        f"$s.IconLocation = '{target},0'; "
        "$s.Description = 'StudyNudge 学习任务提示器'; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    try:
        r = _run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
        return lnk_path().exists() or r.returncode == 0
    except Exception:
        return False


def _make_bat() -> bool:
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
        content = (
            "@echo off\r\n"
            + (f'start "" "{Path(sys.executable).resolve()}"\r\n' if FROZEN
               else f'start "" "{pythonw()}" "{ENTRY}"\r\n')
        )
        bat_path().write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


def enable() -> tuple[bool, str]:
    """返回 (是否成功, 说明)。"""
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, f"启动文件夹不可写：{e}"
    if _make_lnk() and lnk_path().exists():
        return True, f"已创建快捷方式：{lnk_path()}"
    if _make_bat():
        return True, f"已创建启动脚本（无 .lnk 权限）：{bat_path()}"
    return False, "创建自启动项失败"


def disable() -> tuple[bool, str]:
    removed = []
    for p in (lnk_path(), bat_path()):
        try:
            if p.exists():
                p.unlink()
                removed.append(p.name)
        except Exception:
            pass
    return (True, "已移除：" + "、".join(removed)) if removed else (True, "本来就没有自启动项")


if __name__ == "__main__":
    print("startup dir:", startup_dir())
    print("pythonw   :", pythonw())
    print("entry     :", ENTRY, ENTRY.exists())
    print("enabled   :", is_enabled())
