@echo off
cd /d "%~dp0"
rem Personal paths go in start-studio.local.cmd (not versioned); see start-studio.local.example.cmd.
if exist "start-studio.local.cmd" call "start-studio.local.cmd"
rem Raw recordings are read-only. Default: the Windows Videos folder.
if not defined SOURCE_VIDEOS_DIR set "SOURCE_VIDEOS_DIR=%USERPROFILE%\Videos"
python cli.py studio
pause
