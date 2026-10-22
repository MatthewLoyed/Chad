---
name: motion_design
description: Create editable, code-driven motion graphics, animated explainer videos, dynamic charts, and kinetic typography (Claude Motion style). Delivers live HTML animation artifacts or exports MP4/WebM videos via render_animation.
license: MIT
requires: render_animation
---

# Motion Design Skill (Claude Motion for Chad)

Generate studio-grade, code-driven motion graphics, launch ads, metric counters, dynamic typography, and 60 fps explainer videos.

Unlike generative diffusion video (Sora/Runway), **code-driven motion has 0% data hallucination**, razor-sharp vector rendering, and can be edited conversationally in chat turns.

---

## The Motion Design Taste Rules (With Concrete Numbers)

Studio-grade motion graphics require strict numerical discipline. Amateur animations suffer from linear easing, chaotic simultaneous movements, lack of visual hierarchy, and missing reading pauses. Apply these exact numerical rules:

### 1. Easing Curves (Never Use Default 'Linear' or Generic 'Ease')
| Curve | CSS Bezier | When to Use |
|---|---|---|
| **Snappy Entrance** | `cubic-bezier(0.16, 1, 0.3, 1)` | Elements arriving, hero reveals, text popping in. Fast launch with smooth spring-like settle. |
| **Fast Exit** | `cubic-bezier(0.6, 0, 0.9, 0.35)` | Exits. Leaves fast and does NOT linger on screen. |
| **State Morph / Dock** | `cubic-bezier(0.65, 0, 0.35, 1)` | Moving between two states, layout shifts, camera pans. |
| **Landing Overshoot** | `cubic-bezier(0.55, 0, 0.2, 1.12)` | Gauges, metric counters, dials landing on target. |
| **Linear** | `linear` | **ONLY** for continuous infinite background drift or rotation. |

### 2. Timing, Pacing & Pacing Hierarchy
- **Stagger Elements:** Never animate elements simultaneously. Stagger words by `55ms - 90ms`, cards/badges by `150ms - 200ms`, and particle rings by `60ms`.
- **Exits are Half the Entrance Duration:** Entrances take `0.7s - 1.0s`; exits must be fast (`0.35s - 0.5s`) using the fast exit curve.
- **Masked Word Rise:** Wrap text in `overflow: hidden` containers. Reveal text by translating from `translateY(110%)` to `0%` with a subtle `rotate(2deg)` to `0deg`.
- **Scene Overlaps (No Dead Gaps):** The next scene begins entering ~`0.2s` *before* the previous scene completely exits. Hard empty gaps read as "loading" or broken slides.
- **Reading Holds:** Every headline and core message must hold static for at least `1.0s - 1.5s` so humans have time to read before transitions occur.
- **Continuous Subtle Life (Never Frozen):** Keep a micro-drift (e.g. slow `scale: 1.0` to `1.03` camera push, or floating ambient dust/particles) alive during holds so the frame never feels like a frozen screenshot.

### 3. Visual Styling & Color Hierarchy
- **Canvas Base:** High-contrast dark mode (`#080b10` / `#0b0b0c`) or warm studio cream (`#f5f5f2`).
- **Single Accent Color Rule:** Use exactly **ONE** vibrant accent color (e.g. electric cyan `#00f0ff`, lime `#cdf24f`, or neon amber `#ffbe1a`) against monochrome/slate neutrals.
- **Ambient Lighting:** Use soft radial backlighting (`radial-gradient(circle at center, rgba(accent, 0.15), transparent 70%)`) and `drop-shadow(0 0 25px rgba(accent, 0.4))` on hero vector marks.
- **Depth:** Layer subtle dotted grids (`radial-gradient(rgba(255,255,255,0.1) 1px, transparent 1px)` with `background-size: 24px 24px`) behind the foreground elements.

---

## 2-Step Execution Workflow

When the user asks for an animation or video ad:

1. **Step 1 (Craft Self-Contained HTML/SVG/CSS):**
   Write single-file HTML embedding inline `<style>` and SVGs. Structure keyframes according to the total duration.
2. **Step 2 (Call `render_animation`):**
   ```python
   render_animation(
       html=content,
       mode="video",           # "video" or "mp4" exports video artifact
       duration_ms=5000,       # Match animation length
       viewport_width=1280,
       viewport_height=720,
       title="spikeball_ad"
   )
   ```
   *Note: Do NOT run bash or sandbox scripts. The host Brain executes `render_animation` natively.*

---

## Gold Standard Template: Multi-Scene Product Promo (5s Ad)

Here is a reference implementation showing scene choreography, masked text reveal, particle bursts, and camera push:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<style>
  :root {
    --bg: #080b11;
    --accent: #00f0ff;
    --accent-glow: rgba(0, 240, 255, 0.35);
    --yellow: #ffe600;
    --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
    --ease-in: cubic-bezier(0.6, 0, 0.9, 0.35);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    width: 100vw;
    height: 100vh;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  /* Camera container with continuous cinematic micro-push */
  .camera {
    width: 100%;
    height: 100%;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    animation: cameraPush 5s ease-out forwards;
  }
  @keyframes cameraPush {
    0% { transform: scale(1.0); }
    100% { transform: scale(1.05); }
  }
  /* Subtle vector dotted grid */
  .grid-bg {
    position: absolute;
    inset: 0;
    background-image: radial-gradient(rgba(255,255,255,0.08) 1.5px, transparent 1.5px);
    background-size: 32px 32px;
    opacity: 0.6;
  }
  /* Ambient glow pulse */
  .ambient-glow {
    position: absolute;
    width: 600px;
    height: 600px;
    border-radius: 50%;
    background: radial-gradient(circle, var(--accent-glow) 0%, transparent 70%);
    filter: blur(40px);
    animation: pulseGlow 4s ease-in-out infinite;
  }
  @keyframes pulseGlow {
    0%, 100% { transform: scale(0.9); opacity: 0.4; }
    50% { transform: scale(1.15); opacity: 0.7; }
  }
  /* Scene 1: Kinetic Headline (0s -> 2.4s) */
  .scene-1 {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    animation: scene1Exit 0.45s var(--ease-in) 2.2s forwards;
  }
  .mask-wrap {
    overflow: hidden;
    padding: 4px 8px;
  }
  .badge {
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 3px;
    text-transform: uppercase;
    color: var(--yellow);
    transform: translateY(110%);
    animation: textRise 0.7s var(--ease-out) 0.1s forwards;
  }
  .hero-title {
    font-size: 64px;
    font-weight: 900;
    letter-spacing: -1.5px;
    text-transform: uppercase;
    line-height: 1.05;
    transform: translateY(110%) rotate(2deg);
    animation: textRise 0.8s var(--ease-out) 0.25s forwards;
  }
  .hero-title span {
    color: var(--accent);
    text-shadow: 0 0 25px var(--accent-glow);
  }
  @keyframes textRise {
    to { transform: translateY(0%) rotate(0deg); }
  }
  @keyframes scene1Exit {
    to { opacity: 0; transform: translateY(-40px) scale(0.95); }
  }
  /* Scene 2: Hero Ball Smash & Ripple (2.3s -> 5.0s) */
  .scene-2 {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    opacity: 0;
    animation: scene2Enter 0.6s var(--ease-out) 2.4s forwards;
  }
  @keyframes scene2Enter {
    to { opacity: 1; }
  }
  /* Spikeball SVG with dynamic smash trajectory */
  .ball-wrapper {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    animation: ballSlam 1.2s var(--ease-out) 2.4s forwards;
    opacity: 0;
  }
  @keyframes ballSlam {
    0% {
      opacity: 1;
      transform: translate(-350px, -280px) scale(0.3) rotate(-180deg);
    }
    60% {
      transform: translate(-50%, -50%) scale(1.25) rotate(25deg);
    }
    80% {
      transform: translate(-50%, -50%) scale(0.95) rotate(-5deg);
    }
    100% {
      opacity: 1;
      transform: translate(-50%, -50%) scale(1.0) rotate(0deg);
    }
  }
  /* Impact shockwave ring */
  .shockwave {
    position: absolute;
    width: 80px;
    height: 80px;
    border: 3px solid var(--accent);
    border-radius: 50%;
    opacity: 0;
    animation: ripple 0.8s var(--ease-out) 3.1s forwards;
  }
  @keyframes ripple {
    0% { transform: scale(0.4); opacity: 1; }
    100% { transform: scale(4.5); opacity: 0; }
  }
  /* Final Call-to-Action */
  .cta-title {
    font-size: 56px;
    font-weight: 900;
    letter-spacing: 4px;
    text-transform: uppercase;
    text-align: center;
    margin-top: 180px;
    opacity: 0;
    transform: translateY(20px);
    animation: ctaIn 0.7s var(--ease-out) 3.2s forwards;
    text-shadow: 0 0 35px var(--accent-glow);
  }
  @keyframes ctaIn {
    to { opacity: 1; transform: translateY(0); }
  }
</style>
</head>
<body>
  <div class="camera">
    <div class="grid-bg"></div>
    <div class="ambient-glow"></div>

    <!-- Scene 1: Typography Hook -->
    <div class="scene-1">
      <div class="mask-wrap"><div class="badge">Roundnet Championship</div></div>
      <div class="mask-wrap"><h1 class="hero-title">READY TO <span>SERVE?</span></h1></div>
    </div>

    <!-- Scene 2: Hero Impact & Final CTA -->
    <div class="scene-2">
      <div class="shockwave"></div>
      <div class="ball-wrapper">
        <svg width="120" height="120" viewBox="0 0 100 100">
          <defs>
            <radialGradient id="ballGlow" cx="35%" cy="35%" r="65%">
              <stop offset="0%" stop-color="#fff888"/>
              <stop offset="60%" stop-color="#ffe600"/>
              <stop offset="100%" stop-color="#e69500"/>
            </radialGradient>
          </defs>
          <circle cx="50" cy="50" r="46" fill="url(#ballGlow)" stroke="#080b11" stroke-width="3"/>
          <path d="M 20 50 Q 50 30 80 50" stroke="#cc8400" stroke-width="3.5" fill="none"/>
          <path d="M 20 50 Q 50 70 80 50" stroke="#cc8400" stroke-width="3.5" fill="none"/>
          <path d="M 50 20 Q 30 50 50 80" stroke="#cc8400" stroke-width="3.5" fill="none"/>
          <path d="M 50 20 Q 70 50 50 80" stroke="#cc8400" stroke-width="3.5" fill="none"/>
        </svg>
      </div>
      <div class="cta-title">SPIKEBALL</div>
    </div>
  </div>
</body>
</html>
```
