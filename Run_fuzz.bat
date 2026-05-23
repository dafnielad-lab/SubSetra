@echo off
rem Run fresh random fuzz cases and accumulate the cumulative test log.
chcp 65001 >nul
title Reconciliation Tool - Fuzz Tests
cd /d "%~dp0"
set "PYEXE=python"
where python >nul 2>nul || set "PYEXE=py"
"%PYEXE%" -X utf8 test_reconcile.py --fuzz 3000
echo.
echo ============================================================
echo  Fuzz run finished. The cumulative log is in test_log.json
echo ============================================================
pause >nul
