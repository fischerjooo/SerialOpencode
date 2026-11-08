@echo off
setlocal EnableExtensions DisableDelayedExpansion
REM Override defaults with --project "C:\path with spaces" --com COM3 --api-port 4096.
REM No persistent terminal is opened. Use --console for diagnostics, --stop to stop.
py -3 "%~dp0launcher.py" %*
if errorlevel 1 pause
exit /b %errorlevel%
