@echo off
chcp 65001 >nul
rem ─── Сборка НОМЕРОК 3D в один .exe. Нужен установленный Python 3.11+ ───
cd /d "%~dp0"
echo.
echo   Собираю НОМЕРОК 3D...
echo.
if not exist .venv (
    echo   [1/3] Создаю виртуальное окружение...
    python -m venv .venv || goto :err
)
echo   [2/3] Ставлю зависимости...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements-app.txt pyinstaller || goto :err
echo   [3/3] Собираю exe (это займёт пару минут)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean nomerok3d.spec || goto :err
echo.
echo   ГОТОВО: dist\NOMEROK-3D.exe
echo.
pause
exit /b 0
:err
echo.
echo   Что-то пошло не так. Проверь, что установлен Python 3.11+ и есть интернет.
pause
exit /b 1
