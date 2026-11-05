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

function lyricsMode() {
  return store.chordLyricsMode || activeProject()?.chords?.ui?.lyrics_mode || 'chords_only';
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

function buildMeasures(data) {
  const chords = data.chords || [];
  const words = data.words || [];
  const beats = data.beats || [];
  const downbeats = beats.filter(b => b.downbeat).map(b => b.t);
  const boundaries = downbeats.length ? downbeats : beats.map(b => b.t);
  if (!boundaries.length) {
    return [{bar: 1, start: 0, end: Infinity, chords, words}];
  }
  const measures = [];
  for (let i = 0; i < boundaries.length; i++) {
    const start = boundaries[i];
    const end = i + 1 < boundaries.length ? boundaries[i + 1] : Infinity;
    measures.push({
      bar: i + 1,
      start,
      end,
      // Show a chord in the bar where it *starts* (holds span visually via merge).
      chords: chords.filter(c => c.start >= start - 0.02 && c.start < end),
      words: words.filter(w => w.start < end && w.end > start),
    });
  }
  // Trailing chords after last downbeat already included via Infinity end.
  // Chord-only gaps before first lyric: keep measures even with empty words.
  return measures;
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
  const lyrics = el('label', 'check-row chord-lyrics-opt');
  const check = el('input');
  check.type = 'checkbox';
  check.id = 'chord-lyrics-check';
  check.checked = !!store.chordWantLyrics;
  check.onchange = () => { store.chordWantLyrics = check.checked; };
  lyrics.append(check, document.createTextNode(' ' + t('chords.alsoLyrics')));
  box.append(lyrics);
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
      lyrics: !!store.chordWantLyrics,
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
  const lyricSeg = el('div', 'seg');
  const hasWords = (data.words || []).length > 0;
  for (const [id, key] of [['with_lyrics', 'chords.withLyrics'], ['chords_only', 'chords.chordsOnly']]) {
    const btn = el('button', lyricsMode() === id ? 'on' : '', t(key));
    btn.type = 'button';
    btn.disabled = id === 'with_lyrics' && !hasWords;
    btn.onclick = async () => {
      store.chordLyricsMode = id;
      try {
        await api(`${projectUrl(activeProject().id)}/chords`, {
          root: store.state.root, ui: {lyrics_mode: id},
        });
      } catch (_) { /* ignore */ }
      renderChordSheet();
    };
    lyricSeg.append(btn);
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
  bar.append(simpleSeg, lyricSeg, grow, follow, again, exp);
  host.append(bar);
}

function renderSheet(host, data) {
  const sheet = el('div', 'chord-sheet');
  const showLyrics = lyricsMode() === 'with_lyrics' && (data.words || []).length;
  const measures = buildMeasures(data);
  const allChords = data.chords || [];
  measures.forEach((measure) => {
    const row = el('div', 'chord-measure');
    row.dataset.start = String(measure.start);
    row.append(el('div', 'chord-bar-no', measure.bar === 1 || Number.isFinite(measure.end) ? String(measure.bar) : '—'));
    const body = el('div');
    const chordRow = el('div', 'chord-row');
    for (const chord of measure.chords) {
      const index = allChords.indexOf(chord);
      const label = chordLabel(chord, mode());
      if (label === 'N' && !chord.override) continue;
      const conf = Number(chord.confidence || 0);
      const span = el('button', 'chord-chip' + (conf < 0.55 ? ' low' : ''), label);
      span.type = 'button';
      span.dataset.index = String(index);
      span.dataset.start = String(chord.start);
      span.dataset.end = String(chord.end);
      span.onclick = event => {
        event.stopPropagation();
        showPopover(span, chord, index);
      };
      chordRow.append(span);
    }
    if (!chordRow.childNodes.length) {
      chordRow.append(el('span', 'chord-chip low', '—'));
    }
    body.append(chordRow);
    if (showLyrics) {
      const lyric = el('div', 'chord-lyrics');
      if (measure.words.length) {
        for (const word of measure.words) {
          const w = el('span', 'chord-word', word.text + ' ');
          w.dataset.start = String(word.start);
          w.dataset.end = String(word.end);
          lyric.append(w);
        }
      } else {
        lyric.append(el('span', 'chord-instrumental', t('chords.instrumental')));
      }
      body.append(lyric);
    } else if (!measure.chords.length) {
      body.append(el('div', 'chord-lyrics', el('span', 'chord-instrumental', t('chords.instrumental'))));
    }
    row.append(body);
    sheet.append(row);
  });
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
    const warn = el('div', 'alert chord-stale', t('chords.stale'));
    host.append(warn);
  }

  if (!data || !Array.isArray(data.chords)) {
    if (meta) {
      /* empty */
    }
    renderEmpty(host, project);
    return;
  }

  if (meta) {
    if (data.key?.name) meta.append(el('span', 'chip key', t('chords.key', {key: data.key.name})));
    const bpm = estimateBpm(data.beats);
    if (bpm) meta.append(el('span', 'chip', t('chords.bpm', {bpm})));
    meta.append(el('span', 'chip', t('chords.analyzed')));
    if (data.source?.lyrics_error) {
      meta.append(el('span', 'chip', t('chords.lyricsFailed')));
      host.append(el('div', 'alert chord-stale',
        t('chords.lyricsFailedDetail', {error: data.source.lyrics_error})));
    } else if ((data.words || []).length && lyricsMode() !== 'with_lyrics') {
      // Words exist — default the view to chords+lyrics once.
      store.chordLyricsMode = 'with_lyrics';
    }
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
  let activeChip = null;
  sheet.querySelectorAll('.chord-chip').forEach(chip => {
    const start = Number(chip.dataset.start);
    const end = Number(chip.dataset.end);
    const on = Number.isFinite(start) && Number.isFinite(end) && position >= start && position < end;
    chip.classList.toggle('active', on);
    if (on) activeChip = chip;
  });
  sheet.querySelectorAll('.chord-word').forEach(word => {
    const start = Number(word.dataset.start);
    const end = Number(word.dataset.end);
    word.classList.toggle('on', position >= start && position < end);
  });
  if (followPlayhead && activeChip && store.songStudioTab === 'chords') {
    const measure = activeChip.closest('.chord-measure');
    measure?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
}

export function initChordSheet() {
  store.songStudioTab = store.songStudioTab || 'mixer';
  store.chordMode = store.chordMode || null;
  store.chordLyricsMode = store.chordLyricsMode || null;
  store.chordWantLyrics = !!store.chordWantLyrics;
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
    if (openPopover && !openPopover.contains(event.target) && !event.target.closest?.('.chord-chip')) {
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
  // Mixer visibility is still owned by renderSong; only hide when chords tab is active.
  if (mixer && showChords) mixer.hidden = true;
}
