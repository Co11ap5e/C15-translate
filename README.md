# 本地翻译 LocalTranslate

用**你自己 PC 上的本地模型**（Ollama + qwen2.5）做翻译：**免费、离线、隐私安全** ✓

---

# 第一部分：怎么调用模型（3 种方式，从易到难）

## 方式 1：命令行直接聊（最快验证）

```powershell
ollama run qwen2.5:3b            # 快（89 tok/s）
ollama run qwen2.5:7b            # 准（31 tok/s）
# 退出：/bye    查看已加载：ollama ps    看模型清单：ollama list
```

## 方式 2：原生 HTTP API

```http
POST http://127.0.0.1:11434/api/generate
Content-Type: application/json

{
  "model": "qwen2.5:3b",
  "prompt": "把下面这句日语翻成中文：今日はいい天気ですね",
  "stream": false,
  "options": { "temperature": 0.2, "num_ctx": 4096, "num_predict": 512 }
}
```
返回：`{"response":"...", "eval_count":18, "eval_duration":...}`（tok/s = eval_count / eval_duration×1e9 ✓）

**多轮对话**用 `/api/chat`，把 `prompt` 换成 `messages`：
```json
{"model":"qwen2.5:3b","messages":[{"role":"system","content":"你是翻译引擎"},{"role":"user","content":"..."}],"stream":false}
```

## 方式 3：OpenAI 兼容接口 ⭐ 最实用

```http
POST http://127.0.0.1:11434/v1/chat/completions
Authorization: Bearer anything          ← 随便填，Ollama 不校验
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

**这意味着**：任何支持"自定义 OpenAI 接口"的软件都能直接接你的本地模型 ✓
```
接口地址(Base URL) : http://127.0.0.1:11434/v1
API Key           : 随便填（如 local）
模型名            : qwen2.5:3b  或  qwen2.5:7b
```
可接的：Cherry Studio / ChatBox / 沉浸式翻译 / VS Code 插件 / DSH / 各种客户端 ✓

## 代码示例（Python，零第三方依赖）

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

## 流式输出（边生成边显示）

把 `"stream": true` → 服务端返回 **每行一个 JSON**（NDJSON ✓），逐行读、取 `message.content` 拼接即可 ✓
本项目的 `server.py` 就是这么转发给你浏览器的 ✓

## 常用参数（`options` 里）

| 参数 | 作用 | 翻译场景建议 |
|---|---|---|
| `temperature` | 随机性 | **0.1 ~ 0.3**（翻译要稳定 ✓）|
| `num_ctx` | 上下文长度 | 短文 2048；长文/字幕 8192（**越大越吃显存** ✗）|
| `num_predict` | 最多生成多少 token | 512 起步；整篇翻译给 2048+ |
| `top_p` | 采样范围 | 0.9 |
| `keep_alive` | 模型驻留时长 | `"24h"`（避免每次等加载 ✓ 已在环境变量设过 ✓）|

## 性能须知（你的机器实测）

```
qwen2.5:3b  1.9G  89 tok/s   ← 翻译首选（快且够用 ✓）
qwen2.5:7b  4.7G  31 tok/s   ← 长句/文学性文本更准
6G 显存同时只驻留一个模型 ✓（切换时自动换入换出，约 4~7 秒 ✓）
```

---

# 第二部分：这个项目怎么用

## 启动

```powershell
双击  启动.bat
# 或
python D:\DSH\local-translate\server.py
```
然后浏览器打开 **http://127.0.0.1:18765** ✓

## 三个入口

| 入口 | 用途 |
|---|---|
| **网页** `http://127.0.0.1:18765` | 粘贴文本 → 实时流式翻译；可切日→中 / 中→日 / 英→中 |
| **命令行** `python cli.py "文本"` | 快速翻译；`--clip` 读剪贴板；`-f 文件.srt` 翻译字幕（**保留时间轴** ✓）|
| **API** `POST /translate` | 给别的程序调用（返回 SSE 流）|
| **API** `POST /v1/chat/completions` | OpenAI 兼容（可被任何软件当"自定义接口"接入 ✓）|

## 命令行例子

```powershell
python cli.py "今日はいい天気ですね"                 # 日译中
python cli.py --to ja "今天天气真好"                  # 中译日
python cli.py --clip                                 # 翻译剪贴板内容（并写回）
python cli.py -f 番剧.srt --out 番剧.cn.srt           # 字幕整篇翻译，时间轴不变
python cli.py -f 番剧.srt --model qwen2.5:7b          # 换更准的模型
```

## 文件结构

```
local-translate/
├─ README.md          ← 本文（调用模型教程 + 使用说明）
├─ config.json        ← 模型 / 端口 / 语言 / 提示词
├─ server.py          ← 本地服务（网页 + 翻译 API + OpenAI 兼容）
├─ cli.py             ← 命令行工具（含字幕翻译、剪贴板）
├─ web/index.html     ← 网页界面（流式显示，零依赖）
└─ 启动.bat           ← 一键启动并打开浏览器
```

## 下一步（待做）

- 浏览器油猴脚本：日文网页 → **中日双语对照** ✓
- 剪贴板监听模式：复制即译（常驻托盘）✓
- （可选）接 DoH/代理，让"翻译+查词"直接用上你服务器的 AdGuard ✓
