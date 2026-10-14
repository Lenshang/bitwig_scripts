@echo off
rem bwclip2midi 拖放转换脚本
rem 把 .bwclip 文件（或包含它们的文件夹）拖到本脚本上即可转换为 .mid
setlocal
set PYTHON=python
where %PYTHON% >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3 并加入 PATH。
    pause
    exit /b 1
)
if "%~1"=="" (
    echo 用法: 把 .bwclip 文件或文件夹拖放到本脚本上。
    pause
    exit /b 0
)
set SCRIPT_DIR=%~dp0
%PYTHON% "%SCRIPT_DIR%bwclip2midi.py" %*
echo.
pause
