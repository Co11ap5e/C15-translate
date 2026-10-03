#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频和文档的总结

输入可以是字幕文件（srt / vtt / ass）或者纯文本，输出走本机的 qwen 模型：
剧情梗概、要点清单、人物关系、时间线。

字幕动辄几千字，模型的上下文塞不下，所以长文先分段各总结一遍，再把各段结果合并
起来总结第二次。分段大小按字符数算，2800 字左右一段比较稳。

用法（命令行）：
    python summarize.py 番剧.srt
    python summarize.py 番剧.srt --mode points
    python summarize.py 文章.md --model qwen2.5:7b
"""
import argparse
import json
import os
import re
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
SERVICE = "http://127.0.0.1:18765"

MODES = {
    "plot": {
        "label": "剧情梗概",
        "sys": "你是一个擅长整理影视内容的助手。只输出中文，不要客套话，不要复述原文。",
        "ask": "下面是一段视频的日文字幕（可能带时间轴）。请用中文写一段剧情梗概，"
               "按情节发展分段，交代清楚前因后果，600 字以内。",
        "merge": "下面是同一部作品分段总结的结果。请把它们合并成一份连贯的剧情梗概，"
                 "去掉重复和矛盾的地方，按时间顺序排列，800 字以内。",
    },
    "points": {
        "label": "要点清单",
        "sys": "你是一个擅长提炼信息的助手。只输出中文。",
        "ask": "下面是字幕内容。请提炼出关键信息，用条目列出，每条一句话，"
               "保留具体的人名、数字、结论。",
        "merge": "下面是分段提炼的要点。请合并去重，整理成一份完整清单，按重要性排序。",
    },
    "cast": {
        "label": "人物关系",
        "sys": "你是一个擅长分析剧情的助手。只输出中文。",
        "ask": "下面是字幕内容。请列出出现的人物，说明各自的身份、性格，以及彼此的关系。"
               "如果名字不确定就描述其角色。",
        "merge": "下面是分段整理的人物信息。请合并成一份完整的人物表，同一人物不要重复。",
    },
    "timeline": {
        "label": "时间线",
        "sys": "你是一个擅长梳理事件的助手。只输出中文。",
        "ask": "下面是带时间轴的字幕。请按时间顺序整理出发生的事件，格式是"
               "「时间点 - 发生了什么」，只写有实际内容的节点。",
        "merge": "下面是分段整理的时间线。请合并成一份完整的时间线，按时间排序。",
    },
}


def _model(best=True):
    try:
        cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
        m = cfg.get("models", {})
        v = m.get("best") if best else m.get("fast")
        if isinstance(v, dict):
            v = v.get("model")
        return v or "qwen2.5:7b"
    except Exception:
        return "qwen2.5:7b" if best else "qwen2.5:3b"


def ask(messages, model=None, temperature=0.3, timeout=900):
    body = {"model": model or _model(), "messages": messages,
            "stream": False, "temperature": temperature}
    req = urllib.request.Request(SERVICE + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return (r["choices"][0]["message"]["content"] or "").strip()


# ── 读取字幕或文本 ──
def read_text(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    ext = os.path.splitext(path)[1].lower()
    if ext in (".srt", ".vtt"):
        out = []
        for line in raw.replace("\r\n", "\n").split("\n"):
            s = line.strip()
            if not s or s.isdigit() or "-->" in s or s.upper().startswith(("WEBVTT", "NOTE", "KIND:", "LANGUAGE:")):
                continue
            out.append(re.sub(r"<[^>]+>", "", s))
        return "\n".join(out)
    if ext == ".ass":
        out = []
        for line in raw.split("\n"):
            if line.startswith("Dialogue:"):
                parts = line.split(",", 9)
                if len(parts) == 10:
                    out.append(re.sub(r"\{[^}]*\}", "", parts[9]).strip())
        return "\n".join(out)
    return raw


def chunk_text(text, size=2800):
    lines, out, cur = text.split("\n"), [], ""
    for l in lines:
        if len(cur) + len(l) + 1 > size and cur:
            out.append(cur)
            cur = ""
        cur += l + "\n"
    if cur.strip():
        out.append(cur)
    return out or [text]


def summarize(text, mode="plot", model=None, best=True, progress=None):
    m = MODES.get(mode) or MODES["plot"]
    text = (text or "").strip()
    if not text:
        return {"ok": False, "msg": "没有内容"}
    parts = chunk_text(text)
    mdl = model or _model(best)

    def report(i, total, note):
        if progress:
            try:
                progress(i, total, note)
            except Exception:
                pass

    if len(parts) == 1:
        report(1, 1, "总结中")
        out = ask([{"role": "system", "content": m["sys"]},
                   {"role": "user", "content": m["ask"] + "\n\n" + parts[0]}], mdl)
        return {"ok": True, "text": out, "parts": 1, "model": mdl}

    subs = []
    for i, p in enumerate(parts, 1):
        report(i, len(parts) + 1, "分段总结 %d/%d" % (i, len(parts)))
        try:
            subs.append(ask([{"role": "system", "content": m["sys"]},
                             {"role": "user", "content": m["ask"] + "\n\n" + p}], mdl))
        except Exception as e:
            subs.append("（这一段失败：%s）" % str(e)[:60])
    report(len(parts) + 1, len(parts) + 1, "合并中")
    merged = "\n\n".join("【第 %d 段】\n%s" % (i, s) for i, s in enumerate(subs, 1))
    out = ask([{"role": "system", "content": m["sys"]},
               {"role": "user", "content": m["merge"] + "\n\n" + merged}], mdl)
    return {"ok": True, "text": out, "parts": len(parts), "model": mdl}


def main():
    ap = argparse.ArgumentParser(description="字幕或文档总结（本地模型）")
    ap.add_argument("input", help="字幕或文本文件")
    ap.add_argument("--mode", default="plot", choices=list(MODES.keys()))
    ap.add_argument("--model", default=None)
    ap.add_argument("--fast", action="store_true", help="用 3b 模型（快但粗）")
    ap.add_argument("--out", default=None, help="结果写到文件")
    a = ap.parse_args()

    if not os.path.exists(a.input):
        print("找不到文件:", a.input)
        return 2
    text = read_text(a.input)
    print("读到 %d 字，模式：%s，模型：%s" % (len(text), MODES[a.mode]["label"],
                                            a.model or _model(not a.fast)))
    r = summarize(text, a.mode, a.model, best=not a.fast,
                  progress=lambda i, t, n: print("  [%d/%d] %s" % (i, t, n)))
    if not r.get("ok"):
        print("失败:", r.get("msg"))
        return 1
    print("\n" + "=" * 60 + "\n" + r["text"] + "\n" + "=" * 60)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(r["text"])
        print("已写入", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
