@echo off
chcp 65001 >nul
title SETUP - Sustainable Product Optimization

echo.
echo ========================================
echo    SETUP AMBIENTE - Avvio in corso...
echo ========================================
echo.

:: Vai nella cartella del file bat
cd /d "%~dp0"

:: STEP 1 - Rimuovi vecchio venv
if exist "venv" (
    echo [1/4] Rimozione vecchio venv...
    rmdir /s /q venv
    echo       Vecchio venv rimosso.
) else (
    echo [1/4] Nessun venv precedente trovato.
)

:: STEP 2 - Crea nuovo venv
echo.
echo [2/4] Creazione nuovo venv...
py -m venv venv

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo ERRORE: Python non trovato o venv non creato.
    echo Assicurati che Python sia installato correttamente.
    echo.
    pause
    exit /b 1
)
echo       Venv creato con successo.

:: STEP 3 - Installa dipendenze
echo.
echo [3/4] Installazione dipendenze da requirements.txt...
call venv\Scripts\activate.bat
pip install -r requirements.txt

:: STEP 4 - Fine
echo.
echo [4/4] Setup completato!
echo.
echo ========================================
echo    Per avviare il progetto, usa:
echo    START.bat
echo ========================================
echo.
pause
