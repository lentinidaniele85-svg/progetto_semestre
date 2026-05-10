@echo off
chcp 65001 >nul
title Sustainable Product Optimization

:: Vai nella cartella del file bat
cd /d "%~dp0"

:: Controlla che il venv esista
if not exist "venv\Scripts\activate.bat" (
    echo.
    echo ERRORE: Venv non trovato!
    echo Esegui prima SETUP.bat per configurare l'ambiente.
    echo.
    pause
    exit /b 1
)

:: Attiva il venv e avvia Streamlit
echo.
echo Attivazione ambiente virtuale...
call venv\Scripts\activate.bat

echo Avvio applicazione Streamlit...
echo.
streamlit run ui\app.py --server.port 8502
pause
