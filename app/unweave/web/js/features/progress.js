import {SETUP_STEPS, YOUTUBE_STEPS, GUITAR_STEPS, SEPARATION_STEPS, SEPARATION_SPLIT_STEPS, EXPORT_STEPS, CHORD_STEPS, SETUP_STAGE_LABEL, SETUP_PHASE_TITLE, SETUP_PHASE_SUB} from '../core/constants.js';
import {$, el, api, notice, serverNow, duration} from '../core/core.js';
import {store, nav} from '../core/store.js';
import {t, onLangChange} from '../core/i18n.js';
import {byteText, speedText} from './components.js';
import {copyDiagnostics, openFacebook, openGitHubIssues} from './diagnostics.js';
/* ---------- progress view ---------- */
export function buildProgress(panel) {
  panel.innerHTML = `
    <div class="pv-gauge">
      <div class="pv-ring" aria-hidden="true">
        <svg viewBox="0 0 120 120"><defs>
          <linearGradient id="${panel.id}-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffb35c"/><stop offset="1" stop-color="#ff5c6c"/></linearGradient>
          <linearGradient id="${panel.id}-g2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9d8cff"/><stop offset="1" stop-color="#5cc8ff"/></linearGradient>
        </defs>
          <circle class="pv-track" cx="60" cy="60" r="52"/><circle class="pv-bar" cx="60" cy="60" r="52" stroke="url(#${panel.id}-g)"/><circle class="pv-spin" cx="60" cy="60" r="52"/></svg>
        <div class="pv-pct"><span class="pv-num">0</span><small>%</small></div>
      </div>
      <p class="pv-step" hidden></p>
    </div>
    <div class="pv-body">
      <div class="pv-top"><div><h2 class="pv-title"></h2><p class="pv-sub"></p></div><button type="button" class="btn btn-ghost btn-sm pv-cancel"></button></div>
      <div class="pv-stats">
        <div><span class="eyebrow pv-eyebrow-left"></span><strong class="pv-eta">—</strong></div>
        <div><span class="eyebrow pv-eyebrow-elapsed"></span><strong class="pv-elapsed">0s</strong></div>
        <div><span class="eyebrow pv-eyebrow-stems"></span><strong class="pv-stems">—</strong></div>
      </div>
      <div class="pv-job">
        <div class="pv-job-head"><span class="pv-job-label"></span><span class="pv-job-pct">0%</span></div>
        <div class="pv-linebar"><i></i></div>
        <p class="pv-job-note" hidden></p>
      </div>
      <details class="log-details"><summary class="pv-log-summary"></summary><pre class="log-output pv-log"></pre></details>
      <div class="pv-report" hidden>
        <button type="button" class="btn btn-ghost btn-sm pv-copy-report"></button>
        <button type="button" class="btn btn-ghost btn-sm pv-github-issues"></button>
        <button type="button" class="btn btn-ghost btn-sm pv-facebook"></button>
      </div>
    </div>
    <ol class="pv-steps"></ol>`;
  panel.querySelector('.pv-cancel').onclick = async event => {

    event.currentTarget.disabled = true;
    // Cancel this panel's job only; other running or queued jobs keep going.
    try { await api('/api/cancel', panel.dataset.jobId ? {job_id: panel.dataset.jobId} : {}); await nav.refresh(); } catch (error) { notice(error.message, 'error'); }
  };
  panel.querySelector('.pv-copy-report').onclick = async () => {
    try { await copyDiagnostics(panel.dataset.jobId || undefined); }
    catch (error) { notice(error.message, 'error'); }
  };
  panel.querySelector('.pv-github-issues').onclick = () => {
    openGitHubIssues().catch(error => notice(error.message, 'error'));
  };
  panel.querySelector('.pv-facebook').onclick = () => {
    openFacebook().catch(error => notice(error.message, 'error'));
  };
  applyProgressChrome(panel);
}
function applyProgressChrome(panel) {
  panel.querySelector('.pv-cancel').textContent = t('progress.cancel');
  panel.querySelector('.pv-eyebrow-left').textContent = t('progress.stepTimeLeft');
  panel.querySelector('.pv-eyebrow-elapsed').textContent = t('progress.elapsed');
  panel.querySelector('.pv-eyebrow-stems').textContent = t('progress.stemsLabel');
  panel.querySelector('.pv-log-summary').textContent = t('progress.processLog');
  panel.querySelector('.pv-job-label').textContent = t('progress.wholeJob');
  panel.querySelector('.pv-copy-report').textContent = t('diagnostics.copyReport');
  panel.querySelector('.pv-github-issues').textContent = t('diagnostics.openGithub');
  panel.querySelector('.pv-facebook').textContent = t('diagnostics.openFacebook');
}
buildProgress($('job-panel'));
buildProgress($('yt-panel'));
onLangChange(() => { for (const panel of [$('job-panel'), $('yt-panel')]) applyProgressChrome(panel); });
export const CIRCUMFERENCE = 2 * Math.PI * 52;

const TIMED_PHASES = new Set(['separate', 'guitar', 'export', 'download', 'beats', 'chords', 'lyrics']);

export function renderProgress(panel, job) {
  panel.dataset.jobId = job.id; panel.job = job;
  if (job.status === 'queued') { renderQueued(panel, job); return; }
  panel.querySelector('.pv-eta').closest('.pv-stats').hidden = false;
  const youtube = job.kind === 'youtube';
  const setup = job.kind === 'setup';
  const exportJob = job.kind === 'export';
  const chordJob = job.kind === 'chords';
  const steps = setup ? SETUP_STEPS : youtube ? YOUTUBE_STEPS : exportJob ? EXPORT_STEPS : chordJob ? CHORD_STEPS : job.guitar_split ? GUITAR_STEPS : job.split_leaves ? SEPARATION_SPLIT_STEPS : SEPARATION_STEPS;
  const running = ['running', 'cancelling'].includes(job.status);
  let phase = setup ? job.phase : job.status === 'succeeded' ? 'done' : job.phase === 'download' && !youtube ? 'load' : job.phase;
  // A split-only job runs the guitar model as its one and only pass, so its phase
  // belongs to its own "Split guitar" step rather than to a second pass.
  if (job.guitar_split && phase === 'guitar') phase = 'separate';
  const splitPass = phase === 'guitar';
  const progress = job.status === 'succeeded' ? 100 : Math.max(0, Math.min(100, job.progress || 0));
  // The ring counts the current step's own 0-100, so the long guitar pass starts a
  // fresh gauge instead of creeping through the tail of the stem pass's bar. Jobs that
  // never report per-step numbers (setup, queued work) fall back to the overall bar.
  const stepProgress = job.status === 'succeeded' ? 100
    : Math.max(0, Math.min(100, job.step_progress ?? progress));
  panel.dataset.status = job.status;
  panel.dataset.pass = splitPass ? 'guitar' : 'stems';
  panel.classList.toggle('busy', running && (setup ? true : youtube ? progress === 0 : !['separate', 'guitar', 'export'].includes(phase)));
  panel.querySelector('.pv-num').textContent = Math.floor(stepProgress);
  panel.querySelector('.pv-bar').setAttribute('stroke', `url(#${panel.id}-${splitPass ? 'g2' : 'g'})`);
  panel.querySelector('.pv-bar').style.strokeDasharray = `${CIRCUMFERENCE}`;
  panel.querySelector('.pv-bar').style.strokeDashoffset = `${CIRCUMFERENCE * (1 - stepProgress / 100)}`;
  panel.querySelector('.pv-linebar i').style.width = `${progress}%`;
  panel.querySelector('.pv-job-pct').textContent = `${Math.floor(progress)}%`;
  panel.querySelector('.pv-job').hidden = setup;
  const note = panel.querySelector('.pv-job-note');
  if (setup || !(job.pass_count > 1)) {
    note.hidden = true;
    note.textContent = '';
  }
  let title, sub;
  if (setup) {
    const stage = job.stage; const stageLabel = stage ? t(SETUP_STAGE_LABEL[stage] || '') : '';
    const phaseTitles = {
      prepare: t(SETUP_PHASE_TITLE.prepare), finalize: t(SETUP_PHASE_TITLE.finalize),
      download: stage ? t('setup.phase.downloadingStage', {stage: stageLabel}) : t('setup.phase.downloading'),
      install: stage ? t('setup.phase.installingStage', {stage: stageLabel}) : t('setup.phase.installing'),
    };
    const phaseSubs = {prepare: t(SETUP_PHASE_SUB.prepare), download: t(SETUP_PHASE_SUB.download), install: t(SETUP_PHASE_SUB.install), finalize: t(SETUP_PHASE_SUB.finalize)};
    title = job.status === 'cancelling' ? t('progress.stopping') : running ? phaseTitles[phase] || t('progress.working') : (job.status === 'succeeded' ? t('setup.phase.completeTitle') : t('setup.phase.failedTitle'));
    sub = running ? phaseSubs[phase] || '' : job.error || (job.status === 'succeeded' ? t('setup.phase.readyNowSub') : '');
    if (running && (phase === 'download' || job.download_speed)) {
      const speed = speedText(job.download_speed);
      const received = byteText(job.download_received);
      const total = byteText(job.download_total);
      if (speed && received && total) sub = t('setup.phase.download.subSpeed', {received, total, speed});
      else if (speed && received) sub = t('setup.phase.download.subSpeedPartial', {received, speed});
      else if (speed) sub = t('setup.phase.download.subSpeedOnly', {speed});
    }
  } else if (exportJob) {
    const titles = {prepare: t('export.phase.prepare.title'), export: t('export.phase.export.title'), finalize: t('export.phase.finalize.title')};
    const ended = {succeeded: t('export.phase.complete'), failed: t('export.phase.failed'), cancelled: t('progress.cancelled')};
    title = job.status === 'cancelling' ? t('progress.stopping') : running ? titles[phase] || t('progress.working') : ended[job.status];
    sub = running ? t('export.phase.workingSub') : job.error || (job.status === 'succeeded' ? t('export.phase.readySub') : '');
  } else if (chordJob) {
    const titles = {
      prepare: t('chords.phase.prepare'), beats: t('chords.phase.beats'), chords: t('chords.phase.chords'),
      key: t('chords.phase.key'), lyrics: t('chords.phase.lyrics'), finalize: t('chords.phase.finalize'),
    };
    const ended = {succeeded: t('chords.phase.complete'), failed: t('chords.phase.failed'), cancelled: t('progress.cancelled')};
    title = job.status === 'cancelling' ? t('progress.stopping') : running ? titles[phase] || t('progress.working') : ended[job.status];
    sub = running ? t('chords.phase.workingSub') : job.error || (job.status === 'succeeded' ? t('chords.phase.readySub') : '');
  } else {
    const titles = {prepare: job.guitar_split ? t('sep.phase.prepareGuitar') : t('sep.phase.prepareNormal'), load: job.phase === 'download' ? t('sep.phase.downloadModel') : t('sep.phase.loadModel'),
      download: t('sep.phase.downloadYoutube'), separate: job.guitar_split ? t('sep.phase.separateGuitar') : t('sep.phase.separateNormal'),
      guitar: t('sep.phase.splitGuitar'), finalize: youtube ? t('sep.phase.finalizeYoutube') : t('sep.phase.finalizeNormal')};
    const subs = {prepare: t('sep.sub.prepare'), load: job.phase === 'download' ? t('sep.sub.loadDownloading') : t('sep.sub.load'),
      download: t('sep.sub.download'), separate: t('sep.sub.separate'), guitar: t('sep.sub.splitGuitar'), finalize: t('sep.sub.finalize')};
    const ended = {succeeded: youtube ? t('sep.ended.downloadComplete') : t('sep.ended.separationComplete'), failed: youtube ? t('sep.ended.downloadFailed') : t('sep.ended.separationFailed'), cancelled: t('progress.cancelled')};
    title = job.status === 'cancelling' ? t('progress.stopping') : running ? titles[phase] || t('progress.working') : ended[job.status];
    sub = running ? subs[phase] || '' : job.error || (job.status === 'succeeded' ? t('sep.readySub') : '');
  }
  panel.querySelector('.pv-title').textContent = title;
  panel.querySelector('.pv-sub').textContent = sub;
  const cancel = panel.querySelector('.pv-cancel');
  cancel.hidden = !running; cancel.disabled = job.status === 'cancelling';
  panel.querySelector('.pv-stems').textContent = setup || youtube || chordJob ? '—' : (job.stems || []).length;
  const list = panel.querySelector('.pv-steps'); list.replaceChildren();
  const current = phase === 'done' ? steps.length : Math.max(0, steps.findIndex(([key]) => key === phase));
  // Names the step the ring is counting, so its % never reads as the whole job's.
  const stepLabel = panel.querySelector('.pv-step');
  stepLabel.hidden = !running || current >= steps.length;
  stepLabel.textContent = stepLabel.hidden ? '' : t(steps[current][1]);
  steps.forEach(([key, label], index) => {
    const item = el('li', index < current || phase === 'done' ? 'done' : index === current && running ? 'active' : '', t(label));
    if (index === current && !running && job.status !== 'succeeded') item.className = 'failed';
    // The active chip carries this step's own percentage, so "40%" on the ring always
    // belongs to the step that is lit below it.
    if (index === current && running && progress > 0) item.dataset.pct = `${Math.floor(stepProgress)}%`;
    list.append(item);
  });
  panel.querySelector('.pv-log').textContent = (job.logs || []).join('\n');
  const report = panel.querySelector('.pv-report');
  report.hidden = job.status !== 'failed';
  renderTimers(panel, job);
}
function renderQueued(panel, job) {
  panel.dataset.status = 'queued';
  panel.classList.remove('busy');
  panel.querySelector('.pv-num').textContent = 0;
  panel.querySelector('.pv-bar').style.strokeDashoffset = `${CIRCUMFERENCE}`;
  panel.querySelector('.pv-linebar i').style.width = '0%';
  panel.querySelector('.pv-job-pct').textContent = '0%';
  panel.querySelector('.pv-job').hidden = true;
  const stepLabel = panel.querySelector('.pv-step');
  stepLabel.hidden = true; stepLabel.textContent = '';
  const note = panel.querySelector('.pv-job-note');
  note.hidden = true; note.textContent = '';
  panel.querySelector('.pv-title').textContent = t('queue.waitingTitle', {position: job.position});
  panel.querySelector('.pv-sub').textContent = t('queue.waitingSub');
  const cancel = panel.querySelector('.pv-cancel');
  cancel.hidden = false; cancel.disabled = false;
  panel.querySelector('.pv-steps').replaceChildren();
  panel.querySelector('.pv-eta').closest('.pv-stats').hidden = true;
  panel.querySelector('.pv-log').textContent = '';
  panel.querySelector('.pv-report').hidden = true;
}
export function renderTimers(panel, job) {
  if (job.status === 'queued') return;
  const now = serverNow();
  panel.querySelector('.pv-elapsed').textContent = duration((job.finished || now) - job.started);
  const running = ['running', 'cancelling'].includes(job.status);
  // Prefer pass-local step_eta; fall back to whole-job eta for older payloads.
  const stepEta = job.step_eta !== undefined ? job.step_eta : job.eta;
  const stepEtaAt = job.step_eta_at !== undefined ? job.step_eta_at : job.eta_at;
  let eta = '—';
  if (running && stepEta != null) {
    const left = stepEta - (now - (stepEtaAt || now));
    eta = left > 1 ? `~${duration(left)}` : t('progress.etaFewSeconds');
  } else if (running && TIMED_PHASES.has(job.phase)) {
    eta = t('progress.etaEstimating');
  } else if (job.status === 'succeeded') {
    eta = t('progress.etaDone');
  }
  panel.querySelector('.pv-eta').textContent = eta;

  const note = panel.querySelector('.pv-job-note');
  if (job.pass_count > 1 && running) {
    if (job.eta_estimated) {
      note.hidden = false;
      note.textContent = t('progress.guitarNext');
    } else if (job.eta != null) {
      const left = job.eta - (now - (job.eta_at || now));
      if (left > 1) {
        note.hidden = false;
        note.textContent = t('progress.jobDoneIn', {time: `~${duration(left)}`});
      } else {
        note.hidden = false;
        note.textContent = t('progress.jobDoneIn', {time: t('progress.etaFewSeconds')});
      }
    } else {
      note.hidden = true;
      note.textContent = '';
    }
  } else if (job.pass_count > 1 && job.status === 'succeeded') {
    note.hidden = true;
    note.textContent = '';
  } else if (!(job.pass_count > 1)) {
    note.hidden = true;
    note.textContent = '';
  }
}
setInterval(() => {
  for (const panel of [$('job-panel'), $('yt-panel')]) if (!panel.hidden && panel.job) renderTimers(panel, panel.job);
}, 500);
