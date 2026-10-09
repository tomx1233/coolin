@echo off
rem Use the Coolin command line on Windows, e.g.:  run_cli.bat song.mp3 -d 2
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0coolin_cli.py" %*
) else (
    python "%~dp0coolin_cli.py" %*
)
