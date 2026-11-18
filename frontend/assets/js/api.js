// 與 Flask 後端溝通的共用函式
async function apiFetch(url, options = {}) {
  const res = await fetch(url, { credentials: 'same-origin', ...options });
  let body = {};
  try { body = await res.json(); } catch (_) { /* 非 JSON 回應 */ }
  if (!res.ok) {
    const err = new Error(body.error || `請求失敗（${res.status}）`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// 需要登入的頁面呼叫：未登入就導向登入頁
async function requireLogin() {
  try {
    return await apiFetch('/api/me');
  } catch (e) {
    if (e.status === 401) window.location.href = 'login.html';
    throw e;
  }
}

async function logout() {
  await apiFetch('/api/logout', { method: 'POST' });
  window.location.href = 'login.html';
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function todayISO() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 10);
}

// 確認視窗：以大字、大按鈕詢問使用者，回傳 Promise<boolean>
function confirmDialog({ title, message, okText = '確定', cancelText = '取消', danger = false }) {
  if (typeof bootstrap === 'undefined') return Promise.resolve(window.confirm(`${title}\n\n${message}`));
  return new Promise(resolve => {
    const el = document.createElement('div');
    el.className = 'modal fade';
    el.tabIndex = -1;
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('role', 'dialog');
    el.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content app-modal">
          <div class="modal-body">
            <h2 class="app-modal-title">${escapeHtml(title)}</h2>
            <p class="app-modal-text">${escapeHtml(message)}</p>
          </div>
          <div class="app-modal-actions">
            <button type="button" class="btn-app btn-ghost" data-act="cancel">${escapeHtml(cancelText)}</button>
            <button type="button" class="btn-app${danger ? ' btn-danger-app' : ''}" data-act="ok">${escapeHtml(okText)}</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    const modal = new bootstrap.Modal(el);
    let result = false;
    el.querySelector('[data-act=ok]').addEventListener('click', () => { result = true; modal.hide(); });
    el.querySelector('[data-act=cancel]').addEventListener('click', () => modal.hide());
    el.addEventListener('hidden.bs.modal', () => { el.remove(); resolve(result); });
    modal.show();
  });
}

// 成功提示視窗：一個「完成」按鈕，關閉後 resolve
function noticeDialog({ title, message, okText = '完成' }) {
  if (typeof bootstrap === 'undefined') { window.alert(`${title}\n\n${message}`); return Promise.resolve(); }
  return new Promise(resolve => {
    const el = document.createElement('div');
    el.className = 'modal fade';
    el.tabIndex = -1;
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('role', 'dialog');
    el.innerHTML = `
      <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content app-modal app-modal-success">
          <div class="modal-body">
            <div class="success-mark"><i class="bi bi-check-lg"></i></div>
            <h2 class="app-modal-title">${escapeHtml(title)}</h2>
            <p class="app-modal-text">${escapeHtml(message)}</p>
          </div>
          <div class="app-modal-actions">
            <button type="button" class="btn-app btn-block" data-act="ok">${escapeHtml(okText)}</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(el);
    const modal = new bootstrap.Modal(el);
    el.querySelector('[data-act=ok]').addEventListener('click', () => modal.hide());
    el.addEventListener('shown.bs.modal', () => el.querySelector('[data-act=ok]').focus());
    el.addEventListener('hidden.bs.modal', () => { el.remove(); resolve(); });
    modal.show();
  });
}

// 依登入狀態切換導覽列：未登入顯示「登入」，登入後顯示「登出」與個人資料圖示
async function initAuthNav() {
  let s = { logged_in: false };
  try { s = await apiFetch('/api/session'); } catch (_) { /* 後端未啟動時當作未登入 */ }
  const page = location.pathname.split('/').pop();
  if (s.logged_in && (page === 'login.html' || page === 'register.html')) {
    window.location.replace('health.html');  // 已登入就不必再看到登入、註冊表單
    return;
  }
  document.body.classList.toggle('logged-in', !!s.logged_in);
  document.body.classList.add('auth-ready');
  const btn = document.querySelector('.profile-btn');
  if (btn && s.logged_in) {
    const label = `個人資料（${s.display_name || s.username}）`;
    btn.title = label;
    btn.setAttribute('aria-label', label);
  }
}

// 依目前網址標示導覽列中的所在頁面
function markActiveNav() {
  const page = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('#navmenu > ul > li > a[href$=".html"]').forEach(a => {
    a.classList.toggle('active', a.getAttribute('href') === page);
  });
}

(function () {
  const run = () => { initAuthNav(); markActiveNav(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
