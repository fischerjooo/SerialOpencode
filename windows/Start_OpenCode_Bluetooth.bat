@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "PROJECT=C:\WORK\sandbox\Crypto_TestAppl_QEMU"
set "TOOLS=%~dp0"
set "BRIDGE=%TOOLS%opencode_bt_bridge.py"

if not exist "%PROJECT%\" (
    echo ERROR: Project folder not found: %PROJECT%
    pause
    exit /b 1
)
if not exist "%BRIDGE%" (
    echo ERROR: Place opencode_bt_bridge.py next to this BAT file.
    pause
    exit /b 1
)
where opencode >nul 2>&1
if errorlevel 1 (
    echo ERROR: opencode is not available in PATH.
    pause
    exit /b 1
)
where py >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python launcher 'py' is not available in PATH.
    pause
    exit /b 1
)

REM Start the API only when nothing is listening on localhost:4096.
powershell.exe -NoProfile -Command "try { $c=[Net.Sockets.TcpClient]::new('127.0.0.1',4096); $c.Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo Starting OpenCode API for %PROJECT%...
    start "OpenCode API" /D "%PROJECT%" powershell.exe -NoProfile -NoExit -Command "$s=Read-Host 'Choose OpenCode server password' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { $env:OPENCODE_SERVER_PASSWORD=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }; opencode serve --hostname 127.0.0.1 --port 4096"
) else (
    echo OpenCode API already listening on localhost:4096.
)

REM Skip the BT bridge if this Python script is already running.
powershell.exe -NoProfile -Command "$p=Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe','py.exe' -and $_.CommandLine -like '*opencode_bt_bridge.py*' }; if ($p) { exit 0 } else { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo Starting Bluetooth bridge on COM3...
    start "OpenCode Bluetooth Bridge" /D "%TOOLS%" powershell.exe -NoProfile -NoExit -Command "Write-Host 'Waiting for OpenCode API on 127.0.0.1:4096...'; while ($true) { try { $c=[Net.Sockets.TcpClient]::new('127.0.0.1',4096); $c.Close(); break } catch { Start-Sleep -Seconds 1 } }; py -3 '.\opencode_bt_bridge.py' --com COM3 --api-port 4096"
) else (
    echo Bluetooth bridge already running; skipping duplicate.
)

REM Open an interactive terminal attached to the SAME server as the bridge.
echo Opening OpenCode terminal...
start "OpenCode - Crypto_TestAppl_QEMU" /D "%PROJECT%" powershell.exe -NoProfile -NoExit -Command "Write-Host 'Waiting for OpenCode API on 127.0.0.1:4096...'; while ($true) { try { $c=[Net.Sockets.TcpClient]::new('127.0.0.1',4096); $c.Close(); break } catch { Start-Sleep -Seconds 1 } }; $s=Read-Host 'Enter the SAME OpenCode server password' -AsSecureString; $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($s); try { $env:OPENCODE_SERVER_PASSWORD=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }; opencode attach http://127.0.0.1:4096 --continue"

exit /b 0