@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   Score Studio / Composer Chat
echo ==========================================
echo.

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] venv not found: %PY%
    echo Create it with:
    echo   py -3 -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Using: %PY%
echo Starting score_studio.py ...
echo.

"%PY%" "%~dp0score_studio.py"
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
    echo.
    echo [ERROR] Exit code %ERR%
    echo If packages are missing, run:
    echo   venv\Scripts\pip install -r requirements.txt
    pause
    exit /b %ERR%
)

endlocal
