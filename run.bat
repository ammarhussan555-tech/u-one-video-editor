@echo off
REM Quick launcher (no build needed)
cd /d %~dp0
pip install -q -r requirements.txt
python main.py
pause
