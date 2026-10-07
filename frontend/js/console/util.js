/* zcode-hub 控制台 · 公共工具（格式化 / 统计卡 / 通用对话框 / 悬停明细） */
const ZH = { ticksPerYuan: 1e6, keep: 500 };

function zh$(id) { return document.getElementById(id); }

function zhEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function zhPad(n) { return String(n).padStart(2, '0'); }

/* 额度/token 计数：万、亿 缩写 */
function zhFmtNum(v) {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + ' 万';
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}
function zhFmtTok(n) {
  if (n == null || isNaN(n)) return '—';
  if (n >= 1e8) return (n / 1e8).toFixed(2) + ' 亿';
  if (n >= 1e4) return (n / 1e4).toFixed(1) + ' 万';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(Math.round(n));
}
function zhFmtCost(ticks, currency) {
  if (ticks == null || isNaN(ticks)) return '—';
  const sym = currency === 'CNY' ? '¥' : (currency ? currency + ' ' : '');
  return sym + (ticks / ZH.ticksPerYuan).toFixed(4);
}
function zhFmtSec(v) {
  if (v == null || isNaN(v)) return '—';
  return v < 1 ? Math.round(v * 1000) + 'ms' : v.toFixed(2) + 's';
}
function zhFmtTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  if (isNaN(d)) return '—';
  return `${zhPad(d.getMonth() + 1)}-${zhPad(d.getDate())} ${zhPad(d.getHours())}:${zhPad(d.getMinutes())}:${zhPad(d.getSeconds())}`;
}
function zhFmtDate(d) {
  if (!d) return '—';
  const dt = new Date(d * 1000);
  return isNaN(dt) ? '—' : dt.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
function zhFmtDateSec(d) {
  if (!d) return '—';
  const dt = new Date(d * 1000);
  return isNaN(dt) ? '—' : dt.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function zhFmtFull(d) {
  if (!d) return '—';
  const dt = new Date(d * 1000);
  return isNaN(dt) ? '—' : dt.toLocaleString('zh-CN', { hour12: false });
}
function zhFmtDur(s) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return s + '秒';
  const m = Math.floor(s / 60);
  if (m < 60) return m + '分';
  const h = Math.floor(m / 60);
  return h + '小时' + (m % 60 ? (m % 60) + '分' : '');
}

/* 统计卡：label 左上、数值右下，tone 只表达正常/异常（mint / red） */
function zhTiles(el, items) {
  const box = typeof el === 'string' ? zh$(el) : el;
  if (!box) return;
  box.innerHTML = (items || []).map(t => `
    <div class="zh-tile ${t.tone || ''}">
      <div class="zh-tile-label">${zhEsc(t.label)}</div>
      <div class="zh-tile-num"${t.title ? ` title="${zhEsc(t.title)}"` : ''}>${t.valueHtml != null ? t.valueHtml : zhEsc(t.value)}</div>
      ${t.sub != null || t.subHtml != null ? `<div class="zh-tile-sub"${t.subTitle ? ` title="${zhEsc(t.subTitle)}"` : ''}>${t.subHtml != null ? t.subHtml : zhEsc(t.sub)}</div>` : ''}
    </div>`).join('');
}

/* sparkline：调用量走势，110×26。全零时不画 —— 一条贴底的线看着像坏了的下划线 */
function zhSpark(vals, color) {
  const w = 110, h = 26;
  if (!vals || !vals.length || Math.max(...vals) <= 0) return '';
  const max = Math.max(...vals, 1);
  const pts = vals.map((v, i) => `${(i / Math.max(vals.length - 1, 1)) * w},${h - 2 - (v / max) * (h - 5)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/></svg>`;
}

/* ── 浮层（modal-overlay）──
   焦点进得来、回得去，Esc 关得掉 —— 否则键盘用户会被困在浮层后面。 */
let ZH_LAST_FOCUS = null;
function zhOpenOverlay(id) {
  const el = zh$(id);
  if (!el || el.classList.contains('open')) return;
  ZH_LAST_FOCUS = document.activeElement;
  el.classList.add('open');
  const first = el.querySelector('input:not([type=hidden]),textarea,select,button');
  if (first) first.focus();
}
function zhCloseOverlay(id) {
  const el = zh$(id);
  if (!el || !el.classList.contains('open')) return;
  el.classList.remove('open');
  if (ZH_LAST_FOCUS && document.contains(ZH_LAST_FOCUS)) ZH_LAST_FOCUS.focus();
  ZH_LAST_FOCUS = null;
}
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const open = document.querySelector('.modal-overlay.open');
  if (open) { e.preventDefault(); zhCloseOverlay(open.id); }
});
function zhAnyOverlayOpen() { return !!document.querySelector('.modal-overlay.open'); }
document.querySelectorAll('.modal-overlay').forEach(el => {
  el.addEventListener('click', e => { if (e.target === el) zhCloseOverlay(el.id); });
});

/* ── 通用对话框：确认框与表单共用一个 overlay ── */
function zhDialog(opts) {
  const o = opts || {};
  ZH.dialogCb = o.onOk || null;
  ZH.dialogRead = o.read || null;
  zh$('zh-dialog-title').textContent = o.title || '确认';
  zh$('zh-dialog-body').innerHTML = o.bodyHtml || '';
  const ok = zh$('zh-dialog-ok');
  ok.textContent = o.okLabel || '确认';
  ok.className = 'dialog-btn ' + (o.danger ? 'dialog-btn-danger' : 'dialog-btn-primary');
  zhOpenOverlay('zh-dialog');
}
zh$('zh-dialog-ok').addEventListener('click', async () => {
  const cb = ZH.dialogCb;
  zhCloseOverlay('zh-dialog');
  if (cb) await cb(ZH.dialogRead ? ZH.dialogRead() : null);
});
zh$('zh-dialog').querySelector('[data-act="dialog-cancel"]').addEventListener('click', () => zhCloseOverlay('zh-dialog'));

function zhConfirm(title, bodyHtml, onOk, okLabel) {
  zhDialog({ title, bodyHtml, onOk, okLabel: okLabel || '确认', danger: true });
}

/* 事件委托：控制台内所有按钮用 data-act，不用内联 onclick */
const ZH_ACTS = {};
function zhOn(act, fn) { ZH_ACTS[act] = fn; }
document.addEventListener('click', e => {
  const t = e.target && e.target.closest ? e.target.closest('[data-act]') : null;
  if (!t) return;
  const fn = ZH_ACTS[t.dataset.act];
  if (fn) { e.preventDefault(); fn(t, e); }
});

/* ── 模型芯片墙（04 / 模型与计价）──
   全集 = 后端 model_capability（上游实际提供：实测常量 ∪ 号池权益推导），
   **不是**价目表的键。价目表是从开放平台文档站拉下来的另一个产品的账，
   拿它当模型全集会宣称「上游有 32 个模型」，而 ZCode 实际只给两个。
   圆点 = 号池额度，红标签 = 白名单策略挡在外面（能力与策略是两个口径，分开显示）；
   徽章是开放平台参考价折算，前缀「参考」——上游按 units 出账，从不按货币。
   真正的按号 3006 钉死状态没有对应字段，这里不臆造。 */
const ZH_CHIP_LEGEND = [['quota', '号池有额度'], ['zero', '号池额度已用完'], ['nodata', '号池尚无额度数据'], ['out', '名单外（进网关即拒）']];
function zhModelKey(s) { return String(s || '').toLowerCase().replace(/[^a-z0-9]/g, ''); }
function zhTrim(n) { return String(parseFloat(n.toFixed(2))); }
function zhChipBadge(m) {
  const tiers = (m && m.tiers) || [];
  if (!tiers.length) return null;
  const t = tiers.slice().sort((a, b) => (Number(a.min_input) || 0) - (Number(b.min_input) || 0))[0];
  if (t.uncached_input == null && t.output == null) return null;
  return `参考 ${zhTrim((t.uncached_input || 0) / ZH.ticksPerYuan)}/${zhTrim((t.output || 0) / ZH.ticksPerYuan)}`;
}
function zhQuotaMap(accounts) {
  const quotaKeys = new Map();   // 归一名 → 池内剩余量（<=0 记 0，任一账号有余即为有余）
  (accounts || []).forEach(a => {
    Object.entries((a && a.quota) || {}).forEach(([k, w]) => {
      const key = zhModelKey(k);
      if (!key) return;
      const rem = Number(w && w.remaining);
      const cur = quotaKeys.get(key);
      if (!Number.isFinite(rem) || rem <= 0) { if (cur == null) quotaKeys.set(key, 0); return; }
      quotaKeys.set(key, (cur || 0) + rem);
    });
  });
  return quotaKeys;
}
function zhChipPriceMap(models) {
  // 价目表按归一名索引，供能力口径的芯片取徽章；对不上就是没参考价
  const byKey = new Map();
  Object.entries(models || {}).forEach(([name, m]) => byKey.set(zhModelKey(name), m));
  return byKey;
}
function zhChips(el, capability, models, effective, accounts) {
  const box = typeof el === 'string' ? zh$(el) : el;
  if (!box) return;
  const allowedKeys = new Set((effective || []).map(zhModelKey));
  const quotaKeys = zhQuotaMap(accounts);
  const priceKeys = zhChipPriceMap(models);
  const items = (capability || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
  const DOT = { quota: '号池有额度', zero: '号池额度已用完', nodata: '号池尚无该模型的额度数据' };
  box.innerHTML = items.map(item => {
    const k = zhModelKey(item.name);
    const q = quotaKeys.get(k);
    const st = q == null ? 'nodata' : (q > 0 ? 'quota' : 'zero');
    const out = !allowedKeys.has(k);
    const badge = zhChipBadge(priceKeys.get(k));
    const tip = `${DOT[st]}${out ? ' · 名单外，进网关即拒' : ''} · 点击复制`;
    return `<button type="button" class="zh-chip ${st}${out ? ' out' : ''}" data-act="chip-copy" data-name="${zhEsc(item.name)}"`
      + ` aria-label="${zhEsc(`${item.name}，${tip}`)}" title="${zhEsc(tip)}">`
      + `<i class="dot" aria-hidden="true"></i><span class="zh-chip-name">${zhEsc(item.name)}</span>`
      + (badge ? `<span class="zh-chip-price">${zhEsc(badge)}</span>` : '')
      + (out ? '<span class="zh-chip-tag">名单外</span>' : '')
      + '</button>';
  }).join('') || '<div class="zh-empty">号池还没有额度数据，暂时推不出上游提供哪些模型。</div>';
  const legend = typeof el === 'string' ? zh$(el.replace('-chips', '-legend')) : null;
  if (legend) legend.innerHTML = ZH_CHIP_LEGEND.map(([k, l]) => `<span><i class="${k}"></i>${l}</span>`).join('');
}
zhOn('chip-copy', t => zhCopy(t.dataset.name));

/* ── 复制到剪贴板（非安全上下文回落 execCommand） ── */
async function zhCopy(text) {
  try {
    await navigator.clipboard.writeText(text);
    showToast('已复制 ' + text, 'success');
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    ta.remove();
    showToast(ok ? '已复制 ' + text : '复制失败', ok ? 'success' : 'error');
  }
}

/* ── 最近请求 tick 悬停明细：全局单例、fixed 定位（不受 .acct-scroll 裁剪）、贴边翻转 ── */
(function () {
  const tip = zh$('zh-tick-tip');
  if (!tip) return;
  const hide = () => tip.classList.remove('show');
  document.addEventListener('mouseover', e => {
    const t = e.target && e.target.closest ? e.target.closest('.tickbar i') : null;
    if (!t) { hide(); return; }
    const raw = t.dataset.tip || '';
    const nl = raw.indexOf('\n');
    const head = nl < 0 ? raw : raw.slice(0, nl);
    const body = nl < 0 ? '' : raw.slice(nl + 1);
    tip.innerHTML = `<span class="tt-head ${t.classList.contains('ok') ? 'is-ok' : 'is-bad'}">${zhEsc(head)}</span>`
      + (body ? `<span class="tt-body">${zhEsc(body)}</span>` : '');
    tip.classList.add('show');
    tip.style.left = '0px'; tip.style.top = '0px';
    const r = t.getBoundingClientRect(), tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = r.left + r.width / 2 - tw / 2;
    x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
    let y = r.top - th - 10;
    if (y < 8) y = r.bottom + 10;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  window.addEventListener('scroll', hide, true);
  window.addEventListener('blur', hide);
})();
