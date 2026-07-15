@echo off
chcp 65001 >nul
title Baixador de Video MP4
cd /d "%~dp0"

echo ============================================================
echo   PREPARANDO AMBIENTE...
echo ============================================================

REM --- Verifica se o Python existe ---
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale em: https://www.python.org/downloads/
    echo IMPORTANTE: marque "Add Python to PATH" na instalacao.
    echo.
    pause
    exit /b 1
)

REM --- pip atualizado ---
python -m pip install --upgrade pip >nul 2>&1

REM --- Bibliotecas necessarias ---
python -m pip install --upgrade yt-dlp

REM --- FFmpeg (necessario para juntar video+audio em mp4) ---
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo [INFO] FFmpeg nao encontrado. Tentando instalar via winget...
    winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    echo.
    echo [INFO] Se o winget instalou agora, FECHE e ABRA este .bat de novo
    echo        para o Windows reconhecer o ffmpeg no PATH.
    echo.
)

cls
python "%~dp0baixar_video.py"

echo.
pause
