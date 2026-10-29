# -*- coding: utf-8 -*-
"""路径：源码运行 / PyInstaller 打包后都指向 exe 所在目录。"""
import sys
from pathlib import Path


def app_dir() -> Path:
    """数据目录的父目录：打包后 = exe 所在文件夹；源码运行 = 本文件目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def res_dir() -> Path:
    """只读资源（图标等）：打包后位于 _internal，源码运行 = 本文件目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    d = app_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
