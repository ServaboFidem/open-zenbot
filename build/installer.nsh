; Uninstall removes the runtime the app downloaded at first launch (Python, its packages, Node.js, uv's cache) from
; its user data folder, and nothing else: the user's settings, logs and their own files stay. Electron names the
; user data folder after productName, so it is $APPDATA\OpenZenBot. An update runs the old uninstaller too; it
; keeps the runtime then, so the new version's setup starts from what is already downloaded.
!macro customUnInstall
  ; the wake-up tasks the briefing keeper made (OpenZenBot briefing wake 1 to 7); no network, errors ignored
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 1" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 2" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 3" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 4" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 5" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 6" /F'
  Pop $0
  nsExec::Exec '"$SYSDIR\schtasks.exe" /Delete /TN "OpenZenBot briefing wake 7" /F'
  Pop $0
  ${ifNot} ${isUpdated}
    RMDir /r "$APPDATA\OpenZenBot\runtime"
  ${endIf}
!macroend
