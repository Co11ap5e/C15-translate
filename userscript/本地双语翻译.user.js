// ==UserScript==
// @name         本地双语翻译 LocalTranslate（沉浸式风格）
// @namespace    local.translate
// @version      2.4.0
// @description  用你自己电脑上的本地模型（Ollama）整页扫描翻译。段落级双语对照 / 仅译文模式，三种译文样式，沉浸式翻译风格的白底玫红面板 + 收起草莓气泡 + 完成提示条。不联网、免费、隐私安全。
// @author       you
// @match        *://*/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setValue
// @grant        GM_getValue
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// @noframes
// @downloadURL  http://127.0.0.1:18765/userscript.user.js
// @updateURL    http://127.0.0.1:18765/userscript.user.js
// ==/UserScript==

/*
  ── 前提 ──
  1) 翻译服务： python D:\DSH\local-translate\server.py
  2) 本地模型： ollama app.exe
  3) 快捷键：Alt+T 翻译 · Alt+O 显示原文 · Alt+B 双语/仅译文 · Alt+A 自动翻译

  ── v1.3 界面（参考沉浸式翻译）──
  · 白底 + 玫红主色 + 卡片式布局 + 全宽主按钮 + 宫格次级按钮
  · 语言对/服务/模型/样式 做成信息行；底部状态栏
  · 翻译完成弹提示条「已翻译 · 点击显示原文」（可点）
  · 收起 = 玫红圆球（进度徽标 + 脉冲）
*/

(function () {
  'use strict';

  // ═══════════════ 配置 ═══════════════
  const DEF = {
    server: 'http://127.0.0.1:18765',
    lang: 'ja2zh', model: 'fast', auto: false, concurrency: 4, minLen: 8,
    onlyJapanese: true, mode: 'both', style: 'line', collapsed: false, toast: true,
    hover: false, inputTr: true, hoverDelay: 350, template: 'auto',
    theme: 'auto', keys: { translate: 'Alt+T', original: 'Alt+O', auto: 'Alt+A', mode: 'Alt+B' },
    panelWidth: 272, scale: 1
  };
  const cfg = Object.assign({}, DEF, JSON.parse(GM_getValue('cfg', '{}')));
  const saveCfg = () => GM_setValue('cfg', JSON.stringify(cfg));

  const CACHE_MAX = 3000;
  let cache = JSON.parse(GM_getValue('cache', '{}'));
  const cacheGet = t => cache[t];
  let saveTimer = null;
  const cacheSet = (t, v) => {
    cache[t] = v;
    const ks = Object.keys(cache);
    if (ks.length > CACHE_MAX) ks.slice(0, ks.length - CACHE_MAX).forEach(k => delete cache[k]);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => GM_setValue('cache', JSON.stringify(cache)), 800);
  };

  // 语言对：显示成沉浸式那样的「日语(日本语) → 简体中文」
  const LANGS = {
    ja2zh: { from: '日语(日本语)', to: '简体中文', icon: '日' },
    zh2ja: { from: '简体中文', to: '日语(日本语)', icon: '中' },
    en2zh: { from: '英语', to: '简体中文', icon: 'EN' },
    zh2en: { from: '简体中文', to: '英语', icon: '中' },
    auto2zh: { from: '自动检测', to: '简体中文', icon: 'A' }
  };
  const MODELS = { fast: '快 · qwen2.5:3b', best: '准 · qwen2.5:7b', tiny: '极速 · qwen2.5:1.5b' };
  const TEMPLATES = { auto: '通用', subtitle: '字幕', news: '新闻', novel: '轻小说', tech: '技术文档', casual: '口语' };
  const STYLES = { line: '左线', underline: '下划线', fade: '淡化' };

  // ═══════════════ 样式（白底玫红，参考沉浸式翻译）═══════════════
  const CSS = `
  /* ══ 折叠思维 / GitHub Primer 令牌（与 live.co11ap5e.site 一致）══ */
  :root{
    --gh-bg:#0d1117; --gh-surface:#161b22; --gh-subtle:#21262d; --gh-ink:#e6edf3; --gh-muted:#8b949e;
    --gh-line:#30363d; --gh-accent:#3fb950; --gh-accent2:#238636; --gh-link:#58a6ff;
    --gh-signal:#d29922; --gh-danger:#f85149;
  }
  @media (prefers-color-scheme: light){
    :root{ --gh-bg:#fff; --gh-surface:#fff; --gh-subtle:#f6f8fa; --gh-ink:#1f2328; --gh-muted:#59636e;
      --gh-line:#d1d9e0; --gh-accent:#1f883d; --gh-accent2:#1f883d; --gh-link:#0969da;
      --gh-signal:#bc4c00; --gh-danger:#cf222e; }
  }

  /* 手动主题覆盖（放在系统媒体查询之后，优先级更高 ✓）*/
  html.lt-dark{--gh-bg:#0d1117;--gh-surface:#161b22;--gh-subtle:#21262d;--gh-ink:#e6edf3;--gh-muted:#8b949e;
    --gh-line:#30363d;--gh-accent:#3fb950;--gh-accent2:#238636;--gh-link:#58a6ff;--gh-signal:#d29922;--gh-danger:#f85149}
  html.lt-light{--gh-bg:#fff;--gh-surface:#fff;--gh-subtle:#f6f8fa;--gh-ink:#1f2328;--gh-muted:#59636e;
    --gh-line:#d1d9e0;--gh-accent:#1f883d;--gh-accent2:#1f883d;--gh-link:#0969da;--gh-signal:#bc4c00;--gh-danger:#cf222e}

  /* ── 注入到网页的译文：按网页自身明暗自动选色 ── */
  html{--lt-t:#0969da; --lt-l:#1f883d; --lt-dim:#59636e; --lt-err:#cf222e}
  html.lt-dark-page{--lt-t:#58a6ff; --lt-l:#3fb950; --lt-dim:#8b949e; --lt-err:#f85149}
  .lt-trans{display:block;margin:.28em 0 .55em;font:inherit;line-height:inherit;color:var(--lt-t);
    white-space:pre-wrap;word-break:break-word;transition:opacity .15s}
  html.lt-s-line .lt-trans{border-left:3px solid var(--lt-l);padding-left:.72em}
  html.lt-s-underline .lt-trans{border-bottom:1px dashed color-mix(in srgb,var(--lt-l) 60%,transparent);padding-bottom:.12em}
  html.lt-s-fade .lt-trans{opacity:.86}
  .lt-trans.lt-loading{color:var(--lt-dim);font-style:italic;border-color:var(--lt-dim)}
  .lt-trans.lt-err{color:var(--lt-err);font-style:italic}
  html.lt-hide .lt-trans{display:none}
  html.lt-only .lt-src{display:none !important}
  html.lt-only .lt-trans{border:0 !important;padding:0 !important;opacity:1;color:inherit;margin:0 0 .55em}

  /* ── 面板：GitHub Primer 卡片 ── */
  #lt-panel{position:fixed;right:16px;bottom:16px;z-index:2147483600;width:var(--lt-w,272px);
    background:var(--gh-surface);color:var(--gh-ink);border:1px solid var(--gh-line);border-radius:6px;
    box-shadow:0 8px 24px rgba(1,4,9,.5);
    font:calc(12.5px * var(--lt-scale,1))/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans","PingFang SC","Microsoft YaHei",ui-sans-serif,system-ui,sans-serif;
    user-select:none;overflow:hidden;-webkit-font-smoothing:antialiased}
  #lt-panel .lt-hd{display:flex;align-items:center;gap:8px;padding:11px 13px;cursor:move;
    background:var(--gh-subtle);border-bottom:1px solid var(--gh-line)}
  #lt-panel .lt-logo{width:22px;height:22px;border-radius:6px;display:flex;align-items:center;justify-content:center;
    background:var(--gh-accent2);color:#fff;font-size:12px;font-weight:700;flex:0 0 auto}
  #lt-panel .lt-title{flex:1;font-weight:600;font-size:13px;color:var(--gh-ink)}
  #lt-panel .lt-title i{font-style:normal;color:var(--gh-accent)}
  #lt-panel .lt-ico{width:24px;height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;
    color:var(--gh-muted);cursor:pointer;font-size:13px;transition:background .15s,color .15s}
  #lt-panel .lt-ico:hover{background:var(--gh-bg);color:var(--gh-link)}
  #lt-panel .lt-bd{padding:12px 13px 13px}
  #lt-panel svg{display:block;flex:0 0 auto}
  #lt-panel .lt-to{flex:0 0 88px;text-align:center;color:var(--gh-muted);font-size:12px;
    background:var(--gh-subtle);border:1px solid var(--gh-line);border-radius:6px;padding:5px 6px;
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  #lt-panel button:focus-visible,#lt-panel select:focus-visible,#lt-panel .lt-g:focus-visible{
    outline:2px solid var(--gh-accent);outline-offset:1px}
  #lt-panel .lt-primary{display:flex;align-items:center;justify-content:center;gap:7px}
  #lt-panel .lt-g{gap:6px}
  #lt-panel .lt-logo{color:#fff}
  #lt-bubble svg{width:22px;height:22px;stroke:#fff}
  #lt-bubble{color:#fff;font-size:0}

  #lt-panel .lt-langrow{display:flex;align-items:center;gap:6px;background:var(--gh-bg);
    border:1px solid var(--gh-line);border-radius:6px;padding:6px;margin-bottom:10px}
  #lt-panel .lt-langrow select{flex:1;background:var(--gh-subtle);border:1px solid var(--gh-line);border-radius:6px;
    color:var(--gh-ink);padding:5px 6px;font:12px/1.4 inherit;cursor:pointer;min-width:0}
  #lt-panel .lt-langrow select:disabled{color:var(--gh-muted);cursor:default}
  #lt-panel .lt-arrow{color:var(--gh-muted);font-size:12px;flex:0 0 auto}

  #lt-panel .lt-inforow{display:flex;align-items:center;gap:7px;margin:8px 0;font-size:11.5px;color:var(--gh-muted)}
  #lt-panel .lt-inforow .lt-k{flex:0 0 42px}
  #lt-panel .lt-inforow select{flex:1;background:var(--gh-subtle);border:1px solid var(--gh-line);border-radius:6px;
    color:var(--gh-ink);padding:4px 6px;font:11.5px/1.4 inherit;cursor:pointer}
  #lt-panel .lt-dot{width:8px;height:8px;border-radius:50%;background:var(--gh-muted);flex:0 0 auto}
  #lt-panel .lt-dot.ok{background:var(--gh-accent);box-shadow:0 0 0 3px rgba(63,185,80,.18)}
  #lt-panel .lt-dot.bad{background:var(--gh-danger);box-shadow:0 0 0 3px rgba(248,81,73,.18)}

  /* 主按钮 = GitHub green 实心按钮 */
  #lt-panel .lt-primary{width:100%;border:1px solid rgba(240,246,252,.1);border-radius:6px;padding:9px 0;cursor:pointer;
    background:var(--gh-accent2);color:#fff;font:600 13.5px/1 inherit;
    transition:filter .15s,transform .1s}
  #lt-panel .lt-primary:hover{filter:brightness(1.12)}
  #lt-panel .lt-primary:active{transform:translateY(1px)}
  #lt-panel .lt-primary .lt-hint{opacity:.8;font-weight:400;font-size:11.5px}

  #lt-panel .lt-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px}
  #lt-panel .lt-g{display:flex;align-items:center;justify-content:center;gap:5px;background:var(--gh-subtle);
    border:1px solid var(--gh-line);border-radius:6px;padding:8px 4px;cursor:pointer;
    font:12px/1 inherit;color:var(--gh-ink);transition:border-color .15s,background .15s,color .15s}
  #lt-panel .lt-g:hover{border-color:var(--gh-link);background:var(--gh-bg)}
  #lt-panel .lt-g.on{border-color:var(--gh-accent);background:rgba(63,185,80,.12);color:var(--gh-accent);font-weight:600}
  #lt-panel .lt-g .lt-i{font-size:13px;opacity:.9}

  #lt-settings{display:none}
  #lt-settings.open{display:block;margin-top:10px;padding-top:9px;border-top:1px solid var(--gh-line)}
  #lt-panel .lt-seg{display:flex;background:var(--gh-bg);border:1px solid var(--gh-line);border-radius:6px;padding:2.5px}
  #lt-panel .lt-seg button{flex:1;background:transparent;border:0;color:var(--gh-muted);border-radius:5px;padding:5px 0;
    font:12px/1 inherit;cursor:pointer;transition:background .15s,color .15s}
  #lt-panel .lt-seg button:hover{color:var(--gh-ink)}
  #lt-panel .lt-seg button.on{background:var(--gh-accent2);color:#fff;font-weight:600}
  #lt-panel .lt-key{width:60px;background:var(--gh-bg);border:1px solid var(--gh-line);border-radius:6px;
    color:var(--gh-ink);font:11px/1.3 ui-monospace,Consolas,monospace;text-align:center;padding:3.5px 2px}
  #lt-panel input[type=range]{-webkit-appearance:none;appearance:none;height:4px;border-radius:3px;
    background:var(--gh-line);outline:none}
  #lt-panel input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:13px;height:13px;border-radius:50%;
    background:var(--gh-accent);cursor:pointer;border:0}
  #lt-panel .lt-tip{color:var(--gh-muted);font-size:10.5px;margin-top:8px;line-height:1.55}

  #lt-panel .lt-foot{display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding-top:9px;
    border-top:1px solid var(--gh-line);color:var(--gh-muted);font-size:10.5px}
  #lt-panel .lt-foot b{color:var(--gh-ink);font-weight:600}

  #lt-bubble{position:fixed;right:16px;bottom:16px;z-index:2147483600;width:46px;height:46px;border-radius:50%;
    background:var(--gh-accent2);border:1px solid rgba(240,246,252,.15);display:none;align-items:center;
    justify-content:center;cursor:pointer;box-shadow:0 6px 18px rgba(1,4,9,.45);font-size:20px;user-select:none;
    transition:transform .15s,filter .15s}
  #lt-bubble:hover{transform:scale(1.07);filter:brightness(1.1)}
  #lt-bubble.lt-busy{animation:ltpulse 1.1s ease-in-out infinite}
  @keyframes ltpulse{0%,100%{box-shadow:0 6px 18px rgba(1,4,9,.45),0 0 0 0 rgba(63,185,80,.5)}
    50%{box-shadow:0 6px 18px rgba(1,4,9,.45),0 0 0 11px rgba(63,185,80,0)}}
  #lt-bubble .lt-badge{position:absolute;top:-6px;right:-6px;background:var(--gh-bg);border:1px solid var(--gh-line);
    color:var(--gh-ink);border-radius:999px;font:10px/1 inherit;font-weight:600;padding:3px 6px}

  #lt-toast{position:fixed;right:72px;bottom:26px;z-index:2147483600;background:var(--gh-surface);color:var(--gh-ink);
    border:1px solid var(--gh-line);border-radius:6px;padding:9px 14px;
    font:12.5px/1.4 inherit;cursor:pointer;box-shadow:0 8px 24px rgba(1,4,9,.5);
    opacity:1;transition:opacity .3s,transform .3s,border-color .15s;white-space:nowrap}
  #lt-toast:hover{border-color:var(--gh-accent)}
  #lt-toast.hide{opacity:0;transform:translateY(6px)}

  #lt-pop{position:absolute;z-index:2147483601;max-width:430px;background:var(--gh-surface);color:var(--gh-ink);
    border:1px solid var(--gh-line);border-radius:6px;padding:10px 13px;font:13.5px/1.75 inherit;
    box-shadow:0 8px 24px rgba(1,4,9,.5);white-space:pre-wrap}
  #lt-pop b{color:var(--gh-link);font-weight:600}
  `;
  const styleEl = document.createElement('style');
  styleEl.textContent = CSS;
  (document.head || document.documentElement).appendChild(styleEl);

  // ═══════════════ 单色 SVG 图标（Octicon 风格，继承 currentColor）═══════════════
  const ICONS = {
    list: '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M2 3.6h2v1.8H2V3.6zm4 0h8v1.8H6V3.6zM2 7.1h2v1.8H2V7.1zm4 0h8v1.8H6V7.1zM2 10.6h2v1.8H2v-1.8zm4 0h8v1.8H6v-1.8z"/></svg>',
    text: '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M2.2 2h11.6v3h-1.5V3.5H8.8V14h1.7v1.6H5.5V14h1.7V3.5H3.7V5H2.2V2z"/></svg>',
    eye: '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M1.6 8S3.9 4 8 4s6.4 4 6.4 4-2.3 4-6.4 4S1.6 8 1.6 8z"/><circle cx="8" cy="8" r="1.9"/></svg>',
    eyeoff: '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M1.6 8S3.9 4 8 4c1 0 1.9.3 2.7.7M14.4 8s-2.3 4-6.4 4c-1 0-1.9-.3-2.7-.7"/><path d="M3 13 13 3"/></svg>',
    trash: '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M6.2 1.5h3.6l.5 1H14V4H2V2.5h3.7l.5-1zM3.6 5.2h8.8l-.6 8.6a1.1 1.1 0 0 1-1.1 1H5.3a1.1 1.1 0 0 1-1.1-1L3.6 5.2z"/></svg>',
    zap: '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M9.6.6 3.2 9.1h3.9l-.7 6.3 6.4-8.5H8.9L9.6.6z"/></svg>',
    globe: '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.25" aria-hidden="true"><circle cx="8" cy="8" r="6.1"/><path d="M1.9 8h12.2M8 1.9c1.9 2.1 1.9 10.1 0 12.2-1.9-2.1-1.9-10.1 0-12.2z"/></svg>',
    gear: '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><path d="M2.4 4.6h11.2M2.4 8h11.2M2.4 11.4h11.2"/><circle cx="6.1" cy="4.6" r="1.8" fill="currentColor" stroke="none"/><circle cx="10.6" cy="8" r="1.8" fill="currentColor" stroke="none"/><circle cx="5.6" cy="11.4" r="1.8" fill="currentColor" stroke="none"/></svg>',
    chev: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4.2 6.4 8 10.2l3.8-3.8"/></svg>',
    arrow: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M2.5 8h11M9.5 4l4 4-4 4"/></svg>',
    trans: '<svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden="true"><path d="M7.3 1.4h1.7v1h3.2v1.4h-1.1c-.3 1.2-.8 2.3-1.5 3.2.6.6 1.3 1.1 2.1 1.4l-.5 1.4c-1-.4-1.8-.9-2.5-1.6-.7.7-1.5 1.2-2.5 1.6l-.5-1.4c.8-.3 1.5-.8 2.1-1.4-.7-1-1.2-2-1.5-3.2H6.1V2.4h3.2v-1zM6.3 9.1 5.6 11H3.4l2.8 4.6h1.7L6.3 9.1z"/></svg>'
  };
  const icon = n => ICONS[n] || '';

  // ═══════════════ 调用服务 ═══════════════
  function translate(text) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: cfg.server.replace(/\/$/, '') + '/translate',
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ text, lang: cfg.lang, model: cfg.model, template: cfg.template, stream: false }),
        timeout: 120000,
        onload: r => {
          try {
            const j = JSON.parse(r.responseText);
            if (j.error) return reject(new Error(j.error));
            resolve((j.text || '').trim());
          } catch (e) { reject(new Error('返回解析失败')); }
        },
        onerror: () => reject(new Error('连不上翻译服务（server.py 起了吗？）')),
        ontimeout: () => reject(new Error('超时'))
      });
    });
  }
  function checkService() {
    GM_xmlhttpRequest({
      method: 'GET', url: cfg.server.replace(/\/$/, '') + '/health', timeout: 6000,
      onload: r => {
        let ok = false;
        try { ok = JSON.parse(r.responseText).ok === true; } catch (e) { ok = false; }
        setServiceDot(ok ? 'ok' : 'bad');
      },
      onerror: () => setServiceDot('bad'), ontimeout: () => setServiceDot('bad')
    });
  }
  function setServiceDot(cls) {
    const d = document.getElementById('lt-dot');
    if (d) d.className = 'lt-dot ' + cls;
  }


  // ═══════════════ 页面明暗探测（决定注入译文的配色）═══════════════
  function detectPageTheme() {
    try {
      const c = getComputedStyle(document.body).backgroundColor || '';
      const m = c.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!m) return;
      const lum = (0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]) / 255;
      document.documentElement.classList.toggle('lt-dark-page', lum < 0.45);
    } catch (e) { /* 忽略 */ }
  }

  // ═══════════════ 段落采集 ═══════════════
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'KBD', 'SAMP', 'TEXTAREA',
    'INPUT', 'SELECT', 'OPTION', 'SVG', 'CANVAS', 'IFRAME', 'VIDEO', 'AUDIO', 'MATH', 'BUTTON']);
  const SKIP_SEL = '.lt-trans,#lt-panel,#lt-bubble,#lt-toast,#lt-pop,.notranslate,[translate="no"],[contenteditable="true"]';

  function worth(el) {
    if (!el || el.nodeType !== 1) return false;
    if (SKIP_TAGS.has(el.tagName)) return false;
    if (el.closest && el.closest(SKIP_SEL)) return false;
    const t = (el.innerText || '').trim();
    if (t.length < cfg.minLen || t.length > 4000) return false;
    if (!/[a-zA-Z\u3040-\u30ff\u4e00-\u9fff]/.test(t)) return false;
    if (cfg.onlyJapanese && !/[\u3040-\u30ff]/.test(t)) return false;
    const direct = Array.from(el.childNodes).filter(n => n.nodeType === 3)
      .map(n => n.textContent).join('').trim();
    if (direct.length < Math.min(cfg.minLen, t.length * 0.3) && el.children.length > 0) return false;
    if (el.dataset && el.dataset.ltDone) return false;
    return true;
  }
  function collect(root) {
    const out = [];
    const walker = document.createTreeWalker(root || document.body, NodeFilter.SHOW_ELEMENT, {
      acceptNode(el) {
        if (SKIP_TAGS.has(el.tagName) || (el.closest && el.closest(SKIP_SEL))) return NodeFilter.FILTER_REJECT;
        return worth(el) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      }
    });
    let n;
    while ((n = walker.nextNode())) out.push(n);
    return out;
  }
  function makeSlot(el) {
    if (el.nextElementSibling && el.nextElementSibling.classList &&
        el.nextElementSibling.classList.contains('lt-trans')) return el.nextElementSibling;
    const d = document.createElement('div');
    d.className = 'lt-trans';
    el.insertAdjacentElement('afterend', d);
    if (!el.classList.contains('lt-src')) el.classList.add('lt-src');
    return d;
  }

  // ═══════════════ 队列 ═══════════════
  const state = { running: false, done: 0, total: 0, queue: [], active: 0, errors: 0 };

  function refreshPanel() {
    const el = document.getElementById('lt-progress');
    if (el) {
      el.innerHTML = state.running
        ? `<b>翻译中 ${state.done}/${state.total}</b>${state.errors ? ' · 失败 ' + state.errors : ''}`
        : (state.total ? `已译 <b>${state.done}</b>/${state.total}` : '就绪');
    }
    const b = document.getElementById('lt-bubble');
    if (b) {
      b.classList.toggle('lt-busy', state.running);
      const badge = b.querySelector('.lt-badge');
      if (badge) {
        badge.style.display = (state.total && state.running) ? '' : 'none';
        badge.textContent = state.total ? `${state.done}/${state.total}` : '';
      }
    }
  }
  function pump() {
    while (state.active < cfg.concurrency && state.queue.length) {
      const job = state.queue.shift();
      state.active++;
      (async () => {
        try {
          let tr = cacheGet(job.text);
          if (!tr) {
            try { tr = await translate(job.text); }
            catch (e) { tr = await translate(job.text); }
            if (tr) cacheSet(job.text, tr);
          }
          job.slot.classList.remove('lt-loading');
          job.slot.textContent = tr;
          job.el.dataset.ltDone = '1';
          state.done++;
        } catch (e) {
          job.slot.classList.remove('lt-loading');
          job.slot.classList.add('lt-err');
          job.slot.textContent = '（翻译失败：' + e.message + '）';
          state.errors++;
        } finally {
          state.active--;
          refreshPanel();
          if (!state.queue.length && state.active === 0) {
            state.running = false; refreshPanel();
            if (state.done && cfg.toast) toast(`已翻译 ${state.done} 段 · 点击显示原文 (Alt+O)`, 5200);
          }
          pump();
        }
      })();
    }
  }
  function translatePage(root) {
    const els = collect(root);
    if (!els.length) { state.running = false; refreshPanel(); return; }
    els.forEach(el => {
      const text = (el.innerText || '').trim();
      const slot = makeSlot(el);
      const hit = cacheGet(text);
      if (hit) { slot.textContent = hit; el.dataset.ltDone = '1'; state.done++; return; }
      slot.textContent = '翻译中…';
      slot.classList.add('lt-loading');
      state.queue.push({ el, slot, text });
    });
    state.total = state.done + state.queue.length;
    state.running = true;
    refreshPanel();
    pump();
  }

  // ═══════════════ 提示条 ═══════════════
  let toastTimer = null;
  function toast(msg, ms) {
    let t = document.getElementById('lt-toast');
    if (t) t.remove();
    t = document.createElement('div');
    t.id = 'lt-toast';
    t.textContent = msg;
    t.onclick = () => { toggleOriginal(); hideToast(); };
    document.body.appendChild(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, ms || 5000);
  }
  function hideToast() {
    const t = document.getElementById('lt-toast');
    if (!t) return;
    t.classList.add('hide');
    setTimeout(() => t.remove(), 320);
  }


  // ═══════════════ 主题 / 快捷键 / 尺寸 ═══════════════
  function applyTheme(th) {
    cfg.theme = th; saveCfg();
    document.documentElement.classList.toggle('lt-dark', th === 'dark');
    document.documentElement.classList.toggle('lt-light', th === 'light');
    document.querySelectorAll('#lt-theme button').forEach(b => b.classList.toggle('on', b.dataset.th === th));
  }
  function applySize() {
    const r = document.documentElement;
    r.style.setProperty('--lt-w', (cfg.panelWidth || 272) + 'px');
    r.style.setProperty('--lt-scale', String(cfg.scale || 1));
    saveCfg();
  }
  function comboMatches(e, combo) {
    const parts = String(combo || '').toLowerCase().split('+').map(x => x.trim()).filter(Boolean);
    if (!parts.length) return false;
    const key = parts.pop();
    if (!!e.altKey !== parts.includes('alt')) return false;
    if (!!e.ctrlKey !== parts.includes('ctrl')) return false;
    if (!!e.shiftKey !== parts.includes('shift')) return false;
    return (e.key || '').toLowerCase() === key;
  }

  function insertSettingsRows(p) {
    if (p.querySelector('#lt-theme')) return;
    const box = p.querySelector('#lt-settings');
    if (!box) return;
    const wrap = document.createElement('div');
    wrap.innerHTML = `
      <div class="lt-inforow"><span class="lt-k">主题</span>
        <div class="lt-seg" style="flex:1;margin:0" id="lt-theme">
          <button data-th="auto">跟随系统</button><button data-th="dark">深色</button><button data-th="light">浅色</button>
        </div></div>
      <div class="lt-inforow"><span class="lt-k">悬停</span>
        <div class="lt-seg" style="flex:1;margin:0" id="lt-hd">
          <button data-ms="200">灵敏</button><button data-ms="350">标准</button><button data-ms="600">迟钝</button>
        </div></div>
      <div class="lt-inforow"><span class="lt-k">快捷键</span>
        <div class="lt-chip" style="gap:4px">
          <input class="lt-key" data-k="translate" title="翻译本页" value="${cfg.keys.translate}">
          <input class="lt-key" data-k="original" title="显示原文" value="${cfg.keys.original}">
          <input class="lt-key" data-k="auto" title="自动翻译" value="${cfg.keys.auto}">
          <input class="lt-key" data-k="mode" title="双语/仅译文" value="${cfg.keys.mode}">
        </div></div>
      <div class="lt-inforow"><span class="lt-k">面板宽</span>
        <input type="range" id="lt-w" min="240" max="360" step="4" value="${cfg.panelWidth}" style="flex:1">
        <span class="lt-k" style="flex:0 0 34px;text-align:right" id="lt-wv">${cfg.panelWidth}</span></div>
      <div class="lt-inforow"><span class="lt-k">字号</span>
        <input type="range" id="lt-s" min="0.9" max="1.25" step="0.05" value="${cfg.scale}" style="flex:1">
        <span class="lt-k" style="flex:0 0 34px;text-align:right" id="lt-sv">${Number(cfg.scale).toFixed(2)}</span></div>`;
    box.insertBefore(wrap, box.firstChild);
  }

  function bindShortcuts(p, $) {
    insertSettingsRows(p);
    applyTheme(cfg.theme);
    applySize();
    p.querySelectorAll('#lt-theme button').forEach(b => b.onclick = () => applyTheme(b.dataset.th));
    p.querySelectorAll('#lt-hd button').forEach(b => b.onclick = () => {
      cfg.hoverDelay = parseInt(b.dataset.ms, 10); saveCfg();
      p.querySelectorAll('#lt-hd button').forEach(x => x.classList.toggle('on', x === b));
      toast('悬停灵敏度：' + b.textContent + '（' + cfg.hoverDelay + 'ms）', 2200);
    });
    p.querySelectorAll('.lt-key').forEach(inp => {
      inp.onchange = () => {
        cfg.keys[inp.dataset.k] = inp.value.trim() || cfg.keys[inp.dataset.k];
        inp.value = cfg.keys[inp.dataset.k]; saveCfg();
        toast('快捷键 ' + inp.title + ' → ' + inp.value, 2400);
      };
    });
    const w = p.querySelector('#lt-w'), sv = p.querySelector('#lt-s');
    if (w) w.oninput = () => { cfg.panelWidth = parseInt(w.value, 10); p.querySelector('#lt-wv').textContent = w.value; applySize(); };
    if (sv) sv.oninput = () => { cfg.scale = parseFloat(sv.value); p.querySelector('#lt-sv').textContent = cfg.scale.toFixed(2); applySize(); };
  }

  // ═══════════════ 折叠 / 拖动 ═══════════════
  let panelEl = null, bubbleEl = null;
  function setCollapsed(v) {
    cfg.collapsed = !!v; saveCfg();
    if (panelEl) panelEl.style.display = cfg.collapsed ? 'none' : 'block';
    if (bubbleEl) bubbleEl.style.display = cfg.collapsed ? 'flex' : 'none';
  }
  function makeDraggable(handle, target, key) {
    let sx = 0, sy = 0, ox = 0, oy = 0, moving = false;
    handle.addEventListener('mousedown', e => {
      const tn = (e.target.tagName || '').toUpperCase();
      if (tn === 'BUTTON' || tn === 'SELECT' || tn === 'OPTION' || tn === 'INPUT') return;
      moving = true; sx = e.clientX; sy = e.clientY;
      const r = target.getBoundingClientRect(); ox = r.left; oy = r.top;
      target.style.left = ox + 'px'; target.style.top = oy + 'px';
      target.style.right = 'auto'; target.style.bottom = 'auto';
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!moving) return;
      target.style.left = Math.max(0, Math.min(window.innerWidth - 50, ox + e.clientX - sx)) + 'px';
      target.style.top = Math.max(0, Math.min(window.innerHeight - 36, oy + e.clientY - sy)) + 'px';
    });
    document.addEventListener('mouseup', () => {
      if (!moving) return; moving = false;
      GM_setValue(key, JSON.stringify({ left: target.style.left, top: target.style.top }));
    });
  }
  function restorePos(el, key) {
    try {
      const p = JSON.parse(GM_getValue(key, 'null'));
      if (p && p.left) { el.style.left = p.left; el.style.top = p.top; el.style.right = 'auto'; el.style.bottom = 'auto'; }
    } catch (e) { /* 忽略 */ }
  }

  // ═══════════════ 模式 / 样式 / 原文开关 ═══════════════
  function setMode(m, silent) {
    cfg.mode = m; saveCfg();
    document.documentElement.classList.toggle('lt-only', m === 'only');
    const b1 = document.getElementById('lt-both'), b2 = document.getElementById('lt-only');
    if (b1) b1.classList.toggle('on', m === 'both');
    if (b2) b2.classList.toggle('on', m === 'only');
    if (!silent) refreshPanel();
  }
  function setStyle(s, silent) {
    cfg.style = s; saveCfg();
    ['line', 'underline', 'fade'].forEach(k => document.documentElement.classList.toggle('lt-s-' + k, k === s));
    document.querySelectorAll('#lt-style button').forEach(b => b.classList.toggle('on', b.dataset.s === s));
    if (!silent) refreshPanel();
  }
  function toggleOriginal(force) {
    const hide = (force === undefined) ? !document.documentElement.classList.contains('lt-hide') : !!force;
    document.documentElement.classList.toggle('lt-hide', hide);
    const b = document.getElementById('lt-show');
    if (b) {
      b.classList.toggle('on', hide);
      b.querySelector('.lt-label').textContent = hide ? '显示原文' : '隐藏译文';
    }
    return hide;
  }

  // ═══════════════ 面板 ═══════════════
  function buildPanel() {
    const p = document.createElement('div');
    p.id = 'lt-panel';
    const langOpts = Object.entries(LANGS).map(([k, v]) =>
      `<option value="${k}"${k === cfg.lang ? ' selected' : ''}>${v.from}</option>`).join('');
    p.innerHTML = `
      <div class="lt-hd">
        <span class="lt-logo">${icon('trans')}</span>
        <span class="lt-title">本地翻译<i>.</i></span>
        <span class="lt-ico" id="lt-settings-btn" title="设置">${icon('gear')}</span>
        <span class="lt-ico" id="lt-min" title="收起为圆球">${icon('chev')}</span>
      </div>
      <div class="lt-bd">
        <div class="lt-langrow">
          <select id="lt-lang" title="源语言">${langOpts}</select>
          <span class="lt-arrow">${icon('arrow')}</span>
          <span class="lt-to" id="lt-to" title="目标语言">${LANGS[cfg.lang].to}</span>
        </div>

        <div class="lt-inforow">
          <span class="lt-k">服务</span>
          <select id="lt-model" title="本地模型">${Object.entries(MODELS).map(([k, v]) =>
            `<option value="${k}"${k === cfg.model ? ' selected' : ''}>${v}</option>`).join('')}</select>
          <span class="lt-dot" id="lt-dot" title="服务状态"></span>
        </div>

        <button class="lt-primary" id="lt-go">${icon('trans')} 翻译本页 <span class="lt-hint">(Alt+T)</span></button>

        <div class="lt-grid">
          <div class="lt-g on" id="lt-both" title="原文 + 译文">${icon('list')}双语对照</div>
          <div class="lt-g" id="lt-only" title="只显示译文">${icon('text')}仅译文</div>
          <div class="lt-g" id="lt-show" title="Alt+O">${icon('eye')}<span class="lt-label">隐藏译文</span></div>
          <div class="lt-g" id="lt-clear" title="只清当前页">${icon('trash')}清除译文</div>
          <div class="lt-g" id="lt-auto" title="Alt+A">${icon('zap')}自动翻译</div>
          <div class="lt-g" id="lt-jp" title="只翻含假名的段落">${icon('globe')}只翻日文</div>
          <div class="lt-g" id="lt-hover" title="鼠标停 0.35 秒就地出译文">${icon('cursor')}鼠标悬停</div>
          <div class="lt-g" id="lt-input" title="输入框内按 Alt+Enter 翻译">${icon('keyboard')}输入框翻译</div>
        </div>

        <div id="lt-settings">
          <div class="lt-inforow" style="margin-top:10px"><span class="lt-k">风格</span>
            <select id="lt-tpl">${Object.entries(TEMPLATES).map(([k, v]) =>
              `<option value="${k}"${k === cfg.template ? ' selected' : ''}>${v}</option>`).join('')}</select>
          </div>
          <div class="lt-inforow"><span class="lt-k">样式</span>
            <div class="lt-seg" id="lt-style" style="flex:1;margin:0">
              ${Object.entries(STYLES).map(([k, v]) =>
                `<button data-s="${k}" class="${k === cfg.style ? 'on' : ''}">${v}</button>`).join('')}
            </div>
          </div>
          <div class="lt-inforow"><span class="lt-k">完成提示</span>
            <div class="lt-seg" style="flex:1;margin:0" id="lt-toastopt">
              <button data-t="1" class="${cfg.toast ? 'on' : ''}">开</button>
              <button data-t="0" class="${cfg.toast ? '' : 'on'}">关</button>
            </div>
          </div>
          <div class="lt-inforow"><span class="lt-k">缓存</span>
            <div class="lt-seg" style="flex:1;margin:0"><button id="lt-clearcache">清空翻译缓存</button><button id="lt-export" title="导出历史为 Anki 可导入 TSV">导出 Anki</button></div>
          </div>
          <div class="lt-tip">Alt+T 翻译 · Alt+O 显示原文 · Alt+B 双语/仅译文 · Alt+A 自动翻译<br>划词出小气泡 · 输入框内 <b>Alt+Enter</b> 直接翻译 · 开启悬停后停 0.35 秒出译文</div>
        </div>

        <div class="lt-foot"><span id="lt-progress">就绪</span><span id="lt-ver">v2.4 · 折叠思维</span></div>
      </div>`;
    document.body.appendChild(p);
    panelEl = p;
    const $ = s => p.querySelector(s);

    $('#lt-go').onclick = () => { state.done = state.errors = state.total = 0; translatePage(); };
    $('#lt-both').onclick = () => setMode('both');
    $('#lt-only').onclick = () => {
      setMode('only');
      if (!state.total) { state.done = state.errors = state.total = 0; translatePage(); }   // 还没翻过就直接开翻
    };
    $('#lt-show').onclick = () => toggleOriginal();
    $('#lt-clear').onclick = () => {
      document.querySelectorAll('.lt-trans').forEach(n => n.remove());
      document.querySelectorAll('[data-lt-done]').forEach(n => delete n.dataset.ltDone);
      state.done = state.total = state.errors = 0; state.queue = []; state.running = false;
      hideToast(); refreshPanel();
    };
    $('#lt-auto').onclick = () => {
      cfg.auto = !cfg.auto; saveCfg();
      $('#lt-auto').classList.toggle('on', cfg.auto);
      if (cfg.auto && !state.running) translatePage();
    };
    $('#lt-jp').onclick = () => {
      cfg.onlyJapanese = !cfg.onlyJapanese; saveCfg();
      $('#lt-jp').classList.toggle('on', cfg.onlyJapanese);
    };
    if ($('#lt-hover')) {
      $('#lt-hover').classList.toggle('on', cfg.hover);
      $('#lt-hover').onclick = () => {
        cfg.hover = !cfg.hover; saveCfg();
        $('#lt-hover').classList.toggle('on', cfg.hover);
        toast(cfg.hover ? '已开启鼠标悬停翻译（停 0.35 秒出译文）' : '已关闭鼠标悬停翻译', 2800);
      };
    }
    if ($('#lt-input')) {
      $('#lt-input').classList.toggle('on', cfg.inputTr);
      $('#lt-input').onclick = () => {
        cfg.inputTr = !cfg.inputTr; saveCfg();
        $('#lt-input').classList.toggle('on', cfg.inputTr);
        toast(cfg.inputTr ? '已开启输入框翻译（框内按 Alt+Enter）' : '已关闭输入框翻译', 2800);
      };
    }
    $('#lt-lang').onchange = e => {
      cfg.lang = e.target.value; saveCfg();
      const to = p.querySelector('#lt-to');
      if (to) to.textContent = LANGS[cfg.lang].to;
    };
    $('#lt-model').onchange = e => { cfg.model = e.target.value; saveCfg(); };
    if ($('#lt-tpl')) $('#lt-tpl').onchange = e => { cfg.template = e.target.value; saveCfg(); toast('翻译风格：' + TEMPLATES[cfg.template], 2200); };
    $('#lt-style').onclick = e => { if (e.target.dataset.s) setStyle(e.target.dataset.s); };
    $('#lt-toastopt').onclick = e => {
      if (e.target.dataset.t === undefined) return;
      cfg.toast = e.target.dataset.t === '1'; saveCfg();
      p.querySelectorAll('#lt-toastopt button').forEach(b =>
        b.classList.toggle('on', b.dataset.t === (cfg.toast ? '1' : '0')));
    };
    $('#lt-clearcache').onclick = () => {
      cache = {}; GM_setValue('cache', '{}');
      const s = $('#lt-progress'); if (s) s.textContent = '缓存已清空';
      setTimeout(refreshPanel, 1300);
    };
    if ($('#lt-export')) $('#lt-export').onclick = () => {
      const url = cfg.server.replace(/\/$/, '') + '/export/anki.tsv';
      try { window.open(url, '_blank'); toast('已打开导出（浏览器会下载 TSV）', 2600); }
      catch (e) { toast('打开失败，手动访问 ' + url, 4000); }
    };
    $('#lt-settings-btn').onclick = () => $('#lt-settings').classList.toggle('open');
    $('#lt-min').onclick = () => setCollapsed(true);

    const b = document.createElement('div');
    b.id = 'lt-bubble';
    b.title = '本地翻译（点击展开）';
    b.innerHTML = icon('globe') + '<span class="lt-badge" style="display:none"></span>';
    document.body.appendChild(b);
    b.onclick = () => setCollapsed(false);
    bubbleEl = b;

    makeDraggable(p.querySelector('.lt-hd'), p, 'pos.panel');
    makeDraggable(b, b, 'pos.bubble');
    restorePos(p, 'pos.panel');
    restorePos(b, 'pos.bubble');
  }


  // ═══════════════ 鼠标悬停翻译 ═══════════════
  let hoverTimer = null, hoverEl = null;
  function onHover(e) {
    if (!cfg.hover) return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    if (el.closest('#lt-panel,#lt-bubble,#lt-toast,#lt-pop')) return;
    if (hoverEl === el) return;
    clearTimeout(hoverTimer);
    hoverEl = el;
    hoverTimer = setTimeout(() => {
      if (!cfg.hover || !worth(el)) return;
      const text = (el.innerText || '').trim();
      const slot = makeSlot(el);
      if (el.dataset.ltDone || slot.classList.contains('lt-loading')) return;
      const hit = cacheGet(text);
      if (hit) { slot.textContent = hit; slot.classList.remove('lt-loading'); el.dataset.ltDone = '1'; return; }
      slot.textContent = '翻译中…';
      slot.classList.add('lt-loading');
      state.queue.push({ el, slot, text });
      state.total = state.done + state.queue.length;
      state.running = true;
      refreshPanel();
      pump();
    }, cfg.hoverDelay || 350);
  }
  function setupHover() {
    document.addEventListener('mouseover', onHover, true);
    document.addEventListener('mouseout', () => { clearTimeout(hoverTimer); hoverEl = null; }, true);
  }

  // ═══════════════ 输入框翻译（Alt+Enter）═══════════════
  function nativeSetValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }
  async function translateInput(el) {
    const isEditable = el.isContentEditable;
    const src = (isEditable ? el.innerText : el.value || '').trim();
    if (!src) return;
    const hit = cacheGet(src);
    if (hit) {
      isEditable ? (el.innerText = hit) : nativeSetValue(el, hit);
      toast('输入框已翻译（缓存命中）', 2600);
      return;
    }
    el.style.outline = '2px solid var(--gh-accent)';
    toast('输入框翻译中…', 1500);
    try {
      const tr = await translate(src);
      cacheSet(src, tr);
      isEditable ? (el.innerText = tr) : nativeSetValue(el, tr);
      toast('✓ 输入框已翻译', 2600);
    } catch (err) {
      toast('翻译失败：' + err.message, 3600);
    } finally {
      el.style.outline = '';
    }
  }
  function setupInput() {
    document.addEventListener('keydown', e => {
      if (!cfg.inputTr) return;
      if (!e.altKey || e.key !== 'Enter') return;
      const el = e.target;
      if (!(el instanceof Element)) return;
      const ok = (el instanceof HTMLTextAreaElement) || (el instanceof HTMLInputElement && /^(text|search|url|email|)$/i.test(el.type || 'text')) || el.isContentEditable;
      if (!ok) return;
      e.preventDefault(); e.stopPropagation();
      translateInput(el);
    }, true);
  }

  // ═══════════════ 划词翻译 ═══════════════
  function selectionPopup() {
    const sel = window.getSelection();
    const text = sel && sel.toString().trim();
    if (!text || text.length < 2) return;
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const old = document.getElementById('lt-pop');
    if (old) old.remove();
    const pop = document.createElement('div');
    pop.id = 'lt-pop';
    pop.innerHTML = '<b>翻译中…</b>';
    pop.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
    pop.style.top = (rect.bottom + window.scrollY + 8) + 'px';
    document.body.appendChild(pop);
    const esc = t => t.replace(/[<>&]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c])).replace(/\n/g, '<br>');
    const show = t => { pop.innerHTML = '<b>译文</b><br>' + esc(t); };
    const hit = cacheGet(text);
    if (hit) show(hit);
    else translate(text).then(t => { cacheSet(text, t); show(t); })
      .catch(e => { pop.innerHTML = '<b>失败</b><br>' + esc(e.message); });
    setTimeout(() => document.addEventListener('mousedown', function off() {
      pop.remove(); document.removeEventListener('mousedown', off);
    }), 120);
  }

  // ═══════════════ 动态内容 ═══════════════
  let pending = null;
  const observer = new MutationObserver(muts => {
    if (!cfg.auto) return;
    const fresh = [];
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1 && !(n.classList && (n.classList.contains('lt-trans') ||
            n.id === 'lt-panel' || n.id === 'lt-bubble' || n.id === 'lt-toast' || n.id === 'lt-pop'))) fresh.push(n);
      }
    }
    if (!fresh.length) return;
    clearTimeout(pending);
    pending = setTimeout(() => fresh.forEach(n => translatePage(n)), 700);
  });

  // ═══════════════ 启动 ═══════════════
  function boot() {
    buildPanel();
    detectPageTheme();
    setMode(cfg.mode, true);
    setStyle(cfg.style, true);
    setCollapsed(cfg.collapsed);
    document.querySelectorAll('#lt-hd button').forEach(b =>
      b.classList.toggle('on', parseInt(b.dataset.ms, 10) === (cfg.hoverDelay || 350)));
    checkService(); setInterval(checkService, 30000);
    setupHover(); setupInput();
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener('mouseup', () => setTimeout(selectionPopup, 60));
    document.addEventListener('keydown', e => {
      const K = cfg.keys || {};
      if (comboMatches(e, K.translate)) { e.preventDefault(); state.done = state.errors = state.total = 0; translatePage(); return; }
      if (comboMatches(e, K.original)) { e.preventDefault(); toggleOriginal(); return; }
      if (comboMatches(e, K.auto)) { e.preventDefault(); const b = document.getElementById('lt-auto'); if (b) b.click(); return; }
      if (comboMatches(e, K.mode)) { e.preventDefault(); setMode(cfg.mode === 'both' ? 'only' : 'both'); }
    });
    refreshPanel();
    if (cfg.auto) setTimeout(() => translatePage(), 900);
    console.log('[LocalTranslate] v2.4 就绪 ·', cfg.server, '·', cfg.lang, '·', cfg.mode);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
