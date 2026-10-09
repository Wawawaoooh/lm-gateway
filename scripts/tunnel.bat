@echo off
echo [tunnel] starting encrypted channel - KEEP THIS WINDOW OPEN while remote PC uses gateway
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel.ps1"
pause
