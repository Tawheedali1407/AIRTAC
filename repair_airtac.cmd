@echo off
rem Repairs pip inside AIRTAC's .venv and installs every package AIRTAC needs (fixes the vault, QR code, OCR...).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip --version >nul 2>&1 || ".venv\Scripts\python.exe" scripts\repair_pip.py || goto :done
".venv\Scripts\python.exe" -m pip install -r requirements.txt
:done
".venv\Scripts\python.exe" scripts\doctor.py
pause
