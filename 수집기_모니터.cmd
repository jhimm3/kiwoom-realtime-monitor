@echo off
set "PROJECT_ROOT=%~dp0"
set "PYTHON_EXE=C:\Users\pc-1\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYTHON_EXE%' -ArgumentList '""%PROJECT_ROOT%scripts\historical_collection_monitor.py""' -WorkingDirectory '%PROJECT_ROOT%' -Verb RunAs"
