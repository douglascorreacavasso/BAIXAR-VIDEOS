# -*- coding: utf-8 -*-
"""Gera os vídeos de teste (pequenos, 3 s) com o ffmpeg. Cada COR é um vídeo diferente: assim a bateria confere
pela imagem se baixou o vídeo CERTO, não só "algum arquivo". Também gera HLS (simples, com qualidades, criptografado
AES-128, fMP4) e DASH. Tudo fica em testes/midias/ e é apagado no fim (testes/limpar.py)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from baixador.base import SEM_JANELA, achar_programa  # noqa: E402

PASTA = Path(__file__).resolve().parent / "midias"
CORES = {  # nome: (R, G, B)
    "vermelho": (220, 40, 40), "verde": (40, 200, 70), "azul": (40, 80, 220), "amarelo": (230, 210, 40),
    "roxo": (150, 60, 200), "ciano": (40, 200, 210), "laranja": (240, 130, 30), "rosa": (240, 90, 170),
}
FORMATOS = {
    "mp4": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"],
    "webm": ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k", "-c:a", "libopus", "-b:a", "48k"],
    "mov": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    "mkv": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    "flv": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    "ts": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-f", "mpegts"],
    "av1.mp4": ["-c:v", "libsvtav1", "-preset", "12", "-crf", "50", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"],
    "hevc.mp4": ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-c:a", "aac", "-b:a", "64k", "-x265-params", "log-level=error"],
    "mudo.mp4": ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-an"],
}
DUR = 3


def _ff(args, cwd=None):
    r = subprocess.run([achar_programa("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, cwd=cwd, creationflags=SEM_JANELA)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])


def _entrada(cor, tam="320x180"):
    r, g, b = CORES[cor]
    freq = 300 + list(CORES).index(cor) * 110
    # cor sólida + um quadrado branco andando (quadros diferentes ao longo do tempo) + tom de áudio próprio
    return ["-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s={tam}:d={DUR}:r=24,drawbox=x='mod(t*60,280)':y=60:w=30:h=30:c=white:t=fill",
            "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={DUR}"]


def gerar_tudo(forcar=False):
    PASTA.mkdir(exist_ok=True)
    feito = PASTA / ".pronto"
    if feito.exists() and not forcar:
        return
    for cor in CORES:
        for fmt, args in FORMATOS.items():
            alvo = PASTA / f"{cor}.{fmt}"
            if alvo.exists():
                continue
            extra = args
            entrada = _entrada(cor)
            if fmt == "mudo.mp4":
                _ff(entrada[:4] + extra + [str(alvo)])
            else:
                _ff(entrada + extra + ["-shortest", str(alvo)])
        # 1080 "grande" só para o mp4 (testes de qualidade)
        _ff(_entrada(cor, "640x360") + FORMATOS["mp4"] + ["-shortest", str(PASTA / f"{cor}.360.mp4")])
        # só áudio (isca)
        _ff(_entrada(cor)[4:] + ["-c:a", "aac", "-b:a", "64k", str(PASTA / f"{cor}.m4a")])
        # HLS com duas qualidades (master + variantes)
        d = PASTA / f"hls_{cor}"
        d.mkdir(exist_ok=True)
        for alt, tam in (("v360", "640x360"), ("v180", "320x180")):
            (d / alt).mkdir(exist_ok=True)
            _ff(_entrada(cor, tam) + FORMATOS["mp4"][:8] + ["-g", "24", "-shortest", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                                                           "-hls_segment_filename", str(d / alt / "seg%d.ts"), str(d / alt / "index.m3u8")])
        (d / "master.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\n"
                                       "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nv360/index.m3u8\n"
                                       "#EXT-X-STREAM-INF:BANDWIDTH=300000,RESOLUTION=320x180\nv180/index.m3u8\n", encoding="utf-8")
        # HLS criptografado (AES-128)
        e = PASTA / f"hlsaes_{cor}"
        e.mkdir(exist_ok=True)
        chave = os.urandom(16)
        (e / "chave.key").write_bytes(chave)
        (e / "info.txt").write_text(f"chave.key\n{e / 'chave.key'}\n", encoding="utf-8")
        _ff(_entrada(cor) + FORMATOS["mp4"][:8] + ["-g", "24", "-shortest", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                                                  "-hls_key_info_file", str(e / "info.txt"), "-hls_segment_filename", str(e / "s%d.ts"),
                                                  str(e / "master.m3u8")])
        # HLS fMP4
        m = PASTA / f"hlsfmp4_{cor}"
        m.mkdir(exist_ok=True)
        _ff(_entrada(cor) + FORMATOS["mp4"][:8] + ["-g", "24", "-shortest", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                                                  "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
                                                  "-hls_segment_filename", "p%d.m4s", "master.m3u8"], cwd=m)
        # DASH
        da = PASTA / f"dash_{cor}"
        da.mkdir(exist_ok=True)
        _ff(_entrada(cor) + ["-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "24",
                             "-c:a", "aac", "-shortest", "-f", "dash", "-seg_duration", "1", "-use_template", "1", "-use_timeline", "0",
                             "manifest.mpd"], cwd=da)
    feito.write_text("ok", encoding="utf-8")


ULTIMO_DIAG: dict = {}   # por que a cor não saiu (vai para as falhas da bateria)


def cor_media(caminho) -> tuple[int, int, int] | None:
    """Cor média de um quadro do meio (o quadrado branco ocupa pouco e quase não mexe na média)."""
    extra = ["-allowed_extensions", "ALL"] if str(caminho).endswith(".m3u8") else []
    try:
        r = subprocess.run([achar_programa("ffmpeg") or "ffmpeg", "-v", "error", *extra, "-i", str(caminho), "-ss", "1.2", "-frames:v", "1", "-vf", "scale=32:18",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=30,
                           creationflags=SEM_JANELA)  # sem isto, num processo sem console (bateria em segundo plano) o ffmpeg não abria
    except subprocess.TimeoutExpired:  # PC ocupado: conta como "não li a cor", não derruba a bateria
        ULTIMO_DIAG[str(caminho)] = {"codigo": "tempo_esgotado"}
        return None
    b = r.stdout
    if len(b) < 32 * 18 * 3:
        return None
    n = len(b) // 3
    return (sum(b[0::3]) // n, sum(b[1::3]) // n, sum(b[2::3]) // n)


def qual_cor(rgb) -> str | None:
    if not rgb:
        return None
    melhor, dist = None, 1e9
    for nome, c in CORES.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, c))
        if d < dist:
            melhor, dist = nome, d
    return melhor if dist < 60 ** 2 * 3 else None


if __name__ == "__main__":
    gerar_tudo("--forcar" in sys.argv)
    print("mídias em", PASTA, "->", sum(1 for _ in PASTA.rglob("*") if _.is_file()), "arquivos")
