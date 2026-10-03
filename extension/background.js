// ═══════════════════════════════════════════════════════════════════
//  本地翻译扩展 · background service worker
//  作用：所有对本地翻译服务的网络请求都在这里发出
//        （内容脚本在 https 页面里 fetch http://127.0.0.1 会被混合内容策略拦掉 ✗）
// ═══════════════════════════════════════════════════════════════════

const API = 'http://127.0.0.1:18765';

async function jfetch(path, opts) {
  const r = await fetch(API + path, opts);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === 'translate') {
    jfetch('/translate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: msg.text,
        lang: msg.lang || 'ja2zh',
        model: msg.model || 'fast',
        template: msg.template || 'auto',
        stream: false
      })
    })
      .then(d => sendResponse({ ok: true, text: (d.text || '').trim() }))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;   // 异步响应
  }

  if (msg.type === 'health') {
    jfetch('/health')
      .then(d => sendResponse({ ok: true, data: d }))
      .catch(e => sendResponse({ ok: false, error: String(e && e.message || e) }));
    return true;
  }
});

// ── 工具栏按钮：翻译 / 还原 当前页
chrome.action.onClicked.addListener(tab => {
  if (!tab || !tab.id) return;
  chrome.tabs.sendMessage(tab.id, { type: 'toggle' }).catch(() => {});
});

// ── 右键菜单
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: 'lt-page', title: '翻译本页（本地模型）', contexts: ['page'] });
    chrome.contextMenus.create({ id: 'lt-only', title: '切换：双语对照 / 仅译文', contexts: ['page'] });
    chrome.contextMenus.create({ id: 'lt-hide', title: '隐藏 / 显示译文', contexts: ['page'] });
    chrome.contextMenus.create({ id: 'lt-clear', title: '清除本页译文', contexts: ['page'] });
    chrome.contextMenus.create({ id: 'lt-sel', title: '翻译选中文本', contexts: ['selection'] });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || !tab.id) return;
  const map = { 'lt-page': 'toggle', 'lt-only': 'mode', 'lt-hide': 'hide', 'lt-clear': 'clear' };
  if (map[info.menuItemId]) {
    chrome.tabs.sendMessage(tab.id, { type: map[info.menuItemId] }).catch(() => {});
  } else if (info.menuItemId === 'lt-sel' && info.selectionText) {
    chrome.tabs.sendMessage(tab.id, { type: 'selection', text: info.selectionText }).catch(() => {});
  }
});

// 首次安装时给内容脚本一个默认配置
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(['cfg'], r => {
    if (!r || !r.cfg) {
      chrome.storage.local.set({
        cfg: { lang: 'ja2zh', model: 'fast', template: 'auto', mode: 'both',
               style: 'line', onlyJapanese: true, hover: false, hoverDelay: 350,
               theme: 'auto', auto: false, toast: true }
      });
    }
  });
});
