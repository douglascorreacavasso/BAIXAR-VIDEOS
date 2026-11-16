# -*- coding: utf-8 -*-
"""Bateria de 100 mil: 95.000 páginas simuladas (detector + modelo, conjunto cego "teste") + 5.000 cenários de ponta a
ponta no servidor local (programa inteiro, vídeo conferido pela cor). Roda só com a trava de processo pesado livre.

Uso:  .venv\\Scripts\\python.exe testes\\bateria_100k.py [n_nivel1] [n_e2e]
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "saida"
SAIDA.mkdir(exist_ok=True)
os.environ["BAIXADOR_DADOS"] = str(SAIDA / "dados_teste")
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

TRAVA = Path(r"C:\Users\Douglas\Downloads\ARCH-NEURAL 3D-MNLS v3\FORJA\TRAVA_PROCESSO_PESADO.json")
DONO = "Claude (thread Baixador de vídeo V6)"


def pegar_trava(o_que: str):
    avisou = False
    while True:
        if not TRAVA.exists():
            try:
                fd = os.open(str(TRAVA), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"dono": DONO, "desde": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                                         "o_que": o_que, "pid": os.getpid()}, ensure_ascii=False).encode("utf-8"))
                os.close(fd)
                print("trava pega", flush=True)
                return
            except FileExistsError:
                pass
        if not avisou:
            try:
                print("esperando a trava:", TRAVA.read_text(encoding="utf-8")[:200], flush=True)
            except Exception:
                pass
            avisou = True
        time.sleep(20)


def soltar_trava():
    try:
        if json.loads(TRAVA.read_text(encoding="utf-8")).get("dono") == DONO:
            TRAVA.unlink()
            print("trava solta", flush=True)
    except Exception:
        pass


def nivel1(n: int, inicio: int = 0) -> dict:
    from baixador.aprendizado import Modelo, ranquear
    from baixador.base import dominio
    from baixador.detector import analisar_html
    from baixador.pipeline import LIMIAR_MINIMO, Motor
    from gerador import gerar
    modelo = Modelo.carregar(com_uso=False)  # só os pesos de fábrica: o teste cego não pode usar o que o e2e aprendeu
    st = Counter()
    por = defaultdict(Counter)
    falhas = []
    t0 = time.time()
    for s in range(inicio, inicio + n):
        pg = gerar(s, "teste")
        t1 = time.perf_counter()
        pag = analisar_html(pg.html, pg.url)
        ordenados = ranquear(pag.candidatos, modelo, dominio(pg.url), None, pagina_url=pg.url)
        st["ms_total"] += (time.perf_counter() - t1) * 1000
        duvida = Motor._em_duvida(ordenados)
        recusa = not ordenados or ordenados[0].prob < LIMIAR_MINIMO
        if pg.espera == "video":
            if ordenados and ordenados[0].url in pg.verdade and not recusa:
                res = "ok"
            elif recusa:
                res = "nao_achou"          # o programa avisaria / abriria o navegador
            elif not any(c.url in pg.verdade for c in pag.candidatos):
                res = "nao_detectou"
            else:
                res = "isca"               # escolheu outro vídeo da página
        elif pg.espera == "nenhum":
            res = "ok" if recusa else "isca"
        else:  # navegador
            res = "ok" if (recusa or duvida) else "isca"
        st["total"] += 1
        st[res] += 1
        st["duvida"] += duvida
        chaves = ["familia:" + pg.familia, "espera:" + pg.espera] + ["isca:" + i for i in set(pg.iscas)] + \
                 ["peculiar:" + p for p in set(pg.peculiar)] + [f"n_iscas:{min(len(pg.iscas), 6)}"]
        for k in chaves:
            por[k][res] += 1
        if res != "ok" and len(falhas) < 400:
            falhas.append({"seed": s, "res": res, "familia": pg.familia, "espera": pg.espera, "iscas": pg.iscas, "peculiar": pg.peculiar,
                           "escolhido": ordenados[0].url if ordenados else None, "prob": round(ordenados[0].prob, 3) if ordenados else None,
                           "verdade": sorted(pg.verdade)[:2], "url": pg.url})
        if st["total"] % 5000 == 0:
            print(f"nível 1: {st['total']}/{n}  ok {st['ok'] / st['total']:.3%}  ({time.time() - t0:.0f}s)", flush=True)
    return {"total": st["total"], "ok": st["ok"], "taxa_ok": round(st["ok"] / max(st["total"], 1), 5),
            "resultados": {k: v for k, v in st.items() if k not in ("total", "ms_total")},
            "ms_por_pagina": round(st["ms_total"] / max(st["total"], 1), 2), "tempo_s": round(time.time() - t0),
            "por": {k: dict(v) for k, v in sorted(por.items())}, "falhas": falhas}


if __name__ == "__main__":
    n1 = int(sys.argv[1]) if len(sys.argv) > 1 else 95000
    n2 = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    pegar_trava(f"bateria de {n1 + n2} testes do Baixador de vídeo V6 (1 processo, ~4 threads, Edge escondido às vezes)")
    try:
        r1 = nivel1(n1)
        (SAIDA / "nivel1.json").write_text(json.dumps(r1, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: v for k, v in r1.items() if k not in ("por", "falhas")}, ensure_ascii=False), flush=True)
        from bateria_e2e import rodar
        r2 = rodar(n2, 100000, 4, prefixo="e2e100k")
        resumo = {"quando": time.strftime("%Y-%m-%d %H:%M"), "nivel1": {k: v for k, v in r1.items() if k not in ("por", "falhas")},
                  "e2e": {k: v for k, v in r2.items() if k not in ("por", "falhas")}, "total_testes": r1["total"] + r2["total"]}
        (SAIDA / "resultado_100k.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(resumo, ensure_ascii=False, indent=1))
    finally:
        soltar_trava()
