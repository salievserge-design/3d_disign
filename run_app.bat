@echo off
chcp 65001 >nul
rem ─── Запуск НОМЕРОК 3D без сборки exe (нужен Python 3.11+) ───
cd /d "%~dp0"
if not exist .venv (
    python -m venv .venv || goto :err
    ".venv\Scripts\python.exe" -m pip install -r requirements-app.txt || goto :err
)
".venv\Scripts\python.exe" -m app.main
exit /b 0
:err
echo Не удалось подготовить окружение. Нужен Python 3.11+ с галочкой "Add to PATH".
pause
