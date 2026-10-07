/* zcode-hub 控制台 · 07 活动日志
   数据是 SQLite 的 activity 表（不是文件日志，本网关没有文件 sink），所以重启后仍在；
   条数由后端截断到 keep 条，这里展示的是「最近 N 条」而不是全量。
   打开拉一次 + 手动刷新：日志不是实时指标，不值得挂个轮询。 */
window.ZHActivity = (function () {
  let entries = [], kinds = [], keep = 0, total = 0, kind = '', q = '';

  const kindLabel = k => {
    const hit = kinds.find(x => x.kind === k);
    return hit ? hit.label : k;
  };

  function renderChips() {
    const defs = [['', '全部']].concat(kinds.map(k => [k.kind, k.label]));
    zh$('act-kinds').innerHTML = defs.map(([v, label]) =>
      `<button class="filter-chip${kind === v ? ' active' : ''}" type="button" data-act="act-kind" data-k="${zhEsc(v)}">${zhEsc(label)}</button>`).join('');
  }

  function renderList() {
    const needle = q.trim().toLowerCase();
    const list = entries.filter(e => !needle
      || `${e.text} ${kindLabel(e.kind)}`.toLowerCase().includes(needle));
    zh$('act-count').textContent = kind || needle ? `${list.length} / ${entries.length} 条` : `${entries.length} 条`;
    zh$('act-list').innerHTML = list.length ? list.map(e => `
      <div class="zh-log-row${e.kind === 'error' ? ' is-error' : ''}">
        <span class="zh-log-time">${zhFmtTs(e.ts)}</span>
        <span class="zh-log-kind"><i class="zh-log-dot ${zhEsc(e.kind)}" aria-hidden="true"></i>${zhEsc(kindLabel(e.kind))}</span>
        <span class="zh-log-text" title="${zhEsc(e.text)}">${zhEsc(e.text)}</span>
      </div>`).join('')
      : '<div class="zh-empty">这个分类下还没有记录。</div>';
  }

  async function load() {
    const res = await api('GET', '/activity?limit=200' + (kind ? '&kind=' + encodeURIComponent(kind) : ''));
    entries = res.entries || [];
    kinds = res.kinds || [];
    keep = res.keep || 0;
    total = res.total || 0;
    renderChips();
    renderList();
    zh$('act-storage').textContent =
      `共 ${total} 条，列表按时间倒序取最近 ${entries.length} 条；表满 ${keep} 条后最旧的自动丢弃。重启服务不清空。`;
    zh$('act-updated').textContent = `更新于 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
    zhStatus('activity', 'ok', `日志 ${total} 条`);
  }

  zhOn('act-refresh', () => load().catch(e => showToast('加载失败: ' + e.message, 'error')));
  zhOn('act-kind', t => { kind = t.dataset.k; renderChips(); load().catch(() => showToast('加载失败', 'error')); });
  zh$('act-search').addEventListener('input', e => { q = e.target.value; renderList(); });

  function activate() { load().catch(e => { showToast('加载活动日志失败: ' + e.message, 'error'); zhStatus('activity', 'bad', '日志读取失败'); }); }
  function deactivate() { zhStatus('activity', 'idle', ''); }
  return { activate, deactivate };
})();
