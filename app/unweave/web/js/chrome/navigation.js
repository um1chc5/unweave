import {$, notice, api, activeJob, jobRunning, activeProject, setClockSkew} from '../core/core.js';
import {store, nav} from '../core/store.js';
import {clearAudio} from '../audio/audio.js';
import {renderNew, continuePending} from '../views/new.js';
import {renderLibrary} from '../views/library.js';
import {renderSong, exportSummary} from '../views/song.js';
import {renderSetup, renderSetupView, renderSetupBanner} from '../views/setup.js';
import {refreshSettingsOpen} from '../views/settings.js';
import {t, onLangChange} from '../core/i18n.js';
import {jobWhat, openQueue, renderQueue} from '../features/queue.js';
import {renderLinuxAudioBanner} from './linux-audio.js';
function clearSongTrim() {
  store.songTrimEditor?.destroy();
  store.songTrimEditor = null;
  store.songTrimRange = null;
  store.songTrimKey = '';
  $('song-trim-chip').replaceChildren();
  $('song-trim-chip').hidden = true;
}
/* ---------- views ---------- */
export function show(name) {
  if (store.state.setup?.required && !store.setupDeclined && name !== 'setup') name = 'setup';
  store.view = name;
  for (const v of ['new', 'library', 'song', 'setup']) $(`view-${v}`).hidden = v !== name;
  $('tab-new').setAttribute('aria-selected', String(name === 'new'));
  $('tab-library').setAttribute('aria-selected', String(name === 'library' || name === 'song'));
  if (name !== 'song') { store.selected = null; store.audioSignature = ''; clearSongTrim(); clearAudio(); }
  render();
}
document.querySelectorAll('.tab').forEach(tab => tab.onclick = () => {
  if (store.state.setup?.required && !store.setupDeclined) { show('setup'); return; }
  show(tab.dataset.view);
});
$('back').onclick = () => show('library');
export function selectProject(id) {
  if (store.state.setup?.required && !store.setupDeclined) { notice(t('setup.completeFirst'), 'error'); show('setup'); return; }
  if (store.selected === id && store.view === 'song') return;
  clearSongTrim();
  store.selected = id; store.resepOpen = false; store.view = 'song';
  for (const v of ['new', 'library', 'song', 'setup']) $(`view-${v}`).hidden = v !== 'song';
  $('tab-new').setAttribute('aria-selected', 'false'); $('tab-library').setAttribute('aria-selected', 'true');
  $('song-stems').dataset.preset = '';
  render();
}

export function render() {
  // Route to setup for real (toggling which view is actually visible) rather than just
  // setting the `view` variable: that was the bug — it fooled later checks in this same
  // function (like the banner's `view !== 'setup'`) into thinking the user was already
  // looking at the setup screen, while the DOM still showed the normal "Separate" view
  // underneath, with no banner either, on top of a plain page load not calling show()
  // (and so never running this gate at all) — so first-run setup never actually appeared.
  if (store.state.setup?.required && !store.setupDeclined && store.view !== 'setup') { show('setup'); return; }
  renderChrome(); renderNew(); renderLibrary(); renderSong(); renderSetup(); renderSetupView(); renderSetupBanner();
  renderLinuxAudioBanner();
  // Settings is a dialog: only refresh component install chips while it is open.
  refreshSettingsOpen();
}
export function renderChrome() {
  $('project-count').textContent = store.state.projects.length;
  const job = activeJob(), pill = $('job-pill');
  const queued = (store.state.jobs || []).filter(j => j.position > 0).length;
  pill.hidden = !jobRunning() && queued === 0;
  if (jobRunning()) {
    const what = jobWhat(job);
    const tail = queued ? ` • ${t('nav.jobWaiting', {count: queued})}` : '';
    $('job-pill-text').textContent = t('nav.jobPillText', {what, pct: Math.floor(job.progress || 0)}) + tail;
  } else if (queued) {
    $('job-pill-text').textContent = t('nav.jobQueued', {count: queued});
  }
  renderQueue();
}
$('job-pill').onclick = openQueue;

export async function refresh() {
  const next = await api('/api/state');
  setClockSkew(next.now - Date.now() / 1000);
  const rootChanged = store.state.root !== next.root;
  Object.assign(store.state, next);
  if (store.view === 'song' && (rootChanged || !activeProject())) { show(rootChanged ? 'new' : 'library'); return; }
  continuePending();
  announce();
  render();
}
export function announce() {
  // Jobs can finish while another is still running, so walk every recently finished one.
  const jobs = [...(store.state.finished_jobs || [])];
  const job = store.state.job;
  if (job && !jobs.some(item => item.id === job.id)) jobs.push(job);
  jobs.forEach(announceJob);
}
function announceJob(job) {
  if (job.kind === 'youtube' || store.announced.has(job.id)) return;
  if (job.status === 'succeeded') {
    store.announced.add(job.id);
    if (job.kind === 'setup') {
      const youtubeOnly = (job.stages || []).length === 1 && job.stages[0] === 'youtube';
      notice(t(youtubeOnly ? 'youtube.installed' : 'setup.complete'), 'success');
      store.setupJobAnnounced = job.id;
      show('new');
      return;
    }
    if (job.kind === 'export') {
      // On this song the success dialog says the same thing; a toast only helps elsewhere.
      const dialogShows = store.exportPending?.jobId === job.id && store.view === 'song' && store.selected === job.project;
      if (!dialogShows) notice(exportSummary(job), 'success');
      return;
    }
    if (job.kind === 'chords') {
      const p = store.state.projects.find(x => x.id === job.project);
      notice(t('chords.phase.complete') + (p?.title ? ` — ${p.title}` : ''), 'success');
      return;
    }
    store.stemsReady.add(job.project);
    const p = store.state.projects.find(x => x.id === job.project);
    notice(t('nav.songReadyNotice', {title: p?.title || t('nav.defaultSongTitle'), count: (job.stems || []).length}), 'success');
  }
}


export function initNavigation() {
  Object.assign(nav, {show, selectProject, refresh, render});
  onLangChange(render);
}
