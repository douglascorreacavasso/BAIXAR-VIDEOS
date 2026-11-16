# -*- coding: utf-8 -*-
"""Treina os pesos iniciais do modelo nas páginas simuladas (conjunto "treino") e mede no conjunto "teste"
(sementes e palavras de isca diferentes). Grava baixador/modelo_inicial.json.

Uso:  .venv\\Scripts\\python.exe testes\\treinar_modelo.py [n_treino] [n_teste]
"""
from __future__ import annotations

import json
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

from baixador.aprendizado import Modelo, caracteristicas, ids_da_pagina, ranquear  # noqa: E402
from baixador.base import MODELO_INICIAL_PATH, dominio, gravar_json  # noqa: E402
from baixador.detector import analisar_html  # noqa: E402
from baixador.pipeline import Motor  # noqa: E402
from gerador import gerar  # noqa: E402


def tela_simulada(pg, cands, r):
    """Como a semelhança com a área marcada se comporta de verdade (medido em testes/teste_tela.py com vídeos reais:
    mesmo vídeo 0,78-0,99; outro vídeo 0,53-0,70). Prévia do PRÓPRIO vídeo também parece com ele."""
    notas = {}
    for c in cands:
        if c.url in pg.verdade:
            notas[c.url] = r.uniform(0.74, 0.99)
        elif any(p in c.url for p in ("preview", "teaser", "thumbs", "hover", "sprite", "storyboard")) and "/" + str(c.url).split("/")[-2] in str(pg.verdade):
            notas[c.url] = r.uniform(0.6, 0.95)
        else:
            notas[c.url] = r.uniform(0.35, 0.74)
    return notas


def exemplos(seed, conjunto):
    pg = gerar(seed, conjunto)
    pag = analisar_html(pg.html, pg.url)
    dom = dominio(pg.url)
    r = random.Random(seed * 31 + 5)
    notas = tela_simulada(pg, pag.candidatos, r) if r.random() < 0.3 else {}
    out = []
    for c in pag.candidatos:
        y = 1.0 if (pg.espera == "video" and c.url in pg.verdade) else 0.0
        out.append((caracteristicas(c, dom, None, notas.get(c.url), ids_da_pagina(pg.url)), y))
    return pg, pag, out


def avaliar(modelo, n, conjunto="teste", inicio=0):
    stats = Counter()
    por_familia = defaultdict(Counter)
    erros = []
    for s in range(inicio, inicio + n):
        pg = gerar(s, conjunto)
        pag = analisar_html(pg.html, pg.url)
        ordenados = ranquear(pag.candidatos, modelo, dominio(pg.url), None, pagina_url=pg.url)
        duvida = Motor._em_duvida(ordenados)
        if pg.espera == "video":
            ok = bool(ordenados) and ordenados[0].url in pg.verdade
            achou = any(c.url in pg.verdade for c in pag.candidatos)
            stats["video_total"] += 1
            stats["video_acerto"] += ok
            stats["video_detectado"] += achou
            stats["video_duvida"] += duvida
            # o programa de verdade, em dúvida, abre o navegador; aqui conta como "pediu ajuda"
            por_familia[pg.familia]["n"] += 1
            por_familia[pg.familia]["ok"] += ok
            if not ok and len(erros) < 60:
                erros.append({"seed": s, "familia": pg.familia, "iscas": pg.iscas, "achou": achou,
                              "escolhido": ordenados[0].url if ordenados else None, "prob": round(ordenados[0].prob, 3) if ordenados else 0,
                              "verdade": sorted(pg.verdade)[:2]})
        else:
            ok = (not ordenados) or duvida
            stats[pg.espera + "_total"] += 1
            stats[pg.espera + "_acerto"] += ok
            por_familia[pg.familia]["n"] += 1
            por_familia[pg.familia]["ok"] += ok
            if not ok and len(erros) < 60:
                erros.append({"seed": s, "familia": pg.familia, "iscas": pg.iscas, "escolhido": ordenados[0].url,
                              "prob": round(ordenados[0].prob, 3)})
    return stats, por_familia, erros


def treinar(n_treino=20000, epocas=6, taxa=0.08):
    print(f"gerando {n_treino} páginas de treino...", flush=True)
    dados = []
    for s in range(n_treino):
        _, _, ex = exemplos(s, "treino")
        dados.extend(ex)
    pos = sum(1 for _, y in dados if y)
    print(f"{len(dados)} candidatos ({pos} positivos)", flush=True)
    peso_pos = (len(dados) - pos) / max(pos, 1)
    peso_pos = min(peso_pos, 4.0)
    m = Modelo({})
    rng = random.Random(1)
    for ep in range(epocas):
        rng.shuffle(dados)
        t = taxa / (1 + ep)
        for f, y in dados:
            m.atualizar(f, y, taxa=t, l2=2e-5, peso=peso_pos if y else 1.0, na_base=True)
        # nenhum sinal sozinho pode decidir tudo (evita que uma palavra do meu gerador vire lei em site de verdade)
        for k in m.base:
            m.base[k] = max(-6.0, min(6.0, m.base[k]))
        st, _, _ = avaliar(m, 1500, "treino", inicio=n_treino + 10)
        print(f"época {ep + 1}: acerto (validação treino) {st['video_acerto'] / max(st['video_total'], 1):.4f}", flush=True)
    return m


if __name__ == "__main__":
    n_tr = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    n_te = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    t0 = time.time()
    m = treinar(n_tr)
    st, fam, erros = avaliar(m, n_te, "teste")
    res = {k: v for k, v in st.items()}
    res["acerto_video"] = round(st["video_acerto"] / max(st["video_total"], 1), 4)
    res["acerto_sem_video"] = round(st["nenhum_acerto"] / max(st["nenhum_total"], 1), 4)
    res["acerto_so_navegador"] = round(st["navegador_acerto"] / max(st["navegador_total"], 1), 4)
    res["por_familia"] = {k: round(v["ok"] / v["n"], 4) for k, v in sorted(fam.items())}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    gravar_json(MODELO_INICIAL_PATH, {"pesos": {k: round(v, 5) for k, v in sorted(m.pesos.items())},
                                      "origem": "treinado em páginas simuladas por testes/treinar_modelo.py (Claude, 15/11/2026)",
                                      "n_treino": n_tr, "medido_teste_cego": res})
    (AQUI / "saida").mkdir(exist_ok=True)
    gravar_json(AQUI / "saida" / "erros_modelo.json", erros)
    print(f"tempo {time.time() - t0:.0f}s")
