# -*- coding: utf-8 -*-
"""Aprendizado de máquina do baixador.

1) MODELO (regressão logística, em Python puro): recebe as características de cada candidato a vídeo
   (de onde veio, tipo, palavras do endereço, se está dentro de um link de "relacionados", se é vídeo de
   fundo mudo em loop, se casou com a área que você marcou na tela...) e dá a chance de ser O vídeo do link.
   Pesos iniciais: treinados em páginas simuladas (testes/treinar_modelo.py). Depois ele continua
   aprendendo com o uso: cada download confirmado e cada "não era esse" ajusta os pesos.

2) MEMÓRIA DE PADRÕES POR SITE: para cada site guarda qual player ele usa, de onde veio o vídeo certo
   (og:video, JSON-LD, player, rede...), a FORMA do endereço certo (/v/{n}/{h}/index.m3u8) e quais
   motores/estratégias funcionaram. Isso vira característica do modelo na próxima vez.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from urllib.parse import urlparse

from .base import MEMORIA_PATH, MODELO_INICIAL_PATH, MODELO_PATH, dominio, gravar_json, ler_json
from .detector import Candidato, molde_da_url

ORIGENS = ["video_tag", "source_tag", "source_solto", "og_video", "twitter_stream", "jsonld", "iframe", "config_player",
           "script", "script_base64", "data_attr", "link", "preload", "texto", "rede", "ytdlp", "dom"]
TIPOS = ["progressivo", "hls", "dash", "embed", "audio", "desconhecido"]

RE_PROPAGANDA = re.compile(r"(?<![a-z])(ads?|adserver|advert\w*|preroll|midroll|postroll|vast|vpaid|doubleclick|"
                           r"googlesyndication|imasdk|promo|sponsor\w*|banner|outstream|adtag|pubads|exoclick|"
                           r"trafficjunky|juicyads|popads|adnxs|criteo|taboola|outbrain)(?![a-z])", re.I)
RE_PREVIEW = re.compile(r"(?<![a-z])(preview|previews|teaser|trailer|sample|thumb\w*|sprite\w*|storyboard|hover|"
                        r"snippet|intro|clip_preview|gif|loop|bg|background|hero|mini|short_preview|vid_prev)(?![a-z])", re.I)
RE_MESTRE = re.compile(r"(?<![a-z])(master|playlist|index|manifest|main|source|original|full|download)(?![a-z])", re.I)
RE_AUDIO = re.compile(r"(?<![a-z])(audio|aac|mp4a|sound|music_only)(?![a-z])", re.I)


def sigmoide(z: float) -> float:
    if z < -40:
        return 0.0
    if z > 40:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def ids_da_pagina(url: str) -> set[str]:
    """Pedaços do endereço da página que parecem identificador do vídeo (/video/123456, ?v=abc123XYZ, /v/slug-9f3a)."""
    try:
        p = urlparse(url)
    except Exception:
        return set()
    txt = (p.path or "") + "&" + (p.query or "")
    ids = set()
    for tok in re.split(r"[/&=?._\-+,;:]", txt):
        if len(tok) >= 4 and tok.isdigit():
            ids.add(tok)
        elif len(tok) >= 6 and re.search(r"\d", tok) and re.search(r"[a-zA-Z]", tok) and not RE_PROPAGANDA.search(tok):
            ids.add(tok)
    return ids


def caracteristicas(c: Candidato, pagina_host: str = "", memoria_site: dict | None = None,
                    casamento_tela: float | None = None, pagina_ids: set | None = None) -> dict[str, float]:
    f: dict[str, float] = {"vies": 1.0}
    if pagina_ids:
        if any(i in c.url for i in pagina_ids):
            f["id_da_pagina"] = 1.0
        else:
            f["id_ausente"] = 1.0
    for o in c.origens:
        f["origem:" + o] = 1.0
    f["n_origens"] = min(len(c.origens), 5) / 5.0
    f["tipo:" + (c.tipo if c.tipo in TIPOS else "desconhecido")] = 1.0
    url = c.url
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        caminho = (p.path or "") + "?" + (p.query or "")
    except Exception:
        host, caminho = "", url
    if RE_PROPAGANDA.search(host) or RE_PROPAGANDA.search(caminho):
        f["url_propaganda"] = 1.0
    if RE_PREVIEW.search(caminho):
        f["url_previa"] = 1.0
    if RE_MESTRE.search(caminho):
        f["url_mestre"] = 1.0
    if RE_AUDIO.search(caminho) and c.tipo != "embed":
        f["url_audio"] = 1.0
    if pagina_host:  # pagina_host = dominio() da página (sem www/m.)
        f["mesmo_site"] = 1.0 if host and dominio(url) == pagina_host else 0.0
    if re.search(r"(cdn|media|video|stream|vod|edge|cache|akamai|cloudfront|fbcdn|googlevideo)", host):
        f["host_midia"] = 1.0
    f["altura"] = min(c.altura, 2160) / 1080.0 if c.altura else 0.0
    f["sem_altura"] = 0.0 if c.altura else 1.0
    f["ordem"] = 1.0 / c.ordem if c.ordem else 0.0
    f["primeiro"] = 1.0 if c.ordem == 1 else 0.0
    f["ocorrencias"] = math.log1p(c.ocorrencias) / 2.0
    f["n_grupo"] = math.log1p(max(0, c.ctx.get("n_grupo", 1) - 1))
    n_total = c.ctx.get("n_total", 1) or 1
    f["poucos_cands"] = 1.0 if n_total <= 2 else 0.0
    cx = c.ctx
    if cx.get("ctx_ruim") and not cx.get("ctx_ruim_parcial"):
        f["ctx_ruim"] = 1.0
    if cx.get("ctx_bom"):
        f["ctx_bom"] = 1.0
    if cx.get("dentro_link"):
        f["dentro_link"] = 1.0
    if cx.get("aside"):
        f["aside"] = 1.0
    if cx.get("autoplay") and cx.get("muted") and cx.get("loop"):
        f["fundo_mudo_loop"] = 1.0
    elif cx.get("autoplay") and cx.get("muted"):
        f["autoplay_mudo"] = 1.0
    if cx.get("controls"):
        f["controles"] = 1.0
    if cx.get("poster"):
        f["poster"] = 1.0
    if cx.get("indice_video") == 1:
        f["video_1"] = 1.0
    area = (cx.get("largura") or 0) * (cx.get("altura_tag") or 0)
    if area:
        f["area_tag"] = min(math.log1p(area) / 14.0, 1.2)
        if area < 200 * 150:
            f["tag_pequena"] = 1.0
    # vindos do navegador (rede): tamanho, se veio depois do play, área visível
    if cx.get("tamanho_mb") is not None:
        f["tamanho_log"] = min(math.log1p(cx["tamanho_mb"]) / 5.0, 1.5)
        if cx["tamanho_mb"] < 0.3 and c.tipo == "progressivo":
            f["muito_pequeno"] = 1.0
    if cx.get("depois_do_play"):
        f["depois_do_play"] = 1.0
    if cx.get("antes_do_play"):
        f["antes_do_play"] = 1.0
    if cx.get("area_visivel"):
        f["area_visivel"] = min(cx["area_visivel"] / 600000.0, 1.5)
    if cx.get("maior_visivel"):
        f["maior_visivel"] = 1.0
    if cx.get("duracao"):
        d = cx["duracao"]
        f["duracao_log"] = min(math.log1p(d) / 6.0, 1.5)
        if d < 12:
            f["duracao_curta"] = 1.0
    if cx.get("chave") in ("hd_src", "sd_src", "playable_url", "browser_native_hd_url", "contenturl", "hlsurl",
                           "playback_url", "file", "manifest", "videourl", "video_url", "load", "source"):
        f["chave_forte"] = 1.0
    if cx.get("script_ruim") and not cx.get("script_bom_tambem"):
        f["script_ruim"] = 1.0
    if cx.get("script_bom"):
        f["script_bom"] = 1.0
    if cx.get("molde_rep"):
        f["molde_rep"] = math.log1p(cx["molde_rep"])
    if cx.get("e_entrada_ytdlp"):
        f["entrada_ytdlp"] = 1.0
    if casamento_tela is not None:
        f["tela_casou"] = casamento_tela          # 0..1 (1 = imagem igual à área marcada)
        f["tela_marcada"] = 1.0
    # memória de padrões do site
    if memoria_site:
        mol = molde_da_url(url)
        rm = memoria_site.get("moldes", {}).get(mol)
        if rm:
            f["site_molde"] = (rm.get("ok", 0) - rm.get("fail", 0)) / (rm.get("ok", 0) + rm.get("fail", 0) + 1.0)
        best_o = 0.0
        for o in c.origens:
            ro = memoria_site.get("origens", {}).get(o)
            if ro:
                best_o = max(best_o, (ro.get("ok", 0) - ro.get("fail", 0)) / (ro.get("ok", 0) + ro.get("fail", 0) + 1.0))
        if best_o:
            f["site_origem"] = best_o
        rt = memoria_site.get("tipos", {}).get(c.tipo)
        if rt:
            f["site_tipo"] = (rt.get("ok", 0) - rt.get("fail", 0)) / (rt.get("ok", 0) + rt.get("fail", 0) + 1.0)
    return f


AJUSTE_MAX = 2.0   # quanto o uso pode mover cada peso de fábrica, para cima ou para baixo


class Modelo:
    """Regressão logística com pesos esparsos (nome da característica -> peso).
    Peso final = peso de fábrica (modelo_inicial.json) + AJUSTE aprendido com o seu uso (dados/modelo_aprendido.json).
    Guardar só o ajuste deixa uma versão nova trazer pesos de fábrica melhores sem perder o que ele aprendeu com você."""

    def __init__(self, pesos: dict | None = None, ajustes: dict | None = None):
        self.base: dict[str, float] = dict(pesos or {})
        self.ajustes: dict[str, float] = dict(ajustes or {})
        self.n_atualizacoes = 0
        self._lock = threading.Lock()

    @property
    def pesos(self) -> dict[str, float]:
        p = dict(self.base)
        for k, v in self.ajustes.items():
            p[k] = p.get(k, 0.0) + v
        return p

    @classmethod
    def carregar(cls, com_uso: bool = True) -> "Modelo":
        """Pesos de fábrica do JSON; o que ele aprendeu com o uso vem do GRAFO (arestas característica -> vídeo
        principal). O modelo_aprendido.json antigo só é lido uma vez, para migrar."""
        base = ler_json(MODELO_INICIAL_PATH, {}).get("pesos", {})
        m = cls(base, {})
        m.usar_grafo = com_uso
        if not com_uso:
            return m
        from .grafo import Grafo
        do_grafo = Grafo.abrir().carregar_ajustes() if Grafo.abrir().disponivel else None
        if do_grafo is not None:
            m.ajustes, m.n_atualizacoes = do_grafo
        else:
            aprendido = ler_json(MODELO_PATH, {})
            m.ajustes = dict(aprendido.get("ajustes") or {})
            m.n_atualizacoes = int(aprendido.get("n_atualizacoes", 0))
            m.salvar()  # migra para o grafo já na 1ª vez
        return m

    def salvar(self, caminho=None):
        with self._lock:
            base = dict(self.base)
            ajustes = {k: round(v, 5) for k, v in self.ajustes.items() if abs(v) > 1e-6}
            n = self.n_atualizacoes
        if caminho is not None:  # gravação avulsa em arquivo (usada pelos testes)
            gravar_json(caminho, {"ajustes": ajustes, "n_atualizacoes": n, "salvo_em": time.strftime("%Y-%m-%d %H:%M:%S")})
            return
        if getattr(self, "usar_grafo", True):
            from .grafo import Grafo
            g = Grafo.abrir()
            if g.disponivel:
                g.gravar_modelo(base, ajustes, n)
            else:
                gravar_json(MODELO_PATH, {"ajustes": ajustes, "n_atualizacoes": n, "salvo_em": time.strftime("%Y-%m-%d %H:%M:%S")})

    def _w(self, k: str) -> float:
        return self.base.get(k, 0.0) + self.ajustes.get(k, 0.0)

    def prob(self, f: dict[str, float]) -> float:
        return sigmoide(sum(self._w(k) * v for k, v in f.items()))

    def atualizar(self, f: dict[str, float], y: float, taxa: float = 0.05, l2: float = 1e-4, peso: float = 1.0,
                  na_base: bool = False):
        with self._lock:
            erro = (y - self.prob(f)) * peso
            alvo = self.base if na_base else self.ajustes
            for k, v in f.items():
                w = alvo.get(k, 0.0)
                alvo[k] = w + taxa * (erro * v - l2 * w)
                if not na_base:  # o uso ajusta, mas não apaga o que foi treinado (achado: milhares de casos de um site só
                    alvo[k] = max(-AJUSTE_MAX, min(AJUSTE_MAX, alvo[k]))  # derrubavam a chance do vídeo certo para 1%)
            self.n_atualizacoes += 0 if na_base else 1


class Memoria:
    """Memória de padrões por site (substitui o estrategias_memoria.json da V5 e importa o dele)."""

    def __init__(self):
        """Lê do GRAFO (site -> motores/estratégias/origens/formas/players). Na 1ª vez migra o memoria_sites.json."""
        from .grafo import Grafo
        self.grafo = Grafo.abrir()
        # sem o grafo (E: fora, contrato ausente): segue com o JSON, para não perder o que aprendeu
        self.dados: dict = self.grafo.carregar_memoria() if self.grafo.disponivel else ler_json(MEMORIA_PATH, {})
        self._lock = threading.Lock()
        self._sujo = False
        if not self.dados:
            antigo = ler_json(MEMORIA_PATH, {})
            if antigo:
                self.dados = antigo
                self._sujo = True
                self.salvar()

    def site(self, dom: str) -> dict:
        with self._lock:
            return self.dados.get(dom, {})

    def _conta(self, dom: str, grupo: str, chave: str, ok: int = 0, fail: int = 0):
        s = self.dados.setdefault(dom, {})
        r = s.setdefault(grupo, {}).setdefault(chave, {"ok": 0, "fail": 0})
        r["ok"] += ok
        r["fail"] += fail
        s["ultimo"] = time.strftime("%Y-%m-%d %H:%M")
        self._sujo = True

    def registrar(self, dom: str, grupo: str, chave: str, ok: int = 0, fail: int = 0):
        with self._lock:
            self._conta(dom, grupo, chave, ok, fail)

    def registrar_candidato(self, dom: str, c: Candidato, ok: bool, forca: int = 1):
        with self._lock:
            for o in c.origens:
                self._conta(dom, "origens", o, ok=forca if ok else 0, fail=0 if ok else forca)
            self._conta(dom, "tipos", c.tipo, ok=forca if ok else 0, fail=0 if ok else forca)
            self._conta(dom, "moldes", molde_da_url(c.url), ok=forca if ok else 0, fail=0 if ok else forca)

    def registrar_players(self, dom: str, players: list[str]):
        with self._lock:
            s = self.dados.setdefault(dom, {})
            pl = s.setdefault("players", {})
            for p in players:
                pl[p] = pl.get(p, 0) + 1
            self._sujo = True

    def marcar(self, dom: str, chave: str, valor=True):
        with self._lock:
            self.dados.setdefault(dom, {})[chave] = valor
            self._sujo = True

    def ordem(self, dom: str, grupo: str, chaves: list[str]) -> list[str]:
        """Ordena estratégias/motores pela chance de sucesso (média de Laplace), mantendo a ordem original no empate."""
        s = self.site(dom).get(grupo, {})

        def nota(k):
            r = s.get(k, {})
            return (r.get("ok", 0) + 1.0) / (r.get("ok", 0) + r.get("fail", 0) + 2.0)
        return sorted(chaves, key=lambda k: -nota(k))

    def salvar(self):
        with self._lock:
            if not self._sujo:
                return
            copia = json.loads(json.dumps(self.dados))
            self._sujo = False
        if self.grafo.disponivel:
            self.grafo.gravar_memoria(copia)
        else:
            gravar_json(MEMORIA_PATH, copia)

    def n_sites(self) -> int:
        return len([k for k in self.dados if not k.startswith("_")])

    def marcar_meta(self, chave: str, valor):
        with self._lock:
            self.dados[chave] = valor
            self._sujo = True

    def importar_v5(self, caminho) -> int:
        """Traz o que a V5 aprendeu (estratégia de formato por site)."""
        antigo = ler_json(caminho, {})
        n = 0
        with self._lock:
            for dom, estrategias in antigo.items():
                if not isinstance(estrategias, dict):
                    continue
                for sid, r in estrategias.items():
                    if isinstance(r, dict):
                        alvo = self.dados.setdefault(dom, {}).setdefault("estrategias", {}).setdefault(sid, {"ok": 0, "fail": 0})
                        alvo["ok"] += int(r.get("ok", 0))
                        alvo["fail"] += int(r.get("fail", 0))
                        n += 1
            self._sujo = True
        return n


def ranquear(cands: list[Candidato], modelo: Modelo, pagina_host: str, memoria_site: dict | None,
             casamento: dict | None = None, pagina_url: str = "") -> list[Candidato]:
    """Dá a chance de cada candidato e devolve do mais provável ao menos provável."""
    ids = ids_da_pagina(pagina_url) if pagina_url else None
    for c in cands:
        f = caracteristicas(c, pagina_host, memoria_site, (casamento or {}).get(c.url), ids)
        c.ctx["_f"] = f
        c.prob = modelo.prob(f)
    return sorted(cands, key=lambda c: -c.prob)


def escolher_grupo(ordenados: list[Candidato], q_max: int) -> list[Candidato]:
    """Do grupo vencedor (mesmo vídeo em várias qualidades), a melhor qualidade até o limite pedido primeiro."""
    if not ordenados:
        return []
    g = ordenados[0].grupo
    mesmo = [c for c in ordenados if c.grupo == g and (c.tipo == ordenados[0].tipo or {c.tipo, ordenados[0].tipo} <= {"progressivo", "audio"})]

    pref_ext = {"mp4": 0, "m4v": 1, "mov": 2, "webm": 3}

    def chave(c):
        a = c.altura or 0
        dentro = a <= q_max if a else True
        ext = re.search(r"\.([a-z0-9]{2,4})(?:\?|$)", c.url.lower())
        return (0 if dentro else 1, -a if dentro else a, pref_ext.get(ext.group(1) if ext else "", 5), -c.prob)
    return sorted(mesmo, key=chave)
