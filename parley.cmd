@echo off
rem Dev launcher: run Parley from a source checkout.
cd /d "%~dp0"
.venv\Scripts\python.exe -m parley.main %*
