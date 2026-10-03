#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地翻译 · 桌面版

把 server.py（翻译服务）、video-to-subtitle.py、image-translate.py 这些零散脚本
收进一个窗口里。界面用现有的网页技术写，外面套 pywebview 的原生窗口，
比起让用户去双击各个 bat，这里能用文件选择器、能看到进度、能常驻托盘。

运行：python app.py
打包：见 打包成exe.ps1
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import webview
except Exception as _e:      # 打包版要 .NET 桌面运行时，缺了就说明白
    if getattr(sys, "frozen", False):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "启动失败：这套界面需要 .NET 桌面运行时 8（Microsoft.WindowsDesktop.App）。\n\n"
                "装上它再打开；或者先用「打开翻译软件.bat」跑源码版，源码版不需要这个。\n\n"
                "详细信息：%s" % str(_e)[:300],
                "本地翻译", 0x10,
            )
        except Exception:
            pass
    raise
import pystray
from PIL import Image, ImageDraw

import live_captions
import summarize

BASE = os.path.dirname(os.path.abspath(__file__))
# 打包成 exe 之后，代码和资源在临时解包目录里，配置和历史要写在 exe 旁边
FROZEN = getattr(sys, "frozen", False)
RES = getattr(sys, "_MEIPASS", BASE) if FROZEN else BASE
WORK = os.path.dirname(sys.executable) if FROZEN else BASE
SERVICE = "http://127.0.0.1:18765"
CONFIG = os.path.join(WORK, "config.json")

_jobs = {}            # 任务 id -> {state, log, out, t0}
_job_seq = 0
_service_proc = None
_tray = None
_window = None
_overlay = None
_live = live_captions.LiveCaptions()
_sum = {"state": "idle", "text": "", "note": "", "err": "", "mode": "plot"}


def job_id():
    global _job_seq
    _job_seq += 1
    return str(_job_seq)


def http_json(path, payload=None, timeout=180):
    url = SERVICE + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


_image_py = None      # 图片翻译实际用哪个 Python，查一次就记住


def _has_module(py, mod):
    try:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        r = subprocess.run([py, "-c", "import %s" % mod], capture_output=True,
                           creationflags=flags, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def pick_image_python():
    """图片翻译要 manga-ocr，它带着 2.5 GB 的 torch，便携包不塞这个。
    自己这个解释器里没有就找本机装的 Python 顶上，都没有就用自己（会报错提示）。"""
    global _image_py
    if _image_py:
        return _image_py
    import shutil as _sh
    cands = [] if FROZEN else [sys.executable]
    for name in ("python", "python3"):
        p = _sh.which(name)
        if p:
            cands.append(p)
    cands.append(r"C:\Program Files\Python313\python.exe")
    for py in cands:
        if py and os.path.isfile(py) and _has_module(py, "manga_ocr"):
            _image_py = py
            return py
    return sys.executable


def service_alive():
    try:
        urllib.request.urlopen(SERVICE + "/health", timeout=3).read()
        return True
    except Exception:
        return False


def ensure_config():
    """打包后 exe 旁边没有 config.json 时，从内置的那份复制出来。"""
    if not FROZEN:
        return
    dst, src = os.path.join(WORK, "config.json"), os.path.join(RES, "config.json")
    if not os.path.exists(dst) and os.path.exists(src):
        try:
            import shutil
            shutil.copyfile(src, dst)
        except Exception:
            pass


def load_config():
    ensure_config()
    try:
        return json.load(open(CONFIG, encoding="utf-8"))
    except Exception:
        return {}


def save_config(patch):
    cfg = load_config()
    cfg.update(patch or {})
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return cfg


class Api:
    """暴露给网页前端调用的接口。前端用 window.pywebview.api.xxx() 调。"""

    # ── 服务 ──
    def service_status(self):
        return {"up": service_alive(), "url": SERVICE}

    def _start_service_inproc(self):
        """打包后没有独立的 python 解释器，服务只能在本进程里起一个线程。"""
        def run():
            try:
                import runpy
                runpy.run_path(os.path.join(RES, "server.py"), run_name="__main__")
            except Exception as e:
                print("[service]", e)
        threading.Thread(target=run, daemon=True).start()

    def start_service(self):
        global _service_proc
        if service_alive():
            return {"ok": True, "msg": "服务已在运行"}
        if FROZEN:
            self._start_service_inproc()
            for _ in range(40):
                time.sleep(0.5)
                if service_alive():
                    return {"ok": True, "msg": "服务已启动"}
            return {"ok": False, "msg": "启动超时"}
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-len("pythonw.exe")] + "python.exe"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        _service_proc = subprocess.Popen([exe, os.path.join(BASE, "server.py")],
                                         cwd=BASE, creationflags=flags,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            time.sleep(0.5)
            if service_alive():
                return {"ok": True, "msg": "服务已启动"}
        return {"ok": False, "msg": "启动超时，看看 server.py 是不是报错了"}

    # ── 总结 ──
    def sum_modes(self):
        return {k: v["label"] for k, v in summarize.MODES.items()}

    def sum_read(self, path):
        """读文件并返回字数，界面可以先看一眼内容有多少。"""
        try:
            t = summarize.read_text(path)
            return {"ok": True, "chars": len(t),
                    "preview": t[:200]}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:150]}

    def sum_run(self, mode="plot", path=None, text=None, fast=False):
        content = text or ""
        if path:
            try:
                content = summarize.read_text(path)
            except Exception as e:
                return {"ok": False, "msg": "读取失败：%s" % str(e)[:120]}
        if not content.strip():
            return {"ok": False, "msg": "没有内容可总结"}
        _sum.update({"state": "running", "text": "", "note": "准备中", "err": "", "mode": mode})

        def work():
            try:
                r = summarize.summarize(
                    content, mode, best=not fast,
                    progress=lambda i, t, n: _sum.update({"note": n}))
                if r.get("ok"):
                    _sum.update({"state": "done", "text": r["text"], "note": ""})
                else:
                    _sum.update({"state": "error", "err": r.get("msg", "失败")})
            except Exception as e:
                _sum.update({"state": "error", "err": str(e)[:200]})

        threading.Thread(target=work, daemon=True).start()
        return {"ok": True}

    def sum_status(self):
        return dict(_sum)

    def sum_save(self, text):
        try:
            name = "总结-" + time.strftime("%Y%m%d-%H%M%S") + ".md"
            path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE, name)
            open(path, "w", encoding="utf-8").write(text or "")
            subprocess.Popen(["notepad.exe", path])
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    # 总结实时字幕攒下来的内容（看过的那一集）
    def sum_live(self, mode="plot", fast=False):
        text = _live.transcript()
        if len(text.strip()) < 30:
            return {"ok": False, "msg": "实时字幕还没攒到内容，先看一段再总结"}
        return self.sum_run(mode, None, text, fast)

    def live_transcript(self):
        text = _live.transcript()
        return {"ok": True, "text": text, "chars": len(text), "lines": len(_live.lines)}

    def live_clear(self):
        return _live.clear()

    def quit_app(self):
        """设置页那个按钮：连字幕窗一起关掉。"""
        try:
            if _service_proc and _service_proc.poll() is None:
                _service_proc.terminate()
        except Exception:
            pass
        os._exit(0)

    # ── 实时字幕 ──
    def live_devices(self):
        return _live.devices()

    def live_start(self, device=None, lang="auto", model="fast",
                   to_lang="auto", sil=0.45, template="subtitle"):
        r = _live.start(device, lang, model, to_lang, sil, template)
        self.overlay_show()
        return r

    def overlay_set_pos(self, x, y):
        try:
            _overlay.move(int(x), int(y))
            overlay_save_pos(_overlay)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:100]}

    def overlay_fit(self, height):
        """字幕内容多高，窗口就多高，不在下面留空白。"""
        try:
            h = max(70, min(700, int(height)))
            w = int(_overlay.width or 1100)
            if abs(int(_overlay.height or 0) - h) >= 3:
                _overlay.resize(w, h)
            return {"ok": True, "h": h}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:100]}

    def overlay_reset_pos(self):
        try:
            w = int(_overlay.width or 1100)
            h = int(_overlay.height or 215)
            x, y = overlay_default_pos(w, h)
            _overlay.move(x, y)
            overlay_save_pos(_overlay)
            _make_topmost()
            return {"ok": True, "x": x, "y": y}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:100]}

    def live_stop(self):
        return _live.stop()

    def live_status(self, n=8):
        return _live.status(n)

    def overlay_show(self):
        try:
            if _overlay:
                _overlay.show()
                for delay in (0.2, 1.0, 2.0):
                    threading.Timer(delay, _make_topmost).start()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    def overlay_hide(self):
        try:
            if _overlay:
                overlay_save_pos(_overlay)
                _overlay.hide()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    # ── 配置 ──
    def get_config(self):
        cfg = load_config()

        def labels(section, key="label"):
            """config.json 里这一节的值可能是字典，也可能直接是字符串。"""
            out = {}
            for k, v in (cfg.get(section) or {}).items():
                if isinstance(v, dict):
                    out[k] = v.get(key) or k
                elif isinstance(v, str):
                    out[k] = v
                else:
                    out[k] = k
            return out

        models = labels("models", "model")
        if not models:
            models = {"fast": cfg.get("model", "qwen2.5:3b")}
        return {
            "langs": labels("langs"),
            "templates": labels("templates"),
            "models": models,
            "model": cfg.get("model", "qwen2.5:3b"),
            "port": cfg.get("port", 18765),
        }

    def save_settings(self, patch):
        save_config(patch)
        return {"ok": True}

    # ── 文本翻译（走 Python，省得前端跨域）──
    def translate(self, text, template="auto", model=None, lang="ja2zh"):
        if not (text or "").strip():
            return {"ok": False, "msg": "没有内容"}
        if not service_alive() and not self.start_service().get("ok"):
            return {"ok": False, "msg": "本地服务没起来"}
        try:
            body = {"text": text, "lang": lang, "template": template or "auto", "stream": False}
            if model:
                body["model"] = model
            r = http_json("/translate", body)
            return {"ok": True, "text": r.get("text", "")}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:200]}

    def history(self, limit=30):
        try:
            return http_json("/history?limit=%d" % int(limit))
        except Exception:
            return {"count": 0, "items": []}

    # ── 选文件 ──
    def pick_file(self, kind="media"):
        patterns = {
            "media": "*.mp4;*.mkv;*.mov;*.avi;*.flv;*.ts;*.mp3;*.m4a;*.wav;*.flac",
            "doc": "*.srt;*.vtt;*.ass;*.txt;*.md",
            "image": "*.png;*.jpg;*.jpeg;*.webp;*.bmp",
        }.get(kind, "*.*")
        title = {"media": "选择视频或音频", "doc": "选择字幕或文档", "image": "选择图片"}.get(kind, "选择文件")

        try:
            dlg = getattr(webview, "OPEN_DIALOG", None)
            if dlg is None and hasattr(webview, "FileDialog"):
                dlg = webview.FileDialog.OPEN
        except Exception:
            dlg = None

        # pywebview 各版本对 file_types 的格式要求不一样，
        # 依次尝试三种写法，都不行就干脆不传过滤条件，保证对话框能弹出来。
        ascii_name = {"media": "Video Audio", "doc": "Subtitle Document",
                      "image": "Image"}.get(kind, "Files")
        attempts = [
            # 描述尽量用 ASCII：parse_file_type 对中文或斜杠可能解析失败
            dict(file_types=("%s (%s)" % (ascii_name, patterns), "All files (*.*)")),
            dict(file_types=("%s (%s)" % (title, patterns), "所有文件 (*.*)")),
            dict(file_types=[(ascii_name, patterns)]),
            dict(),
        ]
        last = ""
        for kw in attempts:
            try:
                res = _window.create_file_dialog(dlg, allow_multiple=False, **kw)
            except Exception as e:
                last = str(e)[:150]
                continue
            if res is None:
                return {"ok": False, "msg": "没有选择文件"}
            if isinstance(res, (list, tuple)):
                if not res:
                    return {"ok": False, "msg": "没有选择文件"}
                path = res[0]
            else:
                path = res
            return {"ok": True, "path": str(path)}
        return {"ok": False, "msg": "打开选择框失败：%s" % last}

    # ── 跑任务（子进程 + 日志回传）──
    def run_job(self, kind, path, template="subtitle", model="fast"):
        if not path or not os.path.exists(path):
            return {"ok": False, "msg": "文件不存在"}
        if kind != "image" and not service_alive():
            self.start_service()
        jid = job_id()
        _jobs[jid] = {"state": "running", "log": [], "out": "", "t0": time.time(), "kind": kind}

        script = {"media": "video-to-subtitle.py", "doc": "cli.py", "image": "image-translate.py"}[kind]
        if kind == "media":
            cmd = [sys.executable, os.path.join(BASE, script), path, "--lang", "ja",
                   "--model", "large-v3-turbo", "--mt", model, "--keep-ja"]
        elif kind == "doc":
            cmd = [sys.executable, os.path.join(BASE, script), "-f", path,
                   "--template", template or "subtitle", "--model", model, "--out", path + ".zh"]
        else:
            runner = pick_image_python()
            cmd = [runner, os.path.join(BASE, script), path, "--mode", "both", "--mt", model]

        def worker():
            try:
                # 图片翻译要 manga-ocr（拖着 2.5 GB 的 torch），exe 和便携包里都没打进去，
                # 本机装了就用本机的 Python 跑，脚本路径在 RES 里（见 pick_image_python）。
                if kind == "image" and cmd[0] != sys.executable:
                    script_path = os.path.join(RES, os.path.basename(cmd[1]))
                    _jobs[jid]["log"].append("图片翻译交给 %s 运行" % cmd[0])
                    f0 = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    p0 = subprocess.Popen([cmd[0], script_path] + cmd[2:], cwd=RES,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, encoding="utf-8", errors="replace",
                                          creationflags=f0, bufsize=1)
                    for line in p0.stdout:
                        if line.strip():
                            _jobs[jid]["log"].append(line.rstrip())
                            if len(_jobs[jid]["log"]) > 400:
                                _jobs[jid]["log"] = _jobs[jid]["log"][-400:]
                    p0.wait()
                    _jobs[jid]["state"] = "done" if p0.returncode == 0 else "failed"
                    _jobs[jid]["out"] = os.path.dirname(os.path.abspath(path))
                    return
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                p = subprocess.Popen(cmd, cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=flags, bufsize=1)
                for line in p.stdout:
                    line = line.rstrip()
                    if line:
                        _jobs[jid]["log"].append(line)
                        if len(_jobs[jid]["log"]) > 400:
                            _jobs[jid]["log"] = _jobs[jid]["log"][-400:]
                p.wait()
                _jobs[jid]["state"] = "done" if p.returncode == 0 else "failed"
            except Exception as e:
                _jobs[jid]["log"].append("运行失败：%s" % str(e)[:200])
                _jobs[jid]["state"] = "failed"
            _jobs[jid]["out"] = os.path.dirname(os.path.abspath(path))

        threading.Thread(target=worker, daemon=True).start()
        return {"ok": True, "job": jid}

    def job_status(self, jid):
        j = _jobs.get(jid)
        if not j:
            return {"state": "unknown", "log": [], "elapsed": 0}
        return {"state": j["state"], "log": j["log"][-30:],
                "elapsed": round(time.time() - j["t0"], 1), "out": j["out"]}

    def open_folder(self, path):
        try:
            if os.path.isdir(path):
                os.startfile(path)
            elif os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    def clipboard_translate(self, template="auto", model=None):
        """翻译剪贴板内容并写回，托盘菜单和界面按钮都用这个。"""
        try:
            import tkinter
            r = tkinter.Tk()
            r.withdraw()
            text = r.clipboard_get()
            r.destroy()
        except Exception:
            return {"ok": False, "msg": "剪贴板里没有文字"}
        res = self.translate(text, template, model)
        if res.get("ok"):
            try:
                import tkinter
                r = tkinter.Tk()
                r.withdraw()
                r.clipboard_clear()
                r.clipboard_append(res["text"])
                r.update()
                r.destroy()
            except Exception:
                pass
        return res


import ctypes


def _make_topmost(title="实时字幕"):
    """pywebview 的 on_top 在 Edge 内核上不总是生效，用 Win32 再钉一次。"""
    if os.name != "nt":
        return
    try:
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, title)
        if hwnd:
            HWND_TOPMOST = -1
            SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
            u.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            return True
    except Exception:
        pass
    return False


def overlay_default_pos(w, h):
    """默认放屏幕底部居中，字幕看着最舒服。"""
    try:
        sc = webview.screens[0]
        sw, sh = int(sc.width), int(sc.height)
    except Exception:
        sw, sh = 1920, 1080
    return max(0, (sw - w) // 2), max(0, sh - h - 90)


def overlay_load_pos(w, h):
    path = os.path.join(WORK, "overlay_pos.json")
    try:
        d = json.load(open(path, encoding="utf-8"))
        x, y = int(d["x"]), int(d["y"])
        sc = webview.screens[0]
        # 屏幕变了（比如换了显示器）就退回默认位置
        if 0 <= x <= int(sc.width) - 100 and 0 <= y <= int(sc.height) - 60:
            return x, y
    except Exception:
        pass
    return overlay_default_pos(w, h)


def overlay_save_pos(win):
    try:
        json.dump({"x": int(win.x), "y": int(win.y)},
                  open(os.path.join(WORK, "overlay_pos.json"), "w", encoding="utf-8"))
    except Exception:
        pass


def make_tray_image():
    """托盘图标用 FoldLive 的 app.ico，读不到再退回画一个。"""
    for _p in (os.path.join(RES, "app.ico"), os.path.join(WORK, "app.ico")):
        try:
            if os.path.isfile(_p):
                _im = Image.open(_p)
                _im.load()
                return _im.convert("RGBA").resize((64, 64))
        except Exception:
            pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(35, 134, 54, 255))
    d.text((20, 14), "译", fill=(255, 255, 255, 255))
    return img


def tray_thread(api):
    def show(icon, item):
        try:
            _window.show()
            _window.restore()
        except Exception:
            pass

    def hide(icon, item):
        try:
            _window.hide()
        except Exception:
            pass

    def clip(icon, item):
        api.clipboard_translate()

    def quit_(icon, item):
        global _service_proc
        try:
            if _service_proc and _service_proc.poll() is None:
                _service_proc.terminate()
        except Exception:
            pass
        try:
            icon.stop()
        except Exception:
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("显示窗口", show, default=True),
        pystray.MenuItem("隐藏窗口", hide),
        pystray.MenuItem("翻译剪贴板", clip),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_),
    )
    # 先用自己写的 Win32 托盘（会写日志、能看见失败原因），不行再退回 pystray
    try:
        import tray_win32

        tray_win32.Tray(
            os.path.join(RES, "app.ico"), "本地翻译",
            [("显示窗口", lambda: show(None, None)),
             ("隐藏窗口", lambda: hide(None, None)),
             ("翻译剪贴板", lambda: clip(None, None)),
             "sep",
             ("退出", lambda: quit_(None, None))],
            os.path.join(WORK, "tray.log"),
        ).run()
        return
    except Exception as _tw:
        print("[tray win32]", _tw)

    global _tray
    # pystray 建消息窗口时会调 ChangeWindowMessageFilterEx，有些权限环境下 access denied，
    # 把这一次调用变成空操作（UIPI 消息过滤对我们没影响）。
    try:
        from pystray._util import win32 as _pw

        _pw.ChangeWindowMessageFilterEx = lambda *a, **k: None
    except Exception:
        pass
    _tray = pystray.Icon("local-translate", make_tray_image(), "本地翻译", menu)
    try:
        _tray.run()
    except Exception as _e:
        print("[tray]", _e)


def main():
    global _window
    api = Api()
    # 直接读 HTML 内容传进去，避免 WebView2 缓存旧版页面
    with open(os.path.join(RES, "web", "app.html"), encoding="utf-8") as f:
        html = f.read()

    _window = webview.create_window(
        "本地翻译",
        html=html,
        js_api=api,
        width=980, height=680, min_size=(820, 560),
        background_color="#0d1117",
    )

    def on_loaded():
        if not service_alive():
            api.start_service()

    def on_closing():
        """关主窗口只是收起来，字幕窗还要继续挂在视频上；真要退出走托盘或设置页。"""
        try:
            _window.hide()
        except Exception:
            pass
        return False

    # 独立的置顶字幕窗，无边框、可拖动，初始隐藏
    global _overlay
    try:
        with open(os.path.join(RES, "web", "overlay.html"), encoding="utf-8") as f:
            ohtml = f.read()
        ow, oh = 1100, 215
        ox, oy = overlay_load_pos(ow, oh)
        _overlay = webview.create_window(
            "实时字幕", html=ohtml, js_api=api,
            frameless=True, easy_drag=True, on_top=True, transparent=True,
            width=ow, height=oh, x=ox, y=oy, resizable=True, hidden=True,
        )
    except Exception as e:
        print("[overlay]", e)

    _window.events.loaded += on_loaded
    _window.events.closing += on_closing
    threading.Thread(target=tray_thread, args=(api,), daemon=True).start()
    # 固定 WebView2 的数据目录，免得每次启动都在程序目录里新建一个 tmpXXXX
    _store = os.path.join(WORK, "webview")
    try:
        webview.start(debug=False, private_mode=False, storage_path=_store)
    except TypeError:
        webview.start(debug=False)


if __name__ == "__main__":
    main()
