@echo off
chcp 65001 >nul
title Baixador de Video V6
cd /d "%~dp0"

rem ---- 1) ambiente Python proprio (.venv) - so na primeira vez
if not exist ".venv\Scripts\python.exe" (
    echo Preparando o programa pela primeira vez - pode levar alguns minutos...
    where py >nul 2>&1 && (py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv) || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo [ERRO] Nao achei o Python. Instale em https://www.python.org/downloads/ marcando "Add Python to PATH".
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --no-cache-dir -q --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --no-cache-dir -q -r requirements.txt
    if errorlevel 1 (
        echo [ERRO] A instalacao das bibliotecas falhou. Confira a internet e abra de novo.
        pause
        exit /b 1
    )
)

rem ---- 2) yt-dlp atualizado (os sites mudam toda semana) - no maximo 1 vez por dia
set HOJE=%date:~-4%%date:~3,2%%date:~0,2%
set /p ULTIMA=<dados\ultima_atualizacao.txt 2>nul
if not "%ULTIMA%"=="%HOJE%" (
    echo Atualizando o yt-dlp...
    ".venv\Scripts\python.exe" -m pip install --no-cache-dir -q --upgrade yt-dlp
    if not exist dados mkdir dados
    echo %HOJE%>dados\ultima_atualizacao.txt
)

rem ---- 3) FFmpeg
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo O FFmpeg nao foi encontrado. Instalando pelo winget...
    winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    echo Se instalou agora: FECHE esta janela e abra o BAIXAR_VIDEO.bat de novo.
    pause
    exit /b 0
)

rem ---- 4) abre a janela (sem console)
start "" ".venv\Scripts\pythonw.exe" "%~dp0baixar.py"
