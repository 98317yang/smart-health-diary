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

// 各角色登入後的起始頁
function homeFor(role) {
  return role === 'staff' ? 'staff.html' : 'health.html';
}

// 這一頁是否需要登入、需要哪個角色；由 requireLogin 設定，供上一頁快取的檢查使用
let pageNeedsAuth = false;
let pageRole = null;

// 需要登入的頁面呼叫：未登入就導向登入頁
// role 給定時（'patient' 或 'staff'），角色不符會被導回自己的起始頁，
// 避免病人開到醫護頁面、或醫護開到記錄頁面卻無法使用
async function requireLogin(role) {
  pageNeedsAuth = true;
  pageRole = role || null;
  // 確認身分之前先不顯示內容，避免登出後按上一頁時閃過頁面骨架
  document.body.classList.add('needs-auth');
  let me;
  try {
    me = await apiFetch('/api/me');
  } catch (e) {
    if (e.status === 401) window.location.href = 'login.html';
    throw e;
  }
  if (role && me.role !== role) {
    window.location.replace(homeFor(me.role));
    throw new Error('角色不符');
  }
  document.body.classList.add('auth-ok');
  return me;
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

// 生日與性別的顯示格式（個人資料與醫護端共用，避免兩邊寫法不一致）
function formatBirthday(birthday, age) {
  if (!birthday) return '（未填寫）';
  const [y, m, d] = String(birthday).split('-');
  const text = `${Number(y)} 年 ${Number(m)} 月 ${Number(d)} 日`;
  return age === null || age === undefined ? text : `${text}（${age} 歲）`;
}

function formatAgeGender(age, gender) {
  const parts = [];
  if (age !== null && age !== undefined) parts.push(`${age} 歲`);
  if (gender) parts.push(gender);
  return parts.length ? parts.join('・') : '未填基本資料';
}

// 食物類別對應的顏色（健康日誌與醫護端共用）
const FOOD_GROUP_COLOR = { grains_starches: '#d4922a', vegetables: '#3c9a5f', protein: '#cf5e48' };

// 在 canvas 上畫出照片與辨識框
// src 可以是本機選取的照片網址，也可以是 /api/photos/<檔名>
// size 是後端讀到的圖片尺寸（已依 EXIF 轉正）；沒有時以圖片本身的尺寸為準
async function drawDetections(canvas, src, detections, size) {
  const img = new Image();
  img.src = src;
  await img.decode();
  const scale = Math.min(1, 1200 / img.naturalWidth);
  canvas.width = Math.round(img.naturalWidth * scale);
  canvas.height = Math.round(img.naturalHeight * scale);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  const sx = canvas.width / (size ? size[0] : img.naturalWidth);
  const sy = canvas.height / (size ? size[1] : img.naturalHeight);
  // 線寬與字級以「螢幕上實際顯示的寬度」換算，手機上標籤才不會太小
  const k = canvas.width / (canvas.parentElement.clientWidth || canvas.width);
  const lw = Math.max(canvas.width / 240, 2.5 * k);
  const fs = Math.round(Math.max(canvas.width / 38, 15 * k));
  ctx.font = `bold ${fs}px system-ui, sans-serif`;
  for (const d of detections) {
    const color = FOOD_GROUP_COLOR[d.label] || '#3fbbc0';
    const [x1, y1, x2, y2] = d.box;
    const x = x1 * sx, y = y1 * sy, w = (x2 - x1) * sx, h = (y2 - y1) * sy;
    ctx.lineWidth = lw; ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);
    const text = d.label_zh;
    const tw = ctx.measureText(text).width + fs * 0.8, th = fs * 1.5;
    const ty = y - th >= 0 ? y - th : y;
    ctx.fillStyle = color;
    ctx.fillRect(x - lw / 2, ty, tw, th);
    ctx.fillStyle = '#fff';
    ctx.fillText(text, x - lw / 2 + fs * 0.4, ty + fs * 1.1);
  }
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
    window.location.replace(homeFor(s.role));  // 已登入就不必再看到登入、註冊表單
    return;
  }
  document.body.classList.toggle('logged-in', !!s.logged_in);
  document.body.classList.toggle('role-staff', s.role === 'staff');
  document.body.classList.toggle('role-patient', s.logged_in && s.role !== 'staff');
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

// 瀏覽器的「上一頁」會從快取還原整個頁面，不會重新執行頁面程式，
// 因此登出後按上一頁仍會看到原本的內容。這裡在還原時重新確認登入狀態。
window.addEventListener('pageshow', async e => {
  if (!e.persisted) return;
  // 這一頁是從上一頁的快取還原的，裡面可能是登出前的資料。
  // 先立刻遮住內容（不能等 await，否則查證期間畫面會露出來），確認身分後再放行。
  if (pageNeedsAuth) document.body.classList.remove('auth-ok');
  let s = { logged_in: false };
  try { s = await apiFetch('/api/session'); } catch (_) { /* 後端沒回應時當作未登入 */ }
  if (pageNeedsAuth && (!s.logged_in || (pageRole && s.role !== pageRole))) {
    window.location.replace(s.logged_in ? homeFor(s.role) : 'login.html');
    return;
  }
  if (pageNeedsAuth) document.body.classList.add('auth-ok');
  initAuthNav();  // 可能在別的分頁登入或登出，導覽列要跟著更新
});

(function () {
  const run = () => { initAuthNav(); markActiveNav(); };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
