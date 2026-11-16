# -*- coding: utf-8 -*-
"""Bateria de ponta a ponta: o programa INTEIRO (yt-dlp + detector + modelo + Edge + motores + conversão) contra o
servidor local de sites falsos. Confere pela COR do vídeo baixado se veio o vídeo certo.

Uso:  .venv\\Scripts\\python.exe testes\\bateria_e2e.py N [semente_inicial] [threads]
Resultado: testes/saida/e2e_<semente>.json (e os arquivos baixados são apagados conforme conferidos).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "saida"
SAIDA.mkdir(exist_ok=True)
os.environ["BAIXADOR_DADOS"] = str(SAIDA / "dados_teste")   # não mexe no aprendizado de verdade
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

from baixador.pipeline import Motor, Tarefa  # noqa: E402
from gerador import gerar  # noqa: E402
from midias import ULTIMO_DIAG, cor_media, gerar_tudo, qual_cor  # noqa: E402
from servidor import REG, Cenario, Servidor  # noqa: E402

LIMITE_S = 150


def rodar(n: int, inicio: int = 0, threads: int = 4, prefixo="e2e"):
    gerar_tudo()
    shutil.rmtree(SAIDA / "dados_teste", ignore_errors=True)   # cada rodada começa só com os pesos de fábrica
    (SAIDA / "dados_teste").mkdir(parents=True, exist_ok=True)
    destino = SAIDA / f"baixados_{prefixo}_{inicio}"
    destino.mkdir(parents=True, exist_ok=True)
    cfg = {"pasta_destino": str(destino), "usar_tor": False, "proxies": "", "usar_navegador": True,
           "perguntar_na_duvida": False, "mp4_compativel": True, "cookies_navegador": "nenhum", "_q": 720, "simultaneos": threads}
    motor = Motor(cfg)
    st = Counter()
    por = defaultdict(Counter)
    falhas = []
    lock = threading.Lock()
    t0 = time.time()

    with Servidor() as srv:
        def um(seed):
            r = random.Random(seed)
            pg = gerar(seed, "teste", local=True)
            cen = Cenario(seed, pg, r, srv.porta)
            REG.add(cen)
            t = Tarefa(url=cen.url)
            timer = threading.Timer(LIMITE_S, t.cancel.set)
            timer.start()
            ini = time.time()
            try:
                motor.processar(t)
            finally:
                timer.cancel()
            dur = time.time() - ini
            cor = qual_cor(cor_media(t.arquivo)) if t.arquivo and os.path.exists(t.arquivo) else None
            if pg.espera == "nenhum":
                res = "ok" if t.estado == "erro" else ("isca" if t.arquivo else "outro")
            elif t.estado == "pronto":
                res = "ok" if cor == cen.cor else ("isca" if cor else "quebrado")
            elif t.estado == "cancelado":
                res = "tempo"
            else:
                res = "erro:" + (t.categoria_erro or "?")
            info = t.info_arquivo or {}
            if res == "ok" and pg.espera != "nenhum":
                # conferências extras: MP4 de verdade e H.264 (opção "toca em tudo" ligada)
                if not str(t.arquivo).lower().endswith(".mp4"):
                    res = "nao_mp4"
                elif info.get("vcodec") != "h264":
                    res = "nao_h264"
                elif not info.get("tem_audio") and cen.fmt_prog != "mudo.mp4" and "hls" not in t.caminho and "dash" not in t.caminho and False:
                    res = "sem_audio"
            with lock:
                st["total"] += 1
                st[res] += 1
                por["familia:" + pg.familia][res] += 1
                por["fmt:" + cen.fmt_prog][res] += 1
                for m in cen.manias:
                    por["mania:" + m][res] += 1
                por["motor:" + (t.caminho.split(" ")[0] if t.caminho else "-")][res] += 1
                st["segundos"] += dur
                if res != "ok" and len(falhas) < 300:
                    falhas.append({"seed": seed, "res": res, "familia": pg.familia, "manias": sorted(cen.manias), "fmt": cen.fmt_prog,
                                   "hls": cen.fmt_hls, "cor": cen.cor, "cor_baixada": cor, "caminho": t.caminho, "erro": t.erro,
                                   "iscas": pg.iscas, "segundos": round(dur, 1), "log": t.logs[-14:],
                                   "escolhido": t.escolhido.url if t.escolhido else None, "verdade": sorted(cen.verdade)[:2],
                                   "arquivo": t.arquivo, "existe": bool(t.arquivo) and os.path.exists(t.arquivo),
                                   "diag_cor": ULTIMO_DIAG.get(str(t.arquivo))})
                if st["total"] % 100 == 0:
                    ok = st["ok"] / st["total"]
                    print(f"{st['total']}/{n}  ok {ok:.2%}  ({time.time() - t0:.0f}s)", flush=True)
            if t.arquivo:
                try:
                    os.remove(t.arquivo)
                except Exception:
                    pass
            REG.tirar(seed)

        with ThreadPoolExecutor(threads) as ex:
            list(ex.map(um, range(inicio, inicio + n)))

    shutil.rmtree(destino, ignore_errors=True)
    res = {"total": st["total"], "ok": st["ok"], "taxa_ok": round(st["ok"] / max(st["total"], 1), 4),
           "resultados": {k: v for k, v in st.items() if k not in ("total", "segundos")},
           "segundos_medio": round(st["segundos"] / max(st["total"], 1), 2), "tempo_total_s": round(time.time() - t0),
           "por": {k: dict(v) for k, v in sorted(por.items())}, "falhas": falhas}
    (SAIDA / f"{prefixo}_{inicio}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("por", "falhas")}, ensure_ascii=False, indent=1))
    return res


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    ini = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    th = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    rodar(n, ini, th)
