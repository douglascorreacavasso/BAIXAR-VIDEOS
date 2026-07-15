@echo off
chcp 65001 >nul
title Baixador de Video MP4 - v3
cd /d "%~dp0"

echo ============================================================
echo   PREPARANDO AMBIENTE (so demora na primeira vez)...
echo ============================================================

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

python -m pip install --upgrade pip >nul 2>&1
python -m pip install --upgrade yt-dlp requests

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo [INFO] FFmpeg nao encontrado. Instalando via winget...
    winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    echo.
    echo [INFO] Se instalou agora: FECHE e ABRA este .bat de novo,
    echo        para o Windows reconhecer o ffmpeg no PATH.
    echo.
    pause
)

cls
python "%~dp0baixar_video.py"

echo.
pause
