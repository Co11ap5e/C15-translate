#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时字幕

从系统声音里取音频（WASAPI 回环，不是麦克风），切成两三秒一段，
丢给 whisper 服务识别，再用本地模型翻成中文，结果推给界面。

做这个是为了看没有字幕的生肉视频。延迟大概是切块长度加上识别和翻译的时间，
取两秒块的话，字幕落后三四秒左右。

单独用一个模块，是因为 app.py 里塞太多东西会不好读。
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from io import BytesIO

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

WHISPER_URL = "http://127.0.0.1:8178/inference"
TRANSLATE_URL = "http://127.0.0.1:18765/translate"

# whisper 在静音或音乐段落上常见的幻觉句，直接丢掉
JUNK = (
    "ご視聴ありがとうございました", "ご視聴ありがとうございます", "チャンネル登録",
    "おやすみなさい", "Thank you for watching", "Thanks for watching",
    "Subtitles by", "字幕", "MBC", "Amara.org", "ご視聴",
)
MIN_RMS = 0.0035          # 低于这个音量当静音处理


class LiveCaptions:
    def __init__(self, on_line=None):
        self.lines = []              # [{ja, zh, ts}]
        self.state = "idle"          # idle / running / error
        self.error = ""
        self.device = ""
        self.lang = "auto"
        self.chunk = 2.5   # 兼容旧参数，实际由静音切分决定
        self.model = "fast"
        self.template = "subtitle"
        self.to_lang = "auto"   # auto / ja2zh / en2zh / zh2ja / zh2en / off
        self.sil = 0.45         # 多久的停顿算一句话说完
        self.stats = {"chunks": 0, "skipped": 0, "chars": 0}
        self.on_line = on_line
        self._stop = False
        self._th = None
        self._lock = threading.Lock()

    # ── 设备 ──
    def devices(self):
        try:
            import soundcard as sc
            spk = sc.default_speaker()
            out = []
            for m in sc.all_microphones(include_loopback=True):
                if getattr(m, "isloopback", False):
                    out.append(m.name)
            return {"default": spk.name if spk else "", "loopbacks": out}
        except Exception as e:
            return {"default": "", "loopbacks": [], "error": str(e)[:150]}

    # ── 控制 ──
    def start(self, device=None, lang="auto", model="fast",
              to_lang="auto", sil=0.45, template="subtitle"):
        if self.state == "running":
            return {"ok": True, "msg": "已经在跑了"}
        self.device = device or ""
        self.lang = lang or "auto"
        self.model = model or "fast"
        self.template = template or "subtitle"
        self.to_lang = to_lang or "auto"
        try:
            self.sil = min(2.0, max(0.2, float(sil)))
        except Exception:
            self.sil = 0.45
        self.chunk = self.sil   # 旧字段留着，别的地方还在读
        self.error = ""
        self.lines = []
        self.stats = {"chunks": 0, "skipped": 0, "chars": 0}
        self._stop = False
        self.state = "running"
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._th.start()
        threading.Thread(target=self._warmup, daemon=True).start()
        return {"ok": True}

    def _warmup(self):
        """先丢一段静音给 whisper，把模型拉进显存，免得第一句字幕等二十秒。"""
        try:
            import numpy as np
            silent = self._to_wav(np.zeros(16000, dtype="float32"), 16000)
            self._transcribe(silent, "auto")
        except Exception:
            pass

    def stop(self):
        self._stop = True
        self.state = "idle"
        return {"ok": True}

    def status(self, n=8):
        with self._lock:
            return {"state": self.state, "error": self.error,
                    "lines": self.lines[-n:], "stats": dict(self.stats),
                    "device": self.device, "lang": self.lang, "chunk": self.chunk,
                    "to_lang": self.to_lang, "sil": self.sil,
                    "total": len(self.lines)}

    def transcript(self, with_src=True):
        """把这一场看下来的字幕拼成文本，交给总结用。

        只保留有内容的行；带原文的话一行中文一行原文，方便模型对上下文。
        """
        with self._lock:
            lines = list(self.lines)
        out = []
        for ln in lines:
            zh = (ln.get("zh") or "").strip()
            ja = (ln.get("ja") or "").strip()
            if not zh and not ja:
                continue
            ts = ln.get("ts") or ""
            if with_src and ja and zh and zh != ja:
                out.append("[%s] %s\n%s" % (ts, zh, ja))
            else:
                out.append("[%s] %s" % (ts, zh or ja))
        return "\n".join(out)

    def clear(self):
        with self._lock:
            self.lines = []
            self.stats = {"chunks": 0, "skipped": 0, "chars": 0}
        return {"ok": True}

    def save_transcript(self, path):
        text = self.transcript(with_src=False)
        if not text.strip():
            return {"ok": False, "msg": "还没有内容"}
        with open(path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return {"ok": True, "path": path, "chars": len(text)}

    # ── 主循环 ──
    def _loop(self):
        try:
            import numpy as np
            import soundcard as sc
        except Exception as e:
            self.state, self.error = "error", "缺少音频库：%s" % str(e)[:120]
            return
        sr = 16000
        try:
            name = self.device or sc.default_speaker().name
            mic = sc.get_microphone(name, include_loopback=True)
            if mic is None:
                raise RuntimeError("找不到回环设备：%s" % name)
            self.device = name
        except Exception as e:
            self.state, self.error = "error", str(e)[:160]
            return

        # 按静音切句：一句话说完（静音够久）才提交，不再固定切 2.5 秒把句子切断
        block = int(sr * 0.25)            # 每次读 0.25 秒
        sil_need = max(2, int(round(self.sil / 0.25)))   # 连续这么久静音 = 一句结束
        min_speech = int(sr * 0.45)       # 短于这个不算一句
        max_len = int(sr * 4)             # 超过 4 秒强制提交：宁可多切几次，也不要等
        buf = np.zeros(0, dtype="float32")
        sil = 0
        try:
            with mic.recorder(samplerate=sr, channels=1) as rec:
                while not self._stop:
                    blk = np.asarray(rec.record(numframes=block), dtype="float32").reshape(-1)
                    if not len(blk):
                        continue
                    rms = float(np.sqrt(np.mean(blk ** 2)))
                    buf = np.concatenate([buf, blk])
                    sil = sil + 1 if rms < MIN_RMS else 0

                    done = (sil >= sil_need and len(buf) >= min_speech) or len(buf) >= max_len
                    if not done:
                        continue
                    cut = len(buf) - sil * block if sil else len(buf)
                    seg = buf[:cut]
                    buf = buf[cut:] if cut < len(buf) else np.zeros(0, dtype="float32")
                    sil = 0

                    if len(seg) < min_speech:
                        continue
                    if float(np.sqrt(np.mean(seg ** 2))) < MIN_RMS:
                        self.stats["skipped"] += 1
                        continue

                    self.stats["chunks"] += 1
                    text = self._transcribe(self._to_wav(seg, sr), self.lang)
                    if not text:
                        continue
                    if self.lines and self.lines[-1]["ja"] == text:
                        continue
                    if any(j in text for j in JUNK):
                        continue
                    # 长句拆几段分别翻译：第一段翻完就先上屏，不用等整段翻完
                    parts = self._split_parts(text)
                    if len(parts) > 1:
                        self.stats["split"] = self.stats.get("split", 0) + len(parts) - 1
                    for part in parts:
                        self._push(part, self._translate(part))
        except Exception as e:
            self.state, self.error = "error", str(e)[:160]
            return
        self.state = "idle"

    # ── 工具 ──
    def _push(self, ja, zh):
        line = {"ja": ja, "zh": zh, "ts": time.strftime("%H:%M:%S")}
        with self._lock:
            self.lines.append(line)
            if len(self.lines) > 2000:
                self.lines = self.lines[-2000:]
        self.stats["chars"] += len(ja)
        if self.on_line:
            try:
                self.on_line(line)
            except Exception:
                pass

    @staticmethod
    def _split_parts(text, limit=28, hard=45):
        """一次只翻一到两句，短句合并、长句拆开，图的是快。

        limit：合并后的目标长度（约一到两句）
        hard ：单句超过这个长度就按逗号再拆，免得一次喂太多等半天
        """
        t = (text or "").strip()
        if not t:
            return []
        sents = [x.strip() for x in re.split(r"(?<=[。！？!?…；;])", t) if x.strip()]
        if not sents:
            return [t]
        out = []
        for sent in sents:
            if len(sent) <= hard:
                out.append(sent)
                continue
            for piece in re.split(r"(?<=[，、,])", sent):
                piece = piece.strip()
                while len(piece) > hard:
                    out.append(piece[:hard])
                    piece = piece[hard:]
                if piece:
                    out.append(piece)
        merged = []
        for piece in out:
            if merged and len(merged[-1]) + len(piece) <= limit:
                merged[-1] += piece
            else:
                merged.append(piece)
        return merged or [t]

    @staticmethod
    def _to_wav(audio, sr):
        pcm = (audio * 32767).astype("int16").tobytes()
        buf = BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm)
        return buf.getvalue()

    def _transcribe(self, wav_bytes, lang):
        path = os.path.join(tempfile.gettempdir(), "lt_live.wav")
        with open(path, "wb") as f:
            f.write(wav_bytes)
        cmd = ["curl", "-s", "--max-time", "60",
               "-F", "file=@%s" % path,
               "-F", "response_format=text",
               "-F", "language=%s" % (lang or "auto"),
               WHISPER_URL]
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=90, creationflags=flags)
            return (r.stdout or "").strip().replace("\n", " ")
        except Exception:
            return ""

    def _target_lang(self):
        """把界面上的选择换算成翻译服务认的语言对。"""
        to = self.to_lang or "auto"
        if to != "auto":
            return to
        return {"ja": "ja2zh", "en": "en2zh", "zh": "zh2ja", "ko": "ja2zh"}.get(self.lang, "auto2zh")

    def _translate(self, text):
        import json
        to = self._target_lang()
        if to == "off":
            return ""
        body = json.dumps({"text": text, "lang": to, "model": self.model,
                           "template": self.template, "stream": False}).encode()
        req = urllib.request.Request(TRANSLATE_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read()).get("text", "").strip()
        except Exception as e:
            return "（翻译失败：%s）" % str(e)[:40]


if __name__ == "__main__":
    # 命令行自测：抓 20 秒系统声音，打印识别结果
    cap = LiveCaptions(on_line=lambda l: print("  %s  %s\n         %s" % (l["ts"], l["ja"], l["zh"])))
    d = cap.devices()
    print("默认扬声器:", d.get("default"))
    print("回环设备:", len(d.get("loopbacks", [])), "个")
    cap.start(chunk=2.5, lang="auto")
    try:
        time.sleep(20)
    except KeyboardInterrupt:
        pass
    cap.stop()
    print("统计:", cap.status()["stats"])
