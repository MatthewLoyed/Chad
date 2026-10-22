' Chad Dev - hidden launcher for the full dev stack (Vite + dev Brain + dev GUI).
'
' Runs launch_dev.ps1 with no visible console window, so double-clicking the
' "Chad Dev" desktop shortcut behaves like a real app launch (same pattern as
' the prod ChadBrain/Chad shortcuts). Re-running it (re-clicking the shortcut)
' stops the previous dev stack first and relaunches with whatever code is on
' disk now - that's the "update" action for dev.

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
launcher = scriptDir & "\launch_dev.ps1"

Set sh = CreateObject("WScript.Shell")
' 0 = hidden window; False = don't wait (the dev GUI window appears on its own).
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & launcher & """", 0, False
