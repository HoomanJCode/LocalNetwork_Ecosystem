@echo off
REM ===========================================================================
REM  LocalNetwork Ecosystem - Development Runner (Windows)
REM ===========================================================================
REM
REM  Usage:
REM    scripts\run_dev.bat [command]
REM
REM  Commands:
REM    setup       Create virtualenv, install deps + this package (editable)
REM    test        Run all tests
REM    server      Start the mediation server on localhost:54000
REM    client      Start a client daemon connected to localhost:54000
REM    cli         Run the management CLI (pass args after `--`)
REM    demo        Full demo: server + 2 clients in separate windows
REM    clean       Remove virtualenv and __pycache__ dirs
REM    help        Show this message
REM
REM  Examples:
REM    scripts\run_dev.bat setup
REM    scripts\run_dev.bat test
REM    scripts\run_dev.bat server
REM    scripts\run_dev.bat demo
REM    scripts\run_dev.bat cli -- create mynet --password secret
REM
REM ===========================================================================

setlocal
set ROOT=%~dp0..
cd /d "%ROOT%"

set VENV=%ROOT%\.venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe
set LNSERVER=%VENV%\Scripts\localnetwork-server.exe
set LNCLIENT=%VENV%\Scripts\localnetwork-client.exe
set LNCLI=%VENV%\Scripts\localnetwork-cli.exe

REM ---- helpers ------------------------------------------------------------

if "%1"=="" goto help
if "%1"=="help" goto help
if "%1"=="--help" goto help
if "%1"=="-h" goto help

if "%1"=="setup" goto setup
if "%1"=="test" goto test
if "%1"=="server" goto server
if "%1"=="client" goto client
if "%1"=="cli" goto cli
if "%1"=="demo" goto demo
if "%1"=="clean" goto clean

echo Unknown command: %1
echo Run "scripts\run_dev.bat help" for usage.
exit /b 1

:ensure_venv
if exist "%PYTHON%" goto :eof
echo [SETUP] Creating virtual environment...
python -m venv "%VENV%"
echo [SETUP] Installing dependencies + package (editable)...
"%PIP%" install --upgrade pip -q
"%PIP%" install -r requirements.txt -r requirements-dev.txt -q
"%PIP%" install -e . -q
echo [SETUP] Done.
echo          Console commands: localnetwork-server, localnetwork-client, localnetwork-cli
goto :eof

:setup
if exist "%PYTHON%" (
    echo Virtual environment already exists at .venv
    echo Run "scripts\run_dev.bat clean" first to reinstall.
    exit /b 0
)
call :ensure_venv
echo.
echo Setup complete! Try:
echo   scripts\run_dev.bat test
echo   scripts\run_dev.bat demo
exit /b 0

:test
call :ensure_venv
echo.
echo ================================================================
echo   RUNNING TESTS
echo ================================================================
"%PYTHON%" -m pytest tests/ -v --tb=short
echo.
echo [OK] All tests passed.
exit /b 0

:server
call :ensure_venv
echo.
echo ================================================================
echo   STARTING MEDIATION SERVER
echo ================================================================
echo Server listening on tcp://0.0.0.0:54000
echo Press Ctrl+C to stop.
echo.
"%LNSERVER%" --host 0.0.0.0 --port 54000 --log-level INFO
exit /b 0

:client
call :ensure_venv
echo.
echo ================================================================
echo   STARTING VPN CLIENT DAEMON
echo ================================================================
echo Client connecting to localhost:54000
echo Press Ctrl+C to stop.
echo.
"%LNCLIENT%" --server localhost:54000 --log-level INFO
exit /b 0

:cli
call :ensure_venv
shift
REM Optional -- separator between script args and CLI args.
if "%1"=="--" shift
"%LNCLI%" --host localhost --port 54000 %1 %2 %3 %4 %5 %6 %7 %8 %9
exit /b 0

:demo
call :ensure_venv
echo.
echo ================================================================
echo   LAUNCHING DEMO - Server + 2 Clients
echo ================================================================
if not exist "%TEMP%\ln-demo" mkdir "%TEMP%\ln-demo"
if not exist "%TEMP%\ln-demo\identity-a" mkdir "%TEMP%\ln-demo\identity-a"
if not exist "%TEMP%\ln-demo\identity-b" mkdir "%TEMP%\ln-demo\identity-b"

REM Terminal 1: Server
start "LN Server" cmd /k ""%LNSERVER%" --host 0.0.0.0 --port 54000 --log-level INFO"
timeout /t 2 /nobreak >nul

REM Terminal 2: Client A
start "LN Client A" cmd /k ""%LNCLIENT%" --server localhost:54000 --identity-dir "%TEMP%\ln-demo\identity-a" --log-level INFO"
timeout /t 1 /nobreak >nul

REM Terminal 3: Client B
start "LN Client B" cmd /k ""%LNCLIENT%" --server localhost:54000 --identity-dir "%TEMP%\ln-demo\identity-b" --log-level INFO"

echo.
echo Demo launched!
echo   Server   -^> tcp://localhost:54000
echo   Client A -^> identity in %%TEMP%%\ln-demo\identity-a
echo   Client B -^> identity in %%TEMP%%\ln-demo\identity-b
echo.
echo In a 4th terminal, try:
echo   scripts\run_dev.bat cli -- create mynet --password secret
echo   scripts\run_dev.bat cli -- list
echo   scripts\run_dev.bat cli -- status
echo.
exit /b 0

:clean
echo [CLEAN] Removing virtualenv and caches...
if exist "%VENV%" rmdir /s /q "%VENV%"
for /d /r "%ROOT%" %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d"
for /d /r "%ROOT%" %%d in (.pytest_cache) do @if exist "%%d" rmdir /s /q "%%d"
echo [OK] Virtualenv and caches removed.
exit /b 0

:help
echo LocalNetwork Ecosystem - Development Runner
echo.
echo Usage: scripts\run_dev.bat [command]
echo.
echo Commands:
echo   setup       Create virtualenv, install dependencies
echo   test        Run all tests
echo   server      Start the mediation server
echo   client      Start a VPN client daemon
echo   cli [args]  Run the management CLI
echo   demo        Full demo: server + 2 clients in separate windows
echo   clean       Remove virtualenv and caches
echo   help        Show this message
echo.
echo Examples:
echo   scripts\run_dev.bat setup    first-time setup
echo   scripts\run_dev.bat test     run all tests
echo   scripts\run_dev.bat demo     full 3-window demo
echo.
echo After setup + demo, in a 4th terminal:
echo   scripts\run_dev.bat cli -- create mynet --password secret
echo   scripts\run_dev.bat cli -- join ^<network-id^> --password secret
echo   scripts\run_dev.bat cli -- list
echo   scripts\run_dev.bat cli -- status
exit /b 0
