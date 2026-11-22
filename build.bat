@echo off
rem Builds dist\WoWAutoTranslate\WoWAutoTranslate.exe and a release zip.
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :fail
python build.py || goto :fail
pause
exit /b 0

:fail
echo.
echo Build failed.
pause
exit /b 1
