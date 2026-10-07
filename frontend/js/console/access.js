/* zcode-hub 控制台 · 03 客户端接入
   地址一律由当前 origin 推导：后端没有「公网基址」配置项，从别的机器接入时把
   主机名换成对外 IP/域名即可。网关 Key 由后台生成，明文只在创建响应里出现一次，
   列表接口只回掩码，所以这里不存在「读出原值」的可能。 */
window.ZHAccess = (function () {
  const o = () => location.origin;
  let lastKey = '';      // 刚生成的那把明文，只活在本次页面停留期间
  let lastKeyId = '';
  let currentModel = '<模型名>';

  function renderEndpoints(model) {
    currentModel = model;
    zh$('acc-base').value = o() + '/v1';
    zh$('acc-msgs').value = o() + '/v1/messages';
    zh$('acc-oai').value = o() + '/v1/chat/completions';
    zh$('acc-models').value = o() + '/v1/models';
    // 示例里的模型名要用当前真放行的那个，照抄却拿到 3006 就白教一遍。
    zh$('acc-curl').value = `curl ${o()}/v1/messages \\\n`
      + `  -H "Authorization: Bearer <网关 Key>" -H "content-type: application/json" \\\n`
      + `  -d '{"model":"${model}","messages":[{"role":"user","content":"hi"}]}'`;
  }

  function keyRow(k) {
    return `<div class="zh-keyrow">
      <span class="zh-keyrow-label" title="${zhEsc(k.label)}">${zhEsc(k.label)}</span>
      <code class="zh-keyrow-mask">${zhEsc(k.masked)}</code>
      <span class="zh-keyrow-time">${zhFmtDateSec(k.created_at)}</span>
      <button class="zh-keyrow-del" type="button" data-act="acc-del-key" data-id="${zhEsc(k.id)}" data-label="${zhEsc(k.label)}">删除</button>
    </div>`;
  }

  function showReveal(row) {
    const box = zh$('acc-key-reveal');
    box.hidden = false;
    box.innerHTML = `<div class="zh-keyreveal-ttl">新 Key 已生成 · ${zhEsc(row.label)}</div>
      <div class="zh-fld-row">
        <input class="input" id="acc-key-plain" readonly value="${zhEsc(row.key)}" spellcheck="false">
        <button class="page-action-btn page-action-btn-primary" type="button" data-act="acc-copy-key">复制 Key</button>
        <button class="page-action-btn" type="button" data-act="acc-copy-curl">复制 curl</button>
      </div>
      <div class="zh-keyreveal-warn">这是它最后一次以明文出现：离开本页或刷新就读不回来了。先复制，再关掉这个框。</div>`;
    const input = zh$('acc-key-plain');
    input.focus();
    input.select();
  }

  function hideReveal() {
    const box = zh$('acc-key-reveal');
    box.hidden = true;
    box.innerHTML = '';
  }

  async function loadKeys() {
    const res = await api('GET', '/keys');
    const keys = res.keys || [];
    zh$('acc-key-count').textContent = keys.length;
    zh$('acc-gwkey-meta').textContent = keys.length ? `${keys.length} 把 · 校验已开` : '未启用校验';
    zh$('acc-key-list').innerHTML = keys.length
      ? keys.map(keyRow).join('')
      : '<div class="zh-empty">还没有生成过 Key。</div>';
    const on = keys.length > 0;
    zh$('acc-reco').innerHTML = on
      ? '校验已开启：客户端必须带上 Key，否则 401；带错 403。Key 泄露了就删掉重建，旧串立即失效。'
      : '<span style="color:var(--accent)">未生成 Key —— 网关不校验任何凭证，生产环境请立刻生成一把。</span>';
    zhStatus('client', on ? 'ok' : 'warn', on ? '网关鉴权已开' : '网关未鉴权');
  }

  async function load() {
    try {
      const s = await api('GET', '/settings');
      const names = s.model_whitelist_effective || [];
      renderEndpoints(names[0] || '<模型名>');
      zh$('acc-model-meta').textContent = `放行 ${names.length} 个`;
      zh$('acc-model-hint').innerHTML = names.length
        ? `当前放行 ${names.length} 个：` + names.map(zhEsc).join('、')
        : '<strong>不拦任何模型名</strong>，<code>/v1/models</code> 返回内置名单。';
      await loadKeys();
    } catch (e) {
      showToast('读取接入信息失败: ' + e.message, 'error');
      zhStatus('client', 'bad', '接入信息读取失败');
    }
  }

  function createKey() {
    zhDialog({
      title: '生成网关 API Key',
      bodyHtml: `<div class="dialog-field"><span class="dialog-label">备注名</span>`
        + `<input class="input" id="dlg-key-label" type="text" autocomplete="off" maxlength="40" placeholder="如 生产 / 小开的电脑"></div>`
        + `<div class="dialog-help">备注名用来在「用量与明细」里区分这把 Key 是谁在用。生成后明文只显示一次。</div>`,
      read: () => (zh$('dlg-key-label').value || '').trim(),
      okLabel: '生成',
      onOk: async label => {
        try {
          const row = (await api('POST', '/keys', { label: label || '' })).key;
          lastKey = row.key;
          lastKeyId = row.id;
          showReveal(row);
          await loadKeys();
        } catch (e) { showToast('生成失败: ' + e.message, 'error'); }
      },
    });
  }

  function deleteKey(btn) {
    zhConfirm('删除这把 Key',
      `<div class="dialog-help">删除后，用这把 Key 的客户端立刻拿到 403。此操作不可恢复。<br><strong>${zhEsc(btn.dataset.label)}</strong></div>`,
      async () => {
        try {
          await api('DELETE', '/keys/' + encodeURIComponent(btn.dataset.id));
          showToast('已删除', 'success');
          if (btn.dataset.id === lastKeyId) hideReveal();
          await loadKeys();
        } catch (e) { showToast('删除失败: ' + e.message, 'error'); }
      }, '删除');
  }

  zhOn('acc-refresh', () => load());
  zhOn('acc-copy', t => { const el = zh$(t.dataset.src); if (el) zhCopy(el.value); });
  zhOn('acc-new-key', createKey);
  zhOn('acc-del-key', deleteKey);
  zhOn('acc-copy-key', () => { const el = zh$('acc-key-plain'); if (el) zhCopy(el.value); });
  zhOn('acc-copy-curl', () => {
    if (!lastKey) return showToast('没有可复制的 Key', 'error');
    zhCopy(zh$('acc-curl').value.replace('<网关 Key>', lastKey));
  });

  function activate() {
    renderEndpoints(currentModel);
    load();
  }
  function deactivate() { zhStatus('client', 'idle', ''); }
  return { activate, deactivate };
})();
