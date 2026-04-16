@echo off
echo Настройка автозапуска TAT AUTO бота...

set SCRIPT_DIR=%~dp0
set PYTHON_PATH=python
set MAIN_SCRIPT=%SCRIPT_DIR%main.py
set RESTART_SCRIPT=%SCRIPT_DIR%restart_bot.bat

REM Создаём скрипт перезапуска
echo @echo off > "%RESTART_SCRIPT%"
echo :loop >> "%RESTART_SCRIPT%"
echo echo [%%date%% %%time%%] Запуск бота... >> "%RESTART_SCRIPT%"
echo cd /d "%SCRIPT_DIR%" >> "%RESTART_SCRIPT%"
echo %PYTHON_PATH% "%MAIN_SCRIPT%" >> "%RESTART_SCRIPT%"
echo echo [%%date%% %%time%%] Бот завершился. Перезапуск через 10 секунд... >> "%RESTART_SCRIPT%"
echo timeout /t 10 /nobreak ^>nul >> "%RESTART_SCRIPT%"
echo goto loop >> "%RESTART_SCRIPT%"

REM Регистрируем в Task Scheduler
schtasks /delete /tn "TatAutoBot" /f 2>nul
schtasks /create /tn "TatAutoBot" /tr "\"%RESTART_SCRIPT%\"" /sc onstart /ru SYSTEM /rl HIGHEST /f

if %ERRORLEVEL% EQU 0 (
    echo ✅ Задача создана! Бот будет запускаться автоматически при старте Windows.
    echo    Имя задачи: TatAutoBot
    echo    Для управления: Планировщик задач ^> TatAutoBot
) else (
    echo ❌ Ошибка создания задачи. Запустите от имени администратора!
)

echo.
echo Запустить бота прямо сейчас? (Y/N)
set /p choice=
if /i "%choice%"=="Y" start "" "%RESTART_SCRIPT%"

pause
