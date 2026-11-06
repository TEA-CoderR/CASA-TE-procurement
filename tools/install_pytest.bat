@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PY=python
if exist D:\PYTHON\python.exe set PY=D:\PYTHON\python.exe
%PY% -m pip install --upgrade pytest > logs\install_pytest.txt 2>&1
%PY% -m pytest --version >> logs\install_pytest.txt 2>&1
echo done > logs\install_pytest.done
