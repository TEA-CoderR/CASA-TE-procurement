@echo off
cd /d "%~dp0"
echo [1/3] Check Python...
python --version || (echo Python not found. Install from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)
echo [2/3] Install packages...
python -m pip install -r requirements.txt pyinstaller psutil || (echo Install failed. & pause & exit /b 1)
echo [3/3] Build exe (3-5 min)...
for %%f in (*.spec) do python -m PyInstaller --noconfirm "%%f"
echo.
echo Done. New exe is in the dist folder.
pause
