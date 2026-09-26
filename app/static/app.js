/* НОМЕРОК 3D — логика интерфейса. Без внешних библиотек: приложение работает офлайн. */
'use strict';

const $ = (s) => document.querySelector(s);
const api = async (url, body) => {
  const r = await fetch(url, body === undefined
    ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  let data; try { data = await r.json(); } catch { data = {ok:false, error:'Сервер не ответил'}; }
  if (!r.ok && data.error === undefined) data.error = 'Ошибка ' + r.status;
  return data;
};

const ENV = {letters:[], digits:[], outdir:'', desktop:false};
const spec = {number:'Р433ЕК', region:'126', back_text:'Changan', mount:'ear',
              hole_d:6, length:60, base_h:2.4, text_h:0.6, back_depth:0.4, layer_h:0.2};
let side = 'front', lastSvg = {front:'', back:''}, queue = [], lastStats = null;

/* ── колесо выбора символа ────────────────────────────────────────────── */
function makeWheel(items, value, onChange) {
  const el = document.createElement('div');
  el.className = 'wheel';
  el.tabIndex = 0;
  el.innerHTML = items.map(c => `<div class="wheel-item">${c === '' ? '–' : c}</div>`).join('');
  const kids = [...el.children];
  let idx = Math.max(0, items.indexOf(value));
  let itemH = 0, timer = null, silent = false;

  const mark = () => kids.forEach((k, i) => k.classList.toggle('sel', i === idx));
  const layout = () => {
    itemH = kids[0].offsetHeight || 42;
    el.style.paddingTop = el.style.paddingBottom = ((el.clientHeight - itemH) / 2) + 'px';
    silent = true; el.scrollTop = idx * itemH;
    requestAnimationFrame(() => { silent = false; });
    mark();
  };
  const settle = () => {
    const i = Math.min(items.length - 1, Math.max(0, Math.round(el.scrollTop / itemH)));
    if (i !== idx) { idx = i; mark(); onChange(items[idx]); }
  };
  el.addEventListener('scroll', () => {
    if (silent || !itemH) return;
    const i = Math.min(items.length - 1, Math.max(0, Math.round(el.scrollTop / itemH)));
    if (i !== idx) { idx = i; mark(); }
    clearTimeout(timer); timer = setTimeout(settle, 90);
  }, {passive:true});
  el.addEventListener('keydown', (e) => {
    const step = e.key === 'ArrowUp' ? -1 : e.key === 'ArrowDown' ? 1 : 0;
    if (step) { e.preventDefault(); set(items[Math.min(items.length-1, Math.max(0, idx+step))]); onChange(items[idx]); return; }
    const ch = (e.key || '').toUpperCase();
    if (ch.length === 1 && items.includes(ch)) { e.preventDefault(); set(ch); onChange(ch); }
  });
  /* перетаскивание мышкой */
  let drag = null;
  el.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    drag = {y:e.clientY, top:el.scrollTop}; el.setPointerCapture(e.pointerId); el.style.cursor = 'grabbing';
  });
  el.addEventListener('pointermove', (e) => { if (drag) el.scrollTop = drag.top - (e.clientY - drag.y); });
  const stop = () => { drag = null; el.style.cursor = ''; };
  el.addEventListener('pointerup', stop); el.addEventListener('pointercancel', stop);

  function set(v) {
    const i = items.indexOf(v);
    if (i < 0) return;
    idx = i; mark();
    if (itemH) el.scrollTo({top: idx * itemH, behavior:'smooth'});
  }
  setTimeout(layout, 0);
  window.addEventListener('resize', layout);
  return {el, set, get value(){ return items[idx]; }};
}

/* ── сборка номера из колёс ───────────────────────────────────────────── */
let mainWheels = [], regWheels = [];
function buildWheels() {
  const L = ENV.letters, D = ENV.digits;
  const host = $('#wheelsMain'), rhost = $('#wheelsRegion');
  host.innerHTML = ''; rhost.innerHTML = '';
  mainWheels = []; regWheels = [];

  const mask = 'LDDDLL';
  [...mask].forEach((kind, i) => {
    const items = kind === 'L' ? L : D;
    const cur = (spec.number[i] || items[0]);
    const w = makeWheel(items, items.includes(cur) ? cur : items[0], () => { syncNumber(); refresh(); });
    host.appendChild(w.el); mainWheels.push(w);
  });

  const reg = spec.region.padStart(3, ' ').split('').map(c => c === ' ' ? '' : c);
  [['', ...D], D, D].forEach((items, i) => {
    const w = makeWheel(items, items.includes(reg[i]) ? reg[i] : items[0],
                        () => { syncNumber(); refresh(); });
    rhost.appendChild(w.el); regWheels.push(w);
  });
}
function syncNumber() {
  spec.number = mainWheels.map(w => w.value).join('');
  spec.region = regWheels.map(w => w.value).join('');
}
function applyPlateString(str) {
  const s = (str || '').toUpperCase().replace(/[^A-ZА-Я0-9]/g, '');
  const lat = 'ABEKMHOPCTYX', cyr = 'АВЕКМНОРСТУХ';
  const norm = [...s].map(c => { const i = lat.indexOf(c); return i >= 0 ? cyr[i] : c; }).join('');
  const m = norm.match(/^([АВЕКМНОРСТУХ])(\d)(\d)(\d)([АВЕКМНОРСТУХ])([АВЕКМНОРСТУХ])(\d{0,3})$/);
  if (!m) { toast('Не похоже на номер. Формат: Р433ЕК126', 'err'); return false; }
  [1,2,3,4,5,6].forEach((k, i) => mainWheels[i].set(m[k]));
  const reg = (m[7] || spec.region).padStart(3, ' ').split('').map(c => c === ' ' ? '' : c);
  regWheels.forEach((w, i) => w.set(reg[i] ?? ''));
  setTimeout(() => { syncNumber(); refresh(); }, 60);
  return true;
}

/* ── превью ───────────────────────────────────────────────────────────── */
let pending = null, inflight = false;
function refresh() { clearTimeout(pending); pending = setTimeout(doRefresh, 130); }

async function doRefresh() {
  if (inflight) { refresh(); return; }
  inflight = true;
  const res = await api('/api/preview', spec);
  inflight = false;
  if (!res.ok) { toast(res.error || 'Не получилось построить модель', 'err'); return; }

  Object.assign(spec, res.spec);
  lastSvg = res.svg; lastStats = res.stats;
  $('#svgHost').innerHTML = lastSvg[side];
  drawStats(res.stats);
  syncInputs(res.stats);
}

function drawStats(s) {
  $('#sizeCaption').textContent =
    `${s.size_x} × ${s.size_y} × ${s.size_z} мм   ·   цифры ${s.digit_h} мм` +
    (s.back_cap ? `   ·   надпись на обороте ${s.back_cap} мм` : '');

  const cards = [
    ['Габарит', `${s.size_x}<small>×${s.size_y} мм</small>`],
    ['Пластик', `${s.grams}<small>г</small>`],
    ['Печать', `~${s.minutes}<small>мин</small>`],
    ['Пауза на слое', `${s.pause_layer}<small>Z ${s.pause_z} мм</small>`, 'accent'],
  ];
  $('#statGrid').innerHTML = cards.map(([k, v, cls]) =>
    `<div class="stat ${cls || ''}"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');

  $('#warnList').innerHTML = (s.warnings || []).map(w =>
    `<div class="warn"><b>!</b><span>${w}</span></div>`).join('');
}

function syncInputs(s) {
  const holeWrap = $('#holeWrap');
  holeWrap.classList.toggle('hidden', spec.mount === 'none');
  const r = $('#holeRange'), n = $('#holeD');
  r.min = n.min = s.hole_min; r.max = n.max = s.hole_max;
  r.value = spec.hole_d; n.value = spec.hole_d;
  r.style.setProperty('--p', ((spec.hole_d - s.hole_min) / (s.hole_max - s.hole_min) * 100) + '%');
  document.querySelectorAll('.chip[data-hole]').forEach(c =>
    c.classList.toggle('on', Math.abs(+c.dataset.hole - spec.hole_d) < 0.01));

  $('#holeHint').innerHTML = spec.mount === 'ear'
    ? `Ушко вынесено за пластину, номер остаётся целым. Под карабин толщиной <b>Х мм</b>
       бери отверстие <b>Х + 1 мм</b>: для 5 мм — это 6 мм. Максимум ${s.hole_max} мм.`
    : `Отверстие прямо в пластине — компактно, но съедает место у номера.
       Максимум для длины ${spec.length} мм — <b>${s.hole_max} мм</b>
       (иначе до края останется меньше 2 мм пластика).`;

  const lr = $('#lengthRange');
  lr.value = spec.length; $('#lengthD').value = spec.length;
  lr.style.setProperty('--p', ((spec.length - lr.min) / (lr.max - lr.min) * 100) + '%');

  document.querySelectorAll('#mountSeg .seg').forEach(b =>
    b.classList.toggle('on', b.dataset.v === spec.mount));
  if (document.activeElement !== $('#backText')) $('#backText').value = spec.back_text;
  $('#layerH').value = spec.layer_h; $('#baseH').value = spec.base_h;
  $('#textH').value = spec.text_h; $('#backDepth').value = spec.back_depth;
}

/* ── очередь и генерация ──────────────────────────────────────────────── */
function drawQueue() {
  $('#queue').innerHTML = queue.map((it, i) => `
    <div class="q-item">
      <span class="q-plate">${it.number} ${it.region}</span>
      <span class="q-meta">${it.back_text ? '«' + it.back_text + '»' : 'без надписи'} ·
        ${it.mount === 'ear' ? 'ушко' : it.mount === 'hole' ? 'отверстие' : 'без крепления'}
        ${it.mount !== 'none' ? 'Ø' + it.hole_d + ' мм' : ''} · ${it.length} мм</span>
      <button class="icon-btn" data-del="${i}" title="Убрать">✕</button>
    </div>`).join('');
  $('#makeBtn').lastChild.textContent = queue.length
    ? ` Создать 3MF (${queue.length + 1})` : ' Создать 3MF';
}

async function generate() {
  const items = [...queue, {...spec}];
  busy(true, items.length > 1 ? `Считаю ${items.length} брелока…` : 'Строю модель…');
  const res = await api('/api/generate', {items, outdir: $('#outPath').textContent, stl: true});
  busy(false);
  if (!res.ok) { toast(res.error, 'err'); return; }

  const names = res.files.map(f => f.split(/[\\/]/).pop());
  const uniq = [...new Set(names)];
  $('#result').classList.remove('hidden');
  $('#result').innerHTML = `
    <h3>Готово — ${res.files.length} файл(ов), ${res.grams} г пластика, ~${res.minutes} мин печати</h3>
    <div class="files"><code>${res.folder}</code><br>${uniq.slice(0, 14).join('<br>')}
      ${uniq.length > 14 ? '<br>…' : ''}</div>
    <div class="actions">
      ${ENV.desktop ? '<button class="btn" id="openBtn">Открыть папку</button>' : ''}
      <a class="btn" href="/api/zip" download>Скачать ZIP</a>
    </div>`;
  const ob = $('#openBtn');
  if (ob) ob.onclick = () => api('/api/open-folder', {path: res.folder});
  toast('Файлы готовы' + (res.combined ? ' · общий стол собран' : ''), 'ok');
}

/* ── мелочи интерфейса ────────────────────────────────────────────────── */
function toast(text, kind) {
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = text;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}
function busy(on, text) {
  $('#busyText').textContent = text || 'Считаю…';
  $('#busy').classList.toggle('hidden', !on);
}

/* ── старт ────────────────────────────────────────────────────────────── */
(async function init() {
  const env = await api('/api/env');
  Object.assign(ENV, env);
  $('#outPath').textContent = env.outdir;
  $('#modeBadge').textContent = env.desktop ? 'приложение' : 'браузер';
  document.title = `${env.app} — конструктор брелоков`;
  buildWheels();
  $('#backText').value = spec.back_text;

  $('#backText').addEventListener('input', e => { spec.back_text = e.target.value; refresh(); });

  document.querySelectorAll('#mountSeg .seg').forEach(b => b.onclick = () => {
    spec.mount = b.dataset.v;
    if (spec.mount === 'ear' && spec.hole_d < 5) spec.hole_d = 6;
    refresh();
  });
  const holeSet = v => { spec.hole_d = Math.round(v * 10) / 10; refresh(); };
  $('#holeRange').addEventListener('input', e => holeSet(+e.target.value));
  $('#holeD').addEventListener('change', e => holeSet(+e.target.value));
  document.querySelectorAll('.chip[data-hole]').forEach(c => c.onclick = () => {
    if (spec.mount === 'none') spec.mount = 'ear';
    holeSet(+c.dataset.hole);
  });

  const lenSet = v => { spec.length = Math.round(v); refresh(); };
  $('#lengthRange').addEventListener('input', e => lenSet(+e.target.value));
  $('#lengthD').addEventListener('change', e => lenSet(+e.target.value));

  [['layerH','layer_h'], ['baseH','base_h'], ['textH','text_h'], ['backDepth','back_depth']]
    .forEach(([id, key]) => $('#' + id).addEventListener('change', e => {
      spec[key] = +e.target.value; refresh();
    }));

  document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    side = t.dataset.side;
    $('#svgHost').innerHTML = lastSvg[side] || '';
  });

  $('#pasteBtn').onclick = () => {
    $('#pasteRow').classList.toggle('hidden');
    if (!$('#pasteRow').classList.contains('hidden')) $('#pasteInput').focus();
  };
  $('#pasteApply').onclick = () => {
    if (applyPlateString($('#pasteInput').value)) $('#pasteRow').classList.add('hidden');
  };
  $('#pasteInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('#pasteApply').click(); });

  $('#addBtn').onclick = () => {
    queue.push({...spec});
    drawQueue();
    toast(`${spec.number} ${spec.region} — в списке`, 'ok');
  };
  $('#queue').addEventListener('click', e => {
    const i = e.target.dataset?.del;
    if (i !== undefined) { queue.splice(+i, 1); drawQueue(); }
  });
  $('#makeBtn').onclick = generate;

  $('#browseBtn').onclick = async () => {
    if (!ENV.desktop) {
      const p = prompt('Папка для файлов:', $('#outPath').textContent);
      if (p) $('#outPath').textContent = p.trim();
      return;
    }
    const r = await api('/api/pick-folder', {});
    if (r.ok && r.path) $('#outPath').textContent = r.path;
  };

  $('#helpBtn').onclick = () => $('#helpModal').classList.remove('hidden');
  $('#helpClose').onclick = () => $('#helpModal').classList.add('hidden');
  $('#helpModal').onclick = e => { if (e.target.id === 'helpModal') $('#helpModal').classList.add('hidden'); };
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#helpModal').classList.add('hidden'); });

  drawQueue();
  refresh();
})();
