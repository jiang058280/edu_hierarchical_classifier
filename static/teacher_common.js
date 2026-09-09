/* 教师端公共层：认证 / API / Shell 渲染 / Toast / 确认弹窗 */
const T = {
  TOKEN_KEY: 'edu_token_teacher',

  token() { return localStorage.getItem(this.TOKEN_KEY) || ''; },
  setToken(t) { t ? localStorage.setItem(this.TOKEN_KEY, t) : localStorage.removeItem(this.TOKEN_KEY); },

  escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g,
      c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  },

  cleanText(s) {
    return String(s ?? '').replace(/\r\n?/g, '\n').replace(/[ \t]*\n[ \t]*/g, ' ').replace(/[ \t]{2,}/g, ' ').trim();
  },

  async api(path, options) {
    const headers = Object.assign({}, (options && options.headers) || {});
    if (this.token()) headers['Authorization'] = 'Bearer ' + this.token();
    const resp = await fetch(path, Object.assign({}, options, { headers }));
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      if (resp.status === 401) {
        this.setToken('');
        location.href = '/login?portal=teacher&next=' + encodeURIComponent(location.pathname + location.search);
        throw new Error('登录已过期，请重新登录');
      }
      throw new Error(data.error || data.detail || (resp.status + ' ' + resp.statusText));
    }
    return data;
  },

  async downloadFile(path, filename) {
    const resp = await fetch(path, { headers: { Authorization: 'Bearer ' + this.token() } });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.error || data.detail || ('下载失败 ' + resp.status));
    }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  },

  toast(message, type = 'info') {
    let wrap = document.getElementById('toastWrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'toastWrap';
      document.body.appendChild(wrap);
    }
    const icons = { ok: '✓', err: '✕', info: 'i' };
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.innerHTML = `<span class="t-ico" style="font-weight:700">${icons[type] || 'i'}</span><span>${this.escapeHtml(message)}</span>`;
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .25s'; }, 2600);
    setTimeout(() => el.remove(), 2900);
  },

  confirm(message) {
    return new Promise((resolve) => {
      const modal = document.createElement('div');
      modal.className = 'modal show';
      modal.innerHTML = `
        <div class="box" style="width:400px">
          <h3>确认操作</h3>
          <p style="font-size:13.5px;color:var(--text-2);margin-bottom:18px">${this.escapeHtml(message)}</p>
          <div class="modal-foot">
            <button class="btn plain" data-act="cancel">取消</button>
            <button class="btn" data-act="ok" style="background:var(--bad)">确定</button>
          </div>
        </div>`;
      document.body.appendChild(modal);
      modal.querySelector('[data-act="cancel"]').onclick = () => { modal.remove(); resolve(false); };
      modal.querySelector('[data-act="ok"]').onclick = () => { modal.remove(); resolve(true); };
      modal.addEventListener('click', (e) => { if (e.target === modal) { modal.remove(); resolve(false); } });
    });
  },

  statusLabel(s) {
    return { published: '<span class="badge b-ok">已发布</span>',
             draft: '<span class="badge b-warn">草稿</span>',
             pending: '<span class="badge b-info">待审核</span>' }[s] || this.escapeHtml(s || '—');
  },

  diffStars(n) { return n ? '★'.repeat(n) + '☆'.repeat(5 - n) : '—'; },

  /* 渲染侧边导航 + 顶栏；active 为当前导航 key */
  renderShell(active, crumbTitle) {
    const logo = `
      <div class="logo">
        <div class="mark">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 10L12 5 2 10l10 5 10-5z"/><path d="M6 12v5c0 1.7 2.7 3 6 3s6-1.3 6-3v-5"/></svg>
        </div>
        <div class="name">智慧教研平台<small>TEACHING PLATFORM</small></div>
      </div>`;
    const item = (key, href, label, icon) => `
      <a href="${href}" class="${active === key ? 'active' : ''}">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${icon}</svg>
        ${label}
      </a>`;
    const nav = `
      <div class="nav">
        <div class="group">教研工作</div>
        ${item('home', '/teacher', '工作台', '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>')}
        ${item('bank', '/teacher/bank', '题库管理', '<path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>')}
        ${item('entry', '/teacher/entry', 'AI 智能录入', '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/>')}
        ${item('papers', '/teacher/papers', '组卷与试卷', '<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/><path d="M9 13h6M9 17h6"/>')}
        ${item('assignments', '/teacher/assignments', '作业与批改', '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/>')}
        <div class="group">系统</div>
        ${item('admin', '/admin', '治理工作台', '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09a1.65 1.65 0 00-1-1.51 1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09a1.65 1.65 0 001.51-1 1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06a1.65 1.65 0 001.82.33h0a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51h0a1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06a1.65 1.65 0 00-.33 1.82v0a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/>')}
      </div>`;
    const foot = `<div class="foot"><span id="shellModel">—</span></div>`;
    const sidebar = `<div class="sidebar">${logo}${nav}${foot}</div>`;
    const topbar = `
      <div class="topbar">
        <div class="crumb">教师工作台 / <b>${this.escapeHtml(crumbTitle)}</b></div>
        <div class="right">
          <span id="shellUser" style="display:flex;align-items:center;gap:8px"></span>
        </div>
      </div>`;
    document.getElementById('shell').innerHTML = sidebar + topbar;
    this.api('/api/v1/auth/me')
      .then(me => {
        const name = me.real_name || me.username;
        document.getElementById('shellUser').innerHTML = `
          <div class="avatar">${this.escapeHtml(name.slice(0, 1))}</div>
          <span>${this.escapeHtml(name)}</span>
          <a href="/login?portal=teacher" onclick="T.setToken('')" style="font-size:12px;color:var(--muted)">退出</a>`;
      })
      .catch(() => {});
    this.api('/api/v1/health')
      .then(h => {
        const el = document.getElementById('shellModel');
        if (el) el.innerHTML = `模型 <b style="color:var(--text)">${this.escapeHtml(h.model_version)}</b> · 查重${h.dedup_available ? '可用' : '离线'}`;
      })
      .catch(() => {});
  },
};
