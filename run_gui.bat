@echo off
rem Launch the Coolin GUI on Windows.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0coolin_gui.py"
) else (
    python "%~dp0coolin_gui.py"
)
