export const colors = ['#ff8a3d', '#5ec8ff', '#b48cff', '#4fe0a8', '#ffd166', '#ff6f91'];
export const ORIGINAL_COLOR = '#9aa3b2';
// Second element of each stem/step/format tuple below is an i18n key (see i18n.js), not
// literal text, so labels stay in sync when the language is switched at runtime.
export const STEMS = [['vocals', 'stem.vocals', '#ff8a3d'], ['drums', 'stem.drums', '#5ec8ff'], ['bass', 'stem.bass', '#b48cff'],
  ['guitar', 'stem.guitar', '#4fe0a8'], ['piano', 'stem.piano', '#ffd166'], ['other', 'stem.other', '#ff6f91']];
export const STEM_LABEL_KEYS = {vocals: 'stem.vocals', drums: 'stem.drums', bass: 'stem.bass', guitar: 'stem.guitar', piano: 'stem.piano', other: 'stem.other', instrumental: 'stem.instrumental', lead: 'stem.lead', rhythm: 'stem.rhythm'};
// Split outputs: not model stems, so they live outside STEMS (the quick grid and
// the request payload are built from STEMS alone).
export const LEAVES = [['lead', 'stem.lead', '#4fe0a8'], ['rhythm', 'stem.rhythm', '#7ef0c6']];
export const PLAN_META = Object.fromEntries(
  [...STEMS, ...LEAVES].map(([id, key, color]) => [id, {key, color}]));
export const DEFAULT_STEMS = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'other'];
export const AUDIO_EXTENSIONS = ['.wav', '.flac', '.mp3', '.opus', '.ogg', '.m4a', '.aac', '.aiff', '.aif'];
export const SEPARATION_STEPS = [['prepare', 'step.separation.prepare'], ['load', 'step.separation.load'], ['separate', 'step.separation.separate'], ['finalize', 'step.separation.finalize']];
// A separation that also splits the guitar runs a second, much slower model after the
// stem model. Show it as its own step instead of hiding it inside "Separate stems",
// or the dialog sits on one step for minutes and reads as stuck.
export const SEPARATION_SPLIT_STEPS = [['prepare', 'step.separation.prepare'], ['load', 'step.separation.load'], ['separate', 'step.separation.separate'], ['guitar', 'step.separation.guitar'], ['finalize', 'step.separation.finalize']];
export const GUITAR_STEPS = [['prepare', 'step.guitar.prepare'], ['separate', 'step.guitar.separate'], ['finalize', 'step.guitar.finalize']];
export const YOUTUBE_STEPS = [['download', 'step.youtube.download'], ['finalize', 'step.youtube.finalize']];
export const EXPORT_STEPS = [['prepare', 'step.export.prepare'], ['export', 'step.export.export'], ['finalize', 'step.export.finalize']];
export const CHORD_STEPS = [['prepare', 'step.chords.prepare'], ['beats', 'step.chords.beats'], ['chords', 'step.chords.chords'], ['key', 'step.chords.key'], ['lyrics', 'step.chords.lyrics'], ['finalize', 'step.chords.finalize']];
export const EXPORT_FORMATS = [
  ['flac', 'format.flac.title', 'format.flac.sub'],
  ['mp3', 'format.mp3.title', 'format.mp3.sub'],
  ['opus', 'format.opus.title', 'format.opus.sub'],
  ['m4a', 'format.m4a.title', 'format.m4a.sub'],
  ['wav16', 'format.wav16.title', 'format.wav16.sub'],
  ['aiff', 'format.aiff.title', 'format.aiff.sub'],
];
export const SETUP_STEPS = [['prepare', 'step.setup.prepare'], ['download', 'step.setup.download'], ['install', 'step.setup.install'], ['finalize', 'step.setup.finalize']];
export const SETUP_STAGE_LABEL = {vcruntime: 'setup.stageShort.vcruntime', ffmpeg: 'setup.stageShort.ffmpeg', separation: 'setup.stageShort.separation', youtube: 'setup.stageShort.youtube', runtime: 'setup.stageShort.runtime'};
export const SETUP_PHASE_TITLE = {prepare: 'setup.phase.prepare.title', finalize: 'setup.phase.finalize.title'};
export const SETUP_PHASE_SUB = {prepare: 'setup.phase.prepare.sub', download: 'setup.phase.download.sub', install: 'setup.phase.install.sub', finalize: 'setup.phase.finalize.sub'};
export const ORIGINAL_FILE = '__original__';
