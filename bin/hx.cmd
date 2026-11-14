@echo off
rem Windows launcher for hx: runs from source, no pip install needed.
setlocal
set "HX_ROOT=%~dp0.."
set "PYTHONPATH=%HX_ROOT%\src;%PYTHONPATH%"
set "PYTHONUTF8=1"
where py >nul 2>nul && (py -3 -m hx %* & exit /b)
where python >nul 2>nul && (python -m hx %* & exit /b)
echo hx needs Python 3.9 or newer. Install it from https://www.python.org/downloads/ 1>&2
exit /b 127
