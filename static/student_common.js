/* 学生门户共享：令牌、API、Toast 与响应式导航壳。 */
const T2 = {
  token() { return localStorage.getItem('edu_token_student') || ''; },
  setToken(token) { token ? localStorage.setItem('edu_token_student', token) : localStorage.removeItem('edu_token_student'); },
  async api(path, options) {
    const headers = Object.assign({}, (options && options.headers) || {});
    if (this.token()) headers.Authorization = 'Bearer ' + this.token();
    const response = await fetch(path, Object.assign({}, options, {headers}));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) {
        this.setToken('');
        location.href = '/login?portal=student&next=' + encodeURIComponent(location.pathname + location.search);
      }
      const detail = data.error || data.detail;
      const fallback = response.status === 404 ? '请求的页面或服务暂不可用' : `请求失败（${response.status}）`;
      const error = new Error(typeof detail === 'string' && detail !== 'Not Found' ? detail : fallback);
      error.status = response.status;
      throw error;
    }
    return data;
  },
  escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g,
      char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  },
  cleanText(value) {
    return String(value ?? '').replace(/\r\n?/g, '\n').replace(/[ \t]*\n[ \t]*/g, ' ').replace(/[ \t]{2,}/g, ' ').trim();
  },
  toast(message, type = '') {
    let wrap = document.getElementById('toastWrap');
    if (!wrap) { wrap = document.createElement('div'); wrap.id = 'toastWrap'; document.body.appendChild(wrap); }
    const item = document.createElement('div'); item.className = 'toast ' + type;
    item.textContent = message; wrap.appendChild(item); setTimeout(() => item.remove(), 3500);
  },
  statusBadge(status) {
    return {
      not_started: '<span class="badge b-gray">待完成</span>',
      in_progress: '<span class="badge b-warn">进行中</span>',
      submitted: '<span class="badge b-info">已提交</span>',
      checked: '<span class="badge b-ok">已批改</span>'
    }[status] || '<span class="badge b-gray">未知</span>';
  },
  renderShell(active, title) {
    document.body.classList.add('student-theme');
    const nav = (key, href, label, icon) => `<a class="${active === key ? 'active' : ''}" href="${href}">${icon}${label}</a>`;
    const overviewIcon = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>';
    const taskIcon = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 11l2 2 4-4"/><path d="M5 3h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z"/></svg>';
    document.getElementById('shell').innerHTML = `
      <div class="sidebar"><div class="logo"><div class="mark" style="background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28)">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2"><path d="M22 10L12 5 2 10l10 5 10-5z"/><path d="M6 12v5c0 1.7 2.7 3 6 3s6-1.3 6-3v-5"/></svg>
      </div><div class="name">知学课堂<small>STUDENT SPACE</small></div></div>
      <div class="nav"><div class="group">学习空间</div>${nav('home', '/student', '学习概览', overviewIcon)}${nav('assignments', '/student#assignments', '我的作业', taskIcon)}</div><div class="foot">智慧教研平台 · 学生端</div></div>
      <div class="topbar"><div class="crumb">学生门户 / <b>${this.escapeHtml(title)}</b></div><div class="right"><span id="shellUser"></span></div></div>`;
    this.api('/api/v1/auth/me').then(me => {
      const name = me.real_name || me.username;
      document.querySelectorAll('[data-student-name]').forEach(el => { el.textContent = name; });
      document.getElementById('shellUser').innerHTML = `
        <span class="student-account"><span class="avatar">${this.escapeHtml(name.slice(0, 1))}</span>
        <span>${this.escapeHtml(name)}</span><a href="/login?portal=student" onclick="T2.setToken('')" style="font-size:12px;color:var(--muted)">退出</a></span>`;
    }).catch(() => {});
  }
};
