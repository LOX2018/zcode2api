/* zcode-hub 控制台 · 02 账号池与授权
   每个账号一张卡（额度窗口 + 最近结果 + 累计），OAuth 授权是页面下方的独立卡片。 */
window.ZHAccounts = (function () {
  const STATUS_LABEL = { active: '正常', exhausted: '用完', cooling: '限流', invalid: '异常', disabled: '禁用' };
  const NOTCH_COLORS = ['var(--notch-1)', 'var(--notch-2)', 'var(--notch-3)', 'var(--notch-4)', 'var(--notch-5)', 'var(--notch-6)'];

  let all = [], filter = 'all', timer = null, loaded = false;
  const refreshing = new Set(), claiming = new Set();

  /* ── 数据 ── */
  async function load(silent) {
    try {
      const d = await api('GET', '/accounts');
      all = d.accounts || [];
      loaded = true;
      renderTiles(d.stats || {});
      renderFilters();
      renderList();
      zh$('acct-updated').textContent = '更新于 ' + new Date().toLocaleTimeString();
      const usable = all.filter(a => a.status === 'active').length;
      zhStatus('pool', all.length ? (usable ? 'ok' : 'warn') : 'warn',
        all.length ? `号池 ${all.length} · 可用 ${usable}` : '号池为空');
    } catch (e) {
      if (!silent) showToast('加载失败: ' + e.message, 'error');
      zhStatus('pool', 'bad', '号池读取失败');
    }
  }

  // 全量套餐（上游 plans 数组；旧数据回退单个 plan），entitlements 附带所属套餐信息
  function planList(a) {
    if (Array.isArray(a.plans) && a.plans.length) return a.plans;
    const p = a.plan;
    return (p && Object.keys(p).length) ? [p] : [];
  }
  function allEntitlements(a) {
    const out = [];
    planList(a).forEach(pl => {
      const planEnds = Number((pl || {}).ends_at) || Number((pl || {}).expires_at) || 0;
      ((pl || {}).entitlements || []).forEach(e => {
        out.push({ ...e, _planEnds: planEnds, _planName: (pl || {}).name || '' });
      });
    });
    return out;
  }
  // 赠送授权是否已被 balance 窗口体现（上游 2026-09-06 起把 one_time 池并入 balance；
  // 同模型窗口 total >= 授权量即视为已含，避免赠送重复渲染/重复计数）
  function bonusCoveredByBalance(a, e) {
    const grant = Number(e.grant_units) || 0;
    if (!grant) return false;
    return Object.entries(a.quota || {}).some(([k, w]) => {
      if (k !== e.show_name) return false;
      return (Number(w && w.total) || 0) >= grant;
    });
  }

  /* ── 渲染 ── */
  function renderTiles(s) {
    const now = Date.now() / 1000;
    let totalRem = 0;
    all.forEach(a => {
      Object.values(a.quota || {}).forEach(w => { totalRem += Number(w.remaining) || 0; });
      allEntitlements(a).forEach(e => {
        const eff = Number(e.effective_at) || 0;
        const ends = Number(e.ends_at || e.expires_at) || e._planEnds || 0;
        if (e.period === 'one_time' && eff > 0 && eff <= now && (!ends || now <= ends) && !bonusCoveredByBalance(a, e)) {
          totalRem += Number(e.grant_units) || 0;
        }
      });
    });
    zhTiles('acct-tiles', [
      { label: '账户总数', value: s.total || 0 },
      { label: '正常', value: s.active || 0, tone: (s.active || 0) ? 'mint' : (s.total ? 'red' : '') },
      { label: '额度用完', value: s.exhausted || 0, tone: s.exhausted ? 'red' : '' },
      {
        label: '总额度（剩余）', value: zhFmtNum(totalRem),
        title: '日窗口余额 + 已生效一次性活动赠送（赠送池按授权量计入，上游不提供实际剩余）',
      },
    ]);
  }

  function renderFilters() {
    const counts = { all: all.length, exhausted: 0, disabled: 0 };
    all.forEach(a => { counts[a.status] = (counts[a.status] || 0) + 1; });
    const chips = [['all', '全部'], ['exhausted', '用完'], ['disabled', '禁用']];
    zh$('acct-filters').innerHTML = chips.map(([k, l]) =>
      `<button class="filter-chip${filter === k ? ' active' : ''}" type="button" data-act="acct-filter" data-k="${k}">${l}<span class="filter-chip-count">${counts[k] || 0}</span></button>`
    ).join('');
  }

  function renderList() {
    const items = filter === 'all' ? all : all.filter(a => a.status === filter);
    zh$('acct-count').textContent = items.length;
    const el = zh$('acct-list');
    if (!items.length) {
      el.innerHTML = '<div class="zh-empty">号池为空 —— 点右上角「新增」粘贴 JWT / API Key，或用下面「OAuth 快速授权」登录入池。</div>';
      return;
    }
    el.innerHTML = items.map(cardHtml).join('');
  }

  function notchColor(id) {
    let h = 0;
    for (const c of String(id)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return NOTCH_COLORS[h % NOTCH_COLORS.length];
  }
  function tierBadgeHtml(a) {
    const p = a.plan || {};
    const s = String(p.show_name || p.plan_id || '').toLowerCase();
    let label = zhEsc(p.show_name || p.plan_id || ''), cls = 'other';
    if (s.includes('max')) { label = 'Max'; cls = 'max'; }
    else if (s.includes('pro') && !s.includes('preview')) { label = 'Pro'; cls = 'pro'; }
    else if (s.includes('lite')) { label = 'Lite'; cls = 'lite'; }
    else if (s.includes('start') || s.includes('trial') || s.includes('体验')) { label = 'Trial'; cls = 'trial'; }
    return label ? `<span class="tier-b ${cls}">${label}</span>` : '';
  }
  function credCell(a) {
    return `<span class="tok">${zhEsc(a.token_masked)}</span><span>·</span><span>${zhEsc(a.provider)} / ${a.mode === 'jwt' ? 'JWT' : 'API Key'}</span>`;
  }
  // 状态卡片那一行只讲「现在能不能用、为什么」；额度与错误各有自己的位置，不重复
  function statusNote(a) {
    const bits = [];
    // 冷却倒计时（5xx 重试耗尽进入冷却；风控封禁走 disabled 徽章 + err 文案）
    if (a.status === 'cooling' && Number(a.cooling_until)) {
      const left = Math.max(0, Math.round(Number(a.cooling_until) - Date.now() / 1000));
      if (left > 0) bits.push(`❄ 限流冷却，${zhFmtDur(left)}后自动恢复`);
    }
    const planEnd = Math.max(0, ...planList(a).map(pl => Number(pl.ends_at) || Number(pl.expires_at) || 0));
    if (planEnd) bits.push(`套餐至 ${zhFmtDate(planEnd)}`);
    bits.push(`最近请求 ${zhFmtDateSec(a.last_used_at)}`);
    return bits.map(b => `<span>·</span><span>${zhEsc(b)}</span>`).join('');
  }
  function tickbarHtml(a) {
    const r = (a.recent_results || []).slice(-20);
    if (!r.length) return '<div class="tickbar-empty">—</div>';
    // 条目兼容两种形态：明细对象 {ok,at,detail}（新）与纯布尔（历史遗留）
    return `<div class="tickbar" aria-label="最近 ${r.length} 次请求结果，悬停查看明细">${r.map(e => {
      const ok = typeof e === 'object' ? e.ok : !!e;
      const head = `${ok ? '✓ 成功' : '✗ 失败'}`;
      const time = typeof e === 'object' ? zhFmtDateSec(e.at) : '历史记录';
      const detail = typeof e === 'object' ? (e.detail || '') : '无明细';
      return `<i class="${ok ? 'ok' : 'bad'}" data-tip="${zhEsc(`${head} · ${time}\n${detail}`)}"></i>`;
    }).join('')}</div>`;
  }
  function usageCell(a) {
    const used = a.use_count || 0, fail = a.fail_count || 0, strikes = a.risk_strikes || 0;
    // 成功率=成功/(成功+失败)：fail_count 是历史累计（可能大于 use_count），不能用差值公式
    const rate = used > 0 || fail > 0 ? Math.round(used / (used + fail) * 100) : null;
    return `<div class="usage-mini">
      <div class="usage-line"><span class="u-label">调用</span><span class="u-val">${used}</span><span class="u-label">成功率</span><span class="u-val ${rate === null ? '' : (rate >= 90 ? 'u-ok' : 'u-warn')}">${rate === null ? '—' : rate + '%'}</span></div>
      <div class="usage-line"><span class="u-label">失败</span><span class="u-val ${fail > 0 ? 'u-bad' : ''}">${fail}</span><span class="u-label">风控</span><span class="u-val ${strikes > 0 ? 'u-bad' : ''}">${strikes}</span></div>
    </div>`;
  }
  function actionsCell(a) {
    const isJwt = a.mode === 'jwt';
    const loading = refreshing.has(a.id);
    const enabled = a.enabled !== false && a.status !== 'disabled';
    const nm = a.name || a.id;
    const icon = (act, label, cls, paths) => `<button type="button" data-act="${act}" data-id="${a.id}" class="row-icon-btn ${cls}" aria-label="${zhEsc(label + '：' + nm)}" title="${zhEsc(label)}"><svg viewBox="0 0 24 24">${paths}</svg></button>`;
    return `<div class="acct-actions">
      ${isJwt ? icon('acct-refresh', '刷新额度', loading ? 'is-loading' : '', '<path d="M20 11a8 8 0 0 0-14.6-4.6"/><path d="M4 4v5h5"/><path d="M4 13a8 8 0 0 0 14.6 4.6"/><path d="M20 20v-5h-5"/>')
        + icon('acct-claim', '自动领取赠送额度', claiming.has(a.id) ? 'is-loading' : '', '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13"/><path d="M19 12v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7"/><path d="M7.5 8a2.5 2.5 0 0 1 0-5C11 3 12 8 12 8s1-5 4.5-5a2.5 2.5 0 0 1 0 5"/>')
        + icon('acct-claim-manual', '手动领取活动套餐（浏览器过验证码）', '', '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="m9 12 2 2 4-4"/>') : ''}
      ${icon('acct-edit', '编辑', '', '<path d="m4 20 4.5-1 9.5-9.5-3.5-3.5L5 15.5z"/><path d="m13.5 5.5 3.5 3.5"/>')}
      ${icon('acct-delete', '删除', 'row-icon-danger', '<path d="M5 7h14"/><path d="M9 7V4h6v3"/><path d="M8 10v7"/><path d="M12 10v7"/><path d="M16 10v7"/><path d="M7 7l1 13h8l1-13"/>')}
      <label class="switch" title="${enabled ? '禁用账号' : '启用账号'}"><input type="checkbox" data-toggle="${a.id}" aria-label="${zhEsc((enabled ? '禁用' : '启用') + '：' + nm)}" ${enabled ? 'checked' : ''}><span class="switch-slider"></span></label>
    </div>`;
  }
  function cardHtml(a) {
    const off = a.enabled === false || a.status === 'disabled';
    return `<div class="zh-acct" data-id="${a.id}"${off ? ' style="opacity:.6"' : ''}>
      <div class="zh-acct-hd">
        <span class="notch" aria-hidden="true" style="background:${notchColor(a.id)}"></span>
        <span class="zh-acct-name">${zhEsc(a.name)}</span>
        ${tierBadgeHtml(a)}
        <span class="badge badge-${a.status}">${STATUS_LABEL[a.status] || a.status}</span>
        <div class="zh-acct-act">${actionsCell(a)}</div>
      </div>
      <div class="zh-acct-meta">${credCell(a)}${statusNote(a)}</div>
      <div class="zh-acct-bd">
        <div>
          <div class="zh-fld-lb">额度窗口</div>
          ${quotaCell(a)}
        </div>
        <div>
          <div class="zh-fld-lb">最近 20 次请求</div>
          ${tickbarHtml(a)}
        </div>
        <div>
          <div class="zh-fld-lb">累计</div>
          ${usageCell(a)}
        </div>
      </div>
      ${a.last_error ? `<div class="zh-acct-err">${zhEsc(a.last_error)}</div>` : ''}
    </div>`;
  }
  function shortModel(n) { return String(n).replace(/^GLM[- ]?/i, 'GLM-') || n; }
  function quotaCell(a) {
    const q = a.quota || {};
    let rows = Object.keys(q).map(k => {
      const w = q[k] || {};
      const rem = Number(w.remaining) || 0, tot = Number(w.total) || 0;
      const pct = tot > 0 ? Math.max(0, Math.min(100, Math.round(rem / tot * 100))) : 0;
      const color = rem <= 0 ? 'var(--ink-mute)' : (pct < 15 ? 'var(--accent)' : 'var(--mint)');
      const exp = Number(w.expires_at) || 0;
      return `<div class="quota-row">
        <span class="quota-row-name" title="${zhEsc(k)}">${zhEsc(shortModel(k))}</span>
        <span class="quota-row-track"><span class="quota-row-fill" style="width:${pct}%;background:${color}"></span></span>
        <span class="quota-row-val"><span>${zhFmtNum(rem)} / ${zhFmtNum(tot)}</span>${exp ? `<span class="quota-row-exp" title="本窗口额度到期时间">至 ${zhFmtDate(exp)}</span>` : ''}</span>
      </div>`;
    });
    // 活动额度（plan.entitlements）：① one_time 已生效 —— billing/balance 不体现，单独渲染「活动赠送」行
    // ② 待生效（effective_at 在未来）—— 斜纹流动条，过点自动转正
    const now = Date.now() / 1000;
    const ents = allEntitlements(a);
    ents.filter(e => {
      const eff = Number(e.effective_at) || 0, ends = Number(e.ends_at || e.expires_at) || e._planEnds || 0;
      return e.period === 'one_time' && eff > 0 && eff <= now && (!ends || now <= ends) && !bonusCoveredByBalance(a, e);
    }).forEach(e => {
      const eff = Number(e.effective_at) || 0;
      const ends = Number(e.ends_at || e.expires_at) || e._planEnds || 0;
      const endTxt = ends ? `${zhFmtDate(eff)} ~ ${zhFmtDate(ends)}` : `${zhFmtDate(eff)} 起有效`;
      const tip = `活动赠送 ${zhFmtNum(e.grant_units)} · ${zhFmtDate(eff)} 生效${ends ? '，' + zhFmtDate(ends) + ' 结束（按套餐有效期）' : ''} · 实际剩余以上游账单为准`;
      rows.push(`<div class="quota-row is-bonus">
        <span class="quota-row-name" title="${zhEsc(tip)}">${zhEsc(shortModel(e.show_name || '活动额度'))}</span>
        <span class="quota-row-track"><span class="quota-row-fill" style="width:100%"></span></span>
        <span class="quota-row-val"><span>+${zhFmtNum(e.grant_units)}</span><span class="quota-row-exp" title="${zhEsc(tip)}">${endTxt}</span></span>
      </div>`);
    });
    ents.filter(e => Number(e.effective_at) > now).forEach(e => {
      const eff = Number(e.effective_at) || 0;
      const tip = `${e._planName || ''} · ${zhFmtDate(eff)} 起生效${e._planEnds ? '，套餐 ' + zhFmtDate(e._planEnds) + ' 结束' : ''}`;
      rows.push(`<div class="quota-row is-pending">
        <span class="quota-row-name" title="${zhEsc(tip)}">${zhEsc(shortModel(e.show_name || '待生效'))}</span>
        <span class="quota-row-track"><span class="quota-row-fill" style="width:100%"></span></span>
        <span class="quota-row-val"><span>+${zhFmtNum(e.grant_units)}</span><span class="quota-row-exp" title="${zhEsc(tip)}">${e.period === 'one_time' ? '一次性' : '每' + (e.period || '') + ' · ' + zhFmtDate(eff) + ' 生效'}</span></span>
      </div>`);
    });
    if (!rows.length) {
      if (a.mode !== 'jwt') return '<span class="quota-empty">—</span>';
      // 区分「没取到数据」与「上游确实没有套餐」：后者刷新永远无效，给出正确指引
      return planList(a).length === 0
        ? '<span class="quota-empty" title="上游 billing/current 返回空套餐列表：可能套餐未开通，或新账号试用额度未列入 billing（不影响路由使用，以实际请求结果为准）">无套餐数据 · billing 为空</span>'
        : '<span class="quota-empty">额度未获取，点右上角刷新</span>';
    }
    return '<div class="quota-rows">' + rows.join('') + '</div>';
  }

  /* ── 操作 ── */
  async function refreshOne(id) {
    if (refreshing.has(id)) return;
    refreshing.add(id); renderList();
    try { await api('POST', '/accounts/' + id + '/refresh'); showToast('额度已刷新', 'success'); }
    catch (e) { showToast('刷新失败: ' + e.message, 'error'); }
    finally { refreshing.delete(id); await load(); }
  }
  async function refreshAll() {
    showToast('正在刷新全部额度…', 'info');
    try {
      const d = await api('POST', '/accounts/refresh', { all: true });
      showToast(`刷新完成：成功 ${d.summary.ok}，失败 ${d.summary.fail}`, 'success');
      load();
    } catch (e) { showToast('刷新失败: ' + e.message, 'error'); }
  }
  function claimToast(o) {
    if (o.ok) showToast(`「${o.account_name}」已领取「${o.plan_name || o.plan_id}」`, 'success');
    else {
      let msg = o.message || '未知错误';
      if (o.next_at) msg += `（名额约 ${zhFmtDur((o.next_at - Date.now()) / 1000)} 后恢复）`;
      showToast(`「${o.account_name}」领取失败：${msg}`, 'error');
    }
  }
  async function claimOne(id) {
    if (claiming.has(id)) return;
    claiming.add(id); renderList();
    try {
      const d = await api('POST', '/claim', { account_ids: [id] });
      d.outcomes.forEach(claimToast);
    } catch (e) { showToast('领取失败: ' + e.message, 'error'); }
    finally { claiming.delete(id); await load(); }
  }
  async function claimAll() {
    const btn = zh$('acct-btn-claim-all');
    if (btn.disabled) return;
    btn.disabled = true;
    showToast('正在逐账号领取赠送额度…', 'info');
    try {
      const d = await api('POST', '/claim', {});
      d.outcomes.forEach(claimToast);
      if (!d.outcomes.length) showToast('没有可领取的 JWT 账号', 'info');
      else showToast(`领取完成：成功 ${d.summary.ok}，失败 ${d.summary.fail}`, d.summary.ok ? 'success' : 'error');
    } catch (e) { showToast('领取失败: ' + e.message, 'error'); }
    finally { btn.disabled = false; await load(); }
  }
  async function toggleEnabled(id, enable) {
    try { await api('POST', '/accounts/' + id + '/enabled', { enabled: enable }); load(); }
    catch (e) { showToast('操作失败: ' + e.message, 'error'); load(); }
  }
  function openEdit(id) {
    const a = all.find(x => x.id === id);
    if (!a) return;
    zhDialog({
      title: '编辑账号',
      bodyHtml: `<div class="dialog-field"><span class="dialog-label">名称</span><input class="input" id="dlg-name" value="${zhEsc(a.name)}"></div>
        <div class="dialog-field"><span class="dialog-label">Token</span><input class="input" id="dlg-token" placeholder="留空则不修改"></div>`,
      read: () => ({
        name: zh$('dlg-name').value.trim(),
        token: zh$('dlg-token').value.trim(),
      }),
      onOk: async v => {
        try {
          await api('PUT', '/accounts/' + id, { name: v.name, ...(v.token && { token: v.token }) });
          showToast('已保存', 'success'); load();
        } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
      },
    });
  }
  function doDelete(id) {
    const a = all.find(x => x.id === id);
    zhConfirm('删除账号', `确认删除 <code>${zhEsc(a ? a.token_masked : id)}</code>？此操作不可撤销。`, async () => {
      try { await api('DELETE', '/accounts', [id]); showToast('已删除', 'success'); load(); }
      catch (e) { showToast('删除失败: ' + e.message, 'error'); }
    });
  }

  /* ── 新增（粘贴凭证）；OAuth 授权是页面上的独立卡片，不再挤进弹窗 ── */
  // 会话记在 sessionStorage：刷新页面不再等于放弃授权。上游轮询由服务端后台
  // 负责，页面只是读者 —— 页面死了凭证照样入池，回来这一步只是把结果读出来。
  const LOGIN_KEY = 'zh_oauth_login';
  let loginTimer = null, expireTimer = null, loginUrl = '', loginDeadline = 0, loginFlowId = '';
  function openAdd() {
    zh$('acct-add-tokens').value = '';
    zhOpenOverlay('zh-modal-add');
  }
  function setLoginSession(s) {
    try {
      if (s) sessionStorage.setItem(LOGIN_KEY, JSON.stringify(s));
      else sessionStorage.removeItem(LOGIN_KEY);
    } catch { /* 隐私模式写不进去，不影响捕获 */ }
  }
  function resetLoginPane() {
    stopLoginPoll();
    loginFlowId = ''; loginUrl = ''; loginDeadline = 0;
    setLoginSession(null);
    zh$('acct-login-idle').style.display = '';
    zh$('acct-login-active').style.display = 'none';
    zh$('acct-login-state').hidden = true;
    zh$('acct-login-start-btn').disabled = false;
    zh$('acct-login-label').value = '';
    const st = zh$('acct-login-status');
    if (st) delete st.dataset.err;
  }
  function showLoginPane(flowId, url, deadline) {
    loginFlowId = flowId; loginUrl = url; loginDeadline = deadline;
    zh$('acct-login-url').value = url;
    zh$('acct-login-idle').style.display = 'none';
    zh$('acct-login-active').style.display = '';
    zh$('acct-login-state').hidden = false;
    zh$('acct-login-start-btn').disabled = true;
    setLoginSession({ flow_id: flowId, url: url, deadline: deadline });
    setLoginStatus('后台捕获中…', false);
    tickLoginCountdown();
    expireTimer = setInterval(tickLoginCountdown, 1000);
  }
  function tickLoginCountdown() {
    const left = Math.max(0, Math.round((loginDeadline - Date.now()) / 1000));
    const cur = zh$('acct-login-status');
    if (cur && !cur.dataset.err) {
      cur.textContent = left > 0
        ? `后台捕获中… 链接 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`
        : '链接已过期，请重新生成授权链接';
    }
    if (left <= 0 && expireTimer) { clearInterval(expireTimer); expireTimer = null; }
  }
  async function doAdd() {
    const tokens = zh$('acct-add-tokens').value;
    const provider = zh$('acct-add-provider').value;
    const list = tokens.split('\n').map(s => s.trim()).filter(Boolean);
    if (!list.length) return showToast('请输入至少一个 Token', 'error');
    try {
      const d = await api('POST', '/accounts', { provider, tokens: list });
      zhCloseOverlay('zh-modal-add');
      showToast(`新增 ${d.count} 个账号`, 'success');
      load();
    } catch (e) { showToast('添加失败: ' + e.message, 'error'); }
  }
  async function startLogin() {
    const btn = zh$('acct-login-start-btn');
    btn.disabled = true;
    try {
      const label = zh$('acct-login-label').value.trim();
      const d = await api('POST', '/login/start', label ? { label } : {});
      showLoginPane(d.flow_id, d.authorize_url, Date.now() + (d.expires_in || 300) * 1000);
      try { window.open(loginUrl, '_blank', 'noopener'); } catch { /* 弹窗被拦截时仍可手动复制 */ }
      pollLogin(d.flow_id);
    } catch (e) {
      showToast('发起登录失败: ' + e.message, 'error');
      btn.disabled = false;
    }
  }
  async function cancelLogin() {
    const fid = loginFlowId;
    resetLoginPane();
    if (!fid) return;
    // 取消要通知服务端：后台还在问上游，不说的话他之后点了同意仍会入池。
    try { await api('POST', '/login/cancel', { flow_id: fid }); } catch { /* 会话本就已结束 */ }
  }
  function pollLogin(flowId) {
    if (loginTimer) return;
    let n = 0, inflight = false;
    loginTimer = setInterval(async () => {
      if (inflight) return;
      if (++n > 200) { stopLoginPoll(); setLoginStatus('读取捕获结果超时，请重新生成链接', true); return; }
      inflight = true;
      try {
        const d = await api('GET', '/login/poll/' + flowId);
        if (!loginTimer) return;
        if (d.status === 'ready') {
          stopLoginPoll();
          showToast('登录成功，已导入账号池：' + (d.account && d.account.name || ''), 'success');
          resetLoginPane();
          load();
        } else if (d.status === 'failed') {
          stopLoginPoll(); setLoginStatus(d.message || '授权失败或被拒绝', true);
        } else if (d.status === 'expired') {
          stopLoginPoll(); setLoginStatus('链接已过期，请重新生成', true);
        }
      } catch { /* 单次网络抖动继续轮询 */ }
      finally { inflight = false; }
    }, 2500);
  }
  function resumeLogin() {
    let s = null;
    try { s = JSON.parse(sessionStorage.getItem(LOGIN_KEY) || 'null'); } catch { s = null; }
    if (!s || !s.flow_id || !s.url) return;
    if (s.deadline <= Date.now()) { setLoginSession(null); return; }
    showLoginPane(s.flow_id, s.url, s.deadline);
    pollLogin(s.flow_id);
  }
  function setLoginStatus(text, isErr) {
    const el = zh$('acct-login-status');
    el.textContent = text;
    el.dataset.err = isErr ? '1' : '';
    el.classList.toggle('live-dot', !isErr);
    el.style.color = isErr ? 'var(--red)' : '';
  }
  function stopLoginPoll() {
    if (loginTimer) { clearInterval(loginTimer); loginTimer = null; }
    if (expireTimer) { clearInterval(expireTimer); expireTimer = null; }
  }
  /* ── 手动领取：浏览器内过阿里滑块 → verifyParam 提交 hub 转发 ── */
  let sdkLoaded = false, verifyParam = '', cfgRegion = '';
  function claimStatus(msg) { zh$('acct-claim-status').textContent = msg || ''; }
  function claimStatusColor(ok) { zh$('acct-claim-status').style.color = ok ? 'var(--mint)' : ''; }
  function setClaimBusy(b) {
    zh$('acct-claim-submit').disabled = b;
    zh$('acct-claim-submit').textContent = b ? '提交中…' : '领取';
  }
  async function openClaimModal(id) {
    const jwtAll = all.filter(a => a.mode === 'jwt');
    if (!jwtAll.length) { showToast('没有可领取的 JWT 账号', 'info'); return; }
    const sel = zh$('acct-claim-account');
    sel.innerHTML = jwtAll.map(a => `<option value="${a.id}">${zhEsc(a.name)}</option>`).join('');
    if (id) sel.value = id;
    zh$('acct-claim-plan-id').value = '';
    verifyParam = '';
    claimStatus('');
    setClaimBusy(false);
    zhOpenOverlay('zh-modal-claim');
    await loadClaimPlans();
    initClaimCaptcha();
  }
  async function loadClaimPlans() {
    const sel = zh$('acct-claim-plan');
    sel.innerHTML = '<option value="">加载中…</option>';
    try {
      const d = await api('GET', '/claim/preview?account_id=' + encodeURIComponent(zh$('acct-claim-account').value));
      const item = (d.preview || [])[0] || {};
      const plans = item.plans || [];
      if (item.error) claimStatus('套餐列表：' + item.error);
      sel.innerHTML = '<option value="">自动选择（可领套餐中优先级最高）</option>'
        + plans.map(p => `<option value="${zhEsc(p.plan_id)}">${zhEsc(p.name || p.plan_id)}</option>`).join('')
        + (!plans.length ? '<option value="" disabled>（当前无可领活动套餐）</option>' : '');
    } catch (e) {
      sel.innerHTML = '<option value="">自动选择（列表加载失败，可直接填 plan_id）</option>';
      claimStatus('套餐列表加载失败：' + e.message);
    }
  }
  async function loadCaptchaSdk() {
    if (sdkLoaded && typeof window.initAliyunCaptcha === 'function') return;
    await new Promise((res, rej) => {
      const s = document.createElement('script');
      s.src = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';
      s.onload = res; s.onerror = () => rej(new Error('验证码 SDK 加载失败'));
      document.head.appendChild(s);
    });
    sdkLoaded = true;
  }
  async function initClaimCaptcha() {
    const box = zh$('acct-claim-captcha');
    box.innerHTML = '<span class="dialog-help">验证码加载中…</span>';
    try {
      const cfg = await api('GET', '/claim/captcha-config');
      if (cfg.enabled === false || !cfg.scene_id) throw new Error('验证码未启用');
      cfgRegion = cfg.region || '';
      await loadCaptchaSdk();
      box.innerHTML = '<div id="acct-cap-holder"></div><button id="acct-cap-btn" type="button" style="display:none"></button>';
      window.AliyunCaptchaConfig = { region: cfg.region, prefix: cfg.prefix };
      window.initAliyunCaptcha({
        SceneId: cfg.scene_id, mode: 'popup', language: 'zh-CN', showErrorTip: false,
        element: '#acct-cap-holder', button: '#acct-cap-btn',
        getInstance: inst => { try { inst.startTracelessVerification && inst.startTracelessVerification(); } catch { /* 无感降级为交互验证 */ } },
        success: param => {
          verifyParam = typeof param === 'string' ? param : (param && param.captchaVerifyParam) || '';
          claimStatus('✓ 人机验证已通过，点击「领取」提交');
          claimStatusColor(true);
        },
        fail: () => { claimStatus('无感验证未通过，已弹出交互验证，请完成滑块'); claimStatusColor(false); },
        onError: () => { claimStatus('验证码出错，请重试'); claimStatusColor(false); },
      });
      claimStatus('请完成人机验证（无感通过或滑块）');
      claimStatusColor(false);
    } catch (e) {
      box.innerHTML = '';
      claimStatus('验证码初始化失败：' + e.message);
      claimStatusColor(false);
    }
  }
  async function submitManualClaim() {
    if (!verifyParam) { claimStatus('请先完成人机验证'); claimStatusColor(false); return; }
    setClaimBusy(true);
    const account_id = zh$('acct-claim-account').value;
    const planSel = zh$('acct-claim-plan').value;
    const planManual = zh$('acct-claim-plan-id').value.trim();
    try {
      const d = await api('POST', '/claim/manual', {
        account_id,
        captcha_verify_param: verifyParam,
        captcha_region: cfgRegion || undefined,
        plan_id: planManual || planSel || undefined,
      });
      d.outcomes.forEach(claimToast);
      if (d.summary.ok) zhCloseOverlay('zh-modal-claim');
      else {
        claimStatus('领取失败：' + (d.outcomes[0] || {}).message + '，可重做验证再试');
        claimStatusColor(false);
        verifyParam = ''; initClaimCaptcha();
      }
    } catch (e) { claimStatus('提交失败：' + e.message); claimStatusColor(false); }
    finally { setClaimBusy(false); await load(); }
  }

  /* ── 导入 / 导出 ── */
  async function doExport() {
    try {
      const d = await api('GET', '/export');
      const a = document.createElement('a');
      a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' }));
      a.download = 'zcode-accounts.json'; a.click();
    } catch (e) { showToast('导出失败: ' + e.message, 'error'); }
  }
  async function onImportFile(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      const d = await api('POST', '/import', payload);
      showToast(`导入 ${d.count} 个账号`, 'success');
      load();
    } catch (e) { showToast('导入失败: ' + e.message, 'error'); }
    ev.target.value = '';
  }

  /* ── 事件绑定（模块加载即绑，DOM 已在文档里） ── */
  zhOn('acct-filter', t => { filter = t.dataset.k; renderFilters(); renderList(); });
  zhOn('acct-add', openAdd);
  zhOn('acct-import', () => zh$('acct-import-file').click());
  zhOn('acct-export', doExport);
  zhOn('acct-refresh-all', refreshAll);
  zhOn('acct-claim-all', claimAll);
  zhOn('acct-refresh', t => refreshOne(t.dataset.id));
  zhOn('acct-claim', t => claimOne(t.dataset.id));
  zhOn('acct-claim-manual', t => openClaimModal(t.dataset.id));
  zhOn('acct-edit', t => openEdit(t.dataset.id));
  zhOn('acct-delete', t => doDelete(t.dataset.id));
  zhOn('acct-do-add', doAdd);
  zhOn('acct-close-add', () => zhCloseOverlay('zh-modal-add'));
  zhOn('acct-start-login', startLogin);
  zhOn('acct-cancel-login', cancelLogin);
  zhOn('acct-copy-login', () => zhCopy(loginUrl));
  zhOn('acct-open-login', () => { if (loginUrl) window.open(loginUrl, '_blank', 'noopener'); });
  zhOn('acct-close-claim', () => {
    zhCloseOverlay('zh-modal-claim');
    try { window.AliyunCaptchaConfig = undefined; } catch { /* 清理全局配置 */ }
  });
  zhOn('acct-do-claim', submitManualClaim);

  zh$('acct-list').addEventListener('change', e => {
    const box = e.target.closest('input[data-toggle]');
    if (box) toggleEnabled(box.dataset.toggle, box.checked);
  });
  zh$('acct-claim-account').addEventListener('change', () => {
    // 切换账号 = 换了提交主体，旧 verifyParam 作废，重做验证
    verifyParam = '';
    claimStatus('账号已切换，请重新完成人机验证');
    initClaimCaptcha();
    loadClaimPlans();
  });
  zh$('acct-import-file').addEventListener('change', onImportFile);

  /* ── 生命周期：切走即停（授权读取除外）── */
  function activate() {
    if (loaded) renderList();
    load();
    timer = setInterval(() => { if (!zhAnyOverlayOpen()) load(true); }, 5000);
    zh$('acct-live').style.display = '';
    resumeLogin();
  }
  function deactivate() {
    if (timer) { clearInterval(timer); timer = null; }
    // 授权读取不跟页签走：服务端已经在捞凭证，页面停一轮只是晚点看到结果，
    // 停下来反而让人以为还要重新生成链接。
    zh$('acct-live').style.display = 'none';
  }
  return { activate, deactivate };
})();
