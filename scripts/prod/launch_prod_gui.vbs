' Chad - windowless production GUI launcher.
'
' Launches the desktop GUI connected to the always-on production Brain (port 8767)
' with NO console window flash, so the desktop "Chad" shortcut behaves like a real
' app (e.g. the Claude desktop app). The production Brain itself runs 24/7 under the
' supervisor; this only opens the GUI client.
'
' Repo root is derived from this script's own location (scripts\prod\ -> two up),
' so it stays portable if the project folder moves.

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot  = fso.GetParentFolderName(fso.GetParentFolderName(scriptDir))

Set sh = CreateObject("WScript.Shell")
Set env = sh.Environment("Process")
env("CHAD_MODE")       = "cloud"
env("CHAD_SERVER_URL") = "ws://localhost:8767/ws"
env("PYTHONPATH")      = "."
' Load the built React bundle from dist/ (NOT the Vite dev server on :5173).
' Without this, CHAD_UI_DEV defaults to "true" and the window is blank in prod.
env("CHAD_UI_DEV")     = "false"

sh.CurrentDirectory = repoRoot
pythonw = """" & repoRoot & "\.venv\Scripts\pythonw.exe"""

' 0 = hidden window (pythonw has no console anyway); False = don't wait.
sh.Run pythonw & " main_gui.py", 0, False
