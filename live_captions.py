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
        self.chunk = 2.5
        self.model = "fast"
        self.template = "subtitle"
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
    def start(self, device=None, lang="auto", chunk=2.5, model="fast", template="subtitle"):
        if self.state == "running":
            return {"ok": True, "msg": "已经在跑了"}
        self.device = device or ""
        self.lang = lang or "auto"
        self.chunk = float(chunk or 2.5)
        self.model = model or "fast"
        self.template = template or "subtitle"
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
                    "device": self.device, "lang": self.lang, "chunk": self.chunk}

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

        need = int(sr * self.chunk)
        try:
            with mic.recorder(samplerate=sr, channels=1) as rec:
                buf = []
                while not self._stop:
                    block = rec.record(numframes=int(sr * 0.25))
                    buf.append(block)
                    total = sum(len(b) for b in buf)
                    if total < need:
                        continue
                    audio = np.concatenate(buf)
                    rest = audio[need:]
                    buf = [rest] if len(rest) else []
                    audio = audio[:need]

                    rms = float(np.sqrt(np.mean(audio.astype("float32") ** 2)))
                    if rms < MIN_RMS:
                        self.stats["skipped"] += 1
                        continue

                    self.stats["chunks"] += 1
                    text = self._transcribe(self._to_wav(audio, sr), self.lang)
                    if not text:
                        continue
                    if self.lines and self.lines[-1]["ja"] == text:
                        continue
                    if any(j in text for j in JUNK):
                        continue
                    zh = self._translate(text)
                    with self._lock:
                        self.lines.append({"ja": text, "zh": zh,
                                           "ts": time.strftime("%H:%M:%S")})
                        if len(self.lines) > 200:
                            self.lines = self.lines[-200:]
                    self.stats["chars"] += len(text)
                    if self.on_line:
                        try:
                            self.on_line(self.lines[-1])
                        except Exception:
                            pass
        except Exception as e:
            self.state, self.error = "error", str(e)[:160]
            return
        self.state = "idle"

    # ── 工具 ──
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

    def _translate(self, text):
        import json
        body = json.dumps({"text": text, "lang": "ja2zh", "model": self.model,
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
