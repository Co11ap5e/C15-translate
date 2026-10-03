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

import webview
import pystray
from PIL import Image, ImageDraw

import live_captions

BASE = os.path.dirname(os.path.abspath(__file__))
SERVICE = "http://127.0.0.1:18765"
CONFIG = os.path.join(BASE, "config.json")

_jobs = {}            # 任务 id -> {state, log, out, t0}
_job_seq = 0
_service_proc = None
_tray = None
_window = None
_overlay = None
_live = live_captions.LiveCaptions()


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


def service_alive():
    try:
        urllib.request.urlopen(SERVICE + "/health", timeout=3).read()
        return True
    except Exception:
        return False


def load_config():
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

    def start_service(self):
        global _service_proc
        if service_alive():
            return {"ok": True, "msg": "服务已在运行"}
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

    # ── 实时字幕 ──
    def live_devices(self):
        return _live.devices()

    def live_start(self, device=None, lang="auto", chunk=2.5, model="fast", template="subtitle"):
        r = _live.start(device, lang, chunk, model, template)
        try:
            if _overlay:
                _overlay.show()
        except Exception:
            pass
        return r

    def live_stop(self):
        return _live.stop()

    def live_status(self, n=8):
        return _live.status(n)

    def overlay_show(self):
        try:
            if _overlay:
                _overlay.show()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "msg": str(e)[:120]}

    def overlay_hide(self):
        try:
            if _overlay:
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
        types = {
            "media": ("视频/音频 (*.mp4;*.mkv;*.mov;*.avi;*.flv;*.ts;*.mp3;*.m4a;*.wav;*.flac)", "所有文件 (*.*)"),
            "doc": ("字幕/文档 (*.srt;*.vtt;*.ass;*.txt;*.md)", "所有文件 (*.*)"),
            "image": ("图片 (*.png;*.jpg;*.jpeg;*.webp;*.bmp)", "所有文件 (*.*)"),
        }.get(kind, ("所有文件 (*.*)",))
        try:
            dlg_open = getattr(webview, "OPEN_DIALOG", None)
            if dlg_open is None and hasattr(webview, "FileDialog"):
                dlg_open = webview.FileDialog.OPEN
            res = _window.create_file_dialog(dlg_open, allow_multiple=False, file_types=types)
        except Exception as e:
            return {"ok": False, "msg": "打开选择框失败：%s" % str(e)[:120]}
        if not res:
            return {"ok": False, "msg": "没有选择文件"}
        path = res[0] if isinstance(res, (list, tuple)) else res
        return {"ok": True, "path": str(path)}

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
            cmd = [sys.executable, os.path.join(BASE, script), path, "--mode", "both", "--mt", model]

        def worker():
            try:
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


def make_tray_image():
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
        icon.stop()
        try:
            _window.destroy()
        except Exception:
            os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("显示窗口", show, default=True),
        pystray.MenuItem("隐藏窗口", hide),
        pystray.MenuItem("翻译剪贴板", clip),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_),
    )
    global _tray
    _tray = pystray.Icon("local-translate", make_tray_image(), "本地翻译", menu)
    _tray.run()


def main():
    global _window
    api = Api()
    # 直接读 HTML 内容传进去，避免 WebView2 缓存旧版页面
    with open(os.path.join(BASE, "web", "app.html"), encoding="utf-8") as f:
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

    # 独立的置顶字幕窗，无边框、可拖动，初始隐藏
    global _overlay
    try:
        with open(os.path.join(BASE, "web", "overlay.html"), encoding="utf-8") as f:
            ohtml = f.read()
        _overlay = webview.create_window(
            "实时字幕", html=ohtml, js_api=api,
            frameless=True, easy_drag=True, on_top=True, transparent=True,
            width=1100, height=215, x=160, y=60, resizable=True, hidden=True,
        )
    except Exception as e:
        print("[overlay]", e)

    _window.events.loaded += on_loaded
    threading.Thread(target=tray_thread, args=(api,), daemon=True).start()
    webview.start(debug=False)


if __name__ == "__main__":
    main()
