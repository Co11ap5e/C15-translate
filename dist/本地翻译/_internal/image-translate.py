#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片/漫画翻译（全本地）：日语 OCR → 中文翻译 → 回贴覆盖原文

为什么按"行"处理：
  · manga-ocr 只给文本、不给坐标 → 无法直接贴回原位 ✗
  · 按行切分后逐行 OCR，准确率明显更高（行内干扰少）✓
  · 每行的位置已知 → 可以把中文正好贴在那一行上，覆盖原文，保留排版 ✓

用法：
  python image-translate.py 漫画.png                    # 输出 漫画_zh.png + 漫画.txt
  python image-translate.py 图片文件夹 --batch           # 批量
  python image-translate.py 图.png --mode list           # 只出对照文本，不改图
  python image-translate.py 图.png --mode both --scale 1.1

依赖：manga-ocr(PIL/torch) + numpy + 本地翻译服务(server.py)
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SERVER = "http://127.0.0.1:18765"
CN_FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc"]

_mocr = None


def get_ocr():
    global _mocr
    if _mocr is None:
        from manga_ocr import MangaOcr
        _mocr = MangaOcr()
    return _mocr


def cn_font(size):
    for p in CN_FONTS:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, max(8, int(size)))
            except Exception:
                pass
    return ImageFont.load_default()


def detect_lines(img, dark_thr=160, min_h=10, gap=5, pad=8):
    """按暗像素行投影切出文本行 → [(y0, y1)]"""
    g = np.array(img.convert("L"))
    dark = (g < dark_thr).sum(axis=1)
    if dark.max() == 0:
        return []
    thr = max(2, dark.max() * 0.015)
    on = dark > thr
    lines, start, blank = [], None, 0
    for i, v in enumerate(on):
        if v:
            if start is None:
                start = i
            blank = 0
        elif start is not None:
            blank += 1
            if blank >= gap:
                y0, y1 = start, i - blank
                if y1 - y0 >= min_h:
                    lines.append((max(0, y0 - pad), min(img.height, y1 + pad)))
                start = None
    if start is not None and img.height - start >= min_h:
        lines.append((max(0, start - pad), img.height))
    return lines


def translate(text, template="subtitle", model="fast"):
    body = json.dumps({"text": text, "lang": "ja2zh", "model": model,
                       "template": template, "stream": False}).encode()
    req = urllib.request.Request(SERVER + "/translate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=180).read()).get("text", "").strip()
    except Exception as e:
        return "（翻译失败：%s）" % str(e)[:40]


def wrap_cn(draw, text, font, max_w):
    """把中文按宽度折行"""
    out, cur = [], ""
    for ch in text:
        if ch == "\n":
            out.append(cur); cur = ""; continue
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            out.append(cur)
            cur = ch
    if cur:
        out.append(cur)
    return out or [""]


def fit_text(draw, text, box_w, box_h, base_size):
    """自动缩小字号直到塞进框里"""
    for size in range(int(base_size), 7, -1):
        f = cn_font(size)
        lines = wrap_cn(draw, text, f, box_w - 6)
        line_h = size * 1.22
        if len(lines) * line_h <= box_h + 4:
            return f, lines, line_h
    f = cn_font(8)
    return f, wrap_cn(draw, text, f, box_w - 6), 8 * 1.22


def process(path, outdir, mode="overlay", template="subtitle", model="fast",
            bg="white", scale=1.0, keep_txt=True):
    t0 = time.time()
    img = Image.open(path).convert("RGB")
    W, H = img.size
    lines = detect_lines(img)
    # 小字号先整体放大 2 倍再识别 —— 实测能显著提升小字准确率 ✓
    if lines:
        avg_h = sum(b - a for a, b in lines) / len(lines)
        if avg_h < 44:
            img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
            W, H = img.size
            lines = detect_lines(img)
            print("   （平均行高 %.0fpx < 44 → 已放大 2 倍再识别）" % avg_h)
    if not lines:
        print("   ⚠️ 没检测到文本行：%s" % os.path.basename(path)); return None
    ocr = get_ocr()

    # ① 先整图 OCR —— 实测准确率明显高于逐行裁剪 ✓（manga-ocr 对宽扁条状图不适应 ✗）
    full = (ocr(img) or "").strip()
    flines = [l.strip() for l in full.split("\n") if l.strip()]
    items = []
    if flines and len(flines) == len(lines):
        # 行数对得上 → 按顺序配对，保留整图识别的高准确率 ✓
        items = [{"y0": r[0], "y1": r[1], "ja": t} for r, t in zip(lines, flines)]
        print("   OCR（整图）得到 %d 行，与检测行数一致 ✓（%.1fs）" % (len(items), time.time() - t0))
    else:
        # ② 行数不一致 → 逐行 OCR（裁剪 + 2 倍放大 + 垂直留白，补偿宽扁失真 ✓）
        print("   整图 %d 行 vs 检测 %d 行 → 改用逐行 OCR" % (len(flines), len(lines)))
        for (y0, y1) in lines:
            pad = max(12, (y1 - y0) // 2)
            crop = img.crop((0, max(0, y0 - pad), W, min(H, y1 + pad)))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            txt = (ocr(crop) or "").strip()
            if txt:
                items.append({"y0": y0, "y1": y1, "ja": txt})
        print("   逐行 OCR 得到 %d 行（%.1fs）" % (len(items), time.time() - t0))
    if not items:
        return None

    t1 = time.time()
    for it in items:
        it["zh"] = translate(it["ja"], template, model)
    print("   翻译完成（%.1fs）" % (time.time() - t1))

    stem = os.path.splitext(os.path.basename(path))[0]
    base = os.path.join(outdir, stem)

    # 对照文本
    txt_path = base + "_zh.txt"
    if keep_txt:
        with open(txt_path, "w", encoding="utf-8") as f:
            for i, it in enumerate(items, 1):
                f.write("%d\n日：%s\n中：%s\n\n" % (i, it["ja"], it["zh"]))

    # 回贴
    over_path = base + "_zh.png"
    if mode in ("overlay", "both"):
        out = img.copy()
        d = ImageDraw.Draw(out)
        for it in items:
            y0, y1 = it["y0"], it["y1"]
            h = y1 - y0
            box = [2, y0, W - 3, y1]
            d.rectangle(box, fill=bg)                     # 盖住原文
            f, lines_cn, line_h = fit_text(d, it["zh"], W - 12, h, h / 1.22 * scale)
            y = y0 + max(0, (h - len(lines_cn) * line_h) / 2)
            for ln in lines_cn:
                d.text((6, y), ln, font=f, fill=(20, 20, 20))
                y += line_h
        out.save(over_path)
    print("   ✓ 输出: %s%s" % (os.path.basename(over_path) if mode in ("overlay", "both") else "",
                               " / " + os.path.basename(txt_path) if keep_txt else ""))
    return {"img": over_path if mode in ("overlay", "both") else None,
            "txt": txt_path if keep_txt else None, "items": items}


def main():
    ap = argparse.ArgumentParser(description="图片/漫画 → 中文（全本地）")
    ap.add_argument("input", help="图片文件或文件夹")
    ap.add_argument("--mode", default="overlay", choices=["overlay", "list", "both"],
                    help="overlay=回贴覆盖 / list=只出对照文本 / both=都要")
    ap.add_argument("--tpl", default="subtitle", help="翻译风格模板")
    ap.add_argument("--mt", default="fast", help="翻译模型 fast/best")
    ap.add_argument("--outdir", default=None, help="输出目录（默认与输入同目录）")
    ap.add_argument("--bg", default="white", help="覆盖底色（white / #f8f8f8）")
    ap.add_argument("--scale", type=float, default=1.0, help="中文字号缩放（0.8~1.2）")
    a = ap.parse_args()

    targets = []
    if os.path.isdir(a.input):
        for ext in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.bmp"):
            targets += glob.glob(os.path.join(a.input, ext))
        targets = [t for t in targets if "_zh" not in os.path.basename(t)]
        print("文件夹模式：找到 %d 张图片" % len(targets))
    else:
        targets = [a.input]
    if not targets:
        print("✗ 没有可处理的图片"); return 1

    # 检查翻译服务
    try:
        urllib.request.urlopen(SERVER + "/health", timeout=5).read()
    except Exception:
        print("✗ 翻译服务没起（先运行 server.py 或 启动.bat）"); return 2

    print("=" * 66)
    print("  图片/漫画翻译（OCR: manga-ocr · 翻译: 本地 qwen）")
    print("=" * 66)
    ok = 0
    for p in targets:
        print("\n▶ %s" % os.path.basename(p))
        outdir = a.outdir or os.path.dirname(os.path.abspath(p))
        os.makedirs(outdir, exist_ok=True)
        try:
            r = process(p, outdir, a.mode, a.tpl, a.mt, a.bg, a.scale)
            if r:
                ok += 1
                for it in r["items"][:4]:
                    print("     %s → %s" % (it["ja"][:38], it["zh"][:38]))
        except Exception as e:
            print("   ✗ 失败：%s" % str(e)[:140])
    print("\n完成 %d/%d 张 ✓  输出后缀 _zh.png（回贴图）/_zh.txt（对照文本）" % (ok, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
