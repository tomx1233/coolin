@echo off
rem Build a standalone Coolin.exe for Windows with PyInstaller.
rem The --collect flags make sure the bundled static ffmpeg binary ships in the exe.
where py >nul 2>nul
set PY=py -3
if %errorlevel% neq 0 set PY=python

%PY% -m pip install pyinstaller imageio-ffmpeg
%PY% -m PyInstaller --noconfirm --onefile --windowed --name Coolin ^
    --collect-submodules imageio_ffmpeg --collect-binaries imageio_ffmpeg ^
    coolin_gui.py

echo.
echo If everything worked, Coolin.exe is in the "dist" folder.
pause
