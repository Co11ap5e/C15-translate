#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地翻译 · 命令行版

用法:
  python cli.py "今日はいい天気ですね"              # 日译中（流式）
  python cli.py --to ja "今天天气真好"               # 中译日
  python cli.py --lang en2zh "Hello world"
  python cli.py --model best "難しい文章…"           # 用 7b（别名 fast/best/tiny）
  python cli.py --clip                              # 翻译剪贴板内容，并写回剪贴板
  python cli.py -f 番剧.srt --out 番剧.cn.srt        # 字幕整篇翻译（保留时间轴）
  python cli.py -f notes.txt --batch 20             # 普通文本按 20 行一批
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
OLLAMA = CFG.get("ollama", "http://127.0.0.1:11434").rstrip("/")
LANGS = CFG.get("langs", {})
OPTS = CFG.get("options", {})
KEEP = CFG.get("keep_alive", "24h")
TO_MAP = {"ja": "ja2zh", "zh": "zh2ja", "en": "en2zh", "auto": "auto2zh"}


def model_name(alias):
    if not alias:
        return CFG.get("model")
    return CFG.get("models", {}).get(alias, alias)


def clipboard_get():
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                              capture_output=True, text=True, timeout=15).stdout.rstrip("\r\n")
    except Exception:
        return ""


def clipboard_set(text):
    try:
        p = subprocess.Popen(["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
                             stdin=subprocess.PIPE, text=True)
        p.communicate(text)
    except Exception:
        pass


INSTR_USER = {
    "ja2zh": "把下面的日语翻译成简体中文，只输出译文。",
    "zh2ja": "把下面的中文翻译成自然的日语，只输出译文。",
    "en2zh": "把下面的英语翻译成简体中文，只输出译文。",
    "zh2en": "Translate the following Chinese into English. Output only the translation.",
    "auto2zh": "把下面的内容翻译成简体中文（自动识别源语言），只输出译文。",
    "subtitle": "把下面的每一行字幕逐行翻译成简体中文，保持行数完全一致，每行只输出译文。",
}


def stream_translate(text, lang, model, quiet_stats=False, template="auto"):
    system = (LANGS.get(lang) or {}).get("system", "你是专业翻译，只输出译文。")
    opts = dict(OPTS)
    if lang == "subtitle":
        opts["temperature"] = 0.1
    body = json.dumps({"model": model, "stream": True, "keep_alive": KEEP, "options": opts,
                       "template": template,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user",
                                     "content": INSTR_USER.get(lang, INSTR_USER["ja2zh"]) + "\n\n" + text}]}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    acc, t0, ec, ed = [], time.time(), 0, 0
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            piece = (d.get("message") or {}).get("content", "")
            if piece:
                acc.append(piece)
                sys.stdout.write(piece)
                sys.stdout.flush()
            if d.get("done"):
                ec, ed = d.get("eval_count", 0), (d.get("eval_duration") or 1) / 1e9
    out = "".join(acc).strip()
    if not quiet_stats:
        tok_s = ec / ed if ed else 0
        sys.stderr.write("\n[%s · %d tokens · %.1f tok/s · %.1fs]\n"
                         % (model, ec, tok_s, time.time() - t0))
    return out


def translate_lines(lines, lang, model, batch):
    """按批翻译，保证行数一致（字幕关键 ✓）；数目不符则退化为逐行"""
    out = []
    total = len(lines)
    i = 0
    while i < total:
        chunk = lines[i:i + batch]
        text = "\n".join(chunk)
        res = stream_translate(text, "subtitle", model, quiet_stats=True)
        got = [l for l in res.split("\n") if l.strip() != "" or True]
        if len(got) == len(chunk):
            out.extend(got)
        else:                                   # 行数不符 → 逐行重做这一批
            sys.stderr.write("\n  (第 %d 批行数不符 %d≠%d，逐行重做)\n" % (i // batch + 1, len(got), len(chunk)))
            for one in chunk:
                out.append(stream_translate(one, "subtitle", model, quiet_stats=True).strip())
        i += batch
        sys.stderr.write("\r  进度 %d/%d 行" % (min(i, total), total))
        sys.stderr.flush()
    sys.stderr.write("\n")
    return out


def main():
    ap = argparse.ArgumentParser(description="本地翻译命令行")
    ap.add_argument("text", nargs="?", help="要翻译的文本（省略则用 --clip 或 -f）")
    ap.add_argument("--to", choices=list(TO_MAP), help="ja/zh/en/auto 简写")
    ap.add_argument("--lang", help="直接指定语言对（见 config.json，如 ja2zh/subtitle）")
    ap.add_argument("--model", default=None, help="fast / best / tiny 或完整模型名")
    ap.add_argument("--template", default="auto", help="翻译风格：auto/subtitle/news/novel/tech/casual")
    ap.add_argument("--clip", action="store_true", help="翻译剪贴板内容并写回")
    ap.add_argument("-f", "--file", help="翻译文件（.srt 保留时间轴 / 其他按行）")
    ap.add_argument("--out", help="输出文件（默认打印）")
    ap.add_argument("--batch", type=int, default=12, help="整篇翻译时每批行数（默认 12）")
    a = ap.parse_args()

    lang = a.lang or TO_MAP.get(a.to or "ja", "ja2zh")
    model = model_name(a.model)

    # 检查 Ollama
    try:
        urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5).read()
    except Exception:
        print("✗ 连不上 Ollama（%s）→ 先运行: ollama app.exe" % OLLAMA, file=sys.stderr)
        return 2

    # ① 文件模式
    if a.file:
        raw = open(a.file, encoding="utf-8", errors="replace").read()
        _ext = os.path.splitext(a.file)[1].lower()
        is_srt = _ext in (".srt", ".vtt")
        is_ass = _ext == ".ass"
        if is_srt and raw.lstrip().startswith("WEBVTT"):
            raw = "\n".join(l for l in raw.split("\n")
                             if not l.strip().startswith(("WEBVTT", "NOTE", "Kind:", "Language:")))
            raw = raw.lstrip("\n")          # 去掉头部残留空行，避免第一条错位
        if is_ass:
            lines = raw.split("\n")
            idx = []
            for i, l in enumerate(lines):
                if l.startswith("Dialogue:"):
                    parts = l.split(",", 9)          # 前 9 段是时间/样式，第 10 段是文本
                    if len(parts) == 10 and parts[9].strip():
                        idx.append((i, parts[:9], parts[9]))
            texts = [t for _, _, t in idx]
            print("ASS 字幕共 %d 条，模型 %s，开始翻译…" % (len(texts), model), file=sys.stderr)
            zh = translate_lines(texts, "subtitle", model, a.batch)
            for (i, head, _), z in zip(idx, zh):
                lines[i] = ",".join(head) + "," + z
            result = "\n".join(lines)
        elif is_srt:
            lines = raw.split("\n")
            idx = [i for i, l in enumerate(lines) if "-->" in l]     # 时间轴行
            targets = [(i, lines[i + 1]) for i in idx if i + 1 < len(lines) and lines[i + 1].strip()]
            texts = [t for _, t in targets]
            print("字幕共 %d 条，模型 %s，开始翻译…" % (len(texts), model), file=sys.stderr)
            zh = translate_lines(texts, "subtitle", model, a.batch)
            for (i, _), z in zip(targets, zh):
                lines[i + 1] = z
            result = "\n".join(lines)
        else:
            lines = raw.split("\n")
            task = [l for l in lines if l.strip()]
            print("文本 %d 行，模型 %s，开始翻译…" % (len(task), model), file=sys.stderr)
            zh = translate_lines(task, "subtitle", model, a.batch)
            mapping = dict(zip([l for l in lines if l.strip()], zh))
            result = "\n".join(mapping.get(l, l) if l.strip() else l for l in lines)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(result)
            print("✓ 已写出 %s" % a.out)
        else:
            print(result)
        return 0

    # ② 剪贴板模式
    src = a.text
    if a.clip and not src:
        src = clipboard_get()
        if not src.strip():
            print("✗ 剪贴板是空的", file=sys.stderr)
            return 2
        print("剪贴板 %d 字，翻译中…\n" % len(src), file=sys.stderr)

    if not src:
        ap.print_help()
        return 1

    out = stream_translate(src, lang, model, template=a.template)
    if a.clip:
        clipboard_set(out)
        print("\n✓ 已写回剪贴板", file=sys.stderr)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
