; Uninstall removes the runtime the app downloaded at first launch (Python, its packages, Node.js, uv's cache) from
; its user data folder, and nothing else: the user's settings, logs and their own files stay. Electron names the
; user data folder after productName, so it is $APPDATA\OpenZenBot. An update runs the old uninstaller too; it
; keeps the runtime then, so the new version's setup starts from what is already downloaded.
!macro customUnInstall
  ${ifNot} ${isUpdated}
    RMDir /r "$APPDATA\OpenZenBot\runtime"
  ${endIf}
!macroend
