@echo off
cd /d "%~dp0.."
echo Pulling latest code...
git pull
echo Starting Windows Agent...
cd windows-agent
python agent.py
pause
