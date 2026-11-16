# -*- coding: utf-8 -*-
"""Teste do grafo no contrato GEP V1: memória e modelo voltam iguais do grafo, migração dos JSON antigos,
episódio + retorno do doug, e o validador do contrato tem de dizer PASSOU. Usa uma pasta de dados separada
(nunca mexe no E:\\NERAEL_GEP\\baixador.gep.sqlite de verdade)."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PASTA = Path(tempfile.mkdtemp(prefix="gep_teste_"))
os.environ["BAIXADOR_DADOS"] = str(PASTA)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from baixador.aprendizado import Memoria, Modelo  # noqa: E402
from baixador.base import MEMORIA_PATH, MODELO_PATH, gravar_json  # noqa: E402
from baixador.detector import Candidato  # noqa: E402
from baixador.grafo import Grafo, caminho_gep  # noqa: E402
from baixador.pipeline import Tarefa  # noqa: E402

erros = []


def conferir(nome, cond):
    print(("ok   " if cond else "ERRO ") + nome)
    if not cond:
        erros.append(nome)


conferir("arquivo de teste fora do E:", str(caminho_gep()).startswith(str(PASTA)))
g = Grafo.abrir()
conferir("grafo aberto pelo gep.py do contrato", g.disponivel and not g.erro)

antigo = {"_importado_v5": {"registros": 3}, "youtube.com": {"estrategias": {"mp4_prioritario": {"ok": 33, "fail": 5}},
          "players": {"html5": 4}, "moldes": {"/v/{n}/{h}/index.m3u8": {"ok": 2, "fail": 0}}, "impersonar": True}}
gravar_json(MEMORIA_PATH, antigo)
gravar_json(MODELO_PATH, {"ajustes": {"origem:video_tag": 0.25}, "n_atualizacoes": 7})
m = Memoria()
conferir("memória migrou do JSON", m.dados["youtube.com"]["estrategias"]["mp4_prioritario"] == {"ok": 33, "fail": 5})
mod = Modelo.carregar()
conferir("ajustes do modelo migraram", abs(mod.ajustes.get("origem:video_tag", 0) - 0.25) < 1e-9 and mod.n_atualizacoes == 7)

MEMORIA_PATH.unlink()
MODELO_PATH.unlink()
m.registrar("x.com.br", "motores", "direto", ok=2, fail=1)
m.registrar_players("x.com.br", ["jwplayer"])
m.marcar("x.com.br", "checar_formatos", True)
m.salvar()
mod.atualizar({"vies": 1.0, "tela_casou": 0.9}, 1.0)
mod.salvar()
m2 = Memoria()
esperado = json.loads(json.dumps(m.dados))
conferir("memória volta só do grafo", m2.dados == esperado)
if m2.dados != esperado:
    print("   veio:", json.dumps(m2.dados, ensure_ascii=False)[:600])
mod2 = Modelo.carregar()
conferir("modelo volta só do grafo", all(abs(mod2.ajustes.get(k, 0) - round(v, 5)) < 1e-6 for k, v in mod.ajustes.items()))
conferir("mesma chance depois de recarregar",
         abs(mod2.prob({"vies": 1, "tela_casou": 0.9}) - mod.prob({"vies": 1, "tela_casou": 0.9})) < 1e-4)

t = Tarefa(url="https://x.com.br/v/1")
t.estado, t.caminho, t.inicio, t.fim = "pronto", "direto · progressivo", 1.0, 3.5
c = Candidato(url="https://cdn.x.com.br/v/1/720p.mp4", tipo="progressivo")
c.origens = {"video_tag"}
c.prob = 0.93
t.escolhido = c
g.episodio(t, "x.com.br")
g.retorno(t, certo=True)
t2 = Tarefa(url="https://y.com/v/2")
t2.estado, t2.categoria_erro = "erro", "nao_achei"
g.episodio(t2, "y.com")
cont = g.contagem()
conferir("2 episódios gravados", cont["episodios"] == 2)
conferir("erro na amígdala", cont["regioes"].get("amigdala", 0) >= 1)
erros_val, avisos = g.validar()
conferir("validador do contrato: PASSOU", not erros_val)
for e in erros_val[:10]:
    print("   ", e)
r = g.exportar(PASTA / "exp")
conferir("exportação em JSON", Path(r["arquivo"]).exists() and r["passou"])
print(json.dumps(cont, ensure_ascii=False))
print("FALHAS:", erros or "nenhuma")
shutil.rmtree(PASTA, ignore_errors=True)
sys.stdout.flush()
os._exit(1 if erros else 0)
