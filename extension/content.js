// ═══════════════════════════════════════════════════════════════════
//  本地翻译扩展 · content script
//  整页翻译 / 双语对照 / 仅译文 / 鼠标悬停 / 划词 / 输入框（Alt+Enter）
//  网络请求一律走 background（避开混合内容限制 ✓）
// ═══════════════════════════════════════════════════════════════════
(() => {
  if (window.__LT_EXT_LOADED__) return;
  window.__LT_EXT_LOADED__ = true;

  const LANGS = { ja2zh: ['日语(日本语)', '简体中文'], zh2ja: ['简体中文', '日语(日本语)'], en2zh: ['English', '简体中文'] };
  const MODELS = { fast: '快 · qwen2.5:3b', best: '准 · qwen2.5:7b' };
  const TEMPLATES = { auto: '通用', subtitle: '字幕', news: '新闻', novel: '轻小说', tech: '技术文档', casual: '口语' };
  const STYLES = { line: '左线', underline: '下划线', fade: '淡化' };

  let cfg = { lang: 'ja2zh', model: 'fast', template: 'auto', mode: 'both', style: 'line',
              onlyJapanese: true, hover: false, hoverDelay: 350, theme: 'auto', auto: false, toast: true };
  let memCache = {};
  const state = { running: false, done: 0, total: 0, errors: 0, queue: [], active: 0 };

  // ─────────── 与 background 通信 ───────────
  function translate(text) {
    return chrome.runtime.sendMessage({ type: 'translate', text, lang: cfg.lang,
                                        model: cfg.model, template: cfg.template })
      .then(r => {
        if (!r || !r.ok) throw new Error((r && r.error) || '翻译失败');
        return r.text || '';
      });
  }
  function health() {
    return chrome.runtime.sendMessage({ type: 'health' }).then(r => !!(r && r.ok));
  }

  // ─────────── 存储（异步持久化，内存同步用）───────────
  function saveCfg() { chrome.storage.local.set({ cfg }); }
  function saveCache() {
    const keys = Object.keys(memCache);
    if (keys.length > 3000) keys.slice(0, keys.length - 3000).forEach(k => delete memCache[k]);
    chrome.storage.local.set({ cache: memCache });
  }
  const cacheGet = t => memCache[t];
  function cacheSet(t, v) { memCache[t] = v; saveCache(); }

  // ─────────── 该不该翻这一段 ───────────
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'CODE', 'PRE', 'KBD', 'SAMP', 'TEXTAREA',
                        'INPUT', 'SELECT', 'OPTION', 'BUTTON', 'SVG', 'CANVAS', 'IFRAME', 'VIDEO', 'AUDIO']);
  const KANA = /[\u3040-\u309f\u30a0-\u30ff]/;
  function worth(el) {
    if (!el || !el.tagName || SKIP.has(el.tagName)) return false;
    if (el.closest('#lt-panel,#lt-bubble,#lt-pop,#lt-toast')) return false;
    if (el.querySelector('.lt-trans')) return false;
    const t = (el.innerText || '').trim();
    if (t.length < 4 || t.length > 3000) return false;
    if (cfg.onlyJapanese && cfg.lang === 'ja2zh' && !KANA.test(t)) return false;
    if (/^[\d\s\p{P}]+$/u.test(t)) return false;
    return true;
  }
  function collect() {
    const out = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        const p = n.parentElement;
        if (!p || SKIP.has(p.tagName) || p.closest('#lt-panel,#lt-bubble,#lt-pop')) return NodeFilter.FILTER_REJECT;
        return n.nodeValue.trim().length >= 4 ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    const seen = new Set();
    let n;
    while ((n = walker.nextNode())) {
      const el = n.parentElement;
      if (!el || seen.has(el) || el.dataset.ltDone || !worth(el)) continue;
      const txt = (el.innerText || '').trim();
      if (!txt || seen.has(el)) continue;
      seen.add(el);
      out.push({ el, text: txt });
    }
    return out;
  }

  // ─────────── 译文插槽 ───────────
  function makeSlot(el) {
    let s = el.nextElementSibling;
    if (s && s.classList && s.classList.contains('lt-trans')) return s;
    s = document.createElement('div');
    s.className = 'lt-trans';
    el.classList.add('lt-src');
    el.insertAdjacentElement('afterend', s);
    return s;
  }
  function applyMode() {
    const h = document.documentElement;
    h.classList.toggle('lt-only', cfg.mode === 'only');
    h.classList.toggle('lt-hide', cfg.mode === 'hide');
    h.classList.toggle('lt-s-line', cfg.style === 'line');
    h.classList.toggle('lt-s-underline', cfg.style === 'underline');
    h.classList.toggle('lt-s-fade', cfg.style === 'fade');
  }

  // ─────────── 并发翻译 ───────────
  const CONC = 4;
  function pump() {
    while (state.active < CONC && state.queue.length) {
      const job = state.queue.shift();
      state.active++;
      (async () => {
        try {
          let out = cacheGet(job.text);
          if (!out) {
            out = await translate(job.text);
            if (!out) throw new Error('空结果');
            cacheSet(job.text, out);
          }
          job.slot.textContent = out;
          job.slot.classList.remove('lt-loading');
          job.el.dataset.ltDone = '1';
          state.done++;
        } catch (e) {
          job.slot.textContent = '（翻译失败：' + String(e.message || e).slice(0, 40) + '）';
          job.slot.classList.add('lt-err');
          job.slot.classList.remove('lt-loading');
          state.errors++;
        } finally {
          state.active--;
          refresh();
          if (state.queue.length) pump();
          else if (!state.active) finish();
        }
      })();
    }
  }
  function translatePage() {
    const items = collect();
    if (!items.length) { toast('本页没有需要翻译的段落'); return; }
    state.total = items.length; state.done = 0; state.errors = 0; state.running = true;
    document.documentElement.classList.remove('lt-hide');
    for (const it of items) {
      const slot = makeSlot(it.el);
      const hit = cacheGet(it.text);
      if (hit) { slot.textContent = hit; it.el.dataset.ltDone = '1'; state.done++; continue; }
      slot.textContent = '翻译中…';
      slot.classList.add('lt-loading');
      state.queue.push({ el: it.el, slot, text: it.text });
    }
    refresh();
    if (state.queue.length) pump(); else finish();
  }
  function finish() {
    state.running = false;
    refresh();
    if (cfg.toast && state.total) {
      toast('已翻译 ' + state.done + ' 段' + (state.errors ? '（' + state.errors + ' 段失败）' : '') + ' · 点击显示原文');
    }
  }
  function clearPage() {
    document.querySelectorAll('.lt-trans').forEach(n => n.remove());
    document.querySelectorAll('[data-lt-done]').forEach(n => delete n.dataset.ltDone);
    state.done = state.total = state.errors = 0; state.queue = []; state.running = false;
    refresh();
  }

  // ─────────── 面板 ───────────
  let panel, bubble, toastEl;
  const $ = s => document.querySelector(s);

  function buildPanel() {
    panel = document.createElement('div');
    panel.id = 'lt-panel';
    panel.innerHTML = `
      <div class="lt-hd">
        <span class="lt-logo">译</span>
        <span class="lt-title">本地翻译<i>.</i></span>
        <span class="lt-ico" id="lt-min" title="收起为圆球">⌄</span>
      </div>
      <div class="lt-bd">
        <div class="lt-langrow">
          <select id="lt-lang">${Object.entries(LANGS).map(([k, v]) =>
            `<option value="${k}"${k === cfg.lang ? ' selected' : ''}>${v[0]}</option>`).join('')}</select>
          <span class="lt-arrow">→</span>
          <span class="lt-to" id="lt-to">${LANGS[cfg.lang][1]}</span>
        </div>
        <div class="lt-inforow"><span class="lt-k">服务</span>
          <select id="lt-model">${Object.entries(MODELS).map(([k, v]) =>
            `<option value="${k}"${k === cfg.model ? ' selected' : ''}>${v}</option>`).join('')}</select>
          <span class="lt-dot" id="lt-dot" title="服务状态"></span>
        </div>
        <div class="lt-inforow"><span class="lt-k">风格</span>
          <select id="lt-tpl">${Object.entries(TEMPLATES).map(([k, v]) =>
            `<option value="${k}"${k === cfg.template ? ' selected' : ''}>${v}</option>`).join('')}</select>
        </div>
        <button class="lt-primary" id="lt-go">翻译本页 <span class="lt-hint">(Alt+T)</span></button>
        <div class="lt-grid">
          <div class="lt-g${cfg.mode === 'both' ? ' on' : ''}" id="lt-both">双语对照</div>
          <div class="lt-g${cfg.mode === 'only' ? ' on' : ''}" id="lt-only">仅译文</div>
          <div class="lt-g" id="lt-clear">清除译文</div>
          <div class="lt-g${cfg.hover ? ' on' : ''}" id="lt-hover">鼠标悬停</div>
        </div>
        <div class="lt-foot"><span id="lt-progress">就绪</span><span>v1.0 · 扩展</span></div>
      </div>`;
    document.body.appendChild(panel);

    bubble = document.createElement('div');
    bubble.id = 'lt-bubble';
    bubble.title = '本地翻译（点击展开）';
    bubble.textContent = '译';
    document.body.appendChild(bubble);

    $('#lt-go').onclick = () => { state.done = state.total = state.errors = 0; state.queue = []; translatePage(); };
    $('#lt-both').onclick = () => setMode('both');
    $('#lt-only').onclick = () => { setMode('only'); if (!state.total) translatePage(); };
    $('#lt-clear').onclick = clearPage;
    $('#lt-hover').onclick = () => {
      cfg.hover = !cfg.hover; saveCfg();
      $('#lt-hover').classList.toggle('on', cfg.hover);
      toast(cfg.hover ? '已开启鼠标悬停翻译' : '已关闭鼠标悬停翻译');
    };
    $('#lt-lang').onchange = e => { cfg.lang = e.target.value; saveCfg(); $('#lt-to').textContent = LANGS[cfg.lang][1]; };
    $('#lt-model').onchange = e => { cfg.model = e.target.value; saveCfg(); };
    $('#lt-tpl').onchange = e => { cfg.template = e.target.value; saveCfg(); };
    $('#lt-min').onclick = () => setCollapsed(true);
    bubble.onclick = () => setCollapsed(false);
    drag(panel, panel.querySelector('.lt-hd'));
    drag(bubble, bubble);
    applyMode();
    checkService();
    setInterval(checkService, 30000);
  }

  function setMode(m) {
    cfg.mode = m; saveCfg(); applyMode();
    const b = $('#lt-both'), o = $('#lt-only');
    if (b) b.classList.toggle('on', m === 'both');
    if (o) o.classList.toggle('on', m === 'only');
  }
  function setCollapsed(v) {
    cfg.collapsed = v; saveCfg();
    if (panel) panel.style.display = v ? 'none' : '';
    if (bubble) bubble.style.display = v ? 'flex' : 'none';
    if (!v) checkService();
  }
  function refresh() {
    const p = $('#lt-progress');
    if (!p) return;
    p.textContent = state.running
      ? `翻译中 ${state.done}/${state.total}${state.errors ? ' · 失败 ' + state.errors : ''}`
      : (state.total ? `已译 ${state.done}/${state.total}` : '就绪');
  }
  async function checkService() {
    const dot = $('#lt-dot');
    if (!dot) return;
    const ok = await health();
    dot.classList.toggle('ok', ok);
    dot.classList.toggle('bad', !ok);
    dot.title = ok ? '本地翻译服务在线' : '服务未启动（运行 server.py）';
  }
  function drag(el, handle) {
    let sx = 0, sy = 0, ox = 0, oy = 0, on = false;
    handle.addEventListener('mousedown', e => {
      on = true; sx = e.clientX; sy = e.clientY;
      const r = el.getBoundingClientRect(); ox = r.left; oy = r.top;
      e.preventDefault();
    });
    document.addEventListener('mousemove', e => {
      if (!on) return;
      el.style.left = (ox + e.clientX - sx) + 'px';
      el.style.top = (oy + e.clientY - sy) + 'px';
      el.style.right = 'auto'; el.style.bottom = 'auto';
    });
    document.addEventListener('mouseup', () => { on = false; });
  }

  // ─────────── 划词气泡 ───────────
  function showPop(text, x, y) {
    let pop = $('#lt-pop');
    if (!pop) { pop = document.createElement('div'); pop.id = 'lt-pop'; document.body.appendChild(pop); }
    pop.innerHTML = '<b>翻译中…</b>';
    pop.style.left = Math.min(x, window.innerWidth - 440) + 'px';
    pop.style.top = (y + window.scrollY + 14) + 'px';
    pop.style.display = 'block';
    const hit = cacheGet(text);
    if (hit) { pop.innerHTML = '<b>' + hit + '</b>'; return; }
    translate(text).then(out => {
      cacheSet(text, out);
      pop.innerHTML = '<b>' + out + '</b>';
    }).catch(e => { pop.innerHTML = '<b>失败：' + String(e.message || e).slice(0, 50) + '</b>'; });
  }
  document.addEventListener('mouseup', e => {
    if (e.target.closest && e.target.closest('#lt-panel,#lt-bubble,#lt-pop')) return;
    const sel = String(window.getSelection() || '').trim();
    const pop = $('#lt-pop');
    if (!sel || sel.length < 2 || sel.length > 1200) { if (pop) pop.style.display = 'none'; return; }
    showPop(sel, e.clientX, e.clientY);
  });

  // ─────────── 鼠标悬停 ───────────
  let hoverTimer = null, hoverEl = null;
  document.addEventListener('mouseover', e => {
    if (!cfg.hover) return;
    const el = e.target;
    if (!(el instanceof Element) || el.closest('#lt-panel,#lt-bubble,#lt-pop')) return;
    if (hoverEl === el) return;
    hoverEl = el;
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => {
      if (!cfg.hover || !worth(el)) return;
      const text = (el.innerText || '').trim();
      const slot = makeSlot(el);
      const hit = cacheGet(text);
      if (hit) { slot.textContent = hit; el.dataset.ltDone = '1'; return; }
      slot.textContent = '翻译中…'; slot.classList.add('lt-loading');
      state.queue.push({ el, slot, text }); state.total = ++state.total;
      state.running = true; refresh(); pump();
    }, cfg.hoverDelay || 350);
  }, true);

  // ─────────── 输入框（Alt+Enter）───────────
  document.addEventListener('keydown', e => {
    if (!e.altKey) return;
    const el = e.target;
    if (el instanceof HTMLTextAreaElement || (el instanceof HTMLInputElement && /^(text|search|url|email|)$/i.test(el.type || 'text'))) {
      if (e.key === 'Enter') { e.preventDefault(); doInput(el); return; }
    }
    if (e.key && e.key.toLowerCase() === 't') { e.preventDefault(); translatePage(); }
  }, true);

  async function doInput(el) {
    const src = (el.value || '').trim();
    if (!src) return;
    el.style.outline = '2px solid #3fb950';
    try {
      let out = cacheGet(src);
      if (!out) { out = await translate(src); cacheSet(src, out); }
      const desc = Object.getOwnPropertyDescriptor(
        el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value');
      if (desc && desc.set) desc.set.call(el, out); else el.value = out;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      toast('✓ 输入框已翻译');
    } catch (e) { toast('翻译失败：' + String(e.message || e).slice(0, 40)); }
    finally { el.style.outline = ''; }
  }

  // ─────────── 提示条 ───────────
  function toast(msg, ms) {
    if (!toastEl) {
      toastEl = document.createElement('div');
      toastEl.id = 'lt-toast';
      toastEl.onclick = () => { document.documentElement.classList.toggle('lt-hide'); toastEl.classList.add('hide'); };
      document.body.appendChild(toastEl);
    }
    toastEl.textContent = msg;
    toastEl.classList.remove('hide');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => toastEl.classList.add('hide'), ms || 4200);
  }

  // ─────────── 来自 background 的命令 ───────────
  chrome.runtime.onMessage.addListener(msg => {
    if (!msg || !msg.type) return;
    if (msg.type === 'toggle') { document.querySelectorAll('.lt-trans').length ? clearPage() : translatePage(); }
    if (msg.type === 'mode') setMode(cfg.mode === 'both' ? 'only' : 'both');
    if (msg.type === 'hide') document.documentElement.classList.toggle('lt-hide');
    if (msg.type === 'clear') clearPage();
    if (msg.type === 'selection' && msg.text) showPop(msg.text.trim(), 40, 80);
  });

  // ─────────── 样式（折叠思维 / GitHub Primer 令牌）───────────
  const CSS = `
  :root{--gh-bg:#0d1117;--gh-surface:#161b22;--gh-subtle:#21262d;--gh-ink:#e6edf3;--gh-muted:#8b949e;
    --gh-line:#30363d;--gh-accent:#3fb950;--gh-accent2:#238636;--gh-link:#58a6ff;--gh-danger:#f85149}
  @media (prefers-color-scheme: light){:root{--gh-bg:#fff;--gh-surface:#fff;--gh-subtle:#f6f8fa;--gh-ink:#1f2328;
    --gh-muted:#59636e;--gh-line:#d1d9e0;--gh-accent:#1f883d;--gh-accent2:#1f883d;--gh-link:#0969da;--gh-danger:#cf222e}}
  html{--lt-t:#0969da;--lt-l:#1f883d}
  html.lt-dark-page{--lt-t:#58a6ff;--lt-l:#3fb950}
  .lt-trans{display:block;margin:.28em 0 .55em;font:inherit;line-height:inherit;color:var(--lt-t);
    white-space:pre-wrap;word-break:break-word}
  html.lt-s-line .lt-trans{border-left:3px solid var(--lt-l);padding-left:.72em}
  html.lt-s-underline .lt-trans{border-bottom:1px dashed var(--lt-l);padding-bottom:.12em}
  html.lt-s-fade .lt-trans{opacity:.86}
  .lt-trans.lt-loading{color:var(--gh-muted);font-style:italic;border-color:var(--gh-muted)}
  .lt-trans.lt-err{color:#f85149;font-style:italic}
  html.lt-hide .lt-trans{display:none}
  html.lt-only .lt-src{display:none!important}
  html.lt-only .lt-trans{border:0!important;padding:0!important;color:inherit;opacity:1;margin:0 0 .55em}
  #lt-panel{position:fixed;right:16px;bottom:16px;z-index:2147483600;width:272px;background:var(--gh-surface);
    color:var(--gh-ink);border:1px solid var(--gh-line);border-radius:6px;box-shadow:0 8px 24px rgba(1,4,9,.5);
    font:12.5px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans","PingFang SC","Microsoft YaHei",sans-serif;
    user-select:none;overflow:hidden}
  #lt-panel .lt-hd{display:flex;align-items:center;gap:8px;padding:11px 13px;cursor:move;
    background:var(--gh-subtle);border-bottom:1px solid var(--gh-line)}
  #lt-panel .lt-logo{width:22px;height:22px;border-radius:6px;display:flex;align-items:center;justify-content:center;
    background:var(--gh-accent2);color:#fff;font-size:12px;font-weight:700}
  #lt-panel .lt-title{flex:1;font-weight:600;font-size:13px}
  #lt-panel .lt-title i{color:var(--gh-accent);font-style:normal}
  #lt-panel .lt-ico{width:24px;height:24px;border-radius:6px;display:flex;align-items:center;justify-content:center;
    color:var(--gh-muted);cursor:pointer}
  #lt-panel .lt-ico:hover{background:var(--gh-bg);color:var(--gh-link)}
  #lt-panel .lt-bd{padding:12px 13px 13px}
  #lt-panel .lt-langrow{display:flex;align-items:center;gap:6px;background:var(--gh-bg);border:1px solid var(--gh-line);
    border-radius:6px;padding:6px;margin-bottom:9px}
  #lt-panel select{background:var(--gh-subtle);border:1px solid var(--gh-line);border-radius:6px;color:var(--gh-ink);
    padding:4px 6px;font:12px/1.4 inherit;cursor:pointer;flex:1;min-width:0}
  #lt-panel .lt-arrow{color:var(--gh-muted)}
  #lt-panel .lt-to{flex:0 0 86px;text-align:center;color:var(--gh-muted);font-size:12px;background:var(--gh-subtle);
    border:1px solid var(--gh-line);border-radius:6px;padding:4px 6px;overflow:hidden;white-space:nowrap}
  #lt-panel .lt-inforow{display:flex;align-items:center;gap:7px;margin:7px 0;font-size:11.5px;color:var(--gh-muted)}
  #lt-panel .lt-k{flex:0 0 36px}
  #lt-panel .lt-dot{width:8px;height:8px;border-radius:50%;background:var(--gh-muted);flex:0 0 auto}
  #lt-panel .lt-dot.ok{background:var(--gh-accent);box-shadow:0 0 0 3px rgba(63,185,80,.18)}
  #lt-panel .lt-dot.bad{background:#f85149;box-shadow:0 0 0 3px rgba(248,81,73,.18)}
  #lt-panel .lt-primary{width:100%;border:1px solid rgba(240,246,252,.1);border-radius:6px;padding:9px 0;cursor:pointer;
    background:var(--gh-accent2);color:#fff;font:600 13.5px/1 inherit;margin-top:2px}
  #lt-panel .lt-primary:hover{filter:brightness(1.12)}
  #lt-panel .lt-primary .lt-hint{opacity:.8;font-weight:400;font-size:11.5px}
  #lt-panel .lt-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-top:9px}
  #lt-panel .lt-g{display:flex;align-items:center;justify-content:center;background:var(--gh-subtle);
    border:1px solid var(--gh-line);border-radius:6px;padding:8px 4px;cursor:pointer;font:12px/1 inherit;
    color:var(--gh-ink)}
  #lt-panel .lt-g:hover{border-color:var(--gh-link)}
  #lt-panel .lt-g.on{border-color:var(--gh-accent);background:rgba(63,185,80,.12);color:var(--gh-accent);font-weight:600}
  #lt-panel .lt-foot{display:flex;justify-content:space-between;margin-top:10px;padding-top:9px;
    border-top:1px solid var(--gh-line);color:var(--gh-muted);font-size:10.5px}
  #lt-bubble{position:fixed;right:16px;bottom:16px;z-index:2147483600;width:46px;height:46px;border-radius:50%;
    background:var(--gh-accent2);border:1px solid rgba(240,246,252,.15);display:none;align-items:center;
    justify-content:center;cursor:pointer;box-shadow:0 6px 18px rgba(1,4,9,.45);color:#fff;font:700 18px/1 inherit;
    user-select:none}
  #lt-bubble:hover{filter:brightness(1.12)}
  #lt-toast{position:fixed;right:72px;bottom:26px;z-index:2147483600;background:var(--gh-surface);color:var(--gh-ink);
    border:1px solid var(--gh-line);border-radius:6px;padding:9px 14px;font:12.5px/1.4 -apple-system,
    "Microsoft YaHei",sans-serif;cursor:pointer;box-shadow:0 8px 24px rgba(1,4,9,.5);transition:opacity .3s}
  #lt-toast.hide{opacity:0;pointer-events:none}
  #lt-pop{position:absolute;z-index:2147483601;max-width:430px;background:var(--gh-surface);color:var(--gh-ink);
    border:1px solid var(--gh-line);border-radius:6px;padding:10px 13px;font:13.5px/1.75 -apple-system,
    "Microsoft YaHei",sans-serif;box-shadow:0 8px 24px rgba(1,4,9,.5);display:none;white-space:pre-wrap}
  #lt-pop b{color:var(--gh-link)}
  `;

  // ─────────── 启动 ───────────
  function boot() {
    const st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);

    chrome.storage.local.get(['cfg', 'cache'], r => {
      if (r && r.cfg) cfg = Object.assign(cfg, r.cfg);
      if (r && r.cache) memCache = r.cache;
      detectPageTheme();
      buildPanel();
      if (cfg.theme === 'dark') document.documentElement.classList.add('lt-dark');
      if (cfg.theme === 'light') document.documentElement.classList.add('lt-light');
      setCollapsed(!!cfg.collapsed);
      if (cfg.auto) translatePage();
    });
  }
  function detectPageTheme() {
    try {
      const m = (getComputedStyle(document.body).backgroundColor || '').match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      if (!m) return;
      const lum = (0.2126 * +m[1] + 0.7152 * +m[2] + 0.0722 * +m[3]) / 255;
      document.documentElement.classList.toggle('lt-dark-page', lum < 0.45);
    } catch (e) {}
  }

  // 动态内容
  const mo = new MutationObserver(() => { /* 只在开启自动翻译时跟随 */
    if (cfg.auto && !state.running) { clearTimeout(mo._t); mo._t = setTimeout(translatePage, 1200); }
  });

  boot();
  try { mo.observe(document.body, { childList: true, subtree: true }); } catch (e) {}
})();
