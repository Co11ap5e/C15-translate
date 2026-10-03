#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地翻译 · 启动器

为什么 exe 只是个启动器，而不是把整个程序冻进去：

pywebview 在 Windows 上靠 pythonnet 调用 .NET 的 WinForms。pythonnet 在
PyInstaller 冻出来的进程里加载不了随包的 Python.Runtime.dll —— clr_loader 的
ClrLoader 解析 Python.Runtime.Loader.Initialize 会失败（改成 CoreCLR 能加载，
但 pywebview 带的 WebView2 .NET 控件是给 .NET Framework 编的，.NET 8 里没有
System.Windows.Forms.ContextMenu，照样起不来）。这两条路都试过，都走不通。

所以这个 exe 只干一件事：找到本机的 Python，用它跑 app.py。
这样用的就是源码版那一套，不需要额外装 .NET，功能一个不少。

构建：python -m PyInstaller --onefile --noconsole --name 本地翻译 launcher.py
"""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
# 源码可能放在 exe 旁边、上一级，或者原来的仓库目录
APP_CANDIDATES = [
    os.path.join(HERE, "app.py"),
    os.path.join(os.path.dirname(HERE), "app.py"),
    r"D:\DSH\local-translate\app.py",
]


def find_app():
    for path in APP_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


def find_pythonw():
    # 0. 随包带的运行时（便携版就靠这个，不碰系统里的 Python）
    for sub in ("runtime", os.path.join("runtime", "")):
        cand = os.path.join(HERE, sub, "pythonw.exe")
        if os.path.isfile(cand):
            return cand
    cand = os.path.join(os.path.dirname(HERE), "runtime", "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    # 1. PATH 里的 pythonw / python
    for name in ("pythonw", "python"):
        exe = shutil.which(name)
        if exe:
            d, f = os.path.split(exe)
            if name == "python":
                cand = os.path.join(d, "pythonw.exe")
                if os.path.isfile(cand):
                    return cand
            return exe
    # 2. 常见安装位置
    patterns = [
        r"C:\Program Files\Python3*\pythonw.exe",
        r"C:\Program Files (x86)\Python3*\pythonw.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python3*", "pythonw.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WindowsApps", "pythonw.exe"),
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    # 3. py 启动器
    for name in ("pyw.exe", "py.exe"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def complain(msg):
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "本地翻译", 0x30)
    except Exception:
        pass


def main():
    app = find_app()
    if not app:
        complain("找不到 app.py。\n\n请把这个 exe 放到本地翻译的目录里再运行。")
        return 1

    python = find_pythonw()
    if not python:
        complain(
            "没有找到 Python。\n\n"
            "这个 exe 只是启动器，界面本身要用 Python 跑。\n"
            "装一个 Python 3.11 以上（安装时勾选 Add to PATH），"
            "再在翻译目录里执行：\n\n    pip install -r requirements.txt"
        )
        return 1

    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([python, app], cwd=os.path.dirname(app), creationflags=flags, close_fds=True)
    except Exception as e:
        complain("启动失败：%s" % e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
