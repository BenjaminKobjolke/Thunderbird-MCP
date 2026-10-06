@echo off
rem Reinstall the bridge add-on only when addon/ changed, stop a daemon that runs old code, then check.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m tbmcp refresh
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m tbmcp doctor --wait 90
exit /b %errorlevel%
