@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "UV_CACHE_DIR=%~dp0.uv-cache"
echo [1/3] Checking uv...

powershell -NoProfile -Command "try { $client = [System.Net.Sockets.TcpClient]::new('127.0.0.1', 8765); $client.Close(); exit 1 } catch { exit 0 }"
if errorlevel 1 (
    echo Port 8765 is already in use. Close the previous poker server, then run start.bat again.
    goto failed
)

where uv >nul 2>nul
if not errorlevel 1 (
    set "UV=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV=%USERPROFILE%\.local\bin\uv.exe"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
) else (
    echo [1/3] Installing uv...
    set "UV_INSTALL_DIR=%USERPROFILE%\.local\bin"
    where powershell >nul 2>nul
    if errorlevel 1 goto no_powershell
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if errorlevel 1 goto failed
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "UV=%USERPROFILE%\.local\bin\uv.exe"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
    ) else (
        echo uv was installed but uv.exe could not be found.
        goto failed
    )
)

echo [2/3] Preparing Python 3.12 and downloading dependencies...
set "VENV_DIR=%~dp0.venv"
if exist "%VENV_DIR%\Scripts\python.exe" (
    "%VENV_DIR%\Scripts\python.exe" -c "import aiohttp" >nul 2>nul
    if errorlevel 1 (
        echo Existing .venv cannot load dependencies. Preparing a separate environment...
        set "VENV_DIR=%~dp0.venv-%USERNAME%"
    )
)
set "UV_PROJECT_ENVIRONMENT=%VENV_DIR%"
call "%UV%" sync --locked
if errorlevel 1 goto failed
"%VENV_DIR%\Scripts\python.exe" -c "import aiohttp" >nul 2>nul
if errorlevel 1 (
    echo The Python environment could not load aiohttp.
    goto failed
)

echo [3/3] Starting poker server: http://localhost:8765/
"%VENV_DIR%\Scripts\python.exe" "%~dp0server.py"
if errorlevel 1 goto failed
exit /b 0

:no_powershell
echo PowerShell is required to install uv automatically.
:failed
echo Startup failed. See the error above.
pause
exit /b 1
