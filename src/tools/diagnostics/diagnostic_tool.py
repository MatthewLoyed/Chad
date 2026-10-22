import asyncio
import logging
import httpx
from typing import Dict, Any, List, Optional
from src.tools.base_tool import BaseTool
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("DiagnosticTool")

class DiagnosticTool(BaseTool):
    """
    A tool for diagnosing the health of the AI Assistant's connections and models.
    Use this when tools fail due to connection errors, timeouts, or model unavailability.
    """
    
    name: str = "run_diagnostics"
    description: str = (
        "Run a comprehensive system health check. Checks local model connectivity (LM Studio), "
        "cloud model connectivity (Gemini), and validates current configuration. "
        "Use this as the FIRST step when encountering connection, timeout, or model errors."
    )
    category: str = "diagnostics"
    is_side_effecting: bool = False

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "verbose": {
                        "type": "boolean",
                        "description": "Whether to include detailed error logs if a check fails.",
                        "default": False
                    }
                }
            }
        }

    async def execute(self, verbose: bool = False, **kwargs) -> Dict[str, Any]:
        results = {
            "local_model_service": "UNCHECKED",
            "cloud_model_service": "UNCHECKED",
            "active_config": {},
            "issues": []
        }

        # 1. Check Local Model (LM Studio)
        local_cfg = ConfigManager.get_local_model_config()
        base_url = local_cfg.get('base_url', 'http://localhost:1234/v1')
        results["active_config"]["local_base_url"] = base_url
        
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                models_url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
                response = await client.get(models_url)
                
                if response.status_code == 200:
                    data = response.json()
                    model_ids = [m.get("id") for m in data.get("data", [])]
                    results["local_model_service"] = "HEALTHY"
                    results["available_local_models"] = model_ids
                    
                    target_model = local_cfg.get('name')
                    if target_model and target_model not in model_ids:
                        # Some local models have slug prefixes
                        found_match = False
                        for m_id in model_ids:
                            if target_model in m_id or m_id in target_model:
                                found_match = True
                                break
                        if not found_match:
                            results["issues"].append(f"Target local model '{target_model}' not found in LM Studio's active model list: {model_ids}")
                else:
                    results["local_model_service"] = f"ERROR ({response.status_code})"
                    results["issues"].append(f"LM Studio at {base_url} returned status {response.status_code}")
        except Exception as e:
            results["local_model_service"] = "DOWN"
            results["issues"].append(f"Could not reach LM Studio at {base_url}. Make sure LM Studio is running and the 'Local Server' is started.")
            if verbose:
                results["local_error_raw"] = str(e)

        # 2. Check Cloud Model (Gemini)
        gemini_api_key = ConfigManager.get_secret("GEMINI_API_KEY")
        if not gemini_api_key:
            results["cloud_model_service"] = "MISSING_KEY"
            results["issues"].append("GEMINI_API_KEY is not set in environment or secrets.")
        else:
            try:
                # Simple reachability test to Google's API surface
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get("https://generativelanguage.googleapis.com/")
                    if response.status_code in [200, 404]: # 404 is actually fine, it means we reached the service
                        results["cloud_model_service"] = "HEALTHY"
                    else:
                        results["cloud_model_service"] = f"DEGRADED ({response.status_code})"
                        results["issues"].append(f"Gemini API endpoint returned unexpected status: {response.status_code}")
            except Exception as e:
                results["cloud_model_service"] = "UNREACHABLE"
                results["issues"].append(f"Network error trying to reach Google Cloud API: {str(e)}")

        # 3. Mode Check
        is_local = ConfigManager.is_local_model_active()
        results["active_config"]["routing_mode"] = "LOCAL" if is_local else "CLOUD"
        
        # 4. Summary
        if not results["issues"]:
            status_msg = "All systems operational."
        else:
            status_msg = f"Found {len(results['issues'])} potential issues."

        return {
            "success": True,
            "status_summary": status_msg,
            "diagnostics": results,
            "message": "Diagnostics complete. Review the results to troubleshoot connection or model issues."
        }
