@echo off
setlocal
set "ROOT=%~dp0..\.."
cd /d "%ROOT%"
if not exist "%ROOT%\dev" mkdir "%ROOT%\dev"

rem headless needs full access: workspace-write is denied by the Windows sandbox
set DSH_PERMISSION_MODE=danger-full-access
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
rem scheduled tasks start with a minimal PATH; add common npm/python bins
set "PATH=%APPDATA%\npm;%LOCALAPPDATA%\pnpm;%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"

echo [%date% %time%] watch start >> "%ROOT%\dev\mailbox-watch.log"
python "%ROOT%\scripts\vpn\tasks.py" run --vpn --no-lease -- python "%ROOT%\scripts\projects\gh_mailbox.py" --host %COMPUTERNAME% watch --once --agent-stdin --agent-command "dsh headless" >> "%ROOT%\dev\mailbox-watch.log" 2>&1
echo [%date% %time%] watch exit %ERRORLEVEL% >> "%ROOT%\dev\mailbox-watch.log"
