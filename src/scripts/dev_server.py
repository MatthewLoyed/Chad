"""
Chad Dev Server - Fast Iteration Mode
Orchestrates: 
1. Ngrok Tunnel (External public URL)
2. Telegram Webhook sync (Points bot to tunnel)
3. Uvicorn Reload (Hot-reloading local server)

Usage:
    python scripts/dev_server.py
"""

import os
import sys
import asyncio
import subprocess
import signal
import time
from pathlib import Path
from pyngrok import ngrok, conf

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from scripts.setup_telegram_webhook import setup_webhook

logger = setup_logger("DevServer")

def get_ngrok_token():
    """Get ngrok token from env or prompt user."""
    token = os.getenv("NGROK_AUTHTOKEN") or ConfigManager.get_secret("NGROK_AUTHTOKEN")
    if not token:
        logger.warning("\n" + "="*50)
        logger.warning("NGROK AUTH TOKEN MISSING")
        logger.warning("Get it from: https://dashboard.ngrok.com/get-started/your-authtoken")
        logger.warning("="*50 + "\n")
        token = input("Enter your ngrok authtoken: ").strip()
        if token:
            # We don't save it automatically to avoid editing secrets.yaml directly
            # but we use it for this session.
            os.environ["NGROK_AUTHTOKEN"] = token
    return token

async def run_dev_cycle(quiet: bool = False):
    """Main development orchestration loop."""
    token = get_ngrok_token()
    if not token:
        logger.error("No ngrok token provided. Exiting.")
        return

    # Set log level for subprocesses
    env = os.environ.copy()
    if quiet:
        env["CHAD_LOG_LEVEL"] = "WARNING"
        log_level = "warning"
    else:
        log_level = "info"

    # 1. Setup Ngrok
    logger.info("[ TUNNEL ] : Starting ngrok tunnel on port 8767...")
    conf.get_default().auth_token = token
    
    try:
        # Connect tunnel
        public_url = ngrok.connect(8767).public_url
        logger.warning(f"[ TUNNEL ] : Live at {public_url}")
        
        # 2. Update Telegram Webhook
        telegram_webhook_url = f"{public_url}/webhooks/telegram"
        logger.info(f"[ WEBHOOK ] : Updating Telegram to {telegram_webhook_url}...")
        
        success = await setup_webhook(telegram_webhook_url)
        if not success:
            logger.error("[ WEBHOOK ] : Failed to update Telegram webhook. Tunnel might still be open.")
        
        # 3. Start Uvicorn in background
        logger.warning(f"[ SERVER ] : Starting local Brain with --reload (level={log_level})...")
        
        # We use subprocess.Popen so we can manage the lifecycle
        # PYTHONPATH=. ensures local modules are found
        env["PYTHONPATH"] = str(project_root)
        
        # Run uvicorn
        cmd = [
            "uvicorn", 
            "src.remote.server:app", 
            "--host", "0.0.0.0", 
            "--port", "8767", 
            "--reload",
            "--log-level", log_level
        ]
        
        process = subprocess.Popen(
            cmd,
            env=env,
            shell=True if os.name == 'nt' else False
        )

        logger.warning("\n" + "*" * 15)
        logger.warning("FAST ITERATION MODE ACTIVE")
        logger.warning(f"Tunnel: {public_url}")
        logger.warning(f"Gmail Webhook: {public_url}/webhooks/gmail")
        logger.warning("Server: Localhost:8767 (Hot-reload enabled)")
        logger.warning("Press Ctrl+C to stop all services")
        logger.warning("*" * 15 + "\n")

        # Keep alive and handle signals
        try:
            while process.poll() is None:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
            
    except Exception as e:
        logger.error(f"[ ERROR ] : Dev cycle failed: {e}", exc_info=True)
    finally:
        if 'process' in locals() and process.poll() is None:
            logger.warning("[ SHUTDOWN ] : Terminating uvicorn process tree...")
            if os.name == 'nt':
                subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                    capture_output=True,
                )
            else:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        logger.warning("[ SHUTDOWN ] : Cleaning up ngrok tunnels...")
        ngrok.kill()
        logger.info("[ SHUTDOWN ] : Done.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Chad Dev Server")
    parser.add_argument("--quiet", action="store_true", help="Only show WARNING and ERROR logs")
    args = parser.parse_args()

    try:
        asyncio.run(run_dev_cycle(quiet=args.quiet))
    except KeyboardInterrupt:
        pass
