# -*- coding: utf-8 -*-
"""
Baixador de video simples -> MP4
Salva na MESMA pasta onde o script foi executado.
"""

import os
import sys
import subprocess


def instalar(pacote, import_name=None):
    """Instala/atualiza pacote via pip se nao estiver disponivel."""
    import_name = import_name or pacote
    try:
        __import__(import_name)
    except ImportError:
        print(f"[SETUP] Instalando {pacote}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", pacote]
        )


instalar("yt-dlp", "yt_dlp")

import yt_dlp  # noqa: E402


PASTA = os.getcwd()  # pasta onde voce executou


def hook(d):
    if d["status"] == "downloading":
        pct = d.get("_percent_str", "").strip()
        vel = d.get("_speed_str", "").strip()
        print(f"\r  Baixando... {pct}  {vel}   ", end="", flush=True)
    elif d["status"] == "finished":
        print("\r  Download concluido. Convertendo/juntando...        ")


def baixar(url):
    opts = {
        # melhor video + melhor audio, forcando resultado final em mp4
        "format": "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(PASTA, "%(title).150B.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        ],
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        titulo = info.get("title", "video")
    print(f"[OK] Salvo: {titulo}.mp4")
    print(f"     Pasta: {PASTA}\n")


def main():
    print("=" * 60)
    print(" BAIXADOR DE VIDEO -> MP4")
    print(f" Salvando em: {PASTA}")
    print(" Cole a URL e de ENTER. Digite 'sair' para fechar.")
    print("=" * 60)

    while True:
        try:
            url = input("\nURL: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not url:
            continue
        if url.lower() in ("sair", "exit", "quit", "q"):
            break

        try:
            baixar(url)
        except Exception as e:
            print(f"\n[ERRO] Nao consegui baixar: {e}\n")

    print("Ate mais!")


if __name__ == "__main__":
    main()
