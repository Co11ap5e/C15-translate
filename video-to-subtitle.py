#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
视频/音频 → 日文字幕 → 中文双语字幕（全本地）

链路：
  ① ffmpeg        抽音频 → 16kHz 单声道 WAV
  ② whisper-cli   日语识别 → 日文 .srt（带时间轴）
  ③ 本地 qwen2.5  逐条翻译（subtitle 模板）
  ④ 合并           输出「日文 + 中文」双语 .srt

用法：
  python video-to-subtitle.py "D:\\番剧\\第01话.mp4"
  python video-to-subtitle.py 录音.m4a --model medium --keep-ja
  python video-to-subtitle.py 视频.mkv --outdir D:\\字幕 --lang ja

依赖（都在本机）：
  · ffmpeg          （winget 装的，见 .ffmpeg-path）
  · whisper-cli.exe （D:\\DSH\\tools\\whisper\\，见 README）
  · ggml-*.bin      （模型，放 D:\\DSH\\tools\\whisper\\models\\）
  · 翻译服务        （python server.py，默认 127.0.0.1:18765）
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
TOOLS = r"D:\DSH\tools\whisper"
SERVER = "http://127.0.0.1:18765"
WHISPER_SERVER = os.environ.get("WHISPER_SERVER", "http://127.0.0.1:8178")


def find_ffmpeg():
    p = os.path.join(BASE, ".ffmpeg-path")
    if os.path.exists(p):
        cand = open(p, encoding="utf-8").read().strip()
        if cand and os.path.exists(cand):
            return cand
    for c in (r"D:\DSH\tools\ffmpeg\bin\ffmpeg.exe", "ffmpeg"):
        try:
            subprocess.run([c, "-version"], capture_output=True, timeout=10)
            return c
        except Exception:
            continue
    return None


def find_whisper():
    # CUDA 版优先（快 10 倍），其次 CPU 版
    for c in (os.path.join(TOOLS, "cuda", "Release", "whisper-cli.exe"),
              os.path.join(TOOLS, "cuda", "Release", "main.exe"),
              os.path.join(TOOLS, "cuda", "whisper-cli.exe"),
              os.path.join(TOOLS, "cuda", "main.exe"),
              os.path.join(TOOLS, "Release", "whisper-cli.exe"), os.path.join(TOOLS, "Release", "main.exe"),
              os.path.join(TOOLS, "whisper-cli.exe"), os.path.join(TOOLS, "main.exe"),
              "whisper-cli"):
        if c == "whisper-cli" or os.path.exists(c):
            return c
    return None


def find_model(name):
    d = os.path.join(TOOLS, "models")
    if not os.path.isdir(d):
        return None
    want = "ggml-%s.bin" % name
    if os.path.exists(os.path.join(d, want)):
        return os.path.join(d, want)
    # 模糊匹配（比如 large-v3-turbo / medium）
    for f in os.listdir(d):
        if f.endswith(".bin") and name.lower() in f.lower():
            return os.path.join(d, f)
    files = [f for f in os.listdir(d) if f.endswith(".bin")]
    return os.path.join(d, files[0]) if files else None


def whisper_server_alive():
    """常驻 whisper-server 是否在跑（在跑就不用每次加载模型 ✓）"""
    try:
        urllib.request.urlopen(WHISPER_SERVER.rstrip("/") + "/", timeout=2).read(64)
        return True
    except Exception:
        return False


def transcribe_by_server(wav, lang, out_stem_dir, stem, use_gpu=True):
    """用常驻服务的 /inference 拿 SRT（curl 转发，避免手写 multipart）"""
    srt_path = os.path.join(out_stem_dir, stem + ".ja.srt")
    cmd = ["curl", "-s", "--max-time", "7200",
           "-F", "file=@%s" % wav,
           "-F", "response_format=srt",
           "-F", "language=%s" % (lang or "auto"),
           WHISPER_SERVER.rstrip("/") + "/inference"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7200)
    body = r.stdout or ""
    if "-->" not in body:
        return None
    open(srt_path, "w", encoding="utf-8", newline="\n").write(body)
    return srt_path


def sh(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def srt_parse(path):
    """解析 SRT → [{idx, time, text}]"""
    raw = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    blocks, out = re.split(r"\n\s*\n", raw.strip()), []
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->", lines[0]) or \
            (re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->", lines[1]) if len(lines) > 1 else None)
        if not m:
            continue
        ti = 0 if m.group(0) in lines[0] else 1
        time_line = lines[ti]
        text = " ".join(lines[ti + 1:]).strip()
        if text:
            out.append({"time": time_line.strip(), "text": text})
    return out


def translate_lines(lines, template="subtitle", model="best"):
    """逐条调本地翻译服务（并发 4）"""
    import concurrent.futures as cf

    def one(t):
        body = json.dumps({"text": t, "lang": "ja2zh", "model": model,
                           "template": template, "stream": False}).encode()
        req = urllib.request.Request(SERVER + "/translate", data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=180).read()).get("text", "").strip()
        except Exception as e:
            return "（翻译失败：%s）" % str(e)[:40]

    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(one, lines))


def finish(segs, outdir, stem, a, bi_srt):
    """③ 翻译 + ④ 写双语 SRT（供服务模式与命令行模式共用）"""
    if not segs:
        print("   ✗ 没解析出字幕行"); return 1
    print("\n③ 本地模型翻译 %d 条 …" % len(segs))
    t0 = time.time()
    zh = translate_lines([x["text"] for x in segs], a.tpl, a.mt)
    ok = sum(1 for z in zh if z and not z.startswith("（翻译失败"))
    print("   ✓ %d/%d 条成功（用时 %.0fs）" % (ok, len(zh), time.time() - t0))
    with open(bi_srt, "w", encoding="utf-8", newline="\n") as f:
        for i, (x, z) in enumerate(zip(segs, zh), 1):
            f.write("%d\n%s\n%s\n%s\n\n" % (i, x["time"], z or x["text"], x["text"]))
    print("\n④ ✓ 双语字幕：%s" % bi_srt)
    print("\n用播放器（PotPlayer/VLC/MX）加载这个 .srt 即可 ✓")
    return 0


def main():
    ap = argparse.ArgumentParser(description="视频/音频 → 日文+中文双语字幕（全本地）")
    ap.add_argument("input", help="视频或音频文件")
    ap.add_argument("--model", default="large-v3-turbo", help="whisper 模型名（medium / large-v3 / large-v3-turbo）")
    ap.add_argument("--lang", default="ja", help="识别语言（ja/en/auto）")
    ap.add_argument("--tpl", default="subtitle", help="翻译风格模板")
    ap.add_argument("--mt", default="fast", help="翻译模型（fast=3b / best=7b）")
    ap.add_argument("--outdir", default=None, help="输出目录（默认与输入同目录）")
    ap.add_argument("--keep-ja", action="store_true", help="保留日文单语 srt")
    ap.add_argument("--bilingual", action="store_true", default=True, help="输出双语（默认）")
    a = ap.parse_args()

    src = os.path.abspath(a.input)
    if not os.path.exists(src):
        print("✗ 找不到文件：%s" % src); return 2
    outdir = a.outdir or os.path.dirname(src)
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]

    ff = find_ffmpeg()
    if not ff:
        print("✗ 没找到 ffmpeg（winget install Gyan.FFmpeg 或把路径写进 .ffmpeg-path）"); return 2
    wk = find_whisper()
    if not wk:
        print("✗ 没找到 whisper-cli.exe（应放在 %s）" % TOOLS); return 2
    wm = find_model(a.model)
    if not wm:
        print("✗ 没找到模型文件（应放在 %s\\models\\ggml-*.bin）" % TOOLS); return 2

    wav = os.path.join(outdir, stem + ".16k.wav")
    ja_srt = os.path.join(outdir, stem + ".ja.srt")
    bi_srt = os.path.join(outdir, stem + ".双语.srt")

    print("=" * 66)
    print("  视频 → 双语字幕（全本地）")
    print("=" * 66)
    print("  输入   : %s" % src)
    print("  模型   : %s" % os.path.basename(wm))
    print("  翻译   : 本地服务 %s（%s 模板，%s 模型）" % (SERVER, a.tpl, a.mt))

    # ① 抽音频
    print("\n① ffmpeg 抽音频 → 16kHz 单声道 WAV …")
    t0 = time.time()
    r = sh([ff, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], timeout=3600)
    if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
        print("   ✗ 抽音频失败：%s" % (r.stderr or "")[-300:]); return 1
    print("   ✓ %s（%.1f MB，用时 %.0fs）" % (os.path.basename(wav), os.path.getsize(wav) / 1e6, time.time() - t0))

    # ② 识别（常驻服务优先 → 免去每次加载模型 ✓）
    print("\n② whisper 识别（语言=%s）…" % a.lang)
    t0 = time.time()
    if whisper_server_alive():
        print("   引擎: 常驻服务 %s（模型常驻，无需加载 ✓）" % WHISPER_SERVER)
        p = transcribe_by_server(wav, a.lang, outdir, stem)
        if p:
            segs0 = srt_parse(p)
            print("   ✓ 识别出 %d 条字幕（用时 %.1fs）" % (len(segs0), time.time() - t0))
            return finish(segs0, outdir, stem, a, bi_srt)
        print("   ⚠️ 常驻服务调用失败，回退到命令行模式")
    use_gpu = "cuda" in wk.lower()
    cmd = [wk, "-m", wm, "-f", wav, "-l", a.lang, "-osrt", "-of", os.path.join(outdir, stem + ".ja"), "-np", "-nt"]
    if use_gpu:
        cmd += ["-fa"]           # flash attention，CUDA 上更快
    r = sh(cmd, timeout=7200)
    if use_gpu:
        tail = (r.stderr or "") + (r.stdout or "")
        print("   引擎: CUDA（%s）" % ("GPU 生效 ✓" if "CUDA" in tail or "cuda" in tail else "见日志"))
    if not os.path.exists(ja_srt):
        # 有的版本 -of 不要后缀处理差异，兜底找一下
        cand = [f for f in os.listdir(outdir) if f.startswith(stem) and f.endswith(".srt")]
        if cand:
            ja_srt = os.path.join(outdir, cand[0])
        else:
            print("   ✗ 识别失败：%s" % ((r.stderr or r.stdout or "")[-400:])); return 1
    segs = srt_parse(ja_srt)
    print("   ✓ 识别出 %d 条字幕（用时 %.0fs = %.1f 分钟音频/分钟）" % (len(segs), time.time() - t0, 0))

    if not segs:
        print("   ✗ 没解析出字幕行"); return 1

    # ③ 翻译
    print("\n③ 本地模型翻译 %d 条 …" % len(segs))
    t0 = time.time()
    zh = translate_lines([s["text"] for s in segs], a.tpl, a.mt)
    ok = sum(1 for z in zh if z and not z.startswith("（翻译失败"))
    print("   ✓ %d/%d 条成功（用时 %.0fs）" % (ok, len(zh), time.time() - t0))

    # ④ 写双语 SRT
    with open(bi_srt, "w", encoding="utf-8", newline="\n") as f:
        for i, (s, z) in enumerate(zip(segs, zh), 1):
            f.write("%d\n%s\n%s\n%s\n\n" % (i, s["time"], z or s["text"], s["text"]))
    print("\n④ ✓ 双语字幕：%s" % bi_srt)
    if a.keep_ja:
        print("   ✓ 日文单语：%s" % ja_srt)
    else:
        try:
            os.remove(ja_srt)
        except Exception:
            pass
    try:
        os.remove(wav)
    except Exception:
        pass
    print("\n用播放器（PotPlayer/VLC/MX）加载这个 .srt 即可 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
