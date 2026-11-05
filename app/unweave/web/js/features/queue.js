import {$, el, api, notice} from '../core/core.js';
import {store, nav} from '../core/store.js';
import {t, onLangChange} from '../core/i18n.js';
/* ---------- job queue dialog (opened from the job pill) ---------- */
export function jobWhat(job) {
  return job.kind === 'youtube' ? t('nav.downloading') : job.kind === 'setup' ? t('nav.settingUp')
    : job.kind === 'export' ? t('nav.exporting') : job.kind === 'chords' ? t('nav.analyzingChords')
    : job.guitar_split ? t('nav.splittingGuitar') : t('nav.separating');
}
function jobTitle(job) {
  const project = store.state.projects.find(p => p.id === job.project);
  return project?.title || (job.kind === 'youtube' ? t('queue.youtubeTitle') : job.kind === 'setup' ? t('queue.setupTitle') : t('nav.defaultSongTitle'));
}
export function renderQueue() {
  const dialog = $('queue-dialog');
  if (!dialog.open) return;
  // Running jobs first (position 0), then the waiting ones in queue order.
  const jobs = [...(store.state.jobs || [])].sort((a, b) => a.position - b.position);
  const list = $('queue-list'); list.replaceChildren();
  $('queue-empty').hidden = jobs.length > 0;
  for (const job of jobs) {
    const waiting = job.status === 'queued';
    const row = el('li', waiting ? 'queue-row queue-waiting' : 'queue-row');
    const text = el('div', 'queue-text');
    const status = waiting ? t('queue.position', {position: job.position})
      : job.status === 'cancelling' ? t('progress.stopping') : t('nav.jobPillText', {what: jobWhat(job), pct: Math.floor(job.progress || 0)});
    text.append(el('strong', '', jobTitle(job)), el('small', '', waiting ? `${jobWhat(job)} · ${status}` : status));
    const bar = el('div', 'queue-bar'); const fill = el('i'); fill.style.width = `${waiting ? 0 : Math.max(0, Math.min(100, job.progress || 0))}%`; bar.append(fill);
    text.append(bar);
    const actions = el('div', 'queue-actions');
    if (job.project) {
      const open = el('button', 'btn btn-ghost btn-sm', t('queue.open')); open.type = 'button';
      open.onclick = () => { dialog.close(); nav.selectProject(job.project); };
      actions.append(open);
    }
    const cancel = el('button', 'btn btn-ghost btn-sm', t('progress.cancel')); cancel.type = 'button';
    cancel.disabled = job.status === 'cancelling';
    cancel.onclick = async () => {
      cancel.disabled = true;
      try { await api('/api/cancel', {job_id: job.id}); await nav.refresh(); } catch (error) { notice(error.message, 'error'); cancel.disabled = false; }
    };
    actions.append(cancel);
    row.append(text, actions);
    list.append(row);
  }
}
export function openQueue() {
  if (!$('queue-dialog').open) $('queue-dialog').showModal();
  renderQueue();
}
onLangChange(renderQueue);
