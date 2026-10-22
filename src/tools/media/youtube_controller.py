"""
YouTube Desktop Controller - Separate from System Volume
Controls YouTube in the user's browser via window targeting.
"""

import asyncio
import subprocess
try:
    import pygetwindow as gw
    HAS_GW = True
except ImportError:
    gw = None
    HAS_GW = False

from typing import Dict, Any, Optional

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("YTDesktopControl")

class YouTubeDesktopControlTool(BaseTool):
    """
    Dedicated tool for controlling YouTube playback in the user's browser.
    Sends keystrokes directly to windows titled 'YouTube'.
    """
    
    name = "youtube_desktop_control"
    description = (
        "EXCLUSIVE, MANDATORY tool for controlling YouTube playback (mute, max, volume, play/pause) in the user's browser. "
        "Use this for ANY request mentioning YouTube or Video volume. "
        "NEVER fallback to system volume tools for YouTube requests. "
        "Actions include 'maximize' for full volume and 'minimize' for mute."
    )
    category = "media"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mute", "unmute", "volume_up", "volume_down", "play_pause", "fullscreen", "maximize", "minimize"],
                        "description": "The media command to send to the YouTube window."
                    },
                    "window_hint": {
                        "type": "string",
                        "description": "Optional: Specific part of the window title to target."
                    }
                },
                "required": ["action"]
            }
        }
    
    async def execute(self, action: str, window_hint: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Execute the requested media action on a YouTube window."""
        action = action.strip().lower()
        
        try:
            if not HAS_GW:
                return {"success": False, "error": "pygetwindow not available on this platform."}

            # 1. Find target window - prioritize browser tabs over code/project files
            all_yt_windows = gw.getWindowsWithTitle("YouTube")
            
            # Filter Strategy: Exclude common editor/code signatures
            exclusion_keywords = [".py", ".md", ".json", ".git", "AIAssistant", "RadarSystem", "Controller"]
            browser_signatures = ["Chrome", "Edge", "Firefox", "Opera", "Brave"]
            
            refined_targets = []
            for w in all_yt_windows:
                if not w.visible:
                    continue
                
                title = w.title
                # Ignore obvious code files
                if any(k.lower() in title.lower() for k in exclusion_keywords):
                    # But if it's a code file, ONLY keep it if it's the only option and window_hint matches perfectly
                    if window_hint and window_hint.lower() in title.lower():
                        refined_targets.append(w)
                    continue
                
                refined_targets.append(w)

            # Sort: Prioritize windows with browser signatures
            refined_targets.sort(key=lambda w: any(s.lower() in w.title.lower() for s in browser_signatures), reverse=True)
            
            if window_hint:
                refined_targets = [w for w in refined_targets if window_hint.lower() in w.title.lower()]
            
            target_win = refined_targets[0] if refined_targets else None
            
            if not target_win:
                logger.warning(f"[ YT-DESKTOP ] : No suitable YouTube browser window found (Scanned {len(all_yt_windows)} total).")
                return {"success": False, "error": "No visible YouTube browser tab found."}

            logger.info(f"[ YT-DESKTOP ] : Action '{action}' targeting: '{target_win.title}'")

            # 2. Key mapping
            key_map = {
                "volume_down": "{DOWN}",
                "volume_up": "{UP}",
                "mute": "m",
                "unmute": "m",
                "play_pause": "k",
                "fullscreen": "f",
                "maximize": "{UP}",
                "minimize": "m"
            }
            
            key = key_map.get(action)
            if not key:
                return {"success": False, "error": f"Unsupported action: {action}"}

            # 3. Construct robust PowerShell Command (Single Line with retry loop)
            target_title = target_win.title.replace('"', '""') # PS double-quote escape
            
            actual_key = key
            if action == "maximize":
                actual_key = key * 20 # Hammer 'Up' to reach 100%
            elif "volume" in action:
                actual_key = key * 4
            
            # Add {ESC} before the key to clear any search bar focus if possible
            ps_cmd = (
                f'$wshell = New-Object -ComObject WScript.Shell; '
                f'$title = "{target_title}"; '
                f'$success = $false; '
                f'for ($i=0; $i -lt 3; $i++) {{ '
                f'  if ($wshell.AppActivate($title)) {{ '
                f'    Start-Sleep -m 200; '
                f'    $wshell.SendKeys("{{ESC}}"); ' # Clear YT search bar focus
                f'    Start-Sleep -m 50; '
                f'    $wshell.SendKeys("{actual_key}"); '
                f'    $success = $true; '
                f'    break; '
                f'  }} '
                f'  Start-Sleep -m 300; '
                f'}}; '
                f'if (-not $success) {{ Write-Host "ACTIVATE_FAILED" }}'
            )
            
            # Execute via Thread to avoid SelectorLoop issues
            def run_ps():
                return subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd], 
                    capture_output=True,
                    text=True
                )
                
            result = await asyncio.to_thread(run_ps)
            
            if "ACTIVATE_FAILED" in result.stdout:
                return {"success": False, "error": f"Could not focus YouTube window: {target_win.title}"}
                
            if result.returncode != 0:
                logger.error(f"YouTube Desktop failure: {result.stderr}")
                return {"success": False, "error": f"Media command failed: {result.stderr}"}
            
            return {
                "success": True, 
                "result": f"Sent '{action}' to YouTube: {target_win.title}",
                "caption": f"YT Desktop: {action.replace('_', ' ').title()}"
            }
            
        except Exception as e:
            logger.error(f"YouTube Desktop controller error: {e}")
            return {"success": False, "error": str(e)}
            
        except Exception as e:
            logger.error(f"YouTube Desktop controller error: {e}")
            return {"success": False, "error": str(e)}
