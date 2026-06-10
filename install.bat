@echo off
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Done. You can now launch the app with run.bat (silent) or run-debug.bat (with console).
pause
