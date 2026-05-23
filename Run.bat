@echo off
rem Launch the graphical window (no black console window).
cd /d "%~dp0"
set "PYW=pythonw"
where pythonw >nul 2>nul || set "PYW=python"
start "" "%PYW%" "reconcile_gui.py"
