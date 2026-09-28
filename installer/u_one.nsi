; =====================================================================
; U One - AI Automatic Video Editor - NSIS installer script
;
; This script is the Linux-build path for U One. It is compiled with the
; Linux-native NSIS compiler (makensis):
;
;     cd installer && makensis u_one.nsi
;
; Produces:  Output\U_One_Setup.exe
;
; (On a real Windows PC, build_windows.bat uses installer\u_one.iss with
; Inno Setup instead - same app, same layout, same shortcuts.)
;
; The installer:
;   - installs U One for the current user (no admin rights required)
;   - creates a Start Menu shortcut and an optional Desktop shortcut
;   - registers an Uninstaller (Add/Remove Programs)
;   - bundles FFmpeg/FFprobe inside the app folder (no separate install)
; =====================================================================

!define APP_NAME "U One"
!define APP_VERSION "1.1.0"
!define APP_PUBLISHER "U One"
!define APP_EXE "U One.exe"
!define UNINSTALL_REG_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\Output\U_One_Setup.exe"
InstallDir "$LOCALAPPDATA\${APP_NAME}"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "FileDescription" "${APP_NAME} - AI Automatic Video Editor Setup"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "LegalCopyright" "Copyright © ${APP_PUBLISHER}"
VIAddVersionKey "FileVersion" "${APP_VERSION}"

!include "MUI2.nsh"

!define MUI_ICON "..\assets\u_one.ico"
!define MUI_UNICON "..\assets\u_one.ico"
!define MUI_WELCOMEPAGE_TITLE "Welcome to ${APP_NAME} Setup"
!define MUI_WELCOMEPAGE_TEXT "This wizard will install ${APP_NAME} ${APP_VERSION}$\r$\n$\r$\nNo Python, FFmpeg or other tools are needed - everything is bundled."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; --- Main application files (required) ---------------------------------
Section "!${APP_NAME} (required)" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"
  File /r "..\dist\U One\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; Start Menu shortcut
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
      "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk" \
      "$INSTDIR\Uninstall.exe"

  ; Add/Remove Programs entry (per-user)
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "UninstallString" \
      '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_REG_KEY}" "DisplayIcon" \
      "$INSTDIR\${APP_EXE}"
  WriteRegDWord HKCU "${UNINSTALL_REG_KEY}" "NoModify" 1
  WriteRegDWord HKCU "${UNINSTALL_REG_KEY}" "NoRepair" 1
SectionEnd

; --- Desktop shortcut (optional) ----------------------------------------
Section /o "Desktop shortcut" SEC_DESKTOP
  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" \
      "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}" 0
SectionEnd

; --- Uninstaller ----------------------------------------------------------
Section "Uninstall"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall.lnk"
  RMDir "$SMPROGRAMS\${APP_NAME}"

  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKCU "${UNINSTALL_REG_KEY}"
SectionEnd
