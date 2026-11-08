@echo off
py -3 "%~dp0launcher.py" --stop %*
if errorlevel 1 pause
