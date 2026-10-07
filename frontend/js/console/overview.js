/* zcode-hub 控制台 · 01 概览
   纯客户端组合 /accounts + /monitoring + /pricing + /settings —— 后端没有汇总端点，
   也不该有：reqlog 只是 500 条内存环形缓冲，任何「今日汇总」都只是这 500 条的子集，
   所以每张卡都必须写明口径，不能让人以为是长期累计。 */
window.ZHOverview = (function () {
  let timer = null;

  function midnightTs() {
    const d = new Date(); d.setHours(0, 0, 0, 0);
    return d.getTime() / 1000;
  }

  function renderPool(accResp) {
    const box = zh$('ov-pool');
    if (!accResp) {
      zhTiles(box, [{ label: '号池', value: '—', tone: 'red', sub: '账号数据读取失败' }]);
      zh$('ov-pool-meta').textContent = '';
      return;
    }
    const s = accResp.stats || {};
    const accounts = accResp.accounts || [];
    const now = Date.now() / 1000;
    let rem = 0, cooling = 0;
    accounts.forEach(a => {
      Object.values(a.quota || {}).forEach(w => { rem += Number(w.remaining) || 0; });
      if (a.status === 'cooling') cooling++;
    });
    const bad = (s.total || 0) - (s.active || 0);
    zhTiles(box, [
      { label: '账户总数', value: s.total || 0, sub: s.total ? '' : '号池为空，网关无号可用' },
      { label: '可用', value: s.active || 0, tone: (s.active || 0) ? 'mint' : (s.total ? 'red' : ''), sub: s.total ? `占比 ${Math.round((s.active || 0) / s.total * 100)}%` : '' },
      { label: '额度用完', value: s.exhausted || 0, tone: s.exhausted ? 'red' : '' },
      { label: '限流冷却', value: cooling, tone: cooling ? 'red' : '', sub: bad && !cooling ? `其余不可用 ${bad}` : '' },
      {
        label: '额度剩余', value: zhFmtNum(rem), tone: rem ? '' : 'red',
        title: '各账号日窗口余额之和（不含未生效的一次性赠送）',
      },
    ]);
    const last = accounts.map(a => Number(a.last_used_at) || 0).sort((a, b) => b - a)[0];
    zh$('ov-pool-meta').textContent = last ? `最近请求 ${zhFmtDateSec(last)}` : '尚无请求记录';
    zhStatus('pool', s.total ? (s.active ? 'ok' : 'warn') : 'bad',
      s.total ? `号池 ${s.total} · 可用 ${s.active || 0}` : '号池为空');
  }

  function renderTraffic(monResp) {
    if (!monResp) {
      zhTiles('ov-traffic', [{ label: '请求日志', value: '—', tone: 'red', sub: '监控数据读取失败' }]);
      zh$('ov-traffic-meta').textContent = '';
      return;
    }
    const keep = monResp.keep || 500;
    ZH.keep = keep;
    if (monResp.ticks_per_yuan) ZH.ticksPerYuan = monResp.ticks_per_yuan;
    const all = monResp.entries || [];
    const min = midnightTs();
    const today = all.filter(e => e.ts >= min);
    const scope = all.length < keep ? all : today;
    const ok = scope.filter(e => e.ok === true).length;
    const fail = scope.filter(e => e.ok === false).length;
    const inflight = scope.filter(e => e.ok === null).length;
    const done = ok + fail;
    const ttfbs = scope.filter(e => e.t_first != null).map(e => e.t_first);
    const ttfb = ttfbs.length ? ttfbs.reduce((a, b) => a + b, 0) / ttfbs.length : null;
    const rate = done ? (ok / done * 100) : null;

    zhTiles('ov-traffic', [
      { label: '今日请求', value: today.length, sub: `进行中 ${inflight}` },
      { label: '成功', value: ok, tone: ok ? 'mint' : '' },
      { label: '失败', value: fail, tone: fail ? 'red' : '' },
      { label: '成功率', value: rate == null ? '—' : rate.toFixed(1) + '%', tone: rate == null ? '' : (rate >= 95 ? 'mint' : rate >= 80 ? '' : 'red'), sub: done ? `样本 ${done}` : '今日尚无已完成的请求' },
      { label: '平均首字', value: ttfb != null ? zhFmtSec(ttfb) : '—', sub: ttfbs.length ? `样本 ${ttfbs.length}` : '' },
    ]);
    zh$('ov-traffic-meta').textContent = `口径：${scope === today ? '今日' : '缓冲区内全部'} · ${scope.length} 条`;
    zhStatus('today', fail > 0 ? 'warn' : 'ok', `今日 ${today.length} 请求`);
  }

  /* 概览底部两块用账目表而不是格子：四行的口径各不相同（今日 / 累计 / 能力 / 策略），
     并排摆成格子会被读成一组同类指标，列成表才能一眼对上哪一行是哪个口径。 */
  function zhRows(id, head, rows) {
    zh$(id).innerHTML = `<table class="zh-metric"><thead><tr><th>${zhEsc(head[0])}</th><th>${zhEsc(head[1])}</th><th>${zhEsc(head[2])}</th></tr></thead><tbody>`
      + rows.map(r => `<tr><td>${zhEsc(r.k)}</td><td class="m-num${r.tone ? ' ' + r.tone : ''}">${zhEsc(String(r.v))}</td><td class="m-sub">${zhEsc(r.s || '')}</td></tr>`).join('')
      + '</tbody></table>';
  }

  function renderCost(monResp) {
    if (!monResp) {
      zhRows('ov-cost', ['项目', '数值', '说明'], [{ k: '今日 token 与费用', v: '—', s: '监控数据读取失败', tone: 'red' }]);
      zh$('ov-cost-meta').textContent = ''; return;
    }
    const all = monResp.entries || [];
    const min = midnightTs();
    const today = all.filter(e => e.ts >= min);
    const sum = (list, k) => list.reduce((a, e) => a + (Number(e[k]) || 0), 0);
    const tin = sum(today, 'input_tokens'), tout = sum(today, 'output_tokens');
    const cached = sum(today, 'cache_read_tokens'), cw = sum(today, 'cache_write_tokens');
    const cost = sum(today, 'cost_ticks');
    const priced = today.find(e => e.cost_currency);
    zhRows('ov-cost', ['项目', '数值', '说明'], [
      { k: '今日输入', v: zhFmtTok(tin) },
      { k: '今日输出', v: zhFmtTok(tout) },
      { k: '缓存命中', v: zhFmtTok(cached), s: cw ? `写入 ${zhFmtTok(cw)}` : '无缓存写入' },
      { k: '今日参考费用', v: cost ? zhFmtCost(cost, priced ? priced.cost_currency : 'CNY') : '—', s: cost ? '开放平台参考价折算 · 非 ZCode 账单' : '暂无计费请求', tone: cost ? 'mint' : '' },
    ]);
    zh$('ov-cost-meta').textContent = `今日 00:00 起 · 仅统计缓冲区内 ${today.length} 条`;
  }

  function renderAccess(setResp, priceResp) {
    if (!setResp) {
      zhRows('ov-access', ['项目', '数值', '说明'], [{ k: '准入与计价', v: '—', s: '设置数据读取失败', tone: 'red' }]);
      zh$('ov-access-meta').textContent = '';
      return;
    }
    const mode = setResp.model_whitelist_mode || 'manual';
    const effective = setResp.model_whitelist_effective || [];
    // 能力口径，不是价目表的键：ZCode 实际给什么模型，与开放平台列了 32 个无关
    const capability = setResp.model_capability || [];
    const capKeys = capability.map(i => zhModelKey(i.name));
    const fromConst = capability.filter(i => (i.sources || []).includes('constant')).length;
    const allowed = new Set(effective.map(zhModelKey));
    const blocked = capKeys.filter(k => !allowed.has(k)).length;
    const priceKeys = zhChipPriceMap((priceResp || {}).models);
    const priced = capKeys.filter(k => priceKeys.has(k)).length;
    const emptyManual = mode === 'manual' && !(setResp.model_whitelist_names || '').trim();

    zhRows('ov-access', ['项目', '数值', '说明'], [
      {
        k: '上游可调用', v: capability.length, tone: capability.length ? '' : 'red',
        s: capability.length ? `实测 ${fromConst} · 权益推导 ${capability.length - fromConst}` : '号池还没有额度数据',
      },
      {
        k: '白名单模式', v: mode,
        s: emptyManual ? '手填名单为空 · 不拦任何模型名' : `放行 ${effective.length} 个名字`,
      },
      {
        k: '已配参考价', v: `${priced}/${capability.length}`,
        s: '开放平台价折算，非 ZCode 账单',
      },
      {
        k: '上游有但被挡', v: blocked, tone: blocked ? 'red' : 'mint',
        s: blocked ? '名单外 · 进网关即拒' : '名单未挡住任何上游模型',
      },
    ]);
    zh$('ov-access-meta').textContent = `能力口径与号池额度有关 · 改名单去 04 页签`;
    zhStatus('access', emptyManual ? 'warn' : 'ok', `白名单 ${mode} · ${effective.length} 个`);
  }

  async function load() {
    const [acc, mon, price, set] = await Promise.all([
      api('GET', '/accounts').catch(() => null),
      api('GET', '/monitoring').catch(() => null),
      api('GET', '/pricing').catch(() => null),
      api('GET', '/settings').catch(() => null),
    ]);
    if (!acc && !mon && !price && !set) {
      zhStatus('conn', 'bad', '后台接口不可达');
      return;
    }
    zhNote();
    renderPool(acc);
    renderTraffic(mon);
    renderCost(mon);
    renderAccess(set, price);
  }

  function zhNote() {
    zh$('ov-note').textContent = `流量与成本取自内存环形缓冲（最近 ${ZH.keep} 条，重启清零）——不是历史累计；要长期统计请外接采集。`;
  }

  zhOn('ov-refresh', () => load().catch(() => showToast('加载失败', 'error')));

  function activate() {
    load().catch(() => showToast('概览加载失败', 'error'));
    timer = setInterval(() => { load().catch(() => { }); }, 15000);
  }
  function deactivate() {
    if (timer) { clearInterval(timer); timer = null; }
    zhStatus('today', 'idle', '');
  }
  return { activate, deactivate };
})();
