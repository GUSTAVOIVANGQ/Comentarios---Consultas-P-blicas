@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero crea el entorno: python -m venv .venv
  echo Luego instala: .venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
start "Panel CRT" http://127.0.0.1:8765
.venv\Scripts\python.exe -m crt_comments ui
pause
