@echo off
REM ========================================================
REM Install MidiClipExporter.control.js into Bitwig Controller Scripts.
REM JS controller scripts do not need to be compiled, just copy the file.
REM ========================================================
chcp 65001 >nul
cd /d "%~dp0"

set "JS_SRC=%CD%\MidiClipExporter.control.js"
if not exist "%JS_SRC%" (
    echo [ERROR] Source script not found: %JS_SRC%
    pause
    exit /b 1
)

set "JS_DIR=%USERPROFILE%\Documents\Bitwig Studio\Controller Scripts\Generic"
if not exist "%JS_DIR%" (
    echo [INFO] Creating directory: %JS_DIR%
    mkdir "%JS_DIR%"
)

copy /Y "%JS_SRC%" "%JS_DIR%\MidiClipExporter.control.js" >nul
if errorlevel 1 (
    echo [ERROR] Copy failed. If Bitwig is running, remove the controller in Settings -^> Controllers and retry.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Installed to: %JS_DIR%\MidiClipExporter.control.js
echo.
echo In Bitwig:
echo   1) Settings -^> Controllers -^> Add Controller
echo   2) Hardware Vendor: Generic
echo   3) Hardware Product: MIDI Clip Exporter
echo   4) Open the Studio I/O Panel (top-right toolbar) and expand this
echo      controller to find "Export directory" and "Export to .mid".
echo.
echo Tip: after editing the .js file, click Reload in the Controller
echo      Script Console to hot-reload.
echo ============================================================
echo.
pause
