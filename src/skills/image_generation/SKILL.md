---
name: image_generation
description: "Generate images, illustrations, artwork, photos, or visual concepts locally using ComfyUI via generate_image. Call generate_image directly yourself — NEVER delegate to code_agent."
license: MIT
requires: generate_image
runtime: comfyui
install: Run ComfyUI on http://127.0.0.1:8188 (Settings → Services → ComfyUI)
---

# Local Image Generation Skill (ComfyUI)

Use this skill when the user wants an **AI-generated image**, **illustration**, **concept art**, **character design**, or **photo** rendered locally on their GPU.

## When to use

| User Need | Tool / Skill to use |
|-----------|---------------------|
| **AI image / artwork / photo / drawing** | `generate_image` directly yourself (NEVER delegate to `code_agent`) |
| Social post / marketing flyer / Canva template | `load_skill('canva')` → Canva MCP |
| Structured flowchart / architecture diagram | `load_skill('kroki')` → `render_diagram` |
| Hand-drawn whiteboard sketch | `load_skill('excalidraw')` → Excalidraw MCP |
| Stock / Creative Commons photo lookup | `search_images` (Openverse/Unsplash) |
| Slide deck presentation | `load_skill('presenton')` or `load_skill('marp')` |

## SOP (Standard Operating Procedure)

1. **Craft a rich diffusion prompt**:
   - Small casual user prompts (e.g. *"draw a cyber city"*) should be expanded with descriptive visual cues:
     - **Subject**: Specific character, object, or environment description.
     - **Style / Medium**: E.g. *"cinematic digital art"*, *"photorealistic 35mm photo"*, *"vibrant anime illustration"*, *"oil painting on canvas"*.
     - **Lighting**: E.g. *"golden hour sun rays"*, *"volumetric rim lighting"*, *"neon glowing reflections"*, *"dramatic chiaroscuro"*.
     - **Composition & Quality**: E.g. *"8k resolution, intricate details, wide angle shot, masterpiece"*.
2. **Select aspect ratio**:
   - `landscape` (default): Great for wallpapers, scenes, landscapes, vehicles, cityscapes.
   - `portrait`: Great for character portraits, full-body poses, mobile wallpapers.
   - `square`: Great for icons, avatars, album covers, profile pictures.
   - `wide`: Cinematic widescreen panoramas.
3. **Execute directly (do NOT delegate to code_agent)**:
   ```python
   generate_image(
       prompt="masterpiece, best quality, a futuristic cybernetic cat sitting on a glowing neon rooftop, night city skyline, cinematic lighting, 8k, highly detailed",
       aspect_ratio="landscape",
       title="cyber_cat"
   )
   ```
4. **Deliver**:
   - The tool automatically registers the generated `.png` into the `artifacts/` folder and emits an auto-preview card in the chat.
   - Summarize the prompt used and mention that the image was rendered locally on their GPU.
