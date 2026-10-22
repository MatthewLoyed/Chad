/** @typedef {'idle' | 'sleeping' | 'poke' | 'thinking' | 'working' | 'error'} PetMode */

/** @type {Record<string, string>} */
const svgCache = {};

/** Maps Brain cue names to registered SVG keys (add entries as you add art). */
const CUE_TO_SVG = {
  success: 'poke',
  error: 'poke',
};

const POKE_DURATION_MS = 1200;
const CUE_DURATION_MS = 1500;
const DOUBLE_CLICK_MS = 350;

/** @type {PetMode} */
let currentMode = 'idle';
let isReacting = false;
let reactTimer = null;

let clickCount = 0;
let clickTimer = null;

const root = document.getElementById('pet-root');
const wrapper = document.getElementById('svg-wrapper');
const commentBubble = document.getElementById('comment-bubble');

let commentHideTimer = null;
let lastCommentKey = '';

function stripSvgSize(raw) {
  return raw.replace(/\s+width="[^"]*"/, '').replace(/\s+height="[^"]*"/, '');
}

function registerSvg(name, raw) {
  svgCache[name] = stripSvgSize(raw);
}

function resolveSvgKey(modeOrCue) {
  if (modeOrCue === 'thinking') return 'idle';
  if (modeOrCue === 'error') return 'poke';
  // 'listening' (wake word heard) reuses the alert 'poke' pose so the buddy reacts.
  if (modeOrCue === 'listening') return 'poke';
  return CUE_TO_SVG[modeOrCue] || modeOrCue;
}

function loadSvg(modeOrCue) {
  const key = resolveSvgKey(modeOrCue);
  const raw = svgCache[key] || svgCache.idle || '';
  wrapper.innerHTML = raw;
}

function moveEyes(dx, dy) {
  const eyes = document.getElementById('eyes-js');
  if (eyes) eyes.style.transform = `translate(${dx * 0.6}px, ${dy * 0.4}px)`;
  const body = document.getElementById('body-js');
  if (body) body.style.transform = `translate(${dx * 0.15}px, 0)`;
  const shadow = document.getElementById('shadow-js');
  if (shadow) shadow.style.transform = `scaleX(${1 + Math.abs(dx) * 0.03})`;
}

function setMode(mode) {
  if (isReacting && mode !== 'poke') return;
  currentMode = mode;
  loadSvg(mode);
}

/** @deprecated use setMode */
function setState(state) {
  setMode(state);
}

function playTimedReaction(svgKey, durationMs) {
  if (isReacting) return;
  const priorMode = currentMode;
  isReacting = true;
  if (reactTimer) clearTimeout(reactTimer);
  loadSvg(svgKey);
  reactTimer = setTimeout(() => {
    isReacting = false;
    reactTimer = null;
    loadSvg(priorMode === 'sleeping' ? 'sleeping' : priorMode);
  }, durationMs);
}

function playPoke() {
  playTimedReaction('poke', POKE_DURATION_MS);
}

/** One-shot animation from Brain event cues (e.g. tool success). */
function playCue(animation, meta = {}) {
  const svgKey = resolveSvgKey(animation);
  const duration = meta.duration_ms || CUE_DURATION_MS;
  playTimedReaction(svgKey, duration);
}

/** Ambient observation from Brain observer. */
function showComment(text, untilMs = 45000) {
  if (!text || !commentBubble) return;
  const key = `${text}:${untilMs}`;
  if (key === lastCommentKey) return;
  lastCommentKey = key;

  commentBubble.textContent = text;
  commentBubble.classList.remove('hidden');

  if (commentHideTimer) clearTimeout(commentHideTimer);
  commentHideTimer = setTimeout(() => {
    commentBubble.classList.add('hidden');
    commentHideTimer = null;
    lastCommentKey = '';
  }, untilMs);
}

function dismissComment() {
  if (commentBubble) commentBubble.classList.add('hidden');
  if (commentHideTimer) clearTimeout(commentHideTimer);
  commentHideTimer = null;
  lastCommentKey = '';
}

function onPointerDown(e) {
  if (e.button !== 0) return;
  clickCount += 1;
  if (clickTimer) clearTimeout(clickTimer);
  clickTimer = setTimeout(() => {
    const count = clickCount;
    clickCount = 0;
    if (count >= 2) {
      playPoke();
    }
  }, DOUBLE_CLICK_MS);
}

function onContextMenu(e) {
  e.preventDefault();
  if (window.pywebview?.api?.show_menu) {
    window.pywebview.api.show_menu();
  }
}

root.addEventListener('pointerdown', onPointerDown);
root.addEventListener('contextmenu', onContextMenu);

window.chadPet = {
  moveEyes,
  setMode,
  setState,
  playPoke,
  playCue,
  showComment,
  dismissComment,
  registerSvg,
  getMode: () => currentMode,
  getState: () => currentMode,
};
