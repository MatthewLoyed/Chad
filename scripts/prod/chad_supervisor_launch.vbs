' Chad - hidden supervisor launcher (used by the Startup-folder autostart).
'
' Runs chad_supervisor.ps1 with no visible window so the always-on production
' Brain comes up silently at logon. The supervisor keeps the Brain alive and
' logs to data\prod_logs\.

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
supervisor = scriptDir & "\chad_supervisor.ps1"

Set sh = CreateObject("WScript.Shell")
' 0 = hidden window; False = don't wait.
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & supervisor & """", 0, False
