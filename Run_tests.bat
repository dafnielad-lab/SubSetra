@echo off
rem Run the regression test suite and keep the window open to read the summary.
chcp 65001 >nul
title Reconciliation Tool - Tests
cd /d "%~dp0"
set "PYEXE=python"
where python >nul 2>nul || set "PYEXE=py"
"%PYEXE%" -X utf8 test_reconcile.py
echo.
echo ============================================================
echo  Tests finished. Review the PASS/FAIL summary above.
echo ============================================================
pause >nul
