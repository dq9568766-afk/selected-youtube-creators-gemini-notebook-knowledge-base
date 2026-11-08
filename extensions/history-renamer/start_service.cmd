@echo off
setlocal
cd /d "%~dp0..\.."
title History Rename Service
python ytg.py history-rename serve --auto
if errorlevel 1 pause
