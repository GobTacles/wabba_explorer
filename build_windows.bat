@echo off
REM Build a standalone Windows executable with PyInstaller.
REM Run this script from the repository root on a Windows machine.
REM
REM Prerequisites (one-time):
REM   pip install pyinstaller
REM
REM Output: dist\wabba_explorer.exe

pyinstaller ^
    --onefile ^
    --windowed ^
    --hidden-import xxhash ^
    --name wabba_explorer ^
    --icon NONE ^
    main.py

echo.
echo Done.  The standalone executable is in dist\wabba_explorer.exe
pause
