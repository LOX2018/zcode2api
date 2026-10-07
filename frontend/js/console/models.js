/* zcode-hub 控制台 · 04 模型与计价
   两个口径分开摆：芯片墙用 model_capability（上游实际提供），白名单用
   model_whitelist_effective（策略放行）。价目表来自开放平台文档站，是另一个产品的账，
   所以它只作为计价中间层存在，并且默认不周期拉取。 */
window.ZHModels = (function () {
  const WL_LABEL = {
    manual: 'manual · 手填名单（默认）',
    static: 'static · 仅内置名单',
    dynamic: 'dynamic · 仅账号额度',
    hybrid: 'hybrid · 内置 ∪ 额度',
  };
  const SRC_LABEL = { builtin: ['内置', 'badge-disabled'], pulled: ['拉取', 'badge-cooling'], admin: ['覆盖', 'badge-active'] };

  let wlModes = ['manual', 'static', 'dynamic', 'hybrid'];
  let pricingCache = null, accountsCache = [], effectiveCache = [], capabilityCache = [];
  let timer = null;

  function zhK(n) { return n >= 1024 ? Math.round(n / 1024) + 'K' : String(n); }
  function tierLabel(t) {
    // 档位 id 是内部名（lt32k / 0-32768 两套并存），只读表按区间说人话
    if (t.max_input == null) return t.min_input ? '≥' + zhK(t.min_input) : '全区间';
    return zhK(t.min_input) + '–' + zhK(t.max_input);
  }
  function yuan(t) { return t == null ? '—' : (t / ZH.ticksPerYuan).toFixed(2); }

  function priceRow(name, m, first) {
    return (m.tiers || []).map((t, i) => `<div class="price-grid price-row">
      <span class="price-name">${i === 0 && first ? zhEsc(name) : ''}</span>
      <span class="price-num" title="${zhEsc(t.tier_id)}">${tierLabel(t)}</span>
      <span class="price-num">${yuan(t.uncached_input)}</span>
      <span class="price-num">${yuan(t.cached_input)}</span>
      <span class="price-num">${yuan(t.cache_write)}</span>
      <span class="price-num">${yuan(t.output)}</span>
      <span class="badge ${(SRC_LABEL[m.source] || ['', ''])[1]}">${(SRC_LABEL[m.source] || [m.source, ''])[0]}</span>
    </div>`).join('');
  }

  function renderChips() {
    if (!pricingCache) return;
    const models = pricingCache.models || {};
    zhChips('mdl-chips', capabilityCache, models, effectiveCache, accountsCache);
    const priceKeys = zhChipPriceMap(models);
    const priced = capabilityCache.filter(i => priceKeys.has(zhModelKey(i.name))).length;
    zh$('mdl-chip-meta').textContent = `上游 ${capabilityCache.length} · 放行 ${effectiveCache.length} · 已配价 ${priced}`;
  }

  function renderPricing(p) {
    pricingCache = p;
    const models = p.models || {};
    const capKeys = new Set(capabilityCache.map(i => zhModelKey(i.name)));
    const entries = Object.entries(models).sort((a, b) => a[0].localeCompare(b[0]));
    // 主表只摆上游真的提供的模型；其余是开放平台的价目，折起来当参考
    const mine = entries.filter(([n]) => capKeys.has(zhModelKey(n)));
    const other = entries.filter(([n]) => !capKeys.has(zhModelKey(n)));

    zh$('mdl-price-list').innerHTML = mine.length
      ? mine.map(([n, m]) => priceRow(n, m, true)).join('')
      : '<div class="zh-empty">上游提供的模型还没有参考价（号池额度未刷新，或价目层为空）。</div>';
    zh$('mdl-other-count').textContent = other.length;
    zh$('mdl-other-list').innerHTML = other.length
      ? other.map(([n, m]) => priceRow(n, m, true)).join('')
      : '<div class="zh-empty">没有多余的价目条目。</div>';

    const s = p.pull_status || {};
    const RESULT = { ok: '已拉取', unchanged: '页面未变', rejected: '被拒绝', error: '失败', busy: '已有拉取在飞' };
    const bits = [];
    if (s.result) bits.push(`${RESULT[s.result] || s.result}${s.http_status ? ` · HTTP ${s.http_status}` : ''}`);
    if (s.last_attempt_at) bits.push(zhFmtFull(s.last_attempt_at));
    if (s.error) bits.push(s.error);
    if (s.blocked_until && s.blocked_until > Date.now() / 1000) bits.push(`退避至 ${zhFmtFull(s.blocked_until)}`);
    zh$('mdl-pull-status').textContent = bits.join(' · ') || '尚未拉取（间隔 0 = 关闭）';

    const counts = {};
    Object.values(models).forEach(m => { counts[m.source] = (counts[m.source] || 0) + 1; });
    const mix = Object.entries(counts).map(([k, v]) => `${(SRC_LABEL[k] || [k])[0]} ${v}`).join(' · ');
    // 覆盖层已不在界面编辑，只能在存过时露一个回落入口 —— 不然它就是个看不见的暗层
    const ov = !!p.admin_overrides_active;
    zh$('mdl-btn-reset-ov').hidden = !ov;
    zh$('mdl-price-meta').innerHTML =
      `单位 元/百万 token，<strong>开放平台参考价，非 ZCode 账单</strong>（上游按 units 出账）。` +
      `价目共 ${entries.length} 项，其中上游提供的 ${mine.length} 项；来源分布：${mix || '—'}；拉取层${(p.pull_interval || 0) ? `每 ${p.pull_interval} 秒刷新` : '仅手动刷新'}。<br>` +
      `内置基准：${zhEsc(p.builtin_as_of || '—')} · 数据源：<code>${zhEsc(p.source_url)}</code>` +
      (ov ? '<br>已启用后台覆盖表（优先级最高），用右上角「清除覆盖」回落到拉取价 / 内置价。' : '');

    renderChips();
  }

  function showEffective(mode, names) {
    // 手填名单不等于生效名单：空表回落、别名归一都在这行显形，不显示就只能靠猜
    const el = zh$('mdl-wl-effective');
    if (!el) return;
    if (!names || !names.length) { el.textContent = ''; return; }
    el.innerHTML = `<code>${zhEsc(mode || '')}</code> 档当前放行 ${names.length} 个：` + names.map(zhEsc).join('、');
  }

  function renderModeSelect(modes, cur) {
    wlModes = modes || wlModes;
    zh$('mdl-wl-mode').innerHTML = wlModes.map(m =>
      `<option value="${zhEsc(m)}"${m === cur ? ' selected' : ''}>${zhEsc(WL_LABEL[m] || m)}</option>`).join('');
  }

  async function loadPricing() {
    try {
      const p = await api('GET', '/pricing');
      ZH.ticksPerYuan = p.ticks_per_yuan || 1e6;
      renderPricing(p);
    } catch (e) { showToast('计价表加载失败: ' + e.message, 'error'); }
  }

  async function load() {
    try {
      const d = await api('GET', '/settings');
      renderModeSelect(d.whitelist_modes, d.model_whitelist_mode || 'manual');
      zh$('mdl-wl-names').value = d.model_whitelist_names || '';
      effectiveCache = d.model_whitelist_effective || [];
      capabilityCache = d.model_capability || [];
      showEffective(d.model_whitelist_mode, effectiveCache);
      const acc = await api('GET', '/accounts').catch(() => ({ accounts: [] }));
      accountsCache = acc.accounts || [];
      await loadPricing();
      const mode = d.model_whitelist_mode || 'manual';
      zhStatus('models', capabilityCache.length ? 'ok' : 'warn',
        capabilityCache.length ? `上游 ${capabilityCache.length} 个模型` : '上游模型未知');
      zh$('mdl-note').textContent = `号池权益推出上游提供 ${capabilityCache.length} 个模型；价目表有 ${pricingCache ? Object.keys(pricingCache.models || {}).length : '—'} 项，那是开放平台的账，本网关上游按 units 出账。`;
    } catch (e) { showToast('加载失败: ' + e.message, 'error'); }
  }

  async function saveWhitelist() {
    const payload = {
      model_whitelist_mode: zh$('mdl-wl-mode').value,
      model_whitelist_names: zh$('mdl-wl-names').value,
    };
    try {
      const r = await api('PUT', '/settings', payload);
      effectiveCache = r.model_whitelist_effective || [];
      showEffective(payload.model_whitelist_mode, effectiveCache);
      renderChips();
      showToast('白名单已保存', 'success');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
  }

  function resetOverrides() {
    zhConfirm('清除计价覆盖', '删除后台覆盖表，回落到拉取价 / 内置价？白名单与拉取间隔不受影响。', async () => {
      try {
        await api('PUT', '/settings', { pricing_models: '' });
        showToast('已清除覆盖', 'success');
        await loadPricing();
      } catch (e) { showToast('清除失败: ' + e.message, 'error'); }
    }, '清除覆盖');
  }

  async function pullNow() {
    const btn = zh$('mdl-btn-pull');
    btn.disabled = true;
    try {
      const r = await api('POST', '/pricing/pull');
      const s = r.status || {};
      if (s.result === 'ok') showToast(`已拉取：${r.models} 个模型${s.changed ? '（有变化）' : '（无变化）'}`, 'success');
      else if (s.result === 'busy') showToast('已有一次拉取在飞，稍后再试', 'info');
      else showToast(`拉取未成功：${s.error || s.result}（沿用上一份）`, 'error');
      await loadPricing();
    } catch (e) { showToast('拉取失败: ' + e.message, 'error'); }
    finally { btn.disabled = false; }
  }

  zhOn('mdl-refresh', () => load());
  zhOn('mdl-save-wl', saveWhitelist);
  zhOn('mdl-reset-ov', resetOverrides);
  zhOn('mdl-pull', pullNow);

  function activate() {
    load();
    // 拉取层可能由后台周期刷新，本页停留时轻轮询拉取状态
    timer = setInterval(() => { loadPricing().catch(() => { }); }, 30000);
  }
  function deactivate() {
    if (timer) { clearInterval(timer); timer = null; }
    zhStatus('models', 'idle', '');
  }
  return { activate, deactivate };
})();
