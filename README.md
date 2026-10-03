# 本地翻译

用本机的 Ollama（qwen2.5）做翻译。不联网、不花钱，内容不出本机。

这个目录里有两块东西：一块是调用本地模型的笔记，一块是具体工具——网页、命令行、
字幕、图片、浏览器扩展。下面按这两块写。

## 一、怎么调用本地模型

Ollama 装在本机，监听 11434。有三种调用方式，从简单到实用。

### 1. 命令行直接聊

```powershell
ollama run qwen2.5:3b     # 快，89 tok/s
ollama run qwen2.5:7b     # 准，31 tok/s

# /bye 退出；ollama ps 看当前加载的模型；ollama list 看装了哪些
```

### 2. 原生 HTTP 接口

```
POST http://127.0.0.1:11434/api/generate
Content-Type: application/json

{
  "model": "qwen2.5:3b",
  "prompt": "把下面这句日语翻成中文：今日はいい天気ですね",
  "stream": false,
  "options": { "temperature": 0.2, "num_ctx": 4096, "num_predict": 512 }
}
```

返回里有 `response` 和 `eval_count`、`eval_duration`，两者相除就是生成速度。

多轮对话改用 `/api/chat`，把 `prompt` 换成 `messages`：

```json
{"model":"qwen2.5:3b","messages":[{"role":"system","content":"你是翻译引擎"},{"role":"user","content":"..."}],"stream":false}
```

### 3. OpenAI 兼容接口

最实用的一种，因为任何支持「自定义 OpenAI 接口」的软件都能直接接上。

```
POST http://127.0.0.1:11434/v1/chat/completions
Authorization: Bearer anything          # 随便填，Ollama 不校验
Content-Type: application/json

{
  "model": "qwen2.5:3b",
  "messages": [
    {"role": "system", "content": "你是专业的日译中翻译，只输出译文"},
    {"role": "user",   "content": "今日はいい天気ですね"}
  ],
  "stream": false
}
```

在客户端里填的时候：

```
接口地址  http://127.0.0.1:11434/v1
API Key   随便填，比如 local
模型名    qwen2.5:3b 或 qwen2.5:7b
```

Cherry Studio、ChatBox、沉浸式翻译、VS Code 插件、DSH 这些都能接。

### Python 调用示例

零第三方依赖，只用标准库：

```python
import json, urllib.request

def translate(text, model="qwen2.5:3b"):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是专业的日译中翻译，只输出译文，不要解释。"},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    return r["choices"][0]["message"]["content"].strip()

print(translate("今日はいい天気ですね。散歩でも行きましょうか。"))
```

### 流式输出

把 `"stream": true`，服务端会返回一行一个 JSON，逐行读、把 `message.content`
拼起来就是完整译文。本项目的 `server.py` 就是这么转发到浏览器的。

### 常用参数

放在 `options` 里。

| 参数 | 作用 | 翻译场景的建议 |
| --- | --- | --- |
| `temperature` | 随机性 | 0.1 到 0.3，翻译要稳定 |
| `num_ctx` | 上下文长度 | 短文 2048，长文或字幕 8192，调大会吃显存 |
| `num_predict` | 最多生成多少 token | 512 起步，整篇翻译给到 2048 以上 |
| `top_p` | 采样范围 | 0.9 |
| `keep_alive` | 模型驻留时长 | 设成 `"24h"`，免得每次都等加载。已经在环境变量里设过了 |

一个容易踩的坑：最初的提示词把指令和原文放在同一条 user 消息里，模型经常把原文
原样吐回来。后来改成 system 写清角色、user 里用明确句式「把下面这句翻译成中文：」，
才稳定下来。

## 二、这套工具怎么用

### 启动

```powershell
双击 启动.bat
# 或者
python D:\DSH\local-translate\server.py
```

然后浏览器打开 `http://127.0.0.1:18765`。

### 桌面版

上面那些是散着的脚本，日常用的是一个窗口把它们收在一起：

```powershell
双击 本地翻译.exe        # 打包出来的启动器
# 或者
双击 打开翻译软件.bat
# 或者
python app.py
```

窗口里分七页：文本、视频字幕、文档、图片、实时字幕、总结、设置。文本页就是网页版
那一套；视频字幕、文档、图片是三件套的图形入口，选文件、看进度、完事打开输出目录；
设置页能看本地服务状态、翻译历史和模型列表。

**实时字幕**是为了看没有字幕的生肉。它从系统声音里取音频（WASAPI 回环，不是麦克风），
所以用哪个播放器都行，识别到一句话（停顿够久）才提交，不会把句子切一半。页面上能选：

- 声音来源：默认扬声器，多个声卡时可以指定
- 原声语言：日语 / 英语 / 中文 / 韩语 / 自动。日语片子直接选日语，比自动判断准
- 译文：跟着原声走（自动翻成中文）、指定某个方向、或者不翻译只看原文
- 翻译模型：3b 快，7b 稳
- 断句：停顿多久算一句，灵敏 0.3 秒、标准 0.45 秒、稳一点 0.7 秒

字幕显示在一个独立的置顶窗口里，无边框、可以拖到视频画面上方。整个窗口都能拖，
高度跟着字幕行数自己变，不会在下面留一块空白的可拖区域。位置记在 `overlay_pos.json`。

**总结**默认总结的就是这次实时字幕攒下来的台词，看完整集点一下就能出剧情梗概、
要点、人物或时间线，不用另外导出字幕。也可以选文件（srt、vtt、ass、txt、md）
或者直接粘贴文本。内容长的时候先分段各总结一遍再合并，所以整季字幕也吃得下。

### 打包

```powershell
python -m PyInstaller --noconfirm 本地翻译.spec      # 出 dist\本地翻译\
python -m PyInstaller --noconfirm --onefile --noconsole --name 本地翻译 launcher.py
```

`本地翻译.exe` 这个启动器只有几兆，它找到本机的 Python 再跑 `app.py`。为什么界面
本身不冻进 exe：pywebview 在 Windows 上靠 pythonnet 调 .NET 的 WinForms，而
pythonnet 在 PyInstaller 冻出来的进程里加载不了随包的 `Python.Runtime.dll`，
clr_loader 会报解析不到 `Python.Runtime.Loader.Initialize`；换成 CoreCLR 倒是能加载，
但 pywebview 带的 WebView2 控件是给 .NET Framework 编的，.NET 8 里没有
`System.Windows.Forms.ContextMenu`，一样起不来。两条路都试过，都走不通，所以 exe
退回到只做启动。源码版不需要装 .NET，也不用管这些。

`desktop.runtimeconfig.json`、`runtime_hook_clr.py`、两个 spec 文件都留在仓库里，
哪天 pythonnet 修好了可以直接接着用。

### 四个入口

| 入口 | 用途 |
| --- | --- |
| 网页 `http://127.0.0.1:18765` | 粘贴文本，流式翻译，日→中 / 中→日 / 英→中 |
| 命令行 `python cli.py "文本"` | 快速翻译，`--clip` 读剪贴板，`-f 文件.srt` 翻字幕并保留时间轴 |
| `POST /translate` | 给别的程序调用，返回 SSE 流 |
| `POST /v1/chat/completions` | OpenAI 兼容，能被任何软件当自定义接口接进来 |

### 命令行例子

```powershell
python cli.py "今日はいい天気ですね"              # 日译中
python cli.py --to ja "今天天气真好"               # 中译日
python cli.py --clip                              # 翻剪贴板并写回
python cli.py -f 番剧.srt --out 番剧.cn.srt        # 字幕整篇翻译，时间轴不变
python cli.py -f 番剧.ass --template novel        # 换风格模板
```

### 翻译风格

`config.json` 里定义了六套提示词，命令行用 `--template` 选，网页和扩展里是下拉框：

- `auto` 通用
- `subtitle` 字幕，逐行对应、口语化
- `news` 新闻，书面语
- `novel` 轻小说，保留语气
- `tech` 技术文档，术语一致、代码不翻
- `casual` 口语

同一个句子换模板出来的差别挺明显，比如「明日の会議は午後三時からです」，
通用模板是「明天的会议是下午三点开始」，口语模板会变成「明天的会议下午三点开始」。

### 视频和音频

把文件拖到 `视频转双语字幕.bat` 上，或者：

```powershell
python video-to-subtitle.py 视频.mp4 --lang ja --mt fast
```

流程是 ffmpeg 抽 16 kHz 单声道音频，whisper.cpp 识别出带时间轴的日文字幕，
再逐条翻译，最后写出中日双语的 srt。

whisper 有两种用法：命令行模式每次都要重新加载模型（1.5 GB，二十多秒），
所以另外起了一个常驻服务 `whisper-server.exe`（端口 8178），模型一直放在显存里。
实测同一条音频，端到端从 28 秒降到 2.6 秒，而且 `/inference` 接口直接返回逐句的
SRT，时间轴比命令行模式精确得多。`启动字幕引擎.bat` 负责起这个服务，
拖拽的脚本会自动检查并拉起。

显存只有 6 GB，whisper 常驻占 1.5 GB 左右。翻译用 3b 模型（2 GB）比较宽裕，
用 7b（4.7 GB）就会互相挤，能跑但会慢。

### 图片和漫画

```powershell
python image-translate.py 漫画.png --mode both
```

用 manga-ocr 识别日文，再翻译，然后把中文贴回原图对应的位置，输出 `_zh.png`，
同时给一份 `_zh.txt` 对照。

做的时候有两个发现。一是 manga-ocr 只返回文本、不给坐标，所以没法直接知道每段文字
在图上的位置；办法是自己按暗像素做行投影切出文本行，位置就已知了。二是把一行裁成
又宽又扁的条状图之后，识别率反而下降，后来在裁剪时加垂直留白并放大两倍才恢复。
平均行高小于 44 像素时，整张图也先放大两倍再识别。

效果上，清晰的大字基本一字不差；三十像素左右的细体小字错误较多。真实漫画的对白
通常更大更粗，应该比合成的测试图好。

### 浏览器

两个版本，功能略有差别：

- 油猴脚本 `userscript/本地双语翻译.user.js`，功能全，快捷键和主题都能自定义，
  还能导出 Anki
- 扩展 `extension/`，是油猴脚本的重写版，功能少一些，但不依赖 Tampermonkey

扩展版的网络请求走后台 service worker，因为内容脚本在 HTTPS 页面里请求
`http://127.0.0.1` 会被混合内容策略拦掉。

### 翻译历史

服务每次翻译都会往 `history.jsonl` 追加一条，去重后可以导出成 Anki 能导入的 TSV：

```
http://127.0.0.1:18765/history?limit=20
http://127.0.0.1:18765/export/anki.tsv
```

这个文件不入库，里面有你自己翻过的东西。

## 文件结构

```
local-translate/
├─ config.json            模型、端口、语言对、提示词模板
├─ server.py              本地服务：网页 + 翻译 API + OpenAI 兼容 + 历史 + 导出
├─ cli.py                 命令行：文本、剪贴板、字幕（srt / vtt / ass）
├─ video-to-subtitle.py   视频音频转双语字幕（ffmpeg + whisper + 翻译）
├─ image-translate.py     图片漫画翻译并回贴
├─ web/index.html         网页界面，流式显示，零依赖
├─ web/install.html       油猴脚本安装引导
├─ userscript/            油猴脚本
├─ extension/             浏览器扩展（Manifest V3）
├─ history.jsonl          翻译历史，不入库
├─ 启动.bat               起服务并打开浏览器
├─ 启动字幕引擎.bat        起 whisper 常驻服务
├─ 视频转双语字幕.bat       拖视频进来
├─ 文档翻译.bat            拖字幕或文档进来
└─ 图片翻译.bat            拖图片或文件夹进来
```

## 性能（本机实测）

```
qwen2.5:3b   1.9 GB   89 tok/s    翻译首选，快且够用
qwen2.5:7b   4.7 GB   31 tok/s    长句和文学性文本更准
显存 6 GB，同时只驻留一个模型，切换时自动换入换出，大约四到七秒
```

## 还没做的

- 图片翻译对三十像素以下的细体字识别率偏低
- 扩展版还没有快捷键自定义、主题手动切换和尺寸调节
- 文档翻译目前支持 srt / vtt / ass / txt / md，PDF 和 EPUB 还没做
