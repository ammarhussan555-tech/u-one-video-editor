@echo off
REM =====================================================================
REM  U One - AI Automatic Video Editor - ONE-CLICK installer builder
REM
REM  Double-click this file on any Windows 10/11 PC with internet.
REM  ZERO prerequisites: if Python or Inno Setup are missing they are
REM  installed automatically for the current user (no admin rights).
REM
REM  Produces:
REM      Output\U_One_Setup.exe      <- the finished consumer installer
REM      Output\U_One_Portable.zip   <- portable build (no installer)
REM
REM  The installer bundles Python, all dependencies, FFmpeg/FFprobe and
REM  every asset - end users never install Python, pip, FFmpeg or anything.
REM
REM  Written in plain linear batch (goto flow, no nested blocks) so it is
REM  safe under Windows CMD even with spaces in the path, e.g.
REM      C:\Users\Gillani Computers\Downloads\universal-ai-video-editor
REM =====================================================================
cd /d "%~dp0"
set ROOT=%CD%
set BUILDW=%ROOT%\build\windows
set OUTDIR=%ROOT%\Output

echo.
echo  ============================================================
echo   U One - AI Automatic Video Editor - installer builder
echo  ============================================================

echo.
echo  [0/7] Checking for Python 3.10+ ...
set PYCMD=
set _V=
where py >nul 2>&1
if errorlevel 1 goto pycmd
for /f "tokens=2" %%v in ('py -3 --version 2^>^&1') do set _V=%%v
if "%_V%"=="" goto pycmd
for /f "tokens=1,2 delims=." %%a in ("%_V%") do set _MAJ=%%a& set _MIN=%%b
if "%_MAJ%"=="" goto pycmd
if %_MAJ% LSS 3 goto pycmd
if %_MAJ%==3 if %_MIN% LSS 10 goto pycmd
set PYCMD=py -3
goto pyok

:pycmd
set _V=
where python >nul 2>&1
if errorlevel 1 goto pyinstall
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set _V=%%v
if "%_V%"=="" goto pyinstall
for /f "tokens=1,2 delims=." %%a in ("%_V%") do set _MAJ=%%a& set _MIN=%%b
if "%_MAJ%"=="" goto pyinstall
if %_MAJ% LSS 3 goto pyinstall
if %_MAJ%==3 if %_MIN% LSS 10 goto pyinstall
set PYCMD=python
goto pyok

:pyinstall
echo        Python 3.10+ not found - installing Python 3.11 for the
echo        current user (no admin needed)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $env:TEMP\uone-python-setup.exe"
if not exist "%TEMP%\uone-python-setup.exe" goto pyerr
"%TEMP%\uone-python-setup.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_test=0
del "%TEMP%\uone-python-setup.exe" 2>nul
if not exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" goto pyerr
set PYCMD="%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
echo        Python 3.11 installed.
goto pyok

:pyerr
echo        ERROR: Python could not be installed - check internet and re-run.
pause
exit /b 1

:pyok
echo        Using Python: %PYCMD%
%PYCMD% --version

echo.
echo  [1/7] Creating virtual environment ...
set VPY=%BUILDW%\venv\Scripts\python.exe
if not exist "%VPY%" %PYCMD% -m venv "%BUILDW%\venv"
if not exist "%VPY%" goto venverr
"%VPY%" -m pip install --upgrade pip --quiet
goto step2

:venverr
echo        ERROR: could not create the virtual environment.
pause
exit /b 1

:step2
echo.
echo  [2/7] Installing dependencies (one-time, a few minutes) ...
"%VPY%" -m pip install --quiet -r "%ROOT%\requirements.txt"
if errorlevel 1 goto piperr
"%VPY%" -m pip install --quiet pyinstaller
if errorlevel 1 goto piperr
goto step3

:piperr
echo        ERROR: dependency installation failed - see messages above.
pause
exit /b 1

:step3
echo.
echo  [3/7] Fetching FFmpeg Windows binaries (bundled into the app) ...
set FFDIR=%BUILDW%\ffmpeg\bin
if exist "%FFDIR%\ffmpeg.exe" goto ffmpegok
mkdir "%FFDIR%" 2>nul
echo        Downloading FFmpeg release build ...
call :fetch_ffmpeg
:ffmpegok
if not exist "%FFDIR%\ffmpeg.exe" goto ffmpegerr
"%FFDIR%\ffmpeg.exe" -hide_banner -version 2>nul | findstr "ffmpeg version"
goto step4

:ffmpegerr
echo        ERROR: could not obtain ffmpeg.exe - check internet and re-run.
pause
exit /b 1

:step4
echo.
echo  [4/7] Verifying build configuration ...
if not exist "%BUILDW%\u_one.spec" goto nospec
echo        Spec in use: %BUILDW%\u_one.spec
findstr "name=" "%BUILDW%\u_one.spec" | findstr /c:"U One" >nul
if errorlevel 1 goto badspec
echo        Target application: U One - AI Automatic Video Editor
echo        Building U One.exe with PyInstaller (a few minutes) ...
if exist "%ROOT%\dist" rmdir /s /q "%ROOT%\dist"
"%VPY%" -m PyInstaller --noconfirm --clean "%BUILDW%\u_one.spec"
if not exist "%ROOT%\dist\U One\U One.exe" goto badexe
echo        OK: %ROOT%\dist\U One\U One.exe
goto step5

:nospec
echo        ERROR: %BUILDW%\u_one.spec is missing.
pause
exit /b 1

:badspec
echo        ERROR: the spec file does not target the "U One" application name.
pause
exit /b 1

:badexe
echo        ERROR: PyInstaller did not produce dist\U One\U One.exe.
if exist "%ROOT%\dist" dir /b "%ROOT%\dist"
pause
exit /b 1

:step5
echo.
echo  [5/7] Building portable ZIP ...
if not exist "%OUTDIR%" mkdir "%OUTDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%ROOT%\dist\U One\*' -DestinationPath '%OUTDIR%\U_One_Portable.zip' -Force"
if not exist "%OUTDIR%\U_One_Portable.zip" goto ziperr
echo        OK: %OUTDIR%\U_One_Portable.zip
goto step6

:ziperr
echo        ERROR: portable ZIP was not created.
pause
exit /b 1

:step6
echo.
echo  [6/7] Checking for Inno Setup 6 ...
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if defined ISCC goto isccok
where iscc >nul 2>&1
if errorlevel 1 goto innoinstall
for /f "delims=" %%i in ('where iscc') do set "ISCC=%%i"
goto isccok

:innoinstall
echo        Inno Setup 6 not found - installing for the current user ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://jrsoftware.org/download.php/is.exe' -OutFile $env:TEMP\uone-inno-setup.exe"
if not exist "%TEMP%\uone-inno-setup.exe" goto innoerr
"%TEMP%\uone-inno-setup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="%LOCALAPPDATA%\Programs\Inno Setup 6"
del "%TEMP%\uone-inno-setup.exe" 2>nul
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC goto innoerr
goto isccok

:innoerr
echo        ERROR: Inno Setup 6 could not be installed.
echo        Install it manually from https://jrsoftware.org/isdl.php and re-run.
pause
exit /b 1

:isccok
echo        Using: %ISCC%

echo.
echo  [7/7] Compiling the installer ...
"%ISCC%" "%ROOT%\installer\u_one.iss"
if not exist "%OUTDIR%\U_One_Setup.exe" goto isserr
goto done

:isserr
echo        ERROR: Inno Setup did not produce Output\U_One_Setup.exe.
pause
exit /b 1

:done
echo.
echo  ============================================================
echo   BUILD COMPLETE - verified:
echo     %OUTDIR%\U_One_Setup.exe
echo     %OUTDIR%\U_One_Portable.zip
echo.
echo   Install U_One_Setup.exe with a double-click, then validate with:
echo     powershell -ExecutionPolicy Bypass -File build\windows\validate_install.ps1
echo  ============================================================
pause
goto :eof

:fetch_ffmpeg
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$u='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip';" ^
  "$z=\"$env:TEMP\ffmpeg.zip\";" ^
  "Invoke-WebRequest -Uri $u -OutFile $z;" ^
  "Expand-Archive -Path $z -DestinationPath \"$env:TEMP\ffx\" -Force;" ^
  "$d=Get-ChildItem \"$env:TEMP\ffx\" -Directory | Select-Object -First 1;" ^
  "Copy-Item \"$($d.FullName)\bin\ffmpeg.exe\" '%FFDIR%\ffmpeg.exe' -Force;" ^
  "Copy-Item \"$($d.FullName)\bin\ffprobe.exe\" '%FFDIR%\ffprobe.exe' -Force;" ^
  "Remove-Item $z -Force; Remove-Item \"$env:TEMP\ffx\" -Recurse -Force"
goto :eof
