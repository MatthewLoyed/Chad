/** @typedef {'idle' | 'sleeping' | 'poke' | 'thinking' | 'working' | 'error'} PetState */

/** @type {Record<string, string>} */
const svgCache = {};

const POKE_DURATION_MS = 1200;
const DOUBLE_CLICK_MS = 350;

/** @type {PetState} */
let currentState = 'idle';
let isReacting = false;
let reactTimer = null;

let clickCount = 0;
let clickTimer = null;

const root = document.getElementById('pet-root');
const wrapper = document.getElementById('svg-wrapper');

function stripSvgSize(raw) {
  return raw.replace(/\s+width="[^"]*"/, '').replace(/\s+height="[^"]*"/, '');
}

function registerSvg(name, raw) {
  svgCache[name] = stripSvgSize(raw);
}

function loadSvg(state) {
  const mapped = state === 'thinking' ? 'idle' : state === 'error' ? 'poke' : state;
  const raw = svgCache[mapped] || svgCache.idle || '';
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

function setState(state) {
  if (isReacting && state !== 'poke') return;
  currentState = state;
  loadSvg(state);
}

function playPoke() {
  if (isReacting) return;
  const priorState = currentState;
  isReacting = true;
  if (reactTimer) clearTimeout(reactTimer);
  loadSvg('poke');
  reactTimer = setTimeout(() => {
    isReacting = false;
    reactTimer = null;
    loadSvg(priorState === 'sleeping' ? 'sleeping' : 'idle');
  }, POKE_DURATION_MS);
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
  setState,
  playPoke,
  registerSvg,
  getState: () => currentState,
};
