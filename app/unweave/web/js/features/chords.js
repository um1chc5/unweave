import {$, el, api, notice, projectUrl, activeProject} from '../core/core.js';
import {store, nav} from '../core/store.js';
import {t} from '../core/i18n.js';

let followPlayhead = true;
let openPopover = null;

export function chordLabel(chord, mode) {
  if (chord?.override) return chord.override;
  if (mode === 'extended') return chord?.extended || chord?.simple || 'N';
  return chord?.simple || chord?.label || 'N';
}

function mode() {
  return store.chordMode || activeProject()?.chords?.ui?.mode || 'simple';
}

export function setSongStudioTab(tab) {
  store.songStudioTab = tab === 'chords' ? 'chords' : 'mixer';
  const mixerTab = $('song-tab-mixer');
  const chordsTab = $('song-tab-chords');
  if (mixerTab) mixerTab.classList.toggle('on', store.songStudioTab === 'mixer');
  if (chordsTab) chordsTab.classList.toggle('on', store.songStudioTab === 'chords');
  if (mixerTab) mixerTab.setAttribute('aria-selected', String(store.songStudioTab === 'mixer'));
  if (chordsTab) chordsTab.setAttribute('aria-selected', String(store.songStudioTab === 'chords'));
}

function beatsPerBar(data) {
  const n = Number(data?.meter?.beats_per_bar);
  return n === 3 || n === 6 ? n : 4;
}

function closePopover() {
  openPopover?.remove();
  openPopover = null;
}

async function applyOverride(index, override) {
  const project = activeProject();
  if (!project) return;
  try {
    const data = await api(`${projectUrl(project.id)}/chords`, {
      root: store.state.root,
      overrides: [{index, override}],
    });
    project.chords = data;
    closePopover();
    renderChordSheet();
  } catch (error) {
    notice(error.message, 'error');
  }
}

function showPopover(anchor, chord, index) {
  closePopover();
  const pop = el('div', 'chord-popover');
  pop.append(el('strong', '', t('chords.replace')));
  const alts = el('div', 'chord-alts');
  const current = chordLabel(chord, mode());
  const options = [...new Set([...(chord.alternatives || []), current, chord.simple, chord.extended].filter(Boolean))];
  for (const label of options) {
    const btn = el('button', label === current ? 'pick' : '', label);
    btn.type = 'button';
    btn.onclick = () => applyOverride(index, label === chord.simple && !chord.override ? null : label);
    alts.append(btn);
  }
  pop.append(alts);
  const row = el('div', 'chord-popover-actions');
  const custom = el('button', 'btn btn-ghost btn-sm', t('chords.custom'));
  custom.type = 'button';
  custom.onclick = () => {
    const value = window.prompt(t('chords.customPrompt'), current);
    if (value == null) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    applyOverride(index, trimmed);
  };
  const clear = el('button', 'btn btn-ghost btn-sm', t('chords.clearOverride'));
  clear.type = 'button';
  clear.onclick = () => applyOverride(index, null);
  row.append(custom, clear);
  pop.append(row);
  anchor.style.position = 'relative';
  anchor.append(pop);
  openPopover = pop;
}

function renderEmpty(host, project) {
  host.replaceChildren();
  const box = el('div', 'chord-empty');
  box.append(el('h3', '', t('chords.emptyTitle')));
  box.append(el('p', '', t('chords.emptyBody')));
  const go = el('button', 'btn btn-primary', t('chords.analyze'));
  go.type = 'button';
  go.disabled = !project.stems?.length;
  go.onclick = () => startAnalyze();
  box.append(go);
  host.append(box);
}

async function startAnalyze() {
  const project = activeProject();
  if (!project) return;
  try {
    await api(`${projectUrl(project.id)}/analyze-chords`, {
      root: store.state.root,
      lyrics: false,
    });
    store.songStudioTab = 'chords';
    await nav.refresh();
  } catch (error) {
    notice(error.message, 'error');
  }
}

function renderToolbar(host, data) {
  const bar = el('div', 'chord-toolbar');
  const simpleSeg = el('div', 'seg');
  for (const [id, key] of [['simple', 'chords.simple'], ['extended', 'chords.extended']]) {
    const btn = el('button', mode() === id ? 'on' : '', t(key));
    btn.type = 'button';
    btn.onclick = async () => {
      store.chordMode = id;
      try {
        await api(`${projectUrl(activeProject().id)}/chords`, {
          root: store.state.root, ui: {mode: id},
        });
      } catch (_) { /* local toggle still useful */ }
      renderChordSheet();
    };
    simpleSeg.append(btn);
  }
  const grow = el('span', 'grow');
  const follow = el('button', 'btn btn-ghost btn-sm' + (followPlayhead ? ' on' : ''), t('chords.follow'));
  follow.type = 'button';
  follow.onclick = () => { followPlayhead = !followPlayhead; renderChordSheet(); };
  const again = el('button', 'btn btn-ghost btn-sm', t('chords.reanalyze'));
  again.type = 'button';
  again.onclick = () => startAnalyze();
  const exp = el('button', 'btn btn-ghost btn-sm', t('chords.export'));
  exp.type = 'button';
  exp.onclick = async () => {
    try {
      const result = await api(`${projectUrl(activeProject().id)}/export-chordpro`, {
        root: store.state.root, mode: mode(),
      });
      notice(t('chords.exported', {file: result.file || 'chords.chopro'}), 'info');
      await api(`${projectUrl(activeProject().id)}/reveal`, {root: store.state.root, target: 'exports'});
    } catch (error) {
      notice(error.message, 'error');
    }
  };
  bar.append(simpleSeg, grow, follow, again, exp);
  host.append(bar);
}

function chordAtTime(chords, t) {
  for (const chord of chords) {
    if (t + 1e-9 >= chord.start && t < chord.end - 1e-9) return chord;
  }
  // Hold last chord that started at/before t
  let best = null;
  for (const chord of chords) {
    if (chord.start <= t + 1e-9) best = chord;
  }
  return best;
}

function renderSheet(host, data) {
  // Chordify-style: compact wrapping bars, beat cells, label only on change.
  const sheet = el('div', 'chord-sheet chord-sheet-grid');
  const beats = data.beats || [];
  const chords = data.chords || [];
  const bpp = beatsPerBar(data);
  sheet.style.setProperty('--chord-bpp', String(bpp));

  if (!beats.length) {
    sheet.append(el('p', 'chord-instrumental', t('chords.emptyBody')));
    host.append(sheet);
    return;
  }

  const grid = el('div', 'chord-grid');
  grid.style.setProperty('--chord-bpp', String(bpp));
  let bar = null;
  let beatsInBar = 0;
  let lastShown = null;
  let barCount = 0;

  const flushBar = () => {
    if (bar) grid.append(bar);
    bar = null;
    beatsInBar = 0;
  };

  beats.forEach((beat, i) => {
    const startNew = beat.downbeat || !bar || beatsInBar >= bpp;
    if (startNew) {
      flushBar();
      bar = el('div', 'chord-bar');
      bar.style.setProperty('--chord-bpp', String(bpp));
      barCount += 1;
      bar.dataset.bar = String(barCount);
      beatsInBar = 0;
    }
    const nextT = beats[i + 1]?.t;
    const end = nextT != null ? nextT : beat.t + 0.5;
    const chord = chordAtTime(chords, beat.t);
    const index = chord ? chords.indexOf(chord) : -1;
    const raw = chord ? chordLabel(chord, mode()) : '';
    const label = raw && raw !== 'N' ? raw : '';
    const show = label && label !== lastShown;
    if (label) lastShown = label;

    const cell = el('button', 'chord-beat' + (show ? '' : ' hold') + (chord && Number(chord.confidence || 1) < 0.55 ? ' low' : ''));
    cell.type = 'button';
    cell.textContent = show ? label : '';
    cell.dataset.start = String(beat.t);
    cell.dataset.end = String(end);
    if (index >= 0) cell.dataset.index = String(index);
    cell.title = label || lastShown || '';
    cell.onclick = event => {
      event.stopPropagation();
      if (index < 0 || !chord) return;
      showPopover(cell, chord, index);
    };
    bar.append(cell);
    beatsInBar += 1;
  });
  flushBar();
  sheet.append(grid);
  host.append(sheet);
}

export function renderChordSheet() {
  const host = $('chord-panel-body');
  const meta = $('chord-meta');
  const panel = $('chord-panel');
  if (!host || !panel) return;
  closePopover();
  const project = activeProject();
  if (!project) return;
  const data = project.chords;
  host.replaceChildren();
  if (meta) meta.replaceChildren();

  if (data?.stale) {
    host.append(el('div', 'alert chord-stale', t('chords.stale')));
  }

  if (!data || !Array.isArray(data.chords)) {
    renderEmpty(host, project);
    return;
  }

  if (meta) {
    if (data.key?.name) meta.append(el('span', 'chip key', t('chords.key', {key: data.key.name})));
    const meter = data.meter;
    if (meter?.numerator && meter?.denominator) {
      meta.append(el('span', 'chip', t('chords.meter', {
        meter: `${meter.numerator}/${meter.denominator}`,
      })));
    }
    const bpm = estimateBpm(data.beats);
    if (bpm) meta.append(el('span', 'chip', t('chords.bpm', {bpm})));
    meta.append(el('span', 'chip', t('chords.analyzed')));
  }

  renderToolbar(host, data);
  renderSheet(host, data);
  syncChordPlayhead(Number($('seek')?.value) || 0);
}

function estimateBpm(beats) {
  if (!beats || beats.length < 2) return null;
  const intervals = [];
  for (let i = 1; i < Math.min(beats.length, 32); i++) {
    intervals.push(beats[i].t - beats[i - 1].t);
  }
  const avg = intervals.reduce((a, b) => a + b, 0) / intervals.length;
  if (!(avg > 0.2 && avg < 2)) return null;
  return Math.round(60 / avg);
}

export function syncChordPlayhead(position) {
  const sheet = document.querySelector('.chord-sheet');
  if (!sheet) return;
  let active = null;
  sheet.querySelectorAll('.chord-beat').forEach(cell => {
    const start = Number(cell.dataset.start);
    const end = Number(cell.dataset.end);
    const on = Number.isFinite(start) && Number.isFinite(end) && position >= start && position < end;
    cell.classList.toggle('active', on);
    if (on) active = cell;
  });
  if (followPlayhead && active && store.songStudioTab === 'chords') {
    active.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
}

export function initChordSheet() {
  store.songStudioTab = store.songStudioTab || 'mixer';
  store.chordMode = store.chordMode || null;
  $('song-tab-mixer')?.addEventListener('click', () => {
    setSongStudioTab('mixer');
    renderSongStudioVisibility();
  });
  $('song-tab-chords')?.addEventListener('click', () => {
    setSongStudioTab('chords');
    renderSongStudioVisibility();
    renderChordSheet();
  });
  document.addEventListener('click', event => {
    if (openPopover && !openPopover.contains(event.target) && !event.target.closest?.('.chord-beat')) {
      closePopover();
    }
  });
}

export function renderSongStudioVisibility() {
  const project = activeProject();
  const hasStems = !!project?.stems?.length;
  const tabs = $('song-studio-tabs');
  if (tabs) tabs.hidden = !hasStems;
  setSongStudioTab(store.songStudioTab || 'mixer');
  const showChords = hasStems && store.songStudioTab === 'chords';
  const mixer = $('mixer');
  const chordPanel = $('chord-panel');
  if (chordPanel) chordPanel.hidden = !showChords;
  if (mixer && showChords) mixer.hidden = true;
}
