// 與 Flask 後端溝通的共用函式
async function apiFetch(url, options = {}) {
  const res = await fetch(url, { credentials: 'same-origin', ...options });
  let body = {};
  try { body = await res.json(); } catch (_) { /* 非 JSON 回應 */ }
  if (!res.ok) {
    const err = new Error(body.error || `請求失敗（${res.status}）`);
    err.status = res.status;
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
