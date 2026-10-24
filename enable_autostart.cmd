@echo off
rem Makes AIRTAC Advance start by itself (hidden) when you sign in to Windows, plus desktop and Start menu shortcuts.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\make_shortcut.ps1" -AtLogin
timeout /t 5
