@echo off
setlocal

:: Change to the project directory
cd /d "C:\Users\Eight\Projects\New_Trading_Algo_1.6.26"

:: Set environment for persistent logging
set PYTHONUNBUFFERED=1

:: Run the trading bot with integrated web dashboard
"C:\Users\Eight\AppData\Local\Programs\Python\Python312\python.exe" "main.py"

endlocal
