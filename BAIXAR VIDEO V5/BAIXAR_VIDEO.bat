@echo off
chcp 65001 >nul
title Baixador de Video MP4 - v5
cd /d "%~dp0"

echo ============================================================
echo   PREPARANDO (so demora na primeira vez)...
echo ============================================================

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale em https://www.python.org/downloads/ e marque
    echo "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -m pip install --upgrade pip >nul 2>&1
echo [1/3] instalando motores de download (yt-dlp, streamlink)...
python -m pip install --upgrade yt-dlp streamlink requests PySocks "requests[socks]"

echo [2/3] checando FFmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [INFO] FFmpeg nao encontrado. Instalando via winget...
    winget install --id=Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    echo [INFO] Se instalou agora: FECHE e ABRA este .bat de novo.
    pause
)

echo [3/3] (opcional) Tor:
where tor >nul 2>&1
if errorlevel 1 (
    echo [INFO] Tor nao encontrado. E OPCIONAL - so precisa se quiser a
    echo        rota "trocar de pais". Para ter isso, instale o Tor:
    echo        winget install --id=TorProject.TorBrowser
    echo        (ou baixe o "Tor Expert Bundle" e rode tor.exe antes)
    echo.
)

cls
python "%~dp0baixar_video.py"

echo.
pause
