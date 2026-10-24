@echo off
rem Starts AIRTAC Advance and shows what happens: starts the server (if it is not running),
rem waits until it answers, then opens its window. AIRTAC-Advance.cmd does the same without this window.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"
if errorlevel 1 pause
