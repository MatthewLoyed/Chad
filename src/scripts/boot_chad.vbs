Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 1. Dynamically determine project root (parent of the scripts folder)
scriptPath = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptPath)
WshShell.CurrentDirectory = projectRoot

' 2. Kill any zombie Chrome instances to ensure the port opens
WshShell.Run "taskkill /F /IM chrome.exe /T", 0, True

' 3. Launch Chrome with the Neural Link (Debugging Port)
' We use %PROGRAMFILES% and relative path for user-data-dir
chromePath = WshShell.ExpandEnvironmentStrings("%PROGRAMFILES%") & "\Google\Chrome\Application\chrome.exe"
userDataDir = projectRoot & "\chad_browser_data"

WshShell.Run """" & chromePath & """ --remote-debugging-port=9222 --user-data-dir=""" & userDataDir & """", 1, False

' 4. Wait 5 seconds for Chrome to stabilize
WScript.Sleep 5000

' 5. Launch CHAD Main GUI
' We use pythonw.exe to hide the console window. 
WshShell.Run "pythonw.exe main_gui.py", 0, False