@echo off
rem Restarts AIRTAC Advance (stops the server, starts it again with the new code, opens the window).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\restart.ps1"
if errorlevel 1 pause
