const ohlcCanvas = document.getElementById('ohlcChart');
const signalsList = document.getElementById('signalsList');
const currentSignalBox = document.getElementById('currentSignal');
const refreshBtn = document.getElementById('refreshSignals');
const tfButtons = document.querySelectorAll('.tf-group button');
const modeBadge = document.getElementById('mode');
const healthBadge = document.getElementById('health');
const closedToggle = document.getElementById('closedToggle');

let chart;
let currentTf = '5m';

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function formatTs(ts) {
  return new Date(ts).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });
}

function renderSignals(signals) {
  signalsList.innerHTML = '';
  if (!signals.length) {
    signalsList.innerHTML = '<div class="signal">No signals yet.</div>';
    currentSignalBox.innerHTML = '<div class="signal">No current signal.</div>';
    return;
  }
  // latest as current
  const latest = signals[0];
  const latestDirClass = latest.direction?.toLowerCase() === 'long' ? 'long' : 'short';
  const latestDetails = [
    `Entry: ${latest.entry_price ?? latest.level}`,
    `SL: ${latest.sl_active ?? latest.sl_initial ?? '—'}`,
    `T1: ${latest.target1 ?? '—'}`,
    `RR: ${latest.rr ?? '—'}`,
    `Trail: ${latest.trail_basis || '—'}`,
    `Hit T1: ${latest.hit_target1 ? 'yes' : 'no'}`,
  ].join(' | ');
  currentSignalBox.innerHTML = `
    <div class="signal">
      <div class="meta">
        <span class="type">Current: ${latest.direction} · ${latest.signal_type}</span>
        <span class="ts">${formatTs(latest.ts)}</span>
        <span class="ts">${latestDetails}</span>
        <span class="ts">Status: ${latest.status || 'open'}${latest.reason ? ' — ' + latest.reason : ''}</span>
        <span class="ts">Details: ${JSON.stringify(latest.details || {})}</span>
      </div>
      <span class="dir ${latestDirClass}">${latest.direction}</span>
    </div>
  `;

  signals.forEach(s => {
    const div = document.createElement('div');
    div.className = 'signal';
    const dirClass = s.direction?.toLowerCase() === 'long' ? 'long' : 'short';
    const detailLine = [
      `Entry: ${s.entry_price ?? s.level}`,
      `SL: ${s.sl_active ?? s.sl_initial ?? '—'}`,
      `T1: ${s.target1 ?? '—'}`,
      `RR: ${s.rr ?? '—'}`,
      `Trail: ${s.trail_basis || '—'}`,
      `Hit T1: ${s.hit_target1 ? 'yes' : 'no'}`,
    ].join(' | ');
    div.innerHTML = `
      <div class="meta">
        <span class="type">${s.direction} · ${s.signal_type}</span>
        <span class="ts">${formatTs(s.ts)}</span>
        <span class="ts">${detailLine}</span>
        <span class="ts">Status: ${s.status || 'open'}${s.reason ? ' — ' + s.reason : ''}</span>
        <span class="ts">Details: ${JSON.stringify(s.details || {})}</span>
      </div>
      <span class="dir ${dirClass}">${s.direction}</span>
    `;
    signalsList.appendChild(div);
  });
}

function renderChart(data) {
  const labels = data.map(d => formatTs(d.ts));
  const closes = data.map(d => d.close);
  if (chart) chart.destroy();
  chart = new Chart(ohlcCanvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: `${currentTf} close`,
        data: closes,
        borderColor: '#67e8f9',
        backgroundColor: 'rgba(103, 232, 249, 0.2)',
        tension: 0.15,
        fill: true,
        pointRadius: 0,
      }],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { x: { ticks: { color: '#aab3d0' } }, y: { ticks: { color: '#aab3d0' } } },
    }
  });
}

async function loadOhlc() {
  const data = await fetchJSON(`/api/ohlc/${currentTf}`);
  renderChart(data);
}

async function loadSignals() {
  const data = await fetchJSON('/api/signals');
  renderSignals(data);
}

async function checkHealth() {
  const data = await fetchJSON('/api/health');
  healthBadge.textContent = `Health: ${data.status}`;
  healthBadge.style.background = data.status === 'ok'
    ? 'rgba(16,185,129,0.18)' : 'rgba(248,113,113,0.18)';
}

async function loadClosedSetting() {
  try {
    const res = await fetchJSON('/api/settings/closed_candle_trailing');
    closedToggle.checked = (res.value || 'false') === 'true';
  } catch (e) {
    console.warn('Failed to load setting', e);
  }
}

async function saveClosedSetting(value) {
  try {
    await fetch('/api/settings/closed_candle_trailing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: value ? 'true' : 'false' }),
    });
  } catch (e) {
    console.warn('Failed to save setting', e);
  }
}

refreshBtn.addEventListener('click', loadSignals);
tfButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    tfButtons.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentTf = btn.dataset.tf;
    loadOhlc();
  });
});

closedToggle.addEventListener('change', (e) => {
  saveClosedSetting(e.target.checked);
});

(async function init() {
  modeBadge.textContent = `Mode: paper`;
  await Promise.all([loadOhlc(), loadSignals(), checkHealth(), loadClosedSetting()]);
  setInterval(loadSignals, 15000);
  setInterval(loadOhlc, 20000);
  setInterval(checkHealth, 30000);
})();
