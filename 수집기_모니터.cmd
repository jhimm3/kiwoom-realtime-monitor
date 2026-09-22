@echo off
set "PROJECT_ROOT=%~dp0"
set "PYTHON_EXE=C:\Users\pc-1\AppData\Local\Programs\Python\Python313\pythonw.exe"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '""%PROJECT_ROOT%scripts\historical_collection_monitor.py""' -WorkingDirectory '%PROJECT_ROOT%' -Verb RunAs"
