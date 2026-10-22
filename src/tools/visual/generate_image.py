"""Generate images locally via ComfyUI (issue #350 / local image generation)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from typing import Any, Dict, Optional

import httpx


from src.core.orchestration.context import await_with_progress_heartbeat, emit_node_progress
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import deliver_artifact, sanitize_basename, unique_filename

logger = setup_logger("GenerateImage")

DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"
DEFAULT_NEGATIVE_PROMPT = (
    "worst quality, low quality, normal quality, blurry, bad anatomy, deformed, "
    "disfigured, distorted, bad art, watermark, signature"
)

# Resolution presets based on aspect ratio and whether SDXL is used
SD15_RESOLUTIONS = {
    "square": (512, 512),
    "landscape": (768, 512),
    "portrait": (512, 768),
    "wide": (896, 512),
}

SDXL_RESOLUTIONS = {
    "square": (1024, 1024),
    "landscape": (1216, 832),
    "portrait": (832, 1216),
    "wide": (1344, 768),
}


class GenerateImageTool(BaseTool):
    name = "generate_image"
    description = (
        "Generate an image locally via ComfyUI on the local GPU. "
        "Call this tool directly yourself whenever the user asks for an image, "
        "photo, drawing, artwork, wallpaper, or visual scene. "
        "Do NOT delegate to code_agent — this is an atomic GPU rendering tool."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        prompt = truncate_label(str(params.get("prompt") or "image"), max_len=60)
        ar = str(params.get("aspect_ratio") or "landscape")
        return f'Generating local image ({ar}): "{prompt}"'

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Detailed descriptive prompt for the image. Include subject, style, "
                            "lighting, mood, background details, and aesthetic cues."
                        ),
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": (
                            "Elements to exclude from the image. Defaults to standard quality/anatomy filters."
                        ),
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "enum": ["square", "landscape", "portrait", "wide"],
                        "description": "Aspect ratio preset. Default is 'landscape'.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Sampling steps (typically 20-30 for standard models, 4-8 for turbo/lightning). Default 20.",
                    },
                    "cfg": {
                        "type": "number",
                        "description": "Classifier-free guidance scale. Default 7.0 (use 1.5-2.0 for Lightning/Turbo).",
                    },
                    "checkpoint": {
                        "type": "string",
                        "description": "Optional specific model checkpoint name. If omitted, uses the best available local model.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for the output filename (slug-friendly).",
                    },
                },
                "required": ["prompt"],
            },
        }

    def _get_base_url(self) -> str:
        configured = ConfigManager.get("visual.comfyui_url") or DEFAULT_COMFYUI_URL
        return str(configured).rstrip("/")

    async def _resolve_checkpoint(self, client: httpx.AsyncClient, base_url: str, requested: Optional[str] = None) -> str:
        """Resolve the checkpoint model to use from ComfyUI's installed models."""
        try:
            resp = await client.get(f"{base_url}/object_info/CheckpointLoaderSimple", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                ckpts = (
                    data.get("CheckpointLoaderSimple", {})
                    .get("input", {})
                    .get("required", {})
                    .get("ckpt_name", [[]])[0]
                )
                if requested and requested in ckpts:
                    return requested

                # Prefer DreamShaper, SDXL, or first available non-video checkpoint
                for ckpt in ckpts:
                    name_lower = ckpt.lower()
                    if "dreamshaper" in name_lower or "sdxl" in name_lower or "juggernaut" in name_lower:
                        return ckpt
                for ckpt in ckpts:
                    if not any(v in ckpt.lower() for v in ["ltx", "svd", "video"]):
                        return ckpt
                if ckpts:
                    return ckpts[0]
        except Exception as exc:
            logger.warning(f"Failed to query ComfyUI checkpoints: {exc}")

        return requested or "DreamShaper_8_pruned.safetensors"

    _start_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_comfyui_running(self, client: httpx.AsyncClient, base_url: str) -> bool:
        """Verify ComfyUI is responding, or start it just-in-time if local paths are present."""
        try:
            resp = await client.get(f"{base_url}/system_stats", timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:
            pass

        if not ConfigManager.get("visual.comfyui_autostart", True):
            return False

        # Only attempt auto-launch for local loopback URLs
        if not any(h in base_url for h in ["127.0.0.1", "localhost"]):
            return False

        async with self._start_lock:
            # Double check in case another concurrent task started it while waiting on lock
            try:
                resp = await client.get(f"{base_url}/system_stats", timeout=1.5)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass

            python_bin = ConfigManager.get("visual.comfyui_python")
            main_py = ConfigManager.get("visual.comfyui_main")
            base_dir = ConfigManager.get("visual.comfyui_base_dir")

            user_profile = os.environ.get("USERPROFILE", "")
            local_appdata = os.environ.get("LOCALAPPDATA", "")

            if not python_bin and user_profile:
                candidate = Path(user_profile) / "Documents" / "ComfyUI" / ".venv" / "Scripts" / "python.exe"
                if candidate.exists():
                    python_bin = str(candidate)
            if not main_py and local_appdata:
                candidate = Path(local_appdata) / "Programs" / "ComfyUI" / "resources" / "ComfyUI" / "main.py"
                if candidate.exists():
                    main_py = str(candidate)
            if not base_dir and user_profile:
                candidate = Path(user_profile) / "Documents" / "ComfyUI"
                if candidate.exists():
                    base_dir = str(candidate)

            if not (python_bin and Path(python_bin).exists() and main_py and Path(main_py).exists()):
                logger.warning(
                    f"[ COMFYUI ] Auto-start skipped: could not locate ComfyUI executable "
                    f"(python={python_bin}, main={main_py})"
                )
                return False

            logger.info(f"[ COMFYUI ] Starting ComfyUI JIT daemon ({python_bin} {main_py})...")
            log_dir = Path("data/dev_logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "comfyui.log"

            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)

            run_cwd = str(base_dir or Path(main_py).parent)
            with open(log_file, "a", encoding="utf-8") as out:
                subprocess.Popen(
                    [
                        str(python_bin),
                        str(main_py),
                        "--base-directory", run_cwd,
                        "--listen", "127.0.0.1",
                        "--port", "8188",
                        "--disable-smart-memory",
                    ],
                    cwd=run_cwd,
                    creationflags=creationflags,
                    stdout=out,
                    stderr=out,
                )

            # Poll until ready (up to 30s)
            for i in range(30):
                await asyncio.sleep(1.0)
                try:
                    resp = await client.get(f"{base_url}/system_stats", timeout=2.0)
                    if resp.status_code == 200:
                        logger.info(f"[ COMFYUI ] ComfyUI ready after {i+1}s.")
                        return True
                except Exception:
                    pass

            logger.error("[ COMFYUI ] Timed out waiting for ComfyUI to become ready.")
            return False

    async def execute(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        aspect_ratio: str = "landscape",
        steps: int = 20,
        cfg: float = 7.0,
        checkpoint: Optional[str] = None,
        title: str = "image",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return {"success": False, "error": "prompt is required."}

        negative_prompt = (negative_prompt or DEFAULT_NEGATIVE_PROMPT).strip()
        base_url = self._get_base_url()
        title = sanitize_basename(title or "image")

        async with httpx.AsyncClient(timeout=120.0) as client:
            # 1. Health, auto-start & model discovery
            is_ready = await self._ensure_comfyui_running(client, base_url)
            if not is_ready:
                return {
                    "success": False,
                    "error": (
                        f"Could not connect to ComfyUI at {base_url} and JIT auto-start was unsuccessful. "
                        "Please verify ComfyUI is installed or running."
                    ),
                }

            ckpt_name = await self._resolve_checkpoint(client, base_url, checkpoint)
            is_sdxl = any(s in ckpt_name.lower() for s in ["xl", "flux", "sd3"])

            # 2. Pick dimensions & sanitize parameters
            resolutions = SDXL_RESOLUTIONS if is_sdxl else SD15_RESOLUTIONS
            width, height = resolutions.get(aspect_ratio.lower(), resolutions["landscape"])

            try:
                steps_int = int(steps) if steps is not None else 20
            except (ValueError, TypeError):
                steps_int = 20
            try:
                cfg_float = float(cfg) if cfg is not None else 7.0
            except (ValueError, TypeError):
                cfg_float = 7.0

            # 3. Build standard txt2img workflow graph
            seed = secrets.randbelow(1_000_000_000)
            workflow = {
                "3": {
                    "inputs": {
                        "seed": int(seed),
                        "steps": int(steps_int),
                        "cfg": float(cfg_float),
                        "sampler_name": "dpmpp_2m",
                        "scheduler": "karras",
                        "denoise": 1.0,
                        "model": ["4", 0],
                        "positive": ["6", 0],
                        "negative": ["7", 0],
                        "latent_image": ["5", 0],
                    },
                    "class_type": "KSampler",
                },
                "4": {
                    "inputs": {
                        "ckpt_name": ckpt_name,
                    },
                    "class_type": "CheckpointLoaderSimple",
                },
                "5": {
                    "inputs": {
                        "width": width,
                        "height": height,
                        "batch_size": 1,
                    },
                    "class_type": "EmptyLatentImage",
                },
                "6": {
                    "inputs": {
                        "text": prompt,
                        "clip": ["4", 1],
                    },
                    "class_type": "CLIPTextEncode",
                },
                "7": {
                    "inputs": {
                        "text": negative_prompt,
                        "clip": ["4", 1],
                    },
                    "class_type": "CLIPTextEncode",
                },
                "8": {
                    "inputs": {
                        "samples": ["3", 0],
                        "vae": ["4", 2],
                    },
                    "class_type": "VAEDecode",
                },
                "9": {
                    "inputs": {
                        "filename_prefix": "Chad_Gen",
                        "images": ["8", 0],
                    },
                    "class_type": "SaveImage",
                },
            }

            # 4. Submit prompt
            t0 = time.time()
            emit_node_progress()
            try:
                prompt_resp = await client.post(
                    f"{base_url}/prompt",
                    json={"prompt": workflow},
                )
                if prompt_resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"ComfyUI /prompt error: {prompt_resp.text}",
                    }
                prompt_data = prompt_resp.json()
                prompt_id = prompt_data.get("prompt_id")
                if not prompt_id:
                    return {"success": False, "error": "No prompt_id returned by ComfyUI."}
            except Exception as exc:
                return {"success": False, "error": f"Failed to submit prompt to ComfyUI: {exc}"}

            # 5. Poll for completion
            image_info = None
            for _ in range(120):  # up to 120 seconds
                await asyncio.sleep(1.0)
                try:
                    hist_resp = await client.get(f"{base_url}/history/{prompt_id}")
                    if hist_resp.status_code == 200:
                        hist_data = hist_resp.json()
                        if prompt_id in hist_data:
                            outputs = hist_data[prompt_id].get("outputs", {})
                            images = outputs.get("9", {}).get("images", [])
                            if images:
                                image_info = images[0]
                                break
                except Exception:
                    pass

            if not image_info:
                return {
                    "success": False,
                    "error": "Timed out waiting for ComfyUI image generation.",
                }

            # 6. Fetch the generated image bytes
            filename = image_info.get("filename")
            subfolder = image_info.get("subfolder", "")
            img_type = image_info.get("type", "output")
            view_url = f"{base_url}/view?filename={filename}&subfolder={subfolder}&type={img_type}"

            view_resp = await client.get(view_url)
            if view_resp.status_code != 200:
                return {
                    "success": False,
                    "error": f"Failed to download generated image from ComfyUI ({view_resp.status_code}).",
                }

            image_bytes = view_resp.content
            dur = time.time() - t0

            # 7. Deliver artifact
            dest_filename = unique_filename(title, "png")
            artifact = deliver_artifact(
                image_bytes,
                dest_filename,
                caption=f"Generated local image: {prompt[:80]}",
                auto_preview=False,
            )

            return {
                "success": True,
                "filename": dest_filename,
                "path": artifact.get("path"),
                "download_token": artifact.get("download_token"),
                "auto_preview": False,
                "model": ckpt_name,
                "resolution": f"{width}x{height}",
                "aspect_ratio": aspect_ratio,
                "render_time_sec": round(dur, 2),
                "prompt": prompt,
            }
