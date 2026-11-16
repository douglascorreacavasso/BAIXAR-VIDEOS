# -*- coding: utf-8 -*-
"""Grafo de experiência do Baixador — o "pedaço de cérebro" deste programa, no CONTRATO GEP V1.

Regra do doug (15/11/2026): todo programa guarda o que aprende em nós, arestas com peso, frequência de uso,
variáveis e episódios com retorno, no formato do cérebro do Nerael, para depois encaixar no cérebro de uma vez.
Formato e biblioteca são os do contrato único:
    FORJA\\CONTRATO_GRAFO_PROGRAMAS\\CONTRATO_GEP_V1.md  +  gep.py  +  validar_gep.py
Arquivo: E:\\NERAEL_GEP\\baixador.gep.sqlite  (os testes usam outro arquivo, na pasta de dados de teste).

O grafo é USADO, não é enfeite: a memória por site e os ajustes do modelo são lidos daqui ao abrir o programa e
gravados aqui a cada download. Os JSON antigos (memoria_sites.json / modelo_aprendido.json) só servem para migrar.

O que vira o quê:
  site (entidade, cortex) --usa--> motor/estratégia (acao, motora), origem/tipo/forma do endereço/player (padrao, sensorial)
      contadores da aresta = sucessos/falhas/usos; peso = chance de dar certo
  característica (padrao, sensorial) --causa--> vídeo principal (conceito, nucleos_acao)
      peso = peso do modelo (fábrica treinada em simulação + ajuste do uso); histórico de cada peso em gep_variaveis
  cada link = episódio (nós usados juntos ganham coativação); "deu certo / deu erro" = retorno do uso;
      botões ✓ / ✕ = retorno do doug (douglas_feedback)
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import sys
import threading
import time
from concurrent.futures import Future
from pathlib import Path

from .base import DADOS_DIR

PASTA_CONTRATO = Path(r"C:\Users\Douglas\Downloads\ARCH-NEURAL 3D-MNLS v3\FORJA\CONTRATO_GRAFO_PROGRAMAS")
GEP_REAL = Path(r"E:\NERAEL_GEP\baixador.gep.sqlite")
VERSAO_PROGRAMA = "V6"

REGIAO_DO_GRUPO = {"motores": "motora", "estrategias": "motora", "origens": "sensorial", "tipos": "sensorial",
                   "moldes": "sensorial", "players": "sensorial", "confirmados": "hipocampo", "tecnicas": "motora",
                   "obstaculos": "amigdala"}


def caminho_gep() -> Path:
    if os.environ.get("BAIXADOR_DADOS"):          # testes e baterias: nunca no grafo de verdade
        return DADOS_DIR / "baixador_teste.gep.sqlite"
    return GEP_REAL


def _carregar_gep():
    if str(PASTA_CONTRATO) not in sys.path:
        sys.path.insert(0, str(PASTA_CONTRATO))
    import gep  # noqa: E402  (biblioteca única do contrato)
    return gep


def _nome(grupo: str, chave: str) -> str:
    gep = _carregar_gep()
    base = gep._slug(f"{grupo}_{chave}")
    if len(base) > 70 or grupo in ("moldes",):    # formas de endereço: slug + impressão digital (sem colisão)
        base = gep._slug(f"{grupo}_{hashlib.sha1(chave.encode()).hexdigest()[:10]}_{chave}")[:110]
    return base


_abertos: dict[str, "Grafo"] = {}
_lock_abertos = threading.Lock()


class Grafo:
    """Dono único do arquivo GEP: todas as escritas passam por UMA thread (o sqlite do gep.py é de uma thread só)."""

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.erro = ""
        self._fila: queue.Queue = queue.Queue()
        self.g = None
        pronto = threading.Event()
        threading.Thread(target=self._dono, args=(pronto,), daemon=True, name="grafo-gep").start()
        pronto.wait(30)

    @classmethod
    def abrir(cls, caminho: Path | None = None) -> "Grafo":
        c = str(Path(caminho or caminho_gep()))
        with _lock_abertos:
            if c not in _abertos:
                _abertos[c] = cls(Path(c))
            return _abertos[c]

    @property
    def disponivel(self) -> bool:
        return self.g is not None

    def _dono(self, pronto: threading.Event):
        try:
            gep = _carregar_gep()
            self.gep = gep
            self.g = gep.GEP("baixador", str(self.caminho), versao_programa=VERSAO_PROGRAMA, autor="baixador_v6")
        except Exception as e:  # sem E: ou sem o contrato: o programa segue funcionando (memória fica só nesta sessão)
            self.erro = f"{type(e).__name__}: {e}"
        pronto.set()
        while True:
            fn, fut = self._fila.get()
            try:
                fut.set_result(fn(self.g) if self.g is not None else None)
            except Exception as e:
                fut.set_exception(e)

    def _rodar(self, fn, esperar: bool = True, tempo: float = 60):
        fut: Future = Future()
        self._fila.put((fn, fut))
        return fut.result(tempo) if esperar else fut

    # ------------------------------------------------------------ ajudantes (sempre na thread dona)
    @staticmethod
    def _fatos(g, nid: str, novos: dict):
        r = g.con.execute("SELECT fatos FROM gep_nos WHERE id=?", (nid,)).fetchone()
        f = json.loads(r[0] or "{}") if r else {}
        f.update(novos)
        g.con.execute("UPDATE gep_nos SET fatos=? WHERE id=?", (json.dumps(f, ensure_ascii=False), nid))

    @staticmethod
    def _contadores(g, eid: str, sucessos: int, falhas: int, usos: int, peso: float, fatos: dict | None = None):
        g.con.execute("UPDATE gep_arestas SET sucessos=?, falhas=?, usos=?, peso=?, ultimo_uso=? WHERE id=?",
                      (sucessos, falhas, usos, peso, time.strftime("%Y-%m-%d %H:%M:%S"), eid))
        if fatos:
            r = g.con.execute("SELECT fatos FROM gep_arestas WHERE id=?", (eid,)).fetchone()
            f = json.loads(r[0] or "{}") if r else {}
            f.update(fatos)
            g.con.execute("UPDATE gep_arestas SET fatos=? WHERE id=?", (json.dumps(f, ensure_ascii=False), eid))

    def _no_item(self, g, grupo: str, chave: str) -> str:
        tipo = "acao" if grupo.startswith("motores") or grupo in ("estrategias", "tecnicas") else "padrao"
        regiao = REGIAO_DO_GRUPO.get(grupo, "motora" if grupo.startswith("motores") else "sensorial")
        return g.no(_nome(grupo, chave), str(chave)[:600], tipo=tipo, camada=regiao, fatos={"grupo": grupo, "chave": chave})

    def _no_site(self, g, dom: str) -> str:
        return g.no(_nome("site", dom), dom[:600], tipo="entidade", camada="cortex", fatos={"dominio": dom})

    # ------------------------------------------------------------ memória por site
    def gravar_memoria(self, dados: dict):
        def fazer(g):
            meta = {k: v for k, v in dados.items() if k.startswith("_")}
            if meta:
                g.con.execute("INSERT OR REPLACE INTO gep_meta VALUES(?,?)", ("baixador_meta", json.dumps(meta, ensure_ascii=False)))
            for dom, s in dados.items():
                if dom.startswith("_") or not isinstance(s, dict):
                    continue
                sid = self._no_site(g, dom)
                self._fatos(g, sid, {k: v for k, v in s.items() if not isinstance(v, dict)})
                for grupo, conteudo in s.items():
                    if not isinstance(conteudo, dict):
                        continue
                    for chave, r in conteudo.items():
                        nid = self._no_item(g, grupo, chave)
                        eid = g.aresta(sid, nid, "usa", 0.5, fatos={"grupo": grupo}, origem="uso_programa")
                        if grupo == "players":
                            n = int(r) if not isinstance(r, dict) else 0
                            self._contadores(g, eid, 0, 0, n, n / (n + 1.0))
                        elif isinstance(r, dict):
                            ok, fl = int(r.get("ok", 0)), int(r.get("fail", 0))
                            self._contadores(g, eid, ok, fl, ok + fl, (ok + 1.0) / (ok + fl + 2.0))
            g.con.commit()
        self._rodar(fazer)

    def carregar_memoria(self) -> dict:
        def fazer(g):
            dados: dict = {}
            r = g.con.execute("SELECT valor FROM gep_meta WHERE chave='baixador_meta'").fetchone()
            if r:
                dados.update(json.loads(r[0] or "{}"))
            sites = {}
            for nid, fatos in g.con.execute("SELECT id, fatos FROM gep_nos WHERE tipo='entidade' AND camada='cortex'"):
                f = json.loads(fatos or "{}")
                dom = f.pop("dominio", None)
                if dom:
                    sites[nid] = dom
                    dados[dom] = f
            for de, fatos_no, s, fl, usos in g.con.execute(
                    "SELECT a.de, n.fatos, a.sucessos, a.falhas, a.usos FROM gep_arestas a JOIN gep_nos n ON n.id=a.para "
                    "WHERE a.tipo='usa'"):
                if de not in sites:
                    continue
                fn = json.loads(fatos_no or "{}")
                grupo, chave = fn.get("grupo"), fn.get("chave")
                if not grupo:
                    continue
                site = dados[sites[de]]
                if grupo == "players":
                    site.setdefault("players", {})[chave] = usos
                else:
                    site.setdefault(grupo, {})[chave] = {"ok": s, "fail": fl}
            return dados
        return self._rodar(fazer) or {}

    # ------------------------------------------------------------ modelo
    def gravar_modelo(self, base: dict, ajustes: dict, n_atualizacoes: int):
        def fazer(g):
            alvo = g.no("video_principal", "vídeo principal da página", tipo="conceito", camada="nucleos_acao",
                        fatos={"regra": "chance = sigmoide(soma(peso da aresta x valor da característica))"},
                        origem="claude_infraestrutura")
            self._fatos(g, alvo, {"n_atualizacoes": n_atualizacoes})
            for k in sorted(set(base) | set(ajustes)):
                b, a = float(base.get(k, 0.0)), float(ajustes.get(k, 0.0))
                if abs(b) < 1e-6 and abs(a) < 1e-6:
                    continue
                nid = g.no(_nome("caract", k), k[:600], tipo="padrao", camada="sensorial", fatos={"chave": k},
                           origem="simulacao_propria")
                eid = g.aresta(nid, alvo, "causa", round(b + a, 5), fatos={"base": round(b, 5)}, origem="simulacao_propria")
                r = g.con.execute("SELECT peso, fatos FROM gep_arestas WHERE id=?", (eid,)).fetchone()
                antes = json.loads(r[1] or "{}").get("ajuste", 0.0)
                if abs(r[0] - (b + a)) > 1e-6 or abs(antes - a) > 1e-6:
                    g.con.execute("UPDATE gep_arestas SET peso=? WHERE id=?", (round(b + a, 5), eid))
                    f = json.loads(r[1] or "{}")
                    f.update({"base": round(b, 5), "ajuste": round(a, 5)})
                    g.con.execute("UPDATE gep_arestas SET fatos=? WHERE id=?", (json.dumps(f), eid))
                    if abs(antes - a) > 1e-6:  # o uso mexeu neste peso: guarda o histórico (variável ajustável)
                        g.variavel(_nome("peso", k)[:100], round(b + a, 5), -12.0, 12.0, "peso", de=nid)
            g.con.commit()
        self._rodar(fazer)

    def carregar_ajustes(self):
        def fazer(g):
            linhas = g.con.execute("SELECT n.fatos, a.fatos FROM gep_arestas a JOIN gep_nos n ON n.id=a.de "
                                   "WHERE a.tipo='causa' AND a.para=?", (f"{g.programa}:video_principal",)).fetchall()
            if not linhas:
                return None
            ajustes = {}
            for fn, fa in linhas:
                k = json.loads(fn or "{}").get("chave")
                a = json.loads(fa or "{}").get("ajuste", 0.0)
                if k and abs(a) > 1e-6:
                    ajustes[k] = a
            r = g.con.execute("SELECT fatos FROM gep_nos WHERE id=?", (f"{g.programa}:video_principal",)).fetchone()
            n = int(json.loads(r[0] or "{}").get("n_atualizacoes", 0)) if r else 0
            return ajustes, n
        return self._rodar(fazer)

    # ------------------------------------------------------------ episódios e retorno
    def episodio(self, t, dom: str):
        ok = t.estado == "pronto"

        def fazer(g):
            nos = [self._no_site(g, dom)]
            motor = (t.caminho or "").split(" ")[0].split("/")[0]
            if motor:
                nos.append(self._no_item(g, "motores", motor))
            c = t.escolhido
            if c is not None:
                nos += [self._no_item(g, "origens", o) for o in sorted(c.origens)]
                nos.append(self._no_item(g, "tipos", c.tipo))
            for ob in sorted(getattr(t, "obstaculos", {}) or {}):   # obstáculo e técnica juntos no episódio = coativação
                nos.append(self._no_item(g, "obstaculos", ob))
            for tec in getattr(t, "tecnicas_tentadas", []) or []:
                nos.append(self._no_item(g, "tecnicas", tec))
            if getattr(t, "tecnica", ""):
                nos.append(self._no_item(g, "tecnicas", t.tecnica))
            nos = list(dict.fromkeys(nos))
            if t.capturas:
                nos.append(g.no("tela_area_marcada", "área marcada na tela", tipo="padrao", camada="sensorial"))
            if ok:
                nos.append(g.no("resultado_ok", "baixou e conferiu (tem imagem e decodifica)", tipo="estado", camada="hipocampo"))
            else:
                cat = t.categoria_erro or t.estado
                nos.append(g.no(_nome("erro", cat), f"erro: {cat}", tipo="estado", camada="amigdala", fatos={"categoria": cat}))
            info = t.info_arquivo or {}
            entrada = json.dumps({"url": t.url[:300], "site": dom, "estado": t.estado, "caminho": t.caminho[:120],
                                  "altura": info.get("altura"), "vcodec": info.get("vcodec"),
                                  "segundos": round((t.fim or 0) - (t.inicio or 0), 1)}, ensure_ascii=False)[:600]
            arquivo = t.arquivo if ok and t.arquivo and os.path.exists(t.arquivo) else None
            ep = g.episodio(entrada=entrada, saida_arquivo=arquivo, nos=nos, origem="uso_programa")
            g.feedback(ep, 0.8 if ok else 0.2, "baixado e conferido" if ok else f"falhou: {t.categoria_erro}", origem="uso_programa")
            return ep
        t.gep_episodio = self._rodar(fazer)

    def retorno(self, t, certo: bool):
        ep = getattr(t, "gep_episodio", None)
        if not ep:
            return

        def fazer(g):
            g.feedback(ep, 1.0 if certo else 0.0, "era esse vídeo" if certo else "não era esse vídeo", origem="douglas_feedback")
        self._rodar(fazer)

    # ------------------------------------------------------------ leitura, validação e exportação
    def contagem(self) -> dict:
        def fazer(g):
            nos = g.con.execute("SELECT COUNT(*) FROM gep_nos").fetchone()[0]
            ars = g.con.execute("SELECT COUNT(*) FROM gep_arestas").fetchone()[0]
            eps = g.con.execute("SELECT COUNT(*) FROM gep_episodios").fetchone()[0]
            reg = dict(g.con.execute("SELECT camada, COUNT(*) FROM gep_nos GROUP BY camada").fetchall())
            return {"nos": nos, "arestas": ars, "episodios": eps, "regioes": reg}
        return self._rodar(fazer) or {"nos": 0, "arestas": 0, "episodios": 0, "regioes": {}}

    def validar(self) -> tuple[list, list]:
        sys.path.insert(0, str(PASTA_CONTRATO))
        import validar_gep
        self._rodar(lambda g: g.con.commit())
        return validar_gep.validar(validar_gep.carregar(str(self.caminho)))

    def exportar(self, pasta: Path) -> dict:
        sys.path.insert(0, str(PASTA_CONTRATO))
        import validar_gep
        self._rodar(lambda g: g.con.commit())
        d = validar_gep.carregar(str(self.caminho))
        erros, avisos = validar_gep.validar(d)
        pasta = Path(pasta)
        pasta.mkdir(parents=True, exist_ok=True)
        (pasta / "baixador.gep.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"nos": len(d["nos"]), "arestas": len(d["arestas"]), "episodios": len(d["episodios"]),
                "passou": not erros, "erros": erros[:10], "avisos": avisos[:10], "arquivo": str(pasta / "baixador.gep.json")}
