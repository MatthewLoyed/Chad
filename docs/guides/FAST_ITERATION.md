# 🚀 Fast Iteration Guide (Ngrok + Hot Reload)

This guide shows you how to iterate on Chad's Brain with **near-instant feedback**. No more pushing to Git or waiting for Render builds!

---

## 1. Setup (One-time)

1.  **Get an Ngrok Token**: 
    - Go to [dashboard.ngrok.com](https://dashboard.ngrok.com/get-started/your-authtoken).
    - Copy your **Your Authtoken**.
2.  **Add to Config**:
    - Open your `.env` file and paste the token:
      ```bash
      NGROK_AUTHTOKEN=your_token_here
      ```

---

## 2. Start Developing

To start the auto-reloading environment, run:

```powershell
python scripts/dev_server.py
```

### What this does:
1.  **Starts a Tunnel**: Creates a public URL (e.g., `https://xyz.ngrok-free.app`).
2.  **Updates Telegram**: Automatically tells your Telegram Bot to send messages to that URL.
3.  **Starts the Brain**: Runs `src/remote/server.py` with **hot-reload** enabled.

> [!TIP]
> **Try it out!** Change a line of code in `src/remote/server.py` or a tool, save the file, and watch the console. The server will restart instantly, and your Telegram bot will be ready to test immediately.

---

## 3. Testing with the GUI (Limb)

If you want to use the local GUI (`main_gui.py`) with your local Brain instead of Render, open a **second terminal** and run:

```powershell
$env:CHAD_MODE="cloud"
$env:CHAD_SERVER_URL="ws://localhost:8767/ws"
$env:PYTHONPATH="."
python main_gui.py
```

---

## 4. Going Back to Production

When you are finished developing and want to switch the bot back to your **Render** server:

1.  **Stop the dev script** (Ctrl+C).
2.  **Push your changes** to Git.
3.  Render will build and deploy naturally. 
4.  **Note**: The Telegram webhook will remain pointed at ngrok until you manually update it or Render starts up (if Render has auto-update logic). To manually reset to Render:
    ```powershell
    python scripts/setup_telegram_webhook.py --url https://your-app.onrender.com/webhooks/telegram
    ```

> [!IMPORTANT]
> Always remember to stop the `dev_server.py` when you're done to close the public tunnel!

---

## 5. Process cleanup

Hot-reload and MCP prewarm can leave orphaned `cmd.exe` / `node.exe` processes if you force-kill terminals. Run this at the end of each dev session:

```powershell
.\scripts\maintenance\kill_chad_zombies.ps1
```

See [PROCESS_CLEANUP.md](./PROCESS_CLEANUP.md) for when and why.
