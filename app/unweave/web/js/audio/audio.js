import {colors, ORIGINAL_COLOR, STEMS, ORIGINAL_FILE} from '../core/constants.js';
import {$, el, token, time, notice, jobPending, projectJob, projectUrl, stemLabel} from '../core/core.js';
import {store} from '../core/store.js';
import {StemPlayer} from './player.js';
import {openSplitDialog} from './split.js';
import {formatClock} from './trim.js';
import {t, onLangChange} from '../core/i18n.js';
import {syncChordPlayhead} from '../features/chords.js';
export const player = new StemPlayer();
player.onError = error => notice(error.message);
export function clearAudio() {
  store.controller?.abort(); store.loading++; player.clear();
  store.songTrimEditor?.stopPreview();
  store.newTrimEditor?.stopPreview();
  store.splitEditor?.stopPreview();
  $('tracks').replaceChildren();
  $('audio-loading').hidden = true;
  for (const id of ['play', 'restart', 'seek', 'reset-mix', 'playback-speed', 'reset-speed']) $(id).disabled = true;
  $('seek').value = 0; $('seek').max = 1; $('elapsed').textContent = '0:00'; $('duration').textContent = '0:00';
}
export function buildStemPills(items) {
  const pills = new Map();
  $('audio-loading-stems').replaceChildren(...items.map(item => {
    const pill = el('span', 'stem-pill', item.name);
    pills.set(item.file, pill);
    return pill;
  }));
  return pills;
}
export function setLoadingProgress(label, fraction) {
  $('audio-loading-label').textContent = label;
  const pct = Math.round(Math.max(0, Math.min(1, fraction)) * 100);
  $('audio-loading-pct').textContent = `${pct}%`;
  $('audio-loading-bar').value = pct;
}
// Reads the response as a stream instead of one arrayBuffer() call so loading can show
// real byte progress (stems run tens of MB each) rather than just "file i of n".
export async function fetchWithProgress(url, signal, onBytes) {
  const response = await fetch(url, {headers: {'X-Unweave-Token': token}, signal});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const total = Number(response.headers.get('Content-Length')) || 0;
  if (!response.body) { onBytes(total, total); return new Uint8Array(await response.arrayBuffer()).buffer; }
  const reader = response.body.getReader();
  const chunks = []; let received = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    chunks.push(value); received += value.length;
    onBytes(received, total);
  }
  const buffer = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.length; }
  return buffer.buffer;
}
export function buildLoadItems(project, root) {
  const items = [];
  // Original plays alongside the separated stems (muted by default, for A/B — see
  // loadAudio) once they exist, and is the only track when they don't yet, so the song
  // can be previewed before choosing what to separate.
  if (project.original) {
    items.push({name: t('track.original'), file: ORIGINAL_FILE, offset: 0, bytes: project.original_bytes,
      url: `${projectUrl(project.id)}/original?${new URLSearchParams({root})}`});
  }
  for (const item of project.stems) {
    items.push({...item, name: stemLabel(item.file, item.name), url: `${projectUrl(project.id)}/audio/${encodeURIComponent(item.file)}?${new URLSearchParams({root})}`});
  }
  return items;
}
export async function loadAudio(project) {
  clearAudio();
  const epoch = store.loading;
  const root = store.state.root;
  store.controller = new AbortController();
  const signal = store.controller.signal;
  const items = buildLoadItems(project, root);
  if (!items.length) { $('audio-status').textContent = ''; return; }
  const tracks = [];
  const pills = buildStemPills(items);
  // Every file's share of the bar is fixed up front from the sizes the project already
  // lists. Summing only the sizes seen so far made the bar hit 100% after the first file,
  // then fall back each time the next file's size became known.
  const known = items.map(item => Number(item.bytes) || 0);
  const sizes = known.filter(Boolean);
  const fallback = sizes.length ? sizes.reduce((a, b) => a + b, 0) / sizes.length : 1;
  const weights = known.map(size => size || fallback);
  const totalWeight = weights.reduce((a, b) => a + b, 0);
  let shown = 0;
  const report = (i, part) => {
    const done = weights.slice(0, i).reduce((a, b) => a + b, 0);
    shown = Math.max(shown, (done + weights[i] * Math.max(0, Math.min(1, part))) / totalWeight);
    setLoadingProgress(t('loading.itemOf', {name: items[i].name, index: i + 1, total: items.length}), shown);
  };
  $('audio-loading').hidden = false;
  setLoadingProgress(t('loading.item', {name: items[0].name}), 0);
  try {
    for (const [i, item] of items.entries()) {
      pills.get(item.file)?.classList.add('loading');
      report(i, 0);
      const encoded = await fetchWithProgress(item.url, signal, (got, total) => report(i, total ? got / total : 0))
        .catch(error => { throw new Error(t('audio.loadFailed', {name: item.name, error: error.message})); });
      if (epoch !== store.loading) return;
      const buffer = await player.ensureContext().decodeAudioData(encoded);
      if (epoch !== store.loading) return;
      pills.get(item.file)?.classList.replace('loading', 'done');
      tracks.push({...item, buffer});
    }
    setLoadingProgress(t('loading.loaded'), 1);
    for (const track of tracks) track.projectOffset = Number(track.offset) || 0;
    const trim = project.separation?.trim;
    const original = tracks.find(track => track.file === ORIGINAL_FILE);
    let section = null;
    if (trim && original?.buffer) {
      const start = Number(trim.start);
      const end = Number(trim.end);
      if (Number.isFinite(start) && Number.isFinite(end) && end > start) {
        const clamped = {start: Math.max(0, start), end: Math.min(original.buffer.duration, end)};
        if (clamped.end - clamped.start > 0.001) section = clamped;
      }
    }
    if (section) {
      for (const track of tracks) {
        if (track.file === ORIGINAL_FILE) {
          track.offset = 0;
          track.bufferOffset = section.start;
        } else {
          track.offset = Math.max(0, (Number(track.offset) || 0) - section.start);
          track.bufferOffset = 0;
        }
      }
    }
    player.setTracks(tracks, section ? section.end - section.start : null);
    // Hear the separated result by default when there's one to hear; Original stays
    // available to solo for comparison. With no stems yet, Original is the only track
    // and plays normally.
    const hasStems = project.stems.length > 0;
    const originalTrack = player.tracks.find(t => t.file === ORIGINAL_FILE);
    if (originalTrack && hasStems) originalTrack.muted = true;
    $('seek').max = player.duration;
    $('duration').textContent = time(player.duration);
    for (const id of ['play', 'restart', 'seek', 'reset-mix', 'playback-speed', 'reset-speed']) $(id).disabled = false;
    const stemTracks = tracks.filter(track => track.file !== ORIGINAL_FILE);
    const starts = stemTracks.map(track => track.offset || 0);
    const ends = stemTracks.map(track => (track.offset || 0) + track.buffer.duration);
    const sectionStart = starts.length ? Math.min(...starts) : 0;
    const sectionEnd = ends.length ? Math.max(...ends) : 0;
    if (section) {
      $('audio-status').textContent = t('audio.playingSectionFromStart', {end: formatClock(section.end - section.start, 1), length: (section.end - section.start).toFixed(1)});
    } else if (stemTracks.length && sectionStart > 0) {
      await player.seek(sectionStart);
      $('seek').value = sectionStart;
      $('elapsed').textContent = time(sectionStart);
      $('audio-status').textContent = t('audio.playingSectionRange', {start: formatClock(sectionStart, 1), end: formatClock(sectionEnd, 1), length: (sectionEnd - sectionStart).toFixed(1)});
    } else if (hasStems && Math.max(...ends) - Math.min(...ends) > 0.25) {
      $('audio-status').textContent = t('audio.differentDurations');
    } else {
      $('audio-status').textContent = '';
    }
    renderTracks();
    $('audio-loading').hidden = true;
  } catch (error) {
    if (epoch !== store.loading || error.name === 'AbortError') return;
    $('audio-loading').hidden = true;
    $('audio-status').replaceChildren(el('span', '', t('audio.couldNotLoad', {error: error.message})));
    const retry = el('button', 'text-button', t('audio.retry'));
    retry.onclick = () => loadAudio(project);
    $('audio-status').append(retry);
  }
}
export function drawWave(canvas, buffer, color, start = 0, length = null) {
  const width = Math.max(150, canvas.clientWidth);
  const scale = window.devicePixelRatio || 1;
  canvas.width = width * scale; canvas.height = 48 * scale;
  const context = canvas.getContext('2d'); context.scale(scale, scale);
  const data = buffer.getChannelData(0);
  const columns = Math.floor(width / 3);
  const from = Math.max(0, Math.floor(start * buffer.sampleRate));
  const to = Math.min(data.length, Math.floor((start + (length ?? buffer.duration)) * buffer.sampleRate));
  const span = Math.max(1, to - from);
  const stride = Math.max(1, Math.floor(span / columns));
  context.fillStyle = color;
  for (let i = 0; i < columns; i++) {
    let peak = 0;
    // Real samples, bounded work even on a full-length song.
    const skip = Math.max(1, Math.floor(stride / 64));
    const end = Math.min(from + (i + 1) * stride, to);
    for (let j = from + i * stride; j < end; j += skip) peak = Math.max(peak, Math.abs(data[j]));
    const height = Math.max(2, Math.min(46, peak * 60));
    context.fillRect(i * 3, (48 - height) / 2, 2, height);
  }
}
export function renderTracks() {
  $('tracks').replaceChildren();
  for (const [index, track] of player.tracks.entries()) {
    const isOriginal = track.file === ORIGINAL_FILE;
    const stemName = track.file.replace(/\.(flac|wav)$/i, '');  // FLAC since 1.2.1; older projects keep WAV
    const color = isOriginal ? ORIGINAL_COLOR : (STEMS.find(([value]) => value === stemName)?.[2] || colors[index % colors.length]);
    const row = el('div', isOriginal ? 'track track-original' : 'track'); row.style.setProperty('--track-color', color);
    const label = el('div'); label.append(el('div', 'track-name', track.name[0].toUpperCase() + track.name.slice(1)),
      el('div', 'track-detail', `${track.buffer.numberOfChannels === 1 ? 'MONO' : 'STEREO'} · ${time(track.buffer.duration)}`));
    if (!isOriginal && stemName === 'guitar') {
      const split = el('button', 'split-guitar');
      split.type = 'button'; split.title = t('audio.splitGuitarTitle');
      split.setAttribute('aria-label', t('audio.splitGuitarTitle'));
      split.dataset.splitGuitar = ''; split.disabled = jobPending(projectJob(store.selected));
      split.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12h5c4 0 3-7 7-7h4M9 12c4 0 3 7 7 7h4M17 2l3 3-3 3M17 16l3 3-3 3"/></svg>';
      const projectId = store.selected;
      split.onclick = () => { player.pause(); openSplitDialog(projectId, track); };
      label.querySelector('.track-name').append(split);
    }
    const wave = el('div', 'wave-wrap'); const canvas = el('canvas'); canvas.setAttribute('aria-hidden', 'true');
    wave.append(canvas, el('span', 'playhead'));
    wave.onclick = event => {
      if (!player.duration) return;
      const box = wave.getBoundingClientRect();
      player.seek(Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * player.duration).catch(e => notice(e.message, 'error'));
    };
    const controls = el('div', 'track-controls');
    for (const [key, text] of [['muted', 'M'], ['solo', 'S']]) {
      const button = el('button', '', text);
      button.setAttribute('aria-label', t(key === 'muted' ? 'audio.muteLabel' : 'audio.soloLabel', {name: track.name}));
      button.setAttribute('aria-pressed', String(track[key]));
      button.onclick = () => { track[key] = !track[key]; player.updateMix(); syncTrackControls(); };
      button.dataset.control = key; controls.append(button);
    }
    const range = el('input'); range.type = 'range'; range.min = 0; range.max = 1; range.step = .01; range.value = track.volume;
    range.setAttribute('aria-label', t('audio.volumeLabel', {name: track.name}));
    const value = el('span', 'volume-value', `${Math.round(track.volume * 100)}%`);
    range.oninput = () => { track.volume = Number(range.value); value.textContent = `${Math.round(track.volume * 100)}%`; player.updateMix(); };
    controls.append(range, value); row.append(label, wave, controls); $('tracks').append(row);
    drawWave(canvas, track.buffer, color, track.bufferOffset || 0,
      track.file === ORIGINAL_FILE ? player.duration : null);
  }
  syncTrackControls();
}
export function syncTrackControls() {
  [...$('tracks').children].forEach((row, i) => {
    const track = player.tracks[i];
    row.classList.toggle('muted', player.trackGain(track) === 0);
    row.querySelector('[data-control=muted]').setAttribute('aria-pressed', String(track.muted));
    row.querySelector('[data-control=solo]').setAttribute('aria-pressed', String(track.solo));
  });
}
onLangChange(() => {
  if (!player.tracks.length) return;
  for (const track of player.tracks) track.name = track.file === ORIGINAL_FILE ? t('track.original') : stemLabel(track.file, track.name);
  renderTracks();
});
export function animate() {
  const position = player.position();
  $('elapsed').textContent = time(position);
  if (document.activeElement !== $('seek')) $('seek').value = position;
  $('play').dataset.state = player.starting ? 'starting' : player.playing ? 'playing' : 'paused';
  $('play').setAttribute('aria-label', player.starting ? t('audio.cancelPlaybackStart') : player.playing ? t('audio.pause') : t('audio.play'));
  document.querySelectorAll('.playhead').forEach(p => p.style.left = `${player.duration ? position / player.duration * 100 : 0}%`);
  try { syncChordPlayhead(position); } catch (_) { /* chord panel optional */ }
  requestAnimationFrame(animate);
}
