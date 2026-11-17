# -*- coding: utf-8 -*-
"""Diagnóstico do que impede o download e a escolha da técnica de captura (e o aprendizado disso por site).

Pedido do doug (16/11): os alvos são sites que NÃO deixam baixar. O programa tem de entender o que impede e aprender
a capturar. Cada obstáculo vem de EVIDÊNCIA (erro dos motores, respostas do servidor, o que o navegador viu):

  login              pede conta / vídeo privado                  -> cookies do navegador escolhido
  cookie_de_script   a mídia só sai com cookie que o JS da página cria -> navegador (cookies e cabeçalhos capturados)
  cabecalho_especial a mídia exige cabeçalho (Authorization, X-Token...) -> repetir os cabeçalhos exatos do navegador
  referer_origem     403 sem Referer/Origin da página             -> repetir Referer/Origin
  endereco_assinado  endereço com validade curta (expires/token)  -> pegar na hora pelo navegador e baixar já
  anti_robo          Cloudflare/captcha/403 para robô             -> fingir Chrome (impersonate) e navegador real
  escondido_no_js    o endereço só existe depois do JavaScript   -> navegador
  mse_blob           o player monta o vídeo em pedaços (MSE/Blob), endereço embaralhado ou por POST
                                                                   -> CAPTURA do que o player recebe
  drm                chave de DRM (Widevine/PlayReady/FairPlay)  -> não se quebra: Gravar área (tela + som)
  geo                bloqueio por país                           -> outra rota (proxy/Tor)
  limite             429/rate limit                              -> esperar e repetir

A técnica que funcionou fica na memória do site (e no grafo GEP): obstáculo --causa--> técnica, com sucessos e falhas.
Na próxima vez o programa começa direto pela técnica que já deu certo naquele site.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .base import SEM_JANELA, achar_programa, caminho_livre, ffprobe_info, nome_seguro

TECNICAS = ["normal", "fingir_chrome", "cookies_navegador", "cabecalhos_capturados", "navegador_rede",
            "captura_player", "gravar_area", "outra_rota"]
NOME_TECNICA = {"normal": "download normal", "fingir_chrome": "fingir ser o Chrome", "cookies_navegador": "cookies do seu navegador",
                "cabecalhos_capturados": "repetir os cabeçalhos exatos do navegador", "navegador_rede": "Edge escondido escutando a rede",
                "captura_player": "capturar o que o player recebe (MSE/Blob)", "gravar_area": "gravar a área da tela",
                "assistir_gravar": "assistir e gravar quadro a quadro com o som",
                "outra_rota": "outra rota (proxy/Tor)"}
NOME_OBSTACULO = {"login": "pede login", "cookie_de_script": "cookie criado pelo JavaScript", "cabecalho_especial": "cabeçalho especial",
                  "referer_origem": "exige Referer/Origin", "endereco_assinado": "endereço com validade curta",
                  "anti_robo": "proteção anti-robô", "escondido_no_js": "vídeo escondido no JavaScript",
                  "mse_blob": "player monta o vídeo em pedaços (MSE/Blob)", "drm": "DRM (proteção contra cópia)",
                  "geo": "bloqueio por país", "limite": "limite de pedidos (429)",
                  "servidor_morto": "o servidor do vídeo não existe mais (defeito do site)",
                  "player_canvas": "o vídeo é desenhado num canvas (sem arquivo de vídeo)"}
# para cada obstáculo, as técnicas na ordem de tentativa
PLANO = {"login": ["cookies_navegador", "navegador_rede", "captura_player"],
         "cookie_de_script": ["navegador_rede", "cabecalhos_capturados", "captura_player"],
         "cabecalho_especial": ["cabecalhos_capturados", "captura_player"],
         "referer_origem": ["cabecalhos_capturados", "navegador_rede"],
         "endereco_assinado": ["navegador_rede", "captura_player"],
         "anti_robo": ["fingir_chrome", "navegador_rede", "captura_player"],
         "escondido_no_js": ["navegador_rede", "captura_player"],
         "mse_blob": ["captura_player"],
         "drm": ["gravar_area"],
         "geo": ["outra_rota"],
         "limite": ["normal"],
         "servidor_morto": ["navegador_rede", "captura_player"],
         "player_canvas": ["assistir_gravar"]}


def diagnosticar(erros: list, obs: dict | None, html: str = "", urls: list | None = None) -> dict[str, str]:
    """Junta as evidências e devolve {obstáculo: evidência}."""
    d: dict[str, str] = {}
    texto = " | ".join(f"{getattr(e, 'categoria', '')}: {getattr(e, 'msg', str(e))}" for e in erros or [])
    low = texto.lower()
    if re.search(r"login|sign in|private|cookies? (are )?(needed|required)|members", low):
        d["login"] = "o site disse que precisa de conta (" + _trecho(low, r"login|sign in|private|members") + ")"
    if "servidor que não existe mais" in low:
        d["servidor_morto"] = _trecho(low, r"servidor que não existe mais")
    if re.search(r"geo|not available in your country|in your region", low):
        d["geo"] = "o site disse que não libera para o seu país"
    if re.search(r"\b429\b|too many requests|rate.?limit", low):
        d["limite"] = "o servidor respondeu 429 (muitos pedidos)"
    if re.search(r"cloudflare|captcha|just a moment|challenge|\b403\b.*(webpage|página)", low):
        d["anti_robo"] = "403/desafio na página para quem não é navegador"
    if re.search(r"\b403\b|forbidden|sem referer|recusou", low) and "anti_robo" not in d:
        d["referer_origem"] = "a mídia respondeu 403 (o servidor exige algo do pedido)"
    if re.search(r"drm|widevine|playready|fairplay", low):
        d["drm"] = "o yt-dlp avisou DRM"
    for u in urls or []:
        if re.search(r"[?&](expires?|exp|e|token|sig(nature)?|hdnts|hdnea|policy|key-pair-id|st|md5)=", u, re.I):
            d["endereco_assinado"] = "o endereço da mídia tem assinatura/validade (" + re.search(
                r"[?&](expires?|exp|e|token|sig(nature)?|hdnts|hdnea|policy|key-pair-id|st|md5)=", u, re.I).group(1) + "=)"
            break
    if html and re.search(r"just a moment|attention required|checking your browser|captcha", html[:30000], re.I):
        d["anti_robo"] = "a página veio com verificação anti-robô"
    if obs:
        if obs.get("drm") or obs.get("licenca_drm_rede"):
            d["drm"] = "a página pediu chave de DRM: " + ", ".join(obs.get("drm") or ["pedido de licença na rede"])
        if obs.get("desafio_antirrobo"):
            d["anti_robo"] = "o navegador caiu numa verificação anti-robô"
        if obs.get("parede_login"):
            d["login"] = "a página mostrou campo de senha e nenhum vídeo"
        if obs.get("cookies_de_script"):
            d["cookie_de_script"] = "a mídia foi pedida com cookie que não veio do servidor (criado pelo JavaScript)"
        esp = [h for h in obs.get("cabecalhos_especiais", []) if h.lower() not in ("referer", "origin", "cookie")]
        if esp:
            d["cabecalho_especial"] = "o player mandou cabeçalhos próprios: " + ", ".join(esp[:5])
        if obs.get("status_midia"):
            d.setdefault("referer_origem", "a mídia respondeu " + ", ".join(f"{k} ({v}x)" for k, v in obs["status_midia"].items()))
        if obs.get("mse_appends") or obs.get("blobs_video"):
            d["mse_blob"] = f"o player montou o vídeo por MSE/Blob ({obs.get('mse_appends', 0)} pedaços, {obs.get('blobs_video', 0)} blobs)"
        if obs.get("canvas_grande"):
            d["player_canvas"] = "a página desenha o vídeo num <canvas> e não tem <video>"
        if obs.get("manifesto_por_post"):
            d["mse_blob"] = d.get("mse_blob", "") + "; a lista do vídeo veio por POST"
    return d


def _trecho(texto: str, rx: str) -> str:
    m = re.search(rx, texto)
    return texto[max(0, m.start() - 30):m.end() + 40].strip() if m else ""


def plano(diag: dict, memoria_site: dict | None) -> list[str]:
    """Ordem das técnicas: primeiro a que JÁ funcionou neste site; depois as indicadas pelos obstáculos."""
    ordem: list[str] = []
    tec = (memoria_site or {}).get("tecnicas", {})
    aprendidas = sorted((k for k, r in tec.items() if r.get("ok", 0) > 0),
                        key=lambda k: -((tec[k].get("ok", 0) + 1) / (tec[k].get("ok", 0) + tec[k].get("fail", 0) + 2)))
    ordem += aprendidas
    for ob in ("servidor_morto", "drm", "login", "geo", "anti_robo", "cabecalho_especial", "cookie_de_script", "referer_origem",
               "endereco_assinado", "escondido_no_js", "mse_blob", "limite"):
        if ob in diag:
            for t in PLANO[ob]:
                if t not in ordem:
                    ordem.append(t)
    if "drm" in diag:  # com DRM, capturar bytes não adianta (vêm cifrados)
        ordem = [t for t in ordem if t != "captura_player"]
    for t in ("navegador_rede", "captura_player"):
        if t not in ordem and "drm" not in diag:
            ordem.append(t)
    return ordem


def montar_captura(capturas: list[dict], destino: Path, nome: str, log=lambda t: None) -> Path | None:
    """Junta o que o player recebeu (uma faixa só, ou vídeo + áudio separados) num arquivo que o ffmpeg abre."""
    ffmpeg = achar_programa("ffmpeg")
    boas = []
    for c in capturas:
        p = Path(c["arquivo"])
        info = ffprobe_info(p)
        if not info:
            log(f"captura {p.name}: não abre ({c['bytes']} bytes)")
            continue
        boas.append((p, info))
    if not boas or not ffmpeg:
        return None
    com_video = [b for b in boas if b[1].get("tem_video")]
    so_audio = [b for b in boas if not b[1].get("tem_video") and b[1].get("tem_audio")]
    if not com_video:
        return None
    video = max(com_video, key=lambda b: b[0].stat().st_size)
    saida = caminho_livre(destino, nome_seguro(nome), "mkv")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-fflags", "+genpts", "-i", str(video[0])]
    if so_audio and not video[1].get("tem_audio"):
        audio = max(so_audio, key=lambda b: b[0].stat().st_size)
        cmd += ["-fflags", "+genpts", "-i", str(audio[0]), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
    cmd += ["-c", "copy", str(saida)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=SEM_JANELA)
    if r.returncode != 0 or not saida.exists() or saida.stat().st_size < 1000:
        log(f"captura: juntar falhou ({(r.stderr or '')[-160:]})")
        saida.unlink(missing_ok=True)
        return None
    return saida
