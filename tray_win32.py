#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""直接调 Win32 的托盘图标，不依赖 pystray。

pystray 在某些环境里建得起来却看不见，也不告诉你为什么。这里自己调
Shell_NotifyIcon，并且把返回值写进 tray.log，出问题能直接看到是哪一步失败。
"""
import ctypes
import os
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_DESTROY = 0x0002
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111
WM_NULL = 0x0000

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
IDI_APPLICATION = 32512
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
    ]


user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.restype = LRESULT
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class Tray:
    def __init__(self, icon_path=None, tooltip="本地翻译", items=None, log_path=None):
        self.icon_path = icon_path
        self.tooltip = tooltip
        self.items = items or []       # [(label, callable)] 或 "sep"
        self.log_path = log_path
        self.hwnd = None
        self.hicon = None
        self._proc = None
        self._class_atom = None
        self._cmd = {}

    def log(self, msg):
        line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
        try:
            if self.log_path:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass
        print("[tray]", line, flush=True)

    def _load_icon(self):
        if self.icon_path and os.path.isfile(self.icon_path):
            h = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                return h
            self.log("LoadImage 失败，err=%s" % ctypes.get_last_error())
        return user32.LoadIconW(None, wintypes.LPCWSTR(IDI_APPLICATION))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            if lparam == WM_RBUTTONUP:
                self._popup(hwnd)
            elif lparam == WM_LBUTTONDBLCLK:
                self._fire(1)
            return 0
        if msg == WM_COMMAND:
            self._fire(wparam & 0xFFFF)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _fire(self, cmd):
        cb = self._cmd.get(cmd)
        if cb:
            try:
                cb()
            except Exception as e:
                self.log("菜单回调出错: %r" % e)

    def _popup(self, hwnd):
        menu = user32.CreatePopupMenu()
        self._cmd = {}
        cmd = 100
        for it in self.items:
            if it == "sep":
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            label, cb = it
            self._cmd[cmd] = cb
            user32.AppendMenuW(menu, MF_STRING, cmd, label)
            cmd += 1
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                              pt.x, pt.y, 0, hwnd, None)
        user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

    def add(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)
        cls = "LocalTranslateTray"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        atom = user32.RegisterClassExW(ctypes.byref(wc))
        self._class_atom = atom
        self.log("RegisterClassExW -> %s (err=%s)" % (atom, ctypes.get_last_error()))

        self.hwnd = user32.CreateWindowExW(0, cls, "本地翻译", 0, 0, 0, 0, 0, None, None, hinst, None)
        self.log("CreateWindowExW -> %s (err=%s)" % (self.hwnd, ctypes.get_last_error()))
        if not self.hwnd:
            # 换个办法：用隐藏的普通窗口
            self.hwnd = user32.CreateWindowExW(0, "STATIC", "本地翻译", 0, 0, 0, 0, 0, None, None, hinst, None)
            self.log("换成 STATIC 窗口 -> %s (err=%s)" % (self.hwnd, ctypes.get_last_error()))
        if not self.hwnd:
            return False

        self.hicon = self._load_icon()
        self.log("图标句柄 -> %s (err=%s)" % (self.hicon, ctypes.get_last_error()))

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip[:127]
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        err = ctypes.get_last_error()
        self.log("Shell_NotifyIconW(NIM_ADD) -> %s (err=%s)" % (ok, err))
        self._nid = nid
        return bool(ok)

    def run(self):
        if not self.add():
            raise RuntimeError("托盘注册失败，看 tray.log")
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        except Exception:
            pass
