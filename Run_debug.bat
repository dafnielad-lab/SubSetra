@echo off
rem Same app, but keeps a console open to show any error messages.
chcp 65001 >nul
cd /d "%~dp0"
set "PYEXE=python"
where python >nul 2>nul || set "PYEXE=py"
"%PYEXE%" "reconcile_gui.py"
echo.
echo ============================================================
echo  Window closed. If there was an error, it is shown above.
echo ============================================================
pause >nul
