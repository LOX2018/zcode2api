/* zcode-hub 控制台 · 06 系统参数
   口令 + 四个后台周期。后端 PUT /settings 是按字段增量生效的，所以每张卡各存各的，
   不会把另一张卡没动的字段顺手中空值覆盖掉。 */
window.ZHSystem = (function () {
  // 后端回显的是掩码，回填进输入框原样提交必须当成「没改」，否则掩码会被写进真值
  function isSecretMask(v) { return String(v).includes('…') || v === '••••'; }
  function num(el, label) {
    const v = parseInt(el.value, 10);
    if (isNaN(v) || v < 0) { showToast(`${label}必须是非负整数`, 'error'); return null; }
    return v;
  }

  async function load() {
    try {
      const d = await api('GET', '/settings');
      zh$('sys-admin-key').value = d.admin_key_masked || '';
      zh$('sys-admin-hint').style.display = d.admin_key_is_default ? 'block' : 'none';
      zh$('sys-quota-interval').value = d.quota_refresh_interval ?? 60;
      zh$('sys-concurrency').value = d.account_concurrency ?? 2;
      zh$('sys-claim-interval').value = d.claim_round_interval ?? 600;
      zh$('sys-pull-interval').value = d.pricing_pull_interval ?? 0;
      zhStatus('system', d.admin_key_is_default ? 'warn' : 'ok', d.admin_key_is_default ? '默认口令' : '参数就绪');
    } catch (e) {
      showToast('读取系统参数失败: ' + e.message, 'error');
      zhStatus('system', 'bad', '参数读取失败');
    }
  }

  async function saveAdmin() {
    const key = zh$('sys-admin-key').value.trim();
    if (!key || isSecretMask(key)) return showToast('请输入新的后台密码（留空或掩码视为未修改）', 'error');
    try {
      await api('PUT', '/settings', { admin_key: key });
      await adminKey.set(key);
      showToast('口令已更新，后续用新密码登录', 'success');
      zh$('sys-admin-key').value = '';
      zh$('sys-admin-hint').style.display = 'none';
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
  }

  async function saveSched() {
    const quota = num(zh$('sys-quota-interval'), '额度刷新间隔');
    const conc = num(zh$('sys-concurrency'), '单账号并发上限');
    const claim = num(zh$('sys-claim-interval'), '套餐自动领取轮间隔');
    const pull = num(zh$('sys-pull-interval'), '价目拉取间隔');
    if (quota === null || conc === null || claim === null || pull === null) return;
    try {
      await api('PUT', '/settings', {
        quota_refresh_interval: quota,
        account_concurrency: conc,
        claim_round_interval: claim,
        pricing_pull_interval: pull,
      });
      showToast('调度参数已保存，即时生效', 'success');
    } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
  }

  zhOn('sys-save-admin', saveAdmin);
  zhOn('sys-save-sched', saveSched);

  function activate() { load(); }
  function deactivate() { zhStatus('system', 'idle', ''); }
  return { activate, deactivate };
})();
