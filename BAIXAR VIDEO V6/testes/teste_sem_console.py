# -*- coding: utf-8 -*-
"""Confere o conferidor de cor e a verificação do programa num processo SEM console (como a bateria em segundo
plano e o executor rodam). Rodar com pythonw.exe. Resultado em testes/saida/teste_sem_console.json."""
import json
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))
from baixador.base import achar_programa, ffprobe_info  # noqa: E402
from baixador.motores import decodifica  # noqa: E402
import midias  # noqa: E402

arq = midias.PASTA / "vermelho.mp4"
res = {"ffmpeg": achar_programa("ffmpeg"), "cor_nova": midias.cor_media(arq), "ffprobe_programa": bool(ffprobe_info(arq)),
       "decodifica_programa": decodifica(arq)}
try:  # do jeito antigo (sem CREATE_NO_WINDOW)
    r = subprocess.run([achar_programa("ffmpeg"), "-v", "error", "-i", str(arq), "-ss", "1.2", "-frames:v", "1", "-vf", "scale=32:18",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=30)
    res["jeito_antigo_bytes"] = len(r.stdout)
    res["jeito_antigo_codigo"] = r.returncode
except Exception as e:
    res["jeito_antigo_erro"] = repr(e)
(AQUI / "saida" / "teste_sem_console.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
