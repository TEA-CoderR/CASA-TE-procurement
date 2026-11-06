@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PY=python
if exist D:\PYTHON\python.exe set PY=D:\PYTHON\python.exe
set PYTHONIOENCODING=utf-8
%PY% tools\verify_phase0.py > logs\verify_phase0.txt 2>&1
echo done > logs\verify_phase0.done
