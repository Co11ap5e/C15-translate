#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地翻译服务 LocalTranslate —— 把 Ollama 包装成「翻译 API + 网页界面」

启动:  python server.py        （或双击 启动.bat）
网页:  http://127.0.0.1:18765

提供的接口
  GET  /                        网页界面
  GET  /health                  健康检查（顺带报告模型/显存状态）
  POST /translate               翻译（支持流式 SSE）  {"text":"...","lang":"ja2zh","model":"...","stream":true}
  POST /v1/chat/completions     OpenAI 兼容透传（任何软件都能接 ✓）

零第三方依赖（只用标准库）✓
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
OLLAMA = CFG.get("ollama", "http://127.0.0.1:11434").rstrip("/")
HOST = CFG.get("host", "127.0.0.1")
PORT = int(CFG.get("port", 18765))
LANGS = CFG.get("langs", {})

# ── DeepL（免费额度 50 万字符/月）──
# key 放 deepl.json（内容 {"key": "xxxx:fx"}），或环境变量 DEEPL_KEY。
# 免费版 key 以 :fx 结尾，走 api-free；其它走 api。
_DEEPL_PAIRS = {
    "ja2zh": ("JA", "ZH"), "en2zh": ("EN", "ZH"), "zh2ja": ("ZH", "JA"),
    "zh2en": ("ZH", "EN"), "auto2zh": (None, "ZH"), "subtitle": (None, "ZH"),
}


def deepl_key():
    k = (os.environ.get("DEEPL_KEY") or os.environ.get("DEEPL_AUTH_KEY") or "").strip()
    if k:
        return k
    here = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "deepl.json"), os.path.join(here, "deepl-key.txt")):
        try:
            if os.path.isfile(path):
                raw = io.open(path, encoding="utf-8").read().strip()
                if raw.startswith("{"):
                    return (json.loads(raw).get("key") or "").strip()
                return raw
        except Exception:
            pass
    return ""


def deepl_translate(text, lang="ja2zh"):
    key = deepl_key()
    if not key:
        raise RuntimeError('没有 DeepL key：把 key 填进 deepl.json（内容 {"key": "xxxx:fx"}）')
    src, tgt = _DEEPL_PAIRS.get(lang or "ja2zh", (None, "ZH"))
    form = [("text", text), ("target_lang", tgt)]
    if src:
        form.append(("source_lang", src))
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    req = urllib.request.Request(
        "https://%s/v2/translate" % host,
        data=urllib.parse.urlencode(form).encode(),
        headers={"Authorization": "DeepL-Auth-Key " + key,
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:160]
        raise RuntimeError("DeepL 返回 %s: %s" % (e.code, detail))
    return ((data.get("translations") or [{}])[0].get("text") or "").strip()

OPTS = CFG.get("options", {})
TEMPLATES = CFG.get("templates", {})
HIST = CFG.get("history", {})
HIST_FILE = os.path.join(BASE, HIST.get("file", "history.jsonl"))
_hist_lock = __import__("threading").Lock()


def log_history(src, dst, lang, model, template):
    """每次翻译自动落盘，供 /history 与 /export/anki.tsv 使用"""
    if not HIST.get("enabled", True):
        return
    try:
        with _hist_lock:
            with open(HIST_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "src": src,
                                    "dst": dst, "lang": lang, "model": model,
                                    "template": template or "auto"}, ensure_ascii=False) + "\n")
            # 超过上限就裁剪
            mx = int(HIST.get("max_entries", 5000))
            if mx > 0 and os.path.getsize(HIST_FILE) > 200 and _hist_count() > mx:
                lines = open(HIST_FILE, encoding="utf-8").read().splitlines()[-mx:]
                open(HIST_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    except Exception:
        pass


def _hist_count():
    try:
        return sum(1 for _ in open(HIST_FILE, encoding="utf-8"))
    except Exception:
        return 0
KEEP = CFG.get("keep_alive", "24h")


def pick_model(name=None):
    if not name:
        return CFG.get("model", "qwen2.5:3b")
    return CFG.get("models", {}).get(name, name)      # 支持 fast/best/tiny 别名


def build_prompt(lang, text):
    conf = LANGS.get(lang) or LANGS.get("ja2zh") or {}
    system = conf.get("system", "你是专业翻译，只输出译文。")
    opts = dict(OPTS)
    # 字幕模式：行数必须一致，降低随机性
    if lang == "subtitle":
        opts["temperature"] = 0.1
    return system, opts


INSTR = {
    "ja2zh":    "把下面的日语翻译成简体中文，只输出译文。",
    "zh2ja":    "把下面的中文翻译成自然的日语，只输出译文。",
    "en2zh":    "把下面的英语翻译成简体中文，只输出译文。",
    "zh2en":    "Translate the following Chinese into English. Output only the translation.",
    "auto2zh":  "把下面的内容翻译成简体中文（自动识别源语言），只输出译文。",
    "subtitle": "把下面的每一行字幕逐行翻译成简体中文，保持行数完全一致，每行只输出译文。",
}


def build_messages(lang, text, template=None):
    conf = LANGS.get(lang) or LANGS.get("ja2zh") or {}
    tpl = TEMPLATES.get(template or "") or {}
    system = tpl.get("system") or conf.get("system", "你是专业翻译。")
    instr = INSTR.get(lang, INSTR["ja2zh"])
    return [{"role": "system", "content": system},
            {"role": "user", "content": instr + "\n\n" + text}]


def ollama_chat(model, messages, stream=True, options=None, keep_alive=None):
    """调用 Ollama 原生 /api/chat，逐行产出 NDJSON 解析后的 dict"""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "stream": bool(stream),
        "options": options or {},
        "keep_alive": keep_alive or KEEP,
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=600)
    if not stream:
        yield json.loads(resp.read())
        return
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def ollama_tags():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def ollama_ps():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def sse(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalTranslate/1.0"
    protocol_version = "HTTP/1.1"

    # ---------- 工具 ----------
    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        except Exception:
            return {}

    def log_message(self, fmt, *a):
        if os.environ.get("LT_QUIET") != "1":
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    # ---------- GET ----------
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            try:
                html = open(os.path.join(BASE, "web", "index.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            except FileNotFoundError:
                self._json(404, {"error": "web/index.html 缺失"})
        elif p in ("/userscript.user.js", "/userscript"):
            import glob as _g
            cands = _g.glob(os.path.join(BASE, "userscript", "*.user.js"))
            if not cands:
                return self._json(404, {"error": "userscript/*.user.js 不存在"})
            js = open(cands[0], "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(js)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(js)
        elif p == "/install":
            try:
                page = open(os.path.join(BASE, "web", "install.html"), "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            except FileNotFoundError:
                self._json(404, {"error": "web/install.html 不存在"})
        elif p == "/history":
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            lim = 50
            m = re.search(r"limit=(\d+)", q)
            if m:
                lim = min(int(m.group(1)), 1000)
            items = []
            try:
                lines = open(HIST_FILE, encoding="utf-8").read().splitlines()[-lim:]
                items = [json.loads(x) for x in lines if x.strip()]
            except Exception:
                pass
            self._json(200, {"count": _hist_count(), "items": items[::-1]})
        elif p.startswith("/export/anki"):
            rows, seen = [], set()
            try:
                for line in open(HIST_FILE, encoding="utf-8"):
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    key = (d.get("src") or "").strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows.append(d)
            except Exception:
                pass
            tsv = ["#separator:tab", "#html:true", "#columns:原文\t译文\t来源\t时间"]
            for d in rows[-2000:]:
                esc = lambda x: (str(x or "").replace("\t", " ").replace("\n", " ").strip())
                tsv.append("%s\t%s\t%s\t%s" % (esc(d.get("src")), esc(d.get("dst")),
                                                  esc(d.get("template") or d.get("lang")), esc(d.get("ts"))))
            body = ("\n".join(tsv) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/tab-separated-values; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="local-translate-anki.tsv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/health":
            tags, ps = ollama_tags(), ollama_ps()
            loaded = []
            if ps:
                for m in ps.get("models", []):
                    loaded.append({
                        "name": m.get("name"),
                        "size_gb": round((m.get("size") or 0) / 1e9, 2),
                        "vram_gb": round((m.get("size_vram") or 0) / 1e9, 2),
                        "gpu_pct": round((m.get("size_vram") or 0) / max(m.get("size") or 1, 1) * 100),
                    })
            self._json(200, {
                "ok": tags is not None,
                "ollama": OLLAMA,
                "default_model": CFG.get("model"),
                "langs": {k: v.get("label", k) for k, v in LANGS.items()},
                "templates": {k: v.get("label", k) for k, v in TEMPLATES.items()},
                "history": {"enabled": HIST.get("enabled", True), "count": _hist_count()},
                "installed": [m.get("name") for m in (tags or {}).get("models", [])],
                "loaded": loaded,
            })
        else:
            self._json(404, {"error": "not found", "paths": ["/", "/health", "/translate", "/v1/chat/completions"]})

    # ---------- POST ----------
    def do_POST(self):
        p = self.path.split("?")[0]
        if p == "/translate":
            self.handle_translate()
        elif p in ("/v1/chat/completions", "/chat/completions"):
            self.handle_openai()
        else:
            self._json(404, {"error": "not found"})

    def handle_deepl(self, text, lang, template, stream):
        try:
            txt = deepl_translate(text, lang)
        except Exception as e:
            return self._json(502, {"error": str(e)[:220]})
        log_history(text, txt, lang, "deepl", template)
        if not stream:
            return self._json(200, {"text": txt, "model": "deepl"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(sse({"type": "start", "model": "deepl", "lang": lang}))
            self.wfile.write(sse({"type": "delta", "text": txt}))
            self.wfile.write(sse({"type": "done", "text": txt, "tokens": 0, "tok_s": 0, "wall_s": 0}))
        except (BrokenPipeError, ConnectionResetError):
            pass
        try:
            self.wfile.write(b"data: [DONE]\n\n")
        except Exception:
            pass
        return

    def handle_translate(self):
        d = self._body()
        text = (d.get("text") or "").strip()
        lang = d.get("lang") or "ja2zh"
        model = pick_model(d.get("model"))
        stream = bool(d.get("stream", True))
        if str(d.get("model") or "").lower().startswith("deepl"):
            if not text:
                return self._json(400, {"error": "text 不能为空"})
            return self.handle_deepl(text, lang, d.get("template"), stream)
        if not text:
            return self._json(400, {"error": "text 不能为空"})
        template = d.get("template")
        _system, opts = build_prompt(lang, text)
        if d.get("options"):
            opts.update(d["options"])
        messages = build_messages(lang, text, template)

        if not stream:
            try:
                out, stats = "", {}
                for chunk in ollama_chat(model, messages, False, opts):
                    out = (chunk.get("message") or {}).get("content", "")
                    ec, ed = chunk.get("eval_count", 0), (chunk.get("eval_duration") or 1) / 1e9
                    stats = {"tokens": ec, "tok_s": round(ec / ed, 1) if ed else 0,
                             "model": chunk.get("model", model)}
                txt = out.strip()
                log_history(text, txt, lang, model, template)
                return self._json(200, {"text": txt, **stats})
            except Exception as e:
                return self._json(502, {"error": "调用 Ollama 失败: %s" % str(e)[:200]})

        # 流式：SSE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        t0 = time.time()
        try:
            self.wfile.write(sse({"type": "start", "model": model, "lang": lang}))
            acc = []
            for chunk in ollama_chat(model, messages, True, opts):
                piece = (chunk.get("message") or {}).get("content", "")
                if piece:
                    acc.append(piece)
                    self.wfile.write(sse({"type": "delta", "text": piece}))
                if chunk.get("done"):
                    ec = chunk.get("eval_count", 0)
                    ed = (chunk.get("eval_duration") or 1) / 1e9
                    _t = "".join(acc).strip()
                    log_history(text, _t, lang, model, template)
                    self.wfile.write(sse({
                        "type": "done",
                        "text": _t,
                        "tokens": ec,
                        "tok_s": round(ec / ed, 1) if ed else 0,
                        "wall_s": round(time.time() - t0, 1),
                    }))
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.wfile.write(sse({"type": "error", "error": str(e)[:200]}))
            except Exception:
                pass
        try:
            self.wfile.write(b"data: [DONE]\n\n")
        except Exception:
            pass

    def handle_openai(self):
        """OpenAI 兼容：把请求转给 Ollama 的 /v1/chat/completions（原样透传）"""
        d = self._body()
        d.setdefault("model", CFG.get("model"))
        stream = bool(d.get("stream", False))
        body = json.dumps(d).encode()
        req = urllib.request.Request(OLLAMA + "/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": self.headers.get("Authorization", "Bearer local")})
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            return self._json(e.code, {"error": e.read().decode("utf-8", "replace")[:400]})
        except Exception as e:
            return self._json(502, {"error": str(e)[:200]})
        if not stream:
            data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            for line in resp:
                self.wfile.write(line)
        except Exception:
            pass


def main():
    tags = ollama_tags()
    print("=" * 62)
    print("  本地翻译 LocalTranslate")
    print("=" * 62)
    if not tags:
        print("  ✗ 连不上 Ollama（%s）" % OLLAMA)
        print("    请先启动：ollama app.exe   或   ollama serve")
    else:
        names = [m.get("name") for m in tags.get("models", [])]
        print("  ✓ Ollama 已连接，已装模型: %s" % ", ".join(names))
        if CFG.get("model") not in names:
            print("  ⚠️ 默认模型 %s 不在列表里（改 config.json 或 ollama pull）" % CFG.get("model"))
    print("  网页界面 : http://%s:%d" % (HOST, PORT))
    print("  翻译接口 : POST http://%s:%d/translate" % (HOST, PORT))
    print("  OpenAI  : POST http://%s:%d/v1/chat/completions" % (HOST, PORT))
    print("  健康检查 : GET  http://%s:%d/health" % (HOST, PORT))
    print("=" * 62)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
