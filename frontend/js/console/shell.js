/* zcode-hub 控制台 · 外壳：页签生命周期（切走即停）、鉴权引导、状态条 */
(function () {
  const TABS = [
    ['overview', 'ZHOverview', '概览'],
    ['accounts', 'ZHAccounts', '账号池与授权'],
    ['access', 'ZHAccess', '客户端接入'],
    ['models', 'ZHModels', '模型与计价'],
    ['usage', 'ZHUsage', '用量与明细'],
    ['system', 'ZHSystem', '系统参数'],
    ['activity', 'ZHActivity', '活动日志'],
  ];
  const DOT_RANK = { bad: 0, warn: 1, ok: 2, idle: 3 };

  let cur = null;      // 当前显示的页签
  let active = null;   // 已 activate 的页签（切页签或页面隐藏时清空）
  let ready = false;

  const has = n => TABS.some(t => t[0] === n);
  const mod = n => { const t = TABS.find(x => x[0] === n); return t ? window[t[1]] : null; };
  const fromHash = () => { const h = (location.hash || '').replace(/^#/, ''); return has(h) ? h : 'overview'; };

  function renderTabs() {
    zh$('zh-tabs').innerHTML = TABS.map((t, i) =>
      `<button class="zh-tab" data-tab="${t[0]}" type="button"><span class="zh-tab-no">${String(i + 1).padStart(2, '0')}</span><span>/ ${t[2]}</span></button>`
    ).join('');
  }

  function stop() {
    const m = mod(active);
    if (m && m.deactivate) { try { m.deactivate(); } catch (e) { console.error(e); } }
    active = null;
  }
  function start(name) {
    active = name;
    const m = mod(name);
    if (m && m.activate) { try { m.activate(); } catch (e) { console.error(e); } }
  }
  function show(name) {
    TABS.forEach(t => {
      const sec = zh$('zh-sec-' + t[0]);
      if (sec) sec.classList.toggle('on', t[0] === name);
    });
    document.querySelectorAll('.zh-tab').forEach(b => {
      const on = b.dataset.tab === name;
      b.classList.toggle('active', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    cur = name;
  }
  function go(name) {
    if (!has(name)) name = 'overview';
    if (cur === name && active) return;
    stop();
    show(name);
    start(name);
  }

  /* 顶栏状态条：各页签写自己那一段，圆点取最差 */
  const STATUS = {};
  function paint() {
    const vals = Object.values(STATUS);
    const best = vals.length ? vals.reduce((a, b) => (DOT_RANK[b.dot] < DOT_RANK[a.dot] ? b : a)) : { dot: 'idle' };
    const dot = zh$('zh-dot');
    if (dot) dot.className = 'zh-dot ' + best.dot;
    const txt = zh$('zh-status-text');
    if (txt) txt.innerHTML = vals.filter(v => v.text).map(v => zhEsc(v.text)).join('<span class="sep"> · </span>');
  }
  window.zhStatus = function (key, dot, text) { STATUS[key] = { dot, text }; paint(); };

  zh$('zh-tabs').addEventListener('click', e => {
    const b = e.target.closest('.zh-tab');
    if (!b) return;
    if ((location.hash || '') === '#' + b.dataset.tab) go(b.dataset.tab);
    else location.hash = b.dataset.tab;
  });
  zhOn('logout', () => adminLogout());

  async function boot() {
    renderTabs();
    try {
      const r = await fetch('/meta');
      if (r.ok) zh$('zh-version').textContent = 'v' + (await r.json()).version;
    } catch { /* 拿不到版本不挡页面 */ }

    const key = await adminKey.get();
    if (!key || !await verifyKey(ADMIN_API + '/verify', key).catch(() => false)) {
      const lock = zh$('zh-lock');
      lock.textContent = '未登录或密码已失效，正在跳转登录页…';
      setTimeout(() => { location.href = '/admin/login'; }, 400);
      return;
    }
    zh$('zh-lock').classList.remove('on');
    zhStatus('conn', 'ok', '服务正常');
    ready = true;
    go(fromHash());

    window.addEventListener('hashchange', () => go(fromHash()));
    // 切走即停：换页签或整个页面不可见都清掉轮询
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stop();
      else if (ready && cur && !active) start(cur);
    });
  }

  boot();
})();
