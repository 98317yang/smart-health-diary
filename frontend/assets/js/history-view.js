// 歷史資料的共用畫面：病人自己的「歷史資料」與醫護端的「病人紀錄」都用這一份，
// 兩邊的統計定義、達成表與圖表才不會各寫一套而出現不同的數字。
const HistoryView = (() => {
  const WEEKDAY = ['日', '一', '二', '三', '四', '五', '六'];
  const shortDate = iso => iso.slice(5).replace('-', '/');
  const weekday = iso => WEEKDAY[new Date(iso + 'T00:00:00').getDay()];
  const mean = xs => {
    const v = xs.filter(x => x !== null && x !== undefined);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  const unit = u => `<small>${u}</small>`;
  const set = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };

  // 運動、蔬菜的達成率：分母是「有填寫的天數」，沒記錄的日子不算成未達成
  function rateText(rate, days) {
    if (!rate.answered_days) return { value: '—', foot: `${days} 天內沒有填寫` };
    return {
      value: `${rate.done}${unit(`/ ${rate.answered_days} 天`)}`,
      foot: rate.answered_days === days ? `${days} 天都有填寫` : `${days} 天中有 ${rate.answered_days} 天有填寫`
    };
  }

  function renderStats(d) {
    const days = d.dates.length;
    for (const [key, id] of [['exercise', 'Exercise'], ['veggie', 'Veggie']]) {
      const t = rateText(d[key], days);
      set(`stat${id}`, t.value);
      set(`foot${id}`, escapeHtml(t.foot));
    }
    const pre = mean(d.glucose_pre), post = mean(d.glucose_post);
    const ref = d.reference;  // 只有醫護端會帶參考範圍，用來標出超出控制目標的數值
    const cls = (v, lo, hi) => ref && v !== null && (v > hi || v < lo) ? 'is-out' : '';
    set('statGlucose', (pre === null && post === null) ? '—' :
      `<span class="${cls(pre, ref?.pre_low, ref?.pre_high)}">${pre === null ? '—' : Math.round(pre)}</span>` +
      `<small>/</small><span class="${cls(post, ref?.post_low, ref?.post_high)}">${post === null ? '—' : Math.round(post)}</span>`);
    const carbs = mean(d.carbs_g);
    set('statCarbs', carbs === null ? '—' : `${Math.round(carbs)}${unit('公克')}`);
    set('footCarbs', '只計入有拍照的餐點');
  }

  // 每日達成表：欄數跟著區間變；手機上可以橫向捲動，預設捲到最近的日期
  function renderStrip(d) {
    const cell = v => v === null || v === undefined ? '<div class="ws-cell" title="沒有填寫">－</div>'
      : v ? '<div class="ws-cell done" title="達成"><i class="bi bi-check-lg"></i></div>'
          : '<div class="ws-cell miss" title="未達成"><i class="bi bi-x-lg"></i></div>';
    const strip = document.getElementById('weekStrip');
    strip.style.gridTemplateColumns = `var(--ws-label, 110px) repeat(${d.dates.length}, minmax(44px, 1fr))`;
    strip.innerHTML = '<div></div>' +
      d.dates.map(x => `<div class="ws-head">${shortDate(x)}<br>週${weekday(x)}</div>`).join('') +
      '<div class="ws-label">運動</div>' + d.exercise_daily.map(cell).join('') +
      '<div class="ws-label">蔬菜</div>' + d.veggie_daily.map(cell).join('');
    const scroller = strip.closest('.strip-scroll');
    if (scroller) requestAnimationFrame(() => { scroller.scrollLeft = scroller.scrollWidth; });
  }

  // 圖表：每次都重建 canvas。若沿用舊的，前一個區間顯示「沒有資料」時 canvas 已被拿掉，
  // 切換到有資料的區間就會找不到 canvas 而出錯。
  let charts = [];
  const baseOptions = () => ({
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { position: 'bottom', labels: { font: { size: 14 }, boxWidth: 14 } } },
    scales: { x: { offset: true, grid: { display: false }, ticks: { font: { size: 13 } } }, y: { ticks: { font: { size: 13 } } } }
  });

  function freshCanvas(boxId) {
    const box = document.getElementById(boxId);
    box.classList.remove('is-empty');
    box.innerHTML = '<canvas></canvas>';
    return box.querySelector('canvas');
  }

  function showEmpty(boxId, text) {
    const box = document.getElementById(boxId);
    box.classList.add('is-empty');
    box.innerHTML = `<div class="empty-state">${escapeHtml(text)}</div>`;
  }

  function renderCharts(d) {
    charts.forEach(c => c.destroy());
    charts = [];
    if (typeof Chart === 'undefined') {  // 圖表套件載入失敗時，其他內容照常顯示
      showEmpty('lineBox', '圖表無法載入。');
      showEmpty('carbsBox', '圖表無法載入。');
      return;
    }
    const labels = d.dates.map(shortDate);
    if (mean(d.glucose_pre) === null && mean(d.glucose_post) === null) {
      showEmpty('lineBox', '這段期間沒有血糖紀錄。');
    } else {
      charts.push(new Chart(freshCanvas('lineBox'), {
        type: 'line',
        data: {
          labels,
          datasets: [
            { label: '飯前血糖', data: d.glucose_pre, borderColor: '#2a9a9f', backgroundColor: '#2a9a9f',
              spanGaps: true, tension: 0.25, pointRadius: 4, borderWidth: 3 },
            { label: '飯後血糖', data: d.glucose_post, borderColor: '#cf5e48', backgroundColor: '#cf5e48',
              spanGaps: true, tension: 0.25, pointRadius: 4, borderWidth: 3 }
          ]
        },
        options: baseOptions()
      }));
    }
    if (mean(d.carbs_g) === null) {
      showEmpty('carbsBox', '這段期間沒有拍照記錄的餐點。');
    } else {
      charts.push(new Chart(freshCanvas('carbsBox'), {
        type: 'bar',
        data: { labels, datasets: [{ label: '醣類（公克）', data: d.carbs_g, backgroundColor: '#e2b25c',
                                     borderRadius: 6, maxBarThickness: 46 }] },
        options: { ...baseOptions(), plugins: { legend: { display: false } },
                   scales: { x: { offset: true, grid: { display: false }, ticks: { font: { size: 13 } } },
                             y: { beginAtZero: true, ticks: { font: { size: 13 } } } } }
      }));
    }
  }

  function render(d) {
    renderStats(d);
    renderStrip(d);
    renderCharts(d);
  }

  // 區間選擇（7／14／30 天）
  function onRangeChange(handler) {
    document.querySelectorAll('input[name=days]').forEach(r =>
      r.addEventListener('change', () => handler(parseInt(r.value, 10))));
  }

  return { render, onRangeChange, shortDate, weekday, mean };
})();
