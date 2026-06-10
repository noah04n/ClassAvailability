@echo off
REM Launches ClassAvailability in the background (no console window).
cd /d "%~dp0"
start "" pythonw app.py
