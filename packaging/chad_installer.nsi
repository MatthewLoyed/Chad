; Chad Windows installer (issue #429).
;
; Wraps the staged payload from scripts\prod\stage_installer_payload.ps1
; (dist\installer_payload\Chad, Chad-Brain, config, .env.example,
; CLAUDE.md.example) into a single branded NSIS installer .exe.
;
; Per-user install (HKCU, no admin) to %LOCALAPPDATA%\Chad, mirroring
; scripts\prod\install_prod.ps1's existing shortcut / uninstall-registry
; scheme so both installers feel like the same app to Windows.
;
; Build: makensis /DVERSION=x.y.z /DPAYLOAD_DIR=<abs path> packaging\chad_installer.nsi
; (scripts\prod\build_installer.ps1 drives this end-to-end.)

!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "0.0.0-dev"
!endif
!ifndef PAYLOAD_DIR
  !define PAYLOAD_DIR "..\dist\installer_payload"
!endif
!ifndef OUT_FILE
  !define OUT_FILE "..\dist\Chad-Setup-${VERSION}.exe"
!endif

!define APP_NAME "Chad"
!define APP_ICON "..\scripts\prod\chad.ico"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Name "${APP_NAME}"
OutFile "${OUT_FILE}"
InstallDir "$LOCALAPPDATA\${APP_NAME}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma

; --- Branding ---------------------------------------------------------
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Welcome to the Chad Setup Wizard"
!define MUI_WELCOMEPAGE_TEXT "This installs Chad — a personal AI assistant with local-first memory and tools — to your user account. No admin rights needed.$\r$\n$\r$\nClick Next to continue."
!define MUI_FINISHPAGE_RUN "$INSTDIR\Chad\Chad.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Launch Chad now"
!define MUI_FINISHPAGE_LINK "Chad on GitHub"
!define MUI_FINISHPAGE_LINK_LOCATION "https://github.com/MatthewLoyed/AIAssistant"

; --- Pages --------------------------------------------------------------
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; --- Install ------------------------------------------------------------
Section "Chad (required)" SecCore
  SectionIn RO
  SetOutPath "$INSTDIR"

  ; App payloads (each is a PyInstaller one-folder build)
  File /r "${PAYLOAD_DIR}\Chad"
  File /r "${PAYLOAD_DIR}\Chad-Brain"

  ; Config surface CHAD_HOME needs (config/, root .example files) — no
  ; secrets.yaml / preferences.yaml here, those are gitignored and instantiated
  ; on first launch by bootstrap_config_if_needed() / the setup wizard (#428).
  File /r "${PAYLOAD_DIR}\config"
  File /r "${PAYLOAD_DIR}\prompts"
  File /r "${PAYLOAD_DIR}\src"
  File /r "${PAYLOAD_DIR}\scripts"
  File "${PAYLOAD_DIR}\LICENSE"
  File "${PAYLOAD_DIR}\.env.example"
  File "${PAYLOAD_DIR}\CLAUDE.md.example"
  CreateDirectory "$INSTDIR\data"

  ; Point each frozen app's CHAD_HOME at the real install dir, not the
  ; build machine's repo path (stripped by stage_installer_payload.ps1).
  FileOpen $0 "$INSTDIR\Chad\chad_home.txt" w
  FileWrite $0 "$INSTDIR"
  FileClose $0
  FileOpen $0 "$INSTDIR\Chad-Brain\chad_home.txt" w
  FileWrite $0 "$INSTDIR"
  FileClose $0

  ; Shortcuts
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\Chad\Chad.exe" "" "$INSTDIR\Chad\Chad.exe"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\Chad\Chad.exe" "" "$INSTDIR\Chad\Chad.exe"

  ; Add/Remove Programs entry
  WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "Matthew Loyed"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\Chad\Chad.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\Uninstall.exe$\""
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1

  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd

Section /o "Start Chad when I sign in to Windows" SecAutostart
  CreateDirectory "$SMSTARTUP"
  CreateShortCut "$SMSTARTUP\Chad-Brain.lnk" "$INSTDIR\Chad-Brain\Chad-Brain.exe" "" "$INSTDIR\Chad-Brain\Chad-Brain.exe" "" SW_SHOWMINIMIZED
SectionEnd

!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SecCore} "Install Chad on this computer."
  !insertmacro MUI_DESCRIPTION_TEXT ${SecAutostart} "Keep Chad ready in the background when you sign in to Windows. No window will open. Leave unchecked to start Chad only when you open it."
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; --- Uninstall ------------------------------------------------------------
; Deliberately leaves $INSTDIR\config (may hold a user-filled secrets.yaml
; with real API keys) and $INSTDIR\data (local chat history / memory sqlite)
; in place — only the app binaries, shortcuts, and registry entries are
; removed, so a re-install doesn't silently regenerate lost credentials/memory.
Section "Uninstall"
  Delete "$SMSTARTUP\Chad-Brain.lnk"
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"

  RMDir /r "$INSTDIR\Chad"
  RMDir /r "$INSTDIR\Chad-Brain"
  Delete "$INSTDIR\Uninstall.exe"

  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\${APP_NAME}"

  MessageBox MB_OK|MB_ICONINFORMATION "Chad has been uninstalled. Your config (including API keys) and local chat/memory data were kept at $INSTDIR — delete that folder manually if you want a completely clean removal."
SectionEnd
