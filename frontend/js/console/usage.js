/* zcode-hub 控制台 · 05 用量与明细
   数据源是内存环形缓冲（最近 500 条，重启清零），所以任何「合计」都只是子集；
   费用列一律标「参考」——上游 ZCode 按 units 出账，钱是按开放平台价目折算的。 */
window.ZHUsage = (function () {
  let entries = [], tab = 'live', timer = null, loaded = false;
  const state = { range: 'today', status: 'all', q: '' };

  function filtered() {
    const now = Date.now() / 1000;
    const midnight = new Date(); midnight.setHours(0, 0, 0, 0);
    const minTs = state.range === '1h' ? now - 3600 : state.range === 'today' ? midnight.getTime() / 1000 : 0;
    const q = state.q.trim().toLowerCase();
    return entries.filter(e => {
      if (e.ts < minTs) return false;
      if (state.status === 'ok' && e.ok !== true) return false;
      if (state.status === 'fail' && e.ok !== false) return false;
      if (state.status === 'inflight' && e.ok !== null) return false;
      if (q) {
        const hay = `${e.account} ${e.model} ${e.key_label} ${e.preview} ${e.error} ${e.endpoint}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderStats(list) {
    const total = list.length;
    const ok = list.filter(e => e.ok === true).length;
    const fail = list.filter(e => e.ok === false).length;
    const inflight = list.filter(e => e.ok === null).length;
    const tin = list.reduce((a, e) => a + (e.input_tokens || 0), 0);
    const tout = list.reduce((a, e) => a + (e.output_tokens || 0), 0);
    const cost = list.reduce((a, e) => a + (e.cost_ticks || 0), 0);
    const priced = list.find(e => e.cost_currency);
    const costCur = priced ? priced.cost_currency : 'CNY';
    const ttfbs = list.filter(e => e.t_first != null).map(e => e.t_first);
    const ttfb = ttfbs.length ? ttfbs.reduce((a, b) => a + b, 0) / ttfbs.length : null;
    const rate = (ok + fail) ? ok / (ok + fail) * 100 : null;

    // 近 30 分钟每分钟调用量走势
    const nowMin = Math.floor(Date.now() / 1000 / 60);
    const buckets = new Array(30).fill(0);
    for (const e of list) {
      const idx = 30 - (nowMin - Math.floor(e.ts / 60));
      if (idx >= 0 && idx < 30) buckets[idx]++;
    }

    zhTiles('usage-tiles', [
      {
        label: '调用', value: total,
        subHtml: zhSpark(buckets, 'var(--accent)'),
        title: `进行中 ${inflight} · 内存保留 ${ZH.keep} 条`,
      },
      {
        // 分母只算已完成的：全是进行中时算出 0.0% 是谎报成功率
        label: '成功率', value: rate == null ? '—' : rate.toFixed(1) + '%',
        tone: rate == null ? '' : (rate >= 95 ? 'mint' : rate >= 80 ? '' : 'red'),
        sub: `成功 ${ok}`,
      },
      {
        label: '失败', value: fail, tone: fail ? 'red' : '',
        sub: fail ? '把状态筛选切到「失败」查看' : '',
      },
      {
        label: 'Token 总量', value: (tin || tout) ? zhFmtTok(tin + tout) : '—',
        sub: `参考费用 ${cost ? zhFmtCost(cost, costCur) : '—'}`,
      },
      { label: '输入', value: zhFmtTok(tin), sub: `占比 ${tin + tout ? Math.round(tin / (tin + tout) * 100) : 0}%` },
      { label: '输出', value: zhFmtTok(tout), sub: `占比 ${tin + tout ? Math.round(tout / (tin + tout) * 100) : 0}%` },
      { label: '平均首字', value: ttfb != null ? zhFmtSec(ttfb) : '—', sub: ttfbs.length ? `样本 ${ttfbs.length}` : '' },
    ]);
    zh$('usage-count').textContent = total;
  }

  function statusCell(e) {
    if (e.ok === true) return '<span class="badge badge-active">成功</span>';
    if (e.ok === null) return '<span class="badge badge-cooling">进行中</span>';
    const label = e.status === 499 ? '客户端断开' : `失败${e.status ? ` · HTTP ${e.status}` : ''}`;
    return `<span class="badge badge-invalid" title="${zhEsc(e.error || '')}">${zhEsc(label)}</span>`;
  }
  function tickbarHtml(items) {
    if (!items.length) return '<span class="tickbar-empty">—</span>';
    return `<span class="tickbar">${items.map(e => `<i class="${e.ok === true ? 'ok' : e.ok === false ? 'bad' : ''}" data-tip="${zhEsc(`${zhFmtTs(e.ts)} · ${e.ok === true ? '成功' : e.ok === false ? (e.error || '失败') : '进行中'}`)}"></i>`).join('')}</span>`;
  }

  // 聚合口径：分组字段与展示字段分开 —— Key 的标签可重复，按 id 分组才不会串号
  const AGGS = {
    account: { group: 'account', head: '账号' },
    key_id: { group: 'key_id', show: 'key_label', blank: '未带 Key', head: 'Key' },
    model: { group: 'model', head: '模型' },
  };

  function renderAgg(tabKey) {
    const agg = AGGS[tabKey] || AGGS.account;
    const map = new Map();
    for (const e of filtered()) {
      const id = e[agg.group] || '';
      const name = (agg.show ? e[agg.show] : '') || id || agg.blank || '—';
      if (!map.has(id)) map.set(id, { name: name, items: [], ok: 0, fail: 0, inflight: 0, tin: 0, tout: 0, cost: 0, cur: '', ttfbs: [], last: null });
      const g = map.get(id);
      g.items.push(e);
      if (e.ok === true) g.ok++; else if (e.ok === false) g.fail++; else g.inflight++;
      g.tin += e.input_tokens || 0; g.tout += e.output_tokens || 0; g.cost += e.cost_ticks || 0;
      if (e.cost_currency && !g.cur) g.cur = e.cost_currency;
      if (e.t_first != null) g.ttfbs.push(e.t_first);
      if (!g.last || e.ts > g.last) g.last = e.ts;
    }
    const rows = [...map.values()].sort((a, b) => (b.ok + b.fail + b.inflight) - (a.ok + a.fail + a.inflight));
    const head = `<div class="mon-head mon-grid-agg"><span>${agg.head}</span><span>最近状态</span><span>成功率</span><span>调用</span><span>输入</span><span>输出</span><span>参考费用</span><span>平均首字</span><span>最近请求</span></div>`;
    const body = rows.map(g => {
      const done = g.ok + g.fail;
      const rate = done ? Math.round(g.ok / done * 100) + '%' : '—';
      const ttfb = g.ttfbs.length ? zhFmtSec(g.ttfbs.reduce((a, b) => a + b, 0) / g.ttfbs.length) : '—';
      return `<div class="mon-row mon-grid-agg">
        <span class="mon-name">${zhEsc(g.name)}</span>
        <span>${tickbarHtml(g.items.slice(-20))}</span>
        <span class="mon-num">${rate}</span>
        <span class="mon-num">${g.ok + g.fail + g.inflight}</span>
        <span class="mon-num">${zhFmtTok(g.tin)}</span>
        <span class="mon-num">${zhFmtTok(g.tout)}</span>
        <span class="mon-num" style="color:${g.cost ? 'var(--accent)' : 'inherit'}">${g.cost ? zhFmtCost(g.cost, g.cur) : '—'}</span>
        <span class="mon-num">${ttfb}</span>
        <span class="mon-time">${zhFmtTs(g.last)}</span>
      </div>`;
    }).join('');
    zh$('usage-list').innerHTML = rows.length ? `<div class="acct-table">${head}${body}</div>` : '<div class="zh-empty">所选时间范围内没有请求。</div>';
  }

  function renderLive() {
    const list = filtered();
    const head = '<div class="mon-head mon-grid-live"><span>时间</span><span>账号</span><span>模型</span><span>状态</span><span>首字</span><span>总耗时</span><span>输入</span><span>输出</span><span>参考费用</span><span>内容 / 错误</span></div>';
    const body = list.slice(0, 200).map(e => `
      <div class="mon-row mon-grid-live">
        <span class="mon-time">${zhFmtTs(e.ts)}</span>
        <span class="mon-name" title="${zhEsc(e.account || '')}">${zhEsc(e.account || '—')}${e.mode ? ` <span class="tier-b ${e.mode === 'jwt' ? 'jwt' : 'other'}">${e.mode === 'jwt' ? 'JWT' : 'KEY'}</span>` : ''}</span>
        <span class="mon-model" title="${zhEsc(e.model || '')}">${zhEsc(e.model || '—')}</span>
        <span>${statusCell(e)}</span>
        <span class="mon-num">${zhFmtSec(e.t_first)}</span>
        <span class="mon-num">${zhFmtSec(e.t_total)}</span>
        <span class="mon-num">${zhFmtTok(e.input_tokens)}</span>
        <span class="mon-num">${zhFmtTok(e.output_tokens)}</span>
        <span class="mon-num" title="${e.cache_read_tokens != null ? `缓存命中 ${zhFmtTok(e.cache_read_tokens)} · 缓存写入 ${zhFmtTok(e.cache_write_tokens)}` : ''}">${zhFmtCost(e.cost_ticks, e.cost_currency)}</span>
        <span class="mon-preview" title="${zhEsc(e.error || e.preview || '')}">${e.ok === false && e.error ? `<span class="err-line">${zhEsc(e.error)}</span>` : zhEsc(e.preview || '—')}${e.stream ? ' <span class="mon-stream">流</span>' : ''}</span>
      </div>`).join('');
    zh$('usage-list').innerHTML = list.length ? `<div class="acct-table">${head}${body}</div>` : '<div class="zh-empty">所选时间范围内没有请求。</div>';
  }

  function render() {
    const list = filtered();
    renderStats(list);
    if (tab === 'live') renderLive(); else renderAgg(tab);
    zh$('usage-updated').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  }

  function renderRange() {
    const defs = [['1h', '1 小时'], ['today', '今天'], ['all', '全部']];
    zh$('usage-range').innerHTML = defs.map(([v, label]) =>
      `<button class="filter-chip${state.range === v ? ' active' : ''}" type="button" data-act="usage-range" data-k="${v}">${label}</button>`).join('');
  }

  async function load() {
    const res = await api('GET', '/monitoring');
    entries = res.entries || [];
    ZH.keep = res.keep || 500;
    if (res.ticks_per_yuan) ZH.ticksPerYuan = res.ticks_per_yuan;
    loaded = true;
    render();
    const fail = entries.filter(e => e.ok === false).length;
    zhStatus('traffic', fail ? 'warn' : 'ok', `最近 ${entries.length} 条`);
  }

  zhOn('usage-range', t => { state.range = t.dataset.k; renderRange(); render(); });
  zhOn('usage-refresh', () => load().catch(() => showToast('加载失败', 'error')));
  zhOn('usage-clear', async () => {
    try { await api('POST', '/monitoring/clear'); showToast('已清空', 'success'); await load(); }
    catch { showToast('清空失败', 'error'); }
  });
  zh$('usage-tabs').addEventListener('click', e => {
    const b = e.target.closest('.mon-tab');
    if (!b) return;
    tab = b.dataset.tab;
    zh$('usage-tabs').querySelectorAll('.mon-tab').forEach(x => x.classList.toggle('active', x === b));
    render();
  });
  zh$('usage-search').addEventListener('input', e => { state.q = e.target.value; render(); });
  zh$('usage-status').addEventListener('change', e => { state.status = e.target.value; render(); });

  function activate() {
    renderRange();
    if (loaded) render();
    load().catch(() => showToast('加载失败', 'error'));
    timer = setInterval(() => load().catch(() => { }), 5000);
    zh$('usage-live').style.display = '';
  }
  function deactivate() {
    if (timer) { clearInterval(timer); timer = null; }
    zhStatus('traffic', 'idle', '');
    zh$('usage-live').style.display = 'none';
  }
  return { activate, deactivate };
})();
