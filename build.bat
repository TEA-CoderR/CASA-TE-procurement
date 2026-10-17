@echo off
cd /d "%~dp0"
set "APP=dist\Procurement"
set "KEEP=build\_userdata"

echo [1/5] Check Python...
python --version || (echo Python not found. Install from python.org and tick "Add python.exe to PATH". & pause & exit /b 1)

echo [2/5] Install packages...
python -m pip install -r requirements.txt pyinstaller psutil || (echo Install failed. & pause & exit /b 1)

echo [3/5] Save settings and history...
rem PyInstaller empties dist\Procurement on every build, so keep the user data aside first.
rem Old versions (single exe) kept them directly in dist\.
if exist "%KEEP%" rmdir /s /q "%KEEP%"
mkdir "%KEEP%"
for %%d in ("%APP%" "dist") do (
    if exist "%%~d\settings.json" if not exist "%KEEP%\settings.json" copy /y "%%~d\settings.json" "%KEEP%\" >nul
    if exist "%%~d\history" if not exist "%KEEP%\history" xcopy /e /i /q /y "%%~d\history" "%KEEP%\history" >nul
    if exist "%%~d\logs" if not exist "%KEEP%\logs" xcopy /e /i /q /y "%%~d\logs" "%KEEP%\logs" >nul
    if exist "%%~d\suppliers_config.json" if not exist "%KEEP%\suppliers_config.json" copy /y "%%~d\suppliers_config.json" "%KEEP%\" >nul
)

echo [4/5] Build (3-5 min)...
for %%f in (*.spec) do python -m PyInstaller --noconfirm "%%f" || (echo Build failed. Settings and history are kept in %KEEP%. & pause & exit /b 1)

echo [5/5] Restore settings and history...
xcopy /e /i /q /y "%KEEP%" "%APP%" >nul
rem Supplier groups for "Order suggestions" (keep the one already next to the exe if it was edited)
if not exist "%APP%\suppliers_config.json" if exist "suppliers_config.json" copy /y "suppliers_config.json" "%APP%\" >nul
rem The old single-file exe is no longer used
if exist "dist\*.exe" del /q "dist\*.exe"
rem Desktop shortcut to the new exe
powershell -NoProfile -ExecutionPolicy Bypass -Command "$e = Get-ChildItem '%APP%\*.exe' | Select-Object -First 1; $w = New-Object -ComObject WScript.Shell; $l = $w.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ($e.BaseName + '.lnk'))); $l.TargetPath = $e.FullName; $l.WorkingDirectory = $e.DirectoryName; $l.IconLocation = $e.FullName; $l.Save()"

echo.
echo Done. The program is in %APP% (keep the whole folder together).
echo A shortcut was put on the desktop.
rem Update source (written by publish_update.py) goes next to the exe
if exist "update_config.json" copy /y "update_config.json" "%APP%\" >nul
if /i not "%~1"=="nopause" pause
