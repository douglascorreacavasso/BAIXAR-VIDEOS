# -*- coding: utf-8 -*-
"""O caminho de cada link, do colar ao MP4 conferido.

  1. yt-dlp (mais de 1.800 sites conhecidos). Site conhecido => confia nele.
  2. Se o site não é conhecido (extrator "genérico") ou falhou: lê a página, lista todos os candidatos e o
     MODELO aprendido escolhe o vídeo principal (descartando propaganda, prévia, vídeo de fundo, relacionados).
  3. Se ainda está em dúvida: abre a página no Edge escondido, aperta play e escuta a rede.
  4. Área da tela marcada? Compara a imagem com os candidatos e ela pesa na escolha.
  5. Baixa pelo motor certo para o tipo (direto / HLS / DASH / player embutido), com troca de motor e de rota
     (direto -> proxies -> Tor) quando a categoria do erro pede.
  6. Confere com o ffprobe (tem imagem? duração?), passa para MP4 e ensina o modelo e a memória do site.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import requests

from . import navegador
from .aprendizado import Memoria, Modelo, escolher_grupo, ranquear
from .base import HISTORICO_PATH, caminho_livre, dominio, nome_seguro
from .detector import Candidato, analisar_html, chave_grupo, contar_moldes, juntar_candidatos, tipo_da_url
from .motores import (ESTRATEGIAS, UA, Cancelado, Contexto, ErroMotor, baixar_direto, baixar_ffmpeg,
                      baixar_streamlink, verificar_e_converter, ytdlp_baixar, ytdlp_info)
from .rede import montar_rotas, tor_novo_circuito
from .obstaculos import NOME_OBSTACULO, NOME_TECNICA, diagnosticar, montar_captura, plano

LIMIAR_RESERVA = 0.30   # chance mínima para tentar um 2º, 3º vídeo da página quando o 1º falha
LIMIAR_MINIMO = 0.12    # abaixo disso nem o 1º é baixado sozinho: pergunta (se puder) ou avisa "não achei"


def transitorio(msg: str) -> bool:
    return bool(re.search(r"\b(429|500|502|503|504|5xx)\b|too many requests|service unavailable|bad gateway|timed out|"
                          r"temporarily|try again|connection reset|remote end closed|incompleto", msg or "", re.I))

MENSAGENS = {
    "removido": "O vídeo foi removido, é privado ou este link não leva a um vídeo.",
    "login": "Este vídeo pede login. Em \"Cookies do navegador\", escolha o navegador em que você está logado no site.",
    "cookies_travados": "O navegador está aberto e tranca os cookies. Feche ele por alguns segundos e tente de novo.",
    "rede": "Não consegui falar com o site por nenhuma rota (internet, DNS ou bloqueio de rede).",
    "geo": "O site bloqueia o seu país. Ligue o Tor ou coloque um proxy de outro país.",
    "bloqueio": "O site recusou o download (403/429/proteção anti-robô) em todas as rotas.",
    "nao_achei": "Não achei o vídeo nesta página. Use \"Marcar na tela\" para apontar o vídeo, ou \"Gravar a área\".",
    "ffmpeg": "Falta o FFmpeg. Rode o BAIXAR_VIDEO.bat de novo para instalar.",
    "formato": "Achei o vídeo, mas nenhum formato baixou certo.",
    "drm": "Este vídeo tem proteção contra cópia (DRM). Nenhum baixador gratuito abre; use \"Gravar área\" com o vídeo tocando.",
    "outro": "Não deu certo.",
}


class ErroFinal(Exception):
    def __init__(self, categoria: str, detalhe: str = ""):
        super().__init__(MENSAGENS.get(categoria, categoria))
        self.categoria = categoria
        self.detalhe = detalhe


@dataclass
class Tarefa:
    url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    estado: str = "fila"
    texto: str = "na fila"
    progresso: float | None = None
    arquivo: str = ""
    info_arquivo: dict = field(default_factory=dict)
    titulo: str = ""
    candidatos: list = field(default_factory=list)
    escolhido: Candidato | None = None
    caminho: str = ""          # como chegou: "yt-dlp · conexão direta" etc.
    erro: str = ""
    categoria_erro: str = ""
    logs: list = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    capturas: list = field(default_factory=list)   # imagens da área marcada na tela
    casamento: dict = field(default_factory=dict)
    fotos: dict = field(default_factory=dict)
    pagina_url: str = ""
    forcar: Candidato | None = None                 # "não era esse": baixar este
    inicio: float = 0.0
    fim: float = 0.0
    cookies_nav: list = field(default_factory=list)
    obstaculos: dict = field(default_factory=dict)     # o que impede o download (diagnóstico)
    tecnicas_tentadas: list = field(default_factory=list)
    tecnica: str = ""                                   # a técnica que conseguiu
    erros_vistos: list = field(default_factory=list)
    pular_ytdlp: bool = False        # "não era esse" num site conhecido: procurar na página inteira
    sempre_perguntar: bool = False

    def log(self, t: str):
        self.logs.append(f"{time.strftime('%H:%M:%S')} {t}")
        if len(self.logs) > 400:
            del self.logs[:100]


class Motor:
    """Um por programa: guarda o modelo, a memória e os cookies (carregados uma vez)."""

    def __init__(self, cfg: dict, ao_mudar: Callable[[Tarefa], None] = lambda t: None,
                 escolher: Callable[[Tarefa, list], Candidato | None] | None = None):
        self.cfg = cfg
        self.modelo = Modelo.carregar()
        self.memoria = Memoria()
        self.ao_mudar = ao_mudar
        self.escolher = escolher
        self._jar = None
        self._jar_nav = None
        self._lock_jar = threading.Lock()

    # -------------------------------------------------------------- utilidades
    def _avisar(self, t: Tarefa, texto: str | None = None, prog: float | None = None, estado: str | None = None):
        if texto is not None:
            t.texto = texto
            t.log(texto)
        t.progresso = prog
        if estado:
            t.estado = estado
        try:
            self.ao_mudar(t)
        except Exception:
            pass

    def _contexto(self, t: Tarefa, proxy: str | None) -> Contexto:
        def prog(p, texto):
            self._avisar(t, None, p)
            t.texto = texto
            try:
                self.ao_mudar(t)
            except Exception:
                pass
        q = self.cfg.get("_q", 720)
        return Contexto(destino=Path(self.cfg["pasta_destino"]), q=q, proxy=proxy,
                        cookies_navegador=self.cfg.get("cookies_navegador", "nenhum"),
                        compat=bool(self.cfg.get("mp4_compativel", True)), cancel=t.cancel,
                        progresso=prog, log=lambda s: t.log(s))

    def cookies_jar(self):
        nav = self.cfg.get("cookies_navegador", "nenhum")
        if not nav or nav == "nenhum":
            return None
        with self._lock_jar:
            if self._jar is not None and self._jar_nav == nav:
                return self._jar
            try:
                from yt_dlp.cookies import extract_cookies_from_browser
                self._jar = extract_cookies_from_browser(nav)
                self._jar_nav = nav
            except Exception as e:
                msg = str(e)
                if re.search(r"could not copy|locked|permission", msg, re.I):
                    raise ErroFinal("cookies_travados", msg[:200])
                self._jar = None
            return self._jar

    def obter_pagina(self, url: str, proxy: str | None, t: Tarefa | None = None) -> tuple[str, str]:
        """Baixa o HTML (fingindo ser o Chrome quando dá, para passar por proteções simples)."""
        jar = None
        try:
            jar = self.cookies_jar()
        except ErroFinal:
            jar = None
        hdr = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7"}
        px = {"http": proxy, "https": proxy} if proxy else None
        ultimo = None
        tentativas = []
        try:
            from curl_cffi import requests as creq  # type: ignore
            tentativas.append(lambda: creq.get(url, impersonate="chrome", timeout=25, proxies=px, allow_redirects=True,
                                               cookies={c.name: c.value for c in jar} if jar else None))
        except Exception:
            pass
        tentativas.append(lambda: requests.get(url, headers=hdr, timeout=25, proxies=px, cookies=jar, allow_redirects=True))
        for tentar in tentativas:
            try:
                r = tentar()
                if r.status_code >= 400 and r.status_code not in (403, 429):
                    ultimo = ErroMotor("removido" if r.status_code in (404, 410) else "outro", f"HTTP {r.status_code}")
                    continue
                if r.status_code in (403, 429):
                    ultimo = ErroMotor("bloqueio", f"HTTP {r.status_code}")
                    continue
                ct = (r.headers.get("content-type") or "").lower()
                if ct.startswith(("video/", "audio/")) or "mpegurl" in ct or "dash+xml" in ct:
                    return "", str(r.url)   # o próprio link é o vídeo
                if t is not None:  # cookies que a página entregou (alguns sites só soltam o vídeo com eles)
                    try:
                        host = urlparse(str(r.url)).hostname or ""
                        for k, v in dict(r.cookies).items():
                            t.cookies_nav.append({"name": k, "value": v, "domain": host, "path": "/"})
                    except Exception:
                        pass
                conteudo = r.content[:12_000_000]
                enc = r.encoding if r.encoding and r.encoding.lower() not in ("iso-8859-1",) else None
                try:
                    texto = conteudo.decode(enc or "utf-8")
                except Exception:
                    texto = conteudo.decode("utf-8", "replace")
                return texto, str(r.url)
            except Exception as e:
                ultimo = ErroMotor("rede", str(e)[:200])
        if ultimo:
            raise ultimo
        raise ErroMotor("rede", "não consegui abrir a página")

    # -------------------------------------------------------------- caminho principal
    def processar(self, t: Tarefa):
        t.inicio = time.time()
        self._avisar(t, "analisando o link...", None, "analisando")
        cat_final, detalhe = "outro", ""
        try:
            rotas = montar_rotas(self.cfg.get("proxies", ""), bool(self.cfg.get("usar_tor", True)))
            for i, rota in enumerate(rotas):
                if t.cancel.is_set():
                    raise Cancelado("cancelado")
                if rota["tor"]:
                    self._avisar(t, f"trocando de rota: {rota['nome']}...")
                    tor_novo_circuito()
                elif i:
                    self._avisar(t, f"trocando de rota: {rota['nome']}...")
                ctx = self._contexto(t, rota["proxy"])
                try:
                    caminho = self._tentar_rota(t, ctx)
                    t.caminho = f"{t.caminho} · {rota['nome']}"
                    t.arquivo = str(caminho)
                    t.estado = "pronto"
                    dur = t.info_arquivo.get("duracao") or 0
                    alt = t.info_arquivo.get("altura")
                    self._avisar(t, f"pronto: {alt or '?'}p · {int(dur // 60)}:{int(dur % 60):02d} · {t.caminho}", 100.0, "pronto")
                    return
                except ErroFinal as e:
                    cat_final, detalhe = e.categoria, e.detalhe
                    if e.categoria in ("rede", "geo", "bloqueio") and i + 1 < len(rotas):
                        t.log(f"rota {rota['nome']} falhou ({e.categoria}); tentando a próxima")
                        continue
                    break
            raise ErroFinal(cat_final, detalhe)
        except Cancelado:
            t.estado = "cancelado"
            self._avisar(t, "cancelado", None, "cancelado")
        except ErroFinal as e:
            t.erro = str(e)
            if e.categoria in ("outro", "formato") and e.detalhe:
                t.erro += " Motivo: " + re.sub(r"\s+", " ", e.detalhe.split(" | ")[0])[:160]
            t.categoria_erro = e.categoria
            if t.obstaculos:
                t.erro += " O que impede: " + "; ".join(NOME_OBSTACULO.get(k, k) for k in t.obstaculos) + "."
            if t.tecnicas_tentadas:
                t.erro += " Tentei: " + ", ".join(NOME_TECNICA.get(k, k) for k in t.tecnicas_tentadas) + "."
            if e.detalhe:
                t.log("detalhe: " + e.detalhe)
            self._avisar(t, t.erro, None, "erro")
        except Exception as e:  # nunca deixar a fila morrer por um erro meu
            t.erro = f"erro interno: {type(e).__name__}: {e}"
            t.categoria_erro = "interno"
            self._avisar(t, t.erro, None, "erro")
        finally:
            t.fim = time.time()
            self.memoria.salvar()
            self._historico(t)

    # -------------------------------------------------------------- sites que não deixam baixar
    DIFICEIS = ("navegador_rede", "cabecalhos_capturados", "captura_player")

    def _tentar_rota(self, t: Tarefa, ctx: Contexto) -> Path:
        """Caminho normal; se falhar por bloqueio, entra no MODO DIFÍCIL (diagnóstico + técnicas de captura).
        Se o site já ensinou que só uma técnica difícil funciona, começa direto por ela."""
        dom = dominio(t.url)
        tec = self.memoria.site(dom).get("tecnicas", {})
        aprendidas = [k for k in self.DIFICEIS if tec.get(k, {}).get("ok", 0) > tec.get(k, {}).get("fail", 0)]
        normal_ruim = tec.get("normal", {}).get("fail", 0) > tec.get("normal", {}).get("ok", 0)
        if aprendidas and normal_ruim and t.forcar is None:
            t.log("este site já ensinou: o caminho normal não funciona aqui; indo direto para " +
                  ", ".join(NOME_TECNICA[k] for k in aprendidas))
            try:
                return self._modo_dificil(t, ctx, primeiro=aprendidas)
            except ErroFinal as e:
                t.log(f"técnica aprendida falhou desta vez ({e.categoria}); tentando o caminho normal")
        try:
            caminho = self._numa_rota(t, ctx, profundidade=0)
            self.memoria.registrar(dom, "tecnicas", "normal", ok=1)
            t.tecnica = t.tecnica or "normal"
            return caminho
        except ErroFinal as e:
            if e.categoria in ("rede", "geo", "cookies_travados", "removido") or t.forcar is not None:
                raise
            self.memoria.registrar(dom, "tecnicas", "normal", fail=1)
            t.erros_vistos.append(ErroMotor(e.categoria, e.detalhe or str(e)))
            return self._modo_dificil(t, ctx, erro_inicial=e)

    def _cookies_para_navegador(self, dom: str) -> list:
        try:
            jar = self.cookies_jar()
        except ErroFinal:
            return []
        out = []
        for ck in jar or []:
            d = (ck.domain or "").lstrip(".")
            if d and (d == dom or d.endswith("." + dom) or dom.endswith(d)):
                c = {"name": ck.name, "value": ck.value or "", "domain": ck.domain, "path": ck.path or "/",
                     "secure": bool(ck.secure), "httpOnly": False}
                if ck.expires:
                    c["expires"] = float(ck.expires)
                out.append(c)
        return out

    def _modo_dificil(self, t: Tarefa, ctx: Contexto, erro_inicial: ErroFinal | None = None,
                      primeiro: list | None = None) -> Path:
        if not (self.cfg.get("usar_navegador", True) and navegador.disponivel()):
            raise erro_inicial or ErroFinal("nao_achei")
        dom = dominio(t.pagina_url or t.url)
        pagina = t.pagina_url or t.url
        diag = diagnosticar(t.erros_vistos, None, "", [c.url for c in t.candidatos[:12]])
        t.obstaculos.update(diag)
        for ob in diag:
            self.memoria.registrar(dom, "obstaculos", ob, ok=1)
        ordem = plano(diag, self.memoria.site(dom))
        if primeiro:
            ordem = list(primeiro) + [x for x in ordem if x not in primeiro]
        self._avisar(t, "modo difícil: " + (("o que impede: " + ", ".join(NOME_OBSTACULO.get(k, k) for k in diag)) if diag
                                            else "investigando o que impede") + "...", None, "analisando")
        for ob, ev in diag.items():
            t.log(f"obstáculo [{ob}]: {ev}")
        cookies_extra = self._cookies_para_navegador(dom)
        hdr = {"Referer": pagina}
        farejou = False
        ultimo: ErroFinal | None = erro_inicial
        for tec in ordem:
            ctx.checar()
            if tec in ("normal", "fingir_chrome", "outra_rota"):
                continue  # o caminho normal já fingiu ser o Chrome; as rotas são trocadas por fora
            if tec == "cookies_navegador":
                if self.cfg.get("cookies_navegador", "nenhum") == "nenhum":
                    t.log("técnica cookies do navegador: nenhum navegador escolhido em Configurações")
                continue
            if tec == "gravar_area":
                break
            if tec in ("navegador_rede", "cabecalhos_capturados"):
                if farejou:
                    continue
                farejou = True
                t.tecnicas_tentadas.append("navegador_rede")
                self._avisar(t, "modo difícil: abrindo no Edge e copiando os pedidos exatos do player...")
                pasta_hora = Path(self.cfg["pasta_destino"]) / ".parciais" / f"na_hora_{t.id}"
                try:
                    r = navegador.farejar(pagina, ctx.proxy, cancel=t.cancel, headers=hdr, log=t.log, cookies_extra=cookies_extra,
                                          baixar_na_hora=pasta_hora)
                except Exception as e:
                    t.log(f"navegador: {type(e).__name__}: {str(e)[:160]}")
                    shutil.rmtree(pasta_hora, ignore_errors=True)
                    continue
                self._absorver_obs(t, r, dom)
                if "drm" in t.obstaculos:
                    break
                cands = list(r.get("candidatos", []))
                if r.get("html"):
                    cands = self._juntar(cands, analisar_html(r["html"], pagina).candidatos)
                t.cookies_nav += [k for k in r.get("cookies", []) if isinstance(k, dict)]
                t.titulo = t.titulo or r.get("titulo") or ""
                ordenados = self._ranquear(t, cands, dom)
                if not ordenados:
                    self.memoria.registrar(dom, "tecnicas", "navegador_rede", fail=1)
                    continue
                t.candidatos = ordenados
                # 1º o que o próprio navegador já baixou na hora (endereço assinado/curto), na ordem do modelo
                for c in ordenados[:6]:
                    arq = r.get("baixados", {}).get(c.url)
                    if not arq:
                        continue
                    try:
                        convertido, info = verificar_e_converter(Path(arq), ctx)
                    except ErroMotor as e:
                        t.log(f"baixado na hora não serviu ({e.msg[:80]})")
                        continue
                    final = caminho_livre(Path(self.cfg["pasta_destino"]), nome_seguro(t.titulo or dom), convertido.suffix.lstrip("."))
                    shutil.move(str(convertido), str(final))
                    shutil.rmtree(pasta_hora, ignore_errors=True)
                    t.escolhido, t.info_arquivo = c, info
                    t.caminho = f"Edge baixou na hora · {c.resumo()}"
                    t.tecnica = "navegador_rede"
                    self._aprender(dom, c, ordenados, ok=True)
                    self.memoria.registrar(dom, "tecnicas", "navegador_rede", ok=1)
                    return final
                shutil.rmtree(pasta_hora, ignore_errors=True)
                try:
                    caminho = self._baixar_lista(t, ctx, ordenados, dom, pagina, ordenados)
                    self.memoria.registrar(dom, "tecnicas", "navegador_rede", ok=1)
                    t.tecnica = "navegador_rede"
                    return caminho
                except ErroFinal as e:
                    ultimo = e
                    self.memoria.registrar(dom, "tecnicas", "navegador_rede", fail=1)
                    t.log(f"técnica navegador falhou: {e.categoria}")
                    continue
            if tec == "captura_player":
                t.tecnicas_tentadas.append("captura_player")
                pasta = Path(self.cfg["pasta_destino"]) / ".parciais" / f"captura_{t.id}"
                self._avisar(t, "modo difícil: capturando o que o player recebe (tocando em velocidade alta)...", None, "baixando")

                def prog(p, texto):
                    t.progresso, t.texto = p, texto
                    try:
                        self.ao_mudar(t)
                    except Exception:
                        pass
                try:
                    r = navegador.farejar(pagina, ctx.proxy, cancel=t.cancel, headers=hdr, log=t.log, cookies_extra=cookies_extra,
                                          capturar_em=pasta, progresso=prog)
                except Exception as e:
                    t.log(f"captura: {type(e).__name__}: {str(e)[:160]}")
                    self.memoria.registrar(dom, "tecnicas", "captura_player", fail=1)
                    continue
                self._absorver_obs(t, r, dom)
                if "drm" in t.obstaculos:
                    shutil.rmtree(pasta, ignore_errors=True)
                    break
                t.titulo = t.titulo or r.get("titulo") or ""
                arq = montar_captura(r.get("capturas", []), pasta, t.titulo or dom, log=t.log)
                if arq is None:
                    t.log("captura: o player não entregou vídeo aproveitável")
                    self.memoria.registrar(dom, "tecnicas", "captura_player", fail=1)
                    shutil.rmtree(pasta, ignore_errors=True)
                    continue
                try:
                    convertido, info = verificar_e_converter(arq, ctx)
                except ErroMotor as e:
                    t.log(f"captura: arquivo montado não serviu ({e.msg[:120]})")
                    self.memoria.registrar(dom, "tecnicas", "captura_player", fail=1)
                    shutil.rmtree(pasta, ignore_errors=True)
                    continue
                final = caminho_livre(Path(self.cfg["pasta_destino"]), nome_seguro(t.titulo or dom), convertido.suffix.lstrip("."))
                shutil.move(str(convertido), str(final))
                shutil.rmtree(pasta, ignore_errors=True)
                t.info_arquivo = info
                t.caminho = "captura do player (MSE/Blob)"
                t.tecnica = "captura_player"
                self.memoria.registrar(dom, "tecnicas", "captura_player", ok=1)
                return final
        if "drm" in t.obstaculos:
            raise ErroFinal("drm", t.obstaculos["drm"])
        if "login" in t.obstaculos and self.cfg.get("cookies_navegador", "nenhum") == "nenhum":
            raise ErroFinal("login", t.obstaculos["login"])
        if ultimo is not None:
            raise ErroFinal(ultimo.categoria if ultimo.categoria in MENSAGENS else "nao_achei", ultimo.detalhe)
        raise ErroFinal("nao_achei", "; ".join(f"{k}: {v}" for k, v in t.obstaculos.items()))

    def _absorver_obs(self, t: Tarefa, r: dict, dom: str):
        novos = diagnosticar([], r.get("obs"), r.get("html", ""), [c.url for c in r.get("candidatos", [])[:12]])
        for ob, ev in novos.items():
            if ob not in t.obstaculos:
                t.log(f"obstáculo [{ob}]: {ev}")
                self.memoria.registrar(dom, "obstaculos", ob, ok=1)
            t.obstaculos[ob] = ev

    def _numa_rota(self, t: Tarefa, ctx: Contexto, profundidade: int, url: str | None = None,
                   referer: str | None = None) -> Path:
        url = url or t.url
        dom = dominio(t.pagina_url or url)
        site = self.memoria.site(dom)
        if t.forcar is not None:  # o usuário escolheu o candidato ("não era esse")
            return self._baixar_lista(t, ctx, [t.forcar], dom, pagina=t.pagina_url or url, outros=t.candidatos)

        erros: list[ErroMotor] = []
        info = None
        imp = bool(site.get("impersonar"))
        hdr = {"Referer": referer} if referer else None
        if not t.pular_ytdlp:
            self._avisar(t, "perguntando ao yt-dlp...")
        for tentativa in range(0 if t.pular_ytdlp else 2):
            try:
                info = ytdlp_info(url, ctx, hdr, impersonar=imp)
                if imp and tentativa:
                    self.memoria.marcar(dom, "impersonar", True)
                break
            except ErroMotor as e:
                t.log(f"yt-dlp: [{e.categoria}] {e.msg[:200]}")
                t.erros_vistos.append(e)
                if e.categoria == "bloqueio" and not imp:
                    imp = True
                    continue
                if e.categoria == "cookies_travados":
                    raise ErroFinal("cookies_travados", e.msg)
                if e.categoria in ("rede", "geo"):
                    raise ErroFinal(e.categoria, e.msg)
                erros.append(e)
                break

        if info and self._confiar_ytdlp(info, url):
            # site conhecido: o yt-dlp sabe qual é o vídeo
            t.titulo = info.get("title") or t.titulo
            return self._baixar_ytdlp(t, ctx, info.get("webpage_url") or url, dom, hdr, imp, origem_extrator=info.get("extractor_key"))

        cands: list[Candidato] = []
        if info:
            t.titulo = info.get("title") or t.titulo
            cands += self._cands_ytdlp(info, url)

        # detector próprio na página
        pagina_url = url
        html_txt = ""
        pagina_ok = False
        if not (info and info.get("direct")):
            self._avisar(t, "lendo a página e procurando os players...")
            try:
                html_txt, pagina_url = self.obter_pagina(url, ctx.proxy, t)
                pagina_ok = True
            except ErroMotor as e:
                t.log(f"página: [{e.categoria}] {e.msg}")
                t.erros_vistos.append(e)
                if e.categoria in ("rede",) and not cands:
                    raise ErroFinal("rede", e.msg)
                erros.append(e)
        t.pagina_url = pagina_url
        if pagina_ok and not html_txt and not any(c.url == pagina_url for c in cands):
            # o próprio link já é o arquivo de vídeo (o servidor respondeu video/* ou lista HLS/DASH)
            c = Candidato(url=pagina_url, tipo=tipo_da_url(pagina_url), ordem=1, grupo=chave_grupo(pagina_url))
            if c.tipo == "desconhecido":
                c.tipo = "progressivo"
            c.origens = {"link"}
            c.ctx = {"n_total": 1, "n_grupo": 1}
            cands.append(c)
        pag = None
        if html_txt:
            pag = analisar_html(html_txt, pagina_url)
            t.titulo = t.titulo or pag.titulo
            self.memoria.registrar_players(dom, pag.players)
            cands = self._juntar(cands, pag.candidatos)
            if pag.players:
                t.log("players na página: " + ", ".join(pag.players))

        ordenados = self._ranquear(t, cands, dom)
        if (self._em_duvida(ordenados) or t.pular_ytdlp) and self.cfg.get("usar_navegador", True) and navegador.disponivel():
            self._avisar(t, "abrindo a página no Edge escondido e apertando play...")
            try:
                r = navegador.farejar(pagina_url, ctx.proxy, cancel=t.cancel, headers=hdr, log=t.log)
                t.fotos.update(r.get("fotos", {}))
                novos = list(r.get("candidatos", []))
                if r.get("html"):
                    pag2 = analisar_html(r["html"], pagina_url)
                    novos = self._juntar(novos, pag2.candidatos)
                    t.titulo = t.titulo or r.get("titulo") or pag2.titulo
                    self.memoria.registrar_players(dom, pag2.players)
                cands = self._juntar(cands, novos)
                t.cookies_nav += [k for k in r.get("cookies", []) if isinstance(k, dict)]
                self.memoria.registrar(dom, "motores", "navegador", ok=1 if novos else 0, fail=0 if novos else 1)
                ordenados = self._ranquear(t, cands, dom)
            except Cancelado:
                raise
            except Exception as e:
                t.log(f"navegador: {type(e).__name__}: {str(e)[:200]}")

        if not ordenados:
            cats = [e.categoria for e in erros]
            for c in ("login", "removido", "bloqueio"):
                if c in cats:
                    raise ErroFinal(c, "; ".join(e.msg[:150] for e in erros))
            raise ErroFinal("nao_achei", "; ".join(e.msg[:150] for e in erros))

        t.candidatos = ordenados
        if ordenados[0].prob < LIMIAR_MINIMO and not t.sempre_perguntar:
            # só sobrou coisa com cara de isca (prévia, propaganda, fundo): baixar sozinho seria errar
            if self.escolher and self.cfg.get("perguntar_na_duvida", True):
                t.sempre_perguntar = True
            else:
                raise ErroFinal("nao_achei", f"melhor candidato com chance {ordenados[0].prob:.0%}: {ordenados[0].url[:120]}")
        if self.escolher and (t.sempre_perguntar or (self.cfg.get("perguntar_na_duvida", True) and self._em_duvida(ordenados, rigor=True))):
            self._avisar(t, "fiquei em dúvida: escolha qual é o vídeo", None, "escolher")
            esc = self.escolher(t, ordenados)
            t.estado = "analisando"
            if esc is None:
                raise Cancelado("cancelado na escolha")
            ordenados = [esc] + [c for c in ordenados if c is not esc]
            esc.ctx["escolha_humana"] = True
        return self._baixar_lista(t, ctx, ordenados, dom, pagina=pagina_url, outros=ordenados)

    # -------------------------------------------------------------- ajudantes da escolha
    GENERICOS = {"generic", "html5mediaembed", "jsonld", "commonmistakes"}
    ENCURTADORES = {"bit.ly", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly", "rebrand.ly", "shorturl.at",
                    "lnkd.in", "trib.al", "dlvr.it", "tiny.cc", "rb.gy", "s.id", "encurtador.com.br"}

    def _confiar_ytdlp(self, info: dict, url: str) -> bool:
        """Só confia de olhos fechados quando o yt-dlp tem um extrator DO PRÓPRIO SITE.
        Os genéricos (Generic, HTML5MediaEmbed) pegam o 1º <video> da página, que pode ser propaganda ou prévia
        (achado na bateria de ponta a ponta: 11 de 40 vieram com o vídeo errado)."""
        if info.get("_type") == "playlist":
            return False
        chave = (info.get("extractor_key") or info.get("ie_key") or "").lower()
        if not chave or chave in self.GENERICOS:
            return False
        destino = dominio(info.get("webpage_url") or url)
        origem = dominio(url)
        if destino == origem or origem in self.ENCURTADORES:
            return True
        # o link era de um site e o yt-dlp resolveu um player de OUTRO site embutido na página: pode ser isca
        return False
    def _cands_ytdlp(self, info: dict, url: str) -> list[Candidato]:
        brutos = []
        ents = info.get("entries") if info.get("_type") == "playlist" else [info]
        for i, e in enumerate(ents or []):
            if not e:
                continue
            u = e.get("url") or e.get("webpage_url")
            if not u:
                continue
            ie = (e.get("ie_key") or e.get("extractor_key") or "").lower()
            ctx = {"e_entrada_ytdlp": True, "duracao": e.get("duration") or 0, "rotulo": e.get("title") or ""}
            brutos.append((u, "ytdlp", ctx))
        cands = juntar_candidatos(brutos, url)
        ies = {}
        for e in ents or []:
            if e and (e.get("url") or e.get("webpage_url")):
                ies[e.get("url") or e.get("webpage_url")] = (e.get("ie_key") or "").lower()
        for c in cands:
            ie = ies.get(c.url, "")
            if ie and ie != "generic" and c.tipo == "desconhecido":
                c.tipo = "embed"
        return cands

    def _juntar(self, a: list[Candidato], b: list[Candidato]) -> list[Candidato]:
        por = {c.url: c for c in a}
        n = len(a)
        for c in b:
            if c.url in por:
                x = por[c.url]
                x.origens |= c.origens
                for k, v in c.ctx.items():
                    if v and not x.ctx.get(k):
                        x.ctx[k] = v
                x.headers.update(c.headers)
                x.ocorrencias += c.ocorrencias
            else:
                n += 1
                c.ordem = c.ordem or n
                por[c.url] = c
        todos = list(por.values())
        contar_moldes(todos)
        cont: dict[str, int] = {}
        for c in todos:
            cont[c.grupo] = cont.get(c.grupo, 0) + 1
        for c in todos:
            c.ctx["n_grupo"] = cont[c.grupo]
            c.ctx["n_total"] = len(todos)
        return todos

    def _ranquear(self, t: Tarefa, cands: list[Candidato], dom: str) -> list[Candidato]:
        cands = [c for c in cands if c.tipo != "ignorar"]
        if t.capturas and cands:
            faltam = [c for c in cands if c.url not in t.casamento]
            if faltam:
                self._avisar(t, "comparando a área marcada na tela com os vídeos da página...")
                from .tela import casar_com_tela
                pre = ranquear(faltam, self.modelo, dom, self.memoria.site(dom), pagina_url=t.pagina_url or t.url)
                t.casamento.update(casar_com_tela(t.capturas, pre, t.fotos, log=t.log))
        return ranquear(cands, self.modelo, dom, self.memoria.site(dom), t.casamento or None, pagina_url=t.pagina_url or t.url)

    @staticmethod
    def _em_duvida(ordenados: list[Candidato], rigor: bool = False) -> bool:
        if not ordenados:
            return True
        topo = ordenados[0]
        outro = next((c for c in ordenados[1:] if c.grupo != topo.grupo and c.tipo != "audio"), None)
        if rigor:  # perguntar ao usuário só quando há mesmo dois vídeos parecidos
            return outro is not None and topo.prob < 0.8 and (topo.prob - outro.prob) < 0.2 and outro.prob > 0.25
        if topo.tipo == "audio":
            return True
        return topo.prob < 0.55 or (outro is not None and topo.prob - outro.prob < 0.15)

    # -------------------------------------------------------------- downloads
    def _baixar_ytdlp(self, t: Tarefa, ctx: Contexto, url: str, dom: str, hdr, imp: bool,
                      origem_extrator: str | None = None) -> Path:
        ordem_ids = self.memoria.ordem(dom, "estrategias", [s[0] for s in ESTRATEGIAS])
        estrategias = sorted(ESTRATEGIAS, key=lambda s: ordem_ids.index(s[0]))
        ultimo: ErroMotor | None = None
        checar = bool(self.memoria.site(dom).get("checar_formatos"))
        drm_vezes = 0
        for est in estrategias:
            self._avisar(t, f"baixando com yt-dlp ({est[0]})...", None, "baixando")
            try:
                for tentativa in range(3):
                    try:
                        bruto = ytdlp_baixar(url, ctx, est, hdr, impersonar=imp, checar_formatos=checar)
                        break
                    except ErroMotor as e:
                        if e.categoria == "drm" and not checar:
                            # alguns formatos têm DRM e outros não (achado no Vimeo): testar cada um antes
                            t.log("yt-dlp: formato com DRM; testando os outros formatos um por um")
                            checar = True
                            continue
                        if tentativa < 2 and transitorio(e.msg):
                            t.log(f"yt-dlp: erro passageiro ({e.msg[:60]}); tentando de novo")
                            time.sleep(1.5 * (tentativa + 1))
                            ctx.checar()
                            continue
                        raise
                caminho, info = verificar_e_converter(bruto, ctx)
                if checar:
                    self.memoria.marcar(dom, "checar_formatos", True)
                self.memoria.registrar(dom, "estrategias", est[0], ok=1)
                self.memoria.registrar(dom, "motores", "yt-dlp", ok=1)
                t.info_arquivo = info
                t.caminho = f"yt-dlp/{origem_extrator or 'genérico'} ({est[0]})"
                return caminho
            except ErroMotor as e:
                ultimo = e
                t.erros_vistos.append(e)
                t.log(f"yt-dlp {est[0]}: [{e.categoria}] {e.msg[:200]}")
                self.memoria.registrar(dom, "estrategias", est[0], fail=1)
                if e.categoria == "bloqueio" and not imp:
                    imp = True
                    continue
                if e.categoria == "drm":
                    drm_vezes += 1
                    if drm_vezes >= 2:
                        break  # DRM de verdade: as outras estratégias dão no mesmo
                    continue
                if e.categoria not in ("formato", "outro"):
                    break  # erro que não depende do formato: repetir as 7 estratégias não ajuda
        self.memoria.registrar(dom, "motores", "yt-dlp", fail=1)
        cat = ultimo.categoria if ultimo else "outro"
        raise ErroFinal(cat if cat in MENSAGENS else "formato", ultimo.msg if ultimo else "")

    def _baixar_lista(self, t: Tarefa, ctx: Contexto, ordenados: list[Candidato], dom: str, pagina: str,
                      outros: list[Candidato]) -> Path:
        tentados = 0
        grupos_tentados: list[str] = []
        ultimo: ErroMotor | None = None
        fila: list[Candidato] = []
        for c in ordenados:
            if c.grupo in grupos_tentados:
                continue
            # reserva só se ainda for provável: baixar uma isca (prévia, propaganda) é pior do que avisar que falhou
            if grupos_tentados and c.prob < LIMIAR_RESERVA and not c.ctx.get("escolha_humana"):
                continue
            grupos_tentados.append(c.grupo)
            fila += escolher_grupo([x for x in ordenados if x.grupo == c.grupo], ctx.q) or [c]
            if len(grupos_tentados) >= 4:
                break
        origem = urlparse(pagina)
        base_hdr = {"Referer": pagina, "Origin": f"{origem.scheme}://{origem.netloc}"} if pagina else {}
        cookies = t.cookies_nav or None
        for c in fila:
            if tentados >= 6:
                break
            tentados += 1
            ctx.checar()
            hdr = {**base_hdr, **c.headers}
            if cookies and not any(k.lower() == "cookie" for k in c.headers):  # o Cookie exato capturado vale mais
                hdr["Cookie"] = "; ".join(f"{k['name']}={k['value']}" for k in cookies if k.get("name"))
            nome = t.titulo or dominio(pagina)
            t.escolhido = c
            self._avisar(t, f"baixando {c.resumo()} (chance {c.prob:.0%})...", None, "baixando")
            try:
                (caminho, info), motor = self._baixar_candidato(t, ctx, c, hdr, nome, cookies)
                t.info_arquivo = info
                t.caminho = f"{motor} · {c.resumo()}"
                self._aprender(dom, c, outros, ok=True)
                self.memoria.registrar(dom, "motores", motor, ok=1)
                return caminho
            except ErroMotor as e:
                ultimo = e
                t.erros_vistos.append(e)
                t.log(f"{c.resumo()}: [{e.categoria}] {e.msg[:200]}")
                self.memoria.registrar_candidato(dom, c, ok=False)
                if e.categoria in ("rede",):
                    raise ErroFinal("rede", e.msg)
        cat = ultimo.categoria if ultimo else "nao_achei"
        raise ErroFinal(cat if cat in MENSAGENS else "formato", ultimo.msg if ultimo else "")

    def _baixar_candidato(self, t: Tarefa, ctx: Contexto, c: Candidato, hdr: dict, nome: str, cookies) -> tuple[Path, str]:
        if c.tipo == "embed":
            # player de outro site (YouTube, Vimeo...): o yt-dlp resolve, mandando a página como Referer
            caminho = self._baixar_ytdlp(t, ctx, c.url, dominio(c.url), {"Referer": hdr.get("Referer", "")}, False,
                                         origem_extrator="embutido")
            return (caminho, t.info_arquivo), "yt-dlp (embutido)"
        erros = []
        if c.tipo in ("hls", "dash"):
            ordem = ["yt-dlp", "ffmpeg", "streamlink"]
        else:
            ordem = ["direto", "yt-dlp", "ffmpeg"]
        ordem = self.memoria.ordem(dominio(t.pagina_url or t.url), "motores_" + c.tipo, ordem)
        for m in ordem:
            ctx.checar()
            try:
                for tentativa in range(3):
                    try:
                        if m == "direto":
                            p = baixar_direto(c.url, ctx, nome, hdr, cookies)
                        elif m == "yt-dlp":
                            p = ytdlp_baixar(c.url, ctx, ("hls_nativo" if c.tipo == "hls" else "melhor_qualquer",
                                                          "bv*+ba/b/best", {}), hdr, nome=nome)
                        elif m == "ffmpeg":
                            p = baixar_ffmpeg(c.url, ctx, nome, hdr)
                        else:
                            p = baixar_streamlink(c.url, ctx, nome, hdr)
                        break
                    except ErroMotor as e:
                        # 429/503/queda passageira: espera um pouco e tenta o MESMO motor (achado na bateria:
                        # sem isso ele pulava para a isca seguinte)
                        if tentativa < 2 and transitorio(e.msg):
                            t.log(f"  motor {m}: erro passageiro ({e.msg[:60]}); tentando de novo")
                            time.sleep(1.5 * (tentativa + 1))
                            ctx.checar()
                            continue
                        raise
                resultado = verificar_e_converter(p, ctx)  # confere que tem imagem e decodifica; passa para MP4
                self.memoria.registrar(dominio(t.pagina_url or t.url), "motores_" + c.tipo, m, ok=1)
                return resultado, m
            except ErroMotor as e:
                erros.append(e)
                t.log(f"  motor {m}: [{e.categoria}] {e.msg[:160]}")
                self.memoria.registrar(dominio(t.pagina_url or t.url), "motores_" + c.tipo, m, fail=1)
                if e.categoria in ("removido", "nao_suportado") and m == "direto":
                    # página no lugar de vídeo, ou 404: os outros motores dão no mesmo, menos o yt-dlp (genérico)
                    continue
        piores = sorted(erros, key=lambda e: ["rede", "bloqueio", "removido", "login", "formato", "nao_suportado", "outro"].index(e.categoria)
                        if e.categoria in ("rede", "bloqueio", "removido", "login", "formato", "nao_suportado", "outro") else 9)
        raise piores[0] if piores else ErroMotor("outro", "nenhum motor conseguiu")

    # -------------------------------------------------------------- aprendizado
    def _aprender(self, dom: str, certo: Candidato, todos: list[Candidato], ok: bool, forca: float = 1.0):
        f = certo.ctx.get("_f")
        if f:
            self.modelo.atualizar(f, 1.0 if ok else 0.0, taxa=0.08 * forca)
        if ok:
            for c in todos:
                if c.grupo != certo.grupo and c.ctx.get("_f") and c.prob > 0.2:
                    self.modelo.atualizar(c.ctx["_f"], 0.0, taxa=0.03 * forca)
        self.memoria.registrar_candidato(dom, certo, ok=ok, forca=2 if forca > 1 else 1)
        self.modelo.salvar()

    def nao_era_esse(self, t: Tarefa, certo: Candidato | None):
        """Botão "não era esse": o escolhido vira exemplo negativo forte; o que você apontou, positivo."""
        dom = dominio(t.pagina_url or t.url)
        if t.escolhido is not None:
            self._aprender(dom, t.escolhido, [], ok=False, forca=3.0)
        if certo is not None:
            self._aprender(dom, certo, t.candidatos, ok=True, forca=3.0)
        self.memoria.salvar()
        self.memoria.grafo.retorno(t, certo=False)

    def foi_bom(self, t: Tarefa):
        if t.escolhido is not None:
            self._aprender(dominio(t.pagina_url or t.url), t.escolhido, t.candidatos, ok=True, forca=2.0)
        else:
            self.memoria.registrar(dominio(t.url), "confirmados", "ok", ok=1)
        self.memoria.salvar()
        self.memoria.grafo.retorno(t, certo=True)

    def _historico(self, t: Tarefa):
        try:  # episódio no grafo (hipocampo): site, motor, o que escolheu, como terminou
            self.memoria.grafo.episodio(t, dominio(t.pagina_url or t.url))
        except Exception as e:
            t.log(f"grafo: não gravei o episódio ({type(e).__name__}: {e})")
        try:
            with open(HISTORICO_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"quando": time.strftime("%Y-%m-%d %H:%M:%S"), "site": dominio(t.url), "estado": t.estado,
                                    "caminho": t.caminho, "arquivo": Path(t.arquivo).name if t.arquivo else "",
                                    "segundos": round(t.fim - t.inicio, 1), "erro": t.categoria_erro}, ensure_ascii=False) + "\n")
        except Exception:
            pass
