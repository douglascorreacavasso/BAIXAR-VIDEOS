# -*- coding: utf-8 -*-
"""Navegador de verdade (Microsoft Edge escondido, pelo Playwright): farejar e CAPTURAR.

farejar   abre a página, aperta play e escuta a REDE: cada resposta que é vídeo/lista HLS/DASH vira candidato, com
          os CABEÇALHOS EXATOS que o navegador mandou (Referer, Origin, Cookie, Authorization, X-...), para o download
          repetir o pedido igualzinho (sites que exigem token/cabeçalho especial).
capturar  para sites que não deixam baixar: grava o que o PLAYER recebe. Ganchos no JavaScript da página
          (MediaSource.addSourceBuffer / SourceBuffer.appendBuffer e URL.createObjectURL de Blob de vídeo) guardam os
          bytes já prontos para tocar — funciona mesmo com endereço escondido, token que muda, POST, pedaços
          embaralhados que o próprio site desembaralha antes de tocar. Toca em velocidade alta até o fim.
          NÃO quebra DRM: se a página pede chave de DRM (EME/Widevine/PlayReady), isso é anotado e o caminho é
          "Gravar área" com o vídeo tocando na tela.

Também observa o que bloqueia (para o diagnóstico em obstaculos.py): DRM, desafio anti-robô, parede de login,
respostas 401/403 de mídia, manifesto por POST, cabeçalhos especiais, blob/MSE.
Perfil limpo e temporário; cookies do seu navegador só entram se você escolher em Configurações.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path

from .detector import Candidato, chave_grupo, altura_da_url, tipo_da_url

MIME_MIDIA = re.compile(r"video/|mpegurl|dash\+xml|application/octet-stream|binary/octet|mp2t", re.I)
SELETORES_PLAY = ["video", ".vjs-big-play-button", ".jw-icon-playback", ".jw-display-icon-container",
                  ".plyr__control--overlaid", ".fp-play", ".fluid_initial_play", ".art-control-play",
                  "button[aria-label*='play' i]", "button[title*='play' i]", "[class*='play-button']",
                  "[class*='playButton']", "[class*='big-play']", ".ytp-large-play-button", "#play", ".play"]
CABECALHOS_COMUNS = {"accept", "accept-encoding", "accept-language", "user-agent", "sec-ch-ua", "sec-ch-ua-mobile",
                     "sec-ch-ua-platform", "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site", "connection", "host",
                     "range", "if-range", "upgrade-insecure-requests", "priority", "cache-control", "pragma", "dnt"}
RE_LICENCA = re.compile(r"widevine|playready|fairplay|/license|licen[cs]e\?|drm|/lic/|getlicense|clearkey", re.I)
RE_DESAFIO = re.compile(r"just a moment|attention required|checking your browser|verify you are human|cf-chl|"
                        r"ddos-guard|captcha|verifica[cç][aã]o de seguran", re.I)

# Ganchos instalados antes de qualquer script da página.
GANCHOS = r"""
(() => {
  if (window.__bxv) return;
  const B = window.__bxv = {drm: [], sb: [], blobs: [], appends: 0, guardar: %GUARDAR%, ultimo: 0};
  try {
    const rmksa = navigator.requestMediaKeySystemAccess && navigator.requestMediaKeySystemAccess.bind(navigator);
    if (rmksa) navigator.requestMediaKeySystemAccess = function(ks, cfg) { B.drm.push(String(ks)); return rmksa(ks, cfg); };
  } catch (e) {}
  const MS = window.MediaSource || window.ManagedMediaSource;
  if (MS && MS.prototype && MS.prototype.addSourceBuffer) {
    const add = MS.prototype.addSourceBuffer;
    MS.prototype.addSourceBuffer = function(mime) {
      const sb = add.call(this, mime);
      const reg = {mime: String(mime), chunks: [], bytes: 0};
      B.sb.push(reg);
      const app = sb.appendBuffer;
      sb.appendBuffer = function(data) {
        try {
          B.appends++; B.ultimo = Date.now();
          let u8 = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
          reg.bytes += u8.byteLength;
          if (B.guardar) reg.chunks.push(u8.slice());
        } catch (e) {}
        return app.call(this, data);
      };
      return sb;
    };
  }
  try {
    const cou = URL.createObjectURL;
    URL.createObjectURL = function(obj) {
      try { if (obj && obj instanceof Blob && /video|octet|mp4|webm/i.test(obj.type || 'video')) {
        B.blobs.push({type: obj.type, size: obj.size}); if (B.guardar) (B.blobsObj = B.blobsObj || []).push(obj); } } catch (e) {}
      return cou.call(this, obj);
    };
  } catch (e) {}
  window.__bxv_ler = (i, j) => {
    const u8 = B.sb[i].chunks[j]; let s = ''; const P = 0x8000;
    for (let k = 0; k < u8.length; k += P) s += String.fromCharCode.apply(null, u8.subarray(k, k + P));
    return btoa(s);
  };
  window.__bxv_blob = async (i) => {
    const buf = new Uint8Array(await B.blobsObj[i].arrayBuffer()); let s = ''; const P = 0x8000;
    for (let k = 0; k < buf.length; k += P) s += String.fromCharCode.apply(null, buf.subarray(k, k + P));
    return btoa(s);
  };
})();
"""


def disponivel() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False


def _apertar_play(pg):
    for sel in SELETORES_PLAY:
        try:
            loc = pg.locator(sel).first
            if loc.count() and loc.is_visible(timeout=300):
                loc.click(timeout=1200, force=True)
                break
        except Exception:
            continue
    try:
        pg.evaluate("""() => { for (const v of document.querySelectorAll('video')) { try { v.muted = true; v.play(); } catch(e){} } }""")
    except Exception:
        pass


def farejar(url: str, proxy: str | None = None, espera_s: float = 9.0, cancel=None, headers: dict | None = None,
            log=lambda t: None, cookies_extra: list | None = None, capturar_em: Path | None = None,
            tempo_max_captura: float = 1800.0, progresso=lambda p, t: None, baixar_na_hora: Path | None = None) -> dict:
    """Devolve {"candidatos", "html", "titulo", "cookies", "fotos", "obs" (o que bloqueia), "capturas" (arquivos)}.
    capturar_em = pasta: liga a CAPTURA do que o player recebe e toca até o fim."""
    from playwright.sync_api import sync_playwright
    vistos: dict[str, dict] = {}
    pedidos: dict[str, object] = {}
    marco = {"play": None, "t0": time.time()}
    obs = {"drm": [], "mse_appends": 0, "blobs_video": 0, "status_midia": {}, "desafio_antirrobo": False,
           "parede_login": False, "manifesto_por_post": False, "cabecalhos_especiais": [], "licenca_drm_rede": False,
           "cookies_de_script": False}
    out = {"candidatos": [], "html": "", "titulo": "", "cookies": [], "fotos": {}, "erro": "", "obs": obs, "capturas": [],
           "baixados": {}}
    with sync_playwright() as p:
        args = {"headless": True, "args": ["--autoplay-policy=no-user-gesture-required", "--mute-audio",
                                           "--disable-blink-features=AutomationControlled"]}
        if proxy:
            args["proxy"] = {"server": proxy.replace("socks5h://", "socks5://")}
        try:
            nav = p.chromium.launch(channel="msedge", **args)
        except Exception:
            nav = p.chromium.launch(**args)  # Chromium do Playwright, se estiver instalado
        try:
            ctx = nav.new_context(viewport={"width": 1280, "height": 800}, locale="pt-BR",
                                  extra_http_headers={k: v for k, v in (headers or {}).items()
                                                      if k.lower() not in ("user-agent", "cookie")})
            if cookies_extra:
                try:
                    ctx.add_cookies(cookies_extra)
                except Exception as e:
                    log(f"navegador: cookies não entraram ({str(e)[:80]})")
            ctx.add_init_script(GANCHOS.replace("%GUARDAR%", "true" if capturar_em else "false"))
            pg = ctx.new_page()

            def ao_responder(resp):
                try:
                    req = resp.request
                    if RE_LICENCA.search(req.url) and req.method == "POST":
                        obs["licenca_drm_rede"] = True
                    if 300 <= resp.status < 400:
                        return
                    req0 = req
                    while req0.redirected_from is not None:
                        req0 = req0.redirected_from
                    u = req0.url
                    if u.startswith(("data:", "blob:")):
                        return
                    ct = (resp.headers.get("content-type") or "").lower()
                    tipo = tipo_da_url(u, ct if ("video" in ct or "mpegurl" in ct or "dash" in ct) else "")
                    if not (MIME_MIDIA.search(ct) or tipo in ("hls", "dash", "progressivo")):
                        return
                    if resp.status in (401, 403, 451):
                        obs["status_midia"][str(resp.status)] = obs["status_midia"].get(str(resp.status), 0) + 1
                        obs.setdefault("urls_recusadas", []).append(u)  # o player pediu e o servidor recusou: é o vídeo dele
                        return
                    if tipo in ("ignorar",) or (ct.startswith(("image/", "text/css", "font/", "text/javascript")) and tipo == "desconhecido"):
                        return
                    if "octet" in ct and tipo == "desconhecido":
                        return
                    if re.search(r"\.(ts|m4s|aac)(\?|$)|/seg[-_]?\d+|/segment|/frag|/chunk-\d|/range/", u, re.I) and tipo != "hls":
                        return  # pedaços de HLS/DASH: o que interessa é a lista
                    if req.method == "POST" and tipo in ("hls", "dash"):
                        obs["manifesto_por_post"] = True
                    tam = resp.headers.get("content-length")
                    cr = resp.headers.get("content-range", "")
                    if cr and "/" in cr and cr.split("/")[-1].isdigit():
                        tam = cr.split("/")[-1]
                    reg = vistos.setdefault(u, {"ct": ct, "tam": None, "antes": False, "depois": False,
                                                "referer": req.headers.get("referer", ""), "status": resp.status,
                                                "metodo": req.method})
                    pedidos.setdefault(u, req)
                    if tam and str(tam).isdigit():
                        reg["tam"] = max(reg["tam"] or 0, int(tam))
                    if marco["play"] is None:
                        reg["antes"] = True
                    else:
                        reg["depois"] = True
                except Exception:
                    pass

            pg.on("response", ao_responder)
            if baixar_na_hora:
                # o arquivo de vídeo que o <video> pede é baixado INTEIRO no mesmo instante do pedido (o endereço ainda
                # vale) e entregue ao player do mesmo jeito: nada muda para a página
                pasta_hora = Path(baixar_na_hora)
                pasta_hora.mkdir(parents=True, exist_ok=True)

                def ao_rotear(route):
                    req = route.request
                    u = req.url
                    if req.resource_type != "media" or u in out["baixados"] or u.startswith(("blob:", "data:")):
                        return route.continue_()
                    try:
                        h = {k: v for k, v in req.headers.items() if k.lower() not in ("range", "if-range")}
                        resp = route.fetch(headers=h, max_redirects=10, timeout=600000)
                        corpo = resp.body()
                        if resp.ok and len(corpo) > 1000 and not corpo[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                            arq = pasta_hora / f"midia_{len(out['baixados'])}.bin"
                            arq.write_bytes(corpo)
                            out["baixados"][u] = str(arq)
                            log(f"baixei na hora o que o player pediu ({len(corpo) / 1e6:.1f} MB)")
                        route.fulfill(response=resp, body=corpo)
                    except Exception as e:
                        log(f"baixar na hora: {type(e).__name__}: {str(e)[:100]}")
                        try:
                            route.continue_()
                        except Exception:
                            pass
                ctx.route("**/*", ao_rotear)
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                out["erro"] = str(e)[:200]
            try:
                pg.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            # desafio anti-robô (Cloudflare e parecidos): costuma se resolver sozinho em poucos segundos num navegador real
            try:
                if RE_DESAFIO.search(pg.title() or "") or RE_DESAFIO.search((pg.content() or "")[:20000]):
                    obs["desafio_antirrobo"] = True
                    log("navegador: página de verificação anti-robô; esperando ela liberar...")
                    for _ in range(20):
                        pg.wait_for_timeout(1000)
                        if not RE_DESAFIO.search(pg.title() or ""):
                            break
            except Exception:
                pass
            if cancel is not None and cancel.is_set():
                return out
            marco["play"] = time.time()
            _apertar_play(pg)
            fim = time.time() + espera_s
            while time.time() < fim:
                if cancel is not None and cancel.is_set():
                    return out
                pg.wait_for_timeout(500)
                if any(r["depois"] for r in vistos.values()) and time.time() > marco["play"] + 3.5 and not capturar_em:
                    break
            if capturar_em:
                _capturar_ate_o_fim(pg, Path(capturar_em), out, cancel, tempo_max_captura, log, progresso)
            if baixar_na_hora:
                _baixar_na_hora(ctx, vistos, pedidos, Path(baixar_na_hora), out, log)
            # o que a página fez (ganchos)
            try:
                b = pg.evaluate("() => window.__bxv ? {drm: window.__bxv.drm, appends: window.__bxv.appends, blobs: window.__bxv.blobs.length} : null")
                if b:
                    obs["drm"] = sorted(set(b["drm"]))
                    obs["mse_appends"] = b["appends"]
                    obs["blobs_video"] = b["blobs"]
            except Exception:
                pass
            try:  # vídeo desenhado num <canvas> (sem <video>): só assistindo e gravando dá
                obs["canvas_grande"] = bool(pg.evaluate("""() => { const vs = document.querySelectorAll('video').length;
                    const cs = [...document.querySelectorAll('canvas')].filter(c => { const r = c.getBoundingClientRect(); return r.width * r.height > 60000; });
                    return vs === 0 && cs.length > 0; }"""))
            except Exception:
                pass
            try:
                obs["parede_login"] = bool(pg.evaluate(
                    "() => !!document.querySelector('input[type=password]') && document.querySelectorAll('video').length === 0"))
            except Exception:
                pass
            # <video> da página
            try:
                vids = pg.evaluate("""() => Array.from(document.querySelectorAll('video')).map((v, i) => {
                    const r = v.getBoundingClientRect();
                    return {i, src: v.currentSrc || v.src || '', w: r.width, h: r.height, dur: isFinite(v.duration) ? v.duration : 0,
                            muted: v.muted, loop: v.loop, autoplay: v.autoplay, vis: r.width > 0 && r.height > 0,
                            vw: v.videoWidth, vh: v.videoHeight};
                })""")
            except Exception:
                vids = []
            maior = max((v["w"] * v["h"] for v in vids), default=0)
            for v in vids:
                try:
                    if v["vis"] and v["w"] * v["h"] > 4000:
                        foto = pg.locator("video").nth(v["i"]).screenshot(timeout=3000)
                        out["fotos"][f"video#{v['i']}"] = foto
                        if v["src"] and not v["src"].startswith("blob:"):
                            out["fotos"][v["src"]] = foto
                except Exception:
                    pass
            try:
                out["html"] = pg.content()
                out["titulo"] = pg.title()
                out["cookies"] = ctx.cookies()
            except Exception:
                pass
            nomes_cookie_resp = set()
            cands = []
            ordem = 0
            for u, r in vistos.items():
                ordem += 1
                tipo = tipo_da_url(u, r["ct"])
                if tipo in ("ignorar",):
                    continue
                if tipo == "desconhecido" and "video" in r["ct"]:
                    tipo = "progressivo"
                c = Candidato(url=u, tipo=tipo, ordem=ordem, grupo=chave_grupo(u), altura=altura_da_url(u))
                c.origens = {"rede"}
                c.ctx = {"tamanho_mb": (r["tam"] or 0) / 1e6 if r["tam"] else None, "depois_do_play": r["depois"],
                         "antes_do_play": r["antes"] and not r["depois"], "mime": r["ct"], "metodo": r["metodo"]}
                # cabeçalhos EXATOS do pedido (inclui Cookie/Authorization que o navegador pôs na hora)
                try:
                    todos = pedidos[u].all_headers()
                except Exception:
                    todos = {}
                especiais = {k: v for k, v in todos.items() if k.lower() not in CABECALHOS_COMUNS and not k.startswith(":")}
                for k in especiais:
                    if k.lower() not in ("referer", "origin", "cookie") and k not in obs["cabecalhos_especiais"]:
                        obs["cabecalhos_especiais"].append(k)
                if "cookie" in especiais:
                    for par in especiais["cookie"].split(";"):
                        nomes_cookie_resp.add(par.split("=")[0].strip())
                c.headers.update({_titulo(k): v for k, v in especiais.items()})
                if r["referer"] and "Referer" not in c.headers:
                    c.headers["Referer"] = r["referer"]
                cands.append(c)
            # cookie usado pela mídia que não veio de resposta do servidor = posto pelo JavaScript da página
            try:
                cookies_http = {ck["name"] for ck in out["cookies"] if ck.get("httpOnly")}
                obs["cookies_de_script"] = bool(nomes_cookie_resp - cookies_http) and bool(nomes_cookie_resp)
            except Exception:
                pass
            for v in vids:
                src = v.get("src") or ""
                if src and not src.startswith("blob:"):
                    alvo = next((c for c in cands if c.url == src), None)
                    if alvo is None:
                        ordem += 1
                        alvo = Candidato(url=src, tipo=tipo_da_url(src), ordem=ordem, grupo=chave_grupo(src), altura=altura_da_url(src))
                        alvo.origens = {"dom"}
                        alvo.ctx = {}
                        cands.append(alvo)
                    alvo.origens.add("dom")
                    alvo.ctx.update({"area_visivel": v["w"] * v["h"], "maior_visivel": maior > 0 and v["w"] * v["h"] == maior,
                                     "duracao": v["dur"], "autoplay": v["autoplay"], "muted": v["muted"], "loop": v["loop"]})
                    if v.get("vh"):
                        alvo.altura = alvo.altura or int(v["vh"])
                elif src.startswith("blob:") and v["w"] * v["h"] == maior and maior:
                    for c in cands:
                        if c.tipo in ("hls", "dash") and c.ctx.get("depois_do_play"):
                            c.ctx["area_visivel"] = maior
                            c.ctx["maior_visivel"] = True
                            c.ctx["duracao"] = v["dur"]
            for c in cands:
                c.ctx["n_total"] = len(cands)
                c.ctx["n_grupo"] = sum(1 for x in cands if x.grupo == c.grupo)
            out["candidatos"] = cands
        finally:
            try:
                nav.close()
            except Exception:
                pass
    return out


def _baixar_na_hora(ctx, vistos: dict, pedidos: dict, pasta: Path, out: dict, log, limite_bytes: int = 1_500_000_000):
    """Endereço com validade curta (expires/token de uso rápido): baixa AINDA DENTRO do navegador, com os mesmos
    cookies e cabeçalhos, enquanto o endereço vale (achado na bateria de sites difíceis: depois de 10-15 s ele morre)."""
    pasta.mkdir(parents=True, exist_ok=True)
    progressivos = [(u, r) for u, r in vistos.items() if tipo_da_url(u, r["ct"]) in ("progressivo", "desconhecido")
                    and ("video" in r["ct"] or tipo_da_url(u, r["ct"]) == "progressivo")]
    progressivos.sort(key=lambda x: (not x[1]["depois"], -(x[1]["tam"] or 0)))
    for i, (u, r) in enumerate(progressivos[:4]):
        if u in out["baixados"]:
            continue
        if r["tam"] and r["tam"] > limite_bytes:
            log(f"baixar na hora: {u[:60]} é grande demais para o navegador ({r['tam'] / 1e9:.1f} GB)")
            continue
        try:
            h = {k: v for k, v in pedidos[u].all_headers().items()
                 if not k.startswith(":") and k.lower() not in ("range", "if-range", "host", "content-length", "accept-encoding")}
        except Exception:
            h = {}
        try:
            resp = ctx.request.get(u, headers=h, timeout=600000, max_redirects=10)
            if resp.ok:
                corpo = resp.body()
                if len(corpo) > 1000 and not corpo[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                    arq = pasta / f"rede_{i}.bin"
                    arq.write_bytes(corpo)
                    out["baixados"][u] = str(arq)
            else:
                log(f"baixar na hora: {resp.status} em {u[:70]}")
        except Exception as e:
            log(f"baixar na hora: {type(e).__name__}: {str(e)[:100]}")


def _titulo(k: str) -> str:
    return "-".join(p[:1].upper() + p[1:] for p in k.split("-"))


def _capturar_ate_o_fim(pg, pasta: Path, out: dict, cancel, tempo_max: float, log, progresso):
    """Toca o vídeo principal rápido até o fim e grava tudo o que o player recebeu (MSE ou Blob)."""
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        pg.evaluate("""() => { const vs = Array.from(document.querySelectorAll('video'));
            for (const v of vs) { try { v.muted = true; v.playbackRate = 16; v.play(); } catch(e){} } }""")
    except Exception:
        pass
    inicio = time.time()
    parado_desde = time.time()
    ultimo_total = -1
    while time.time() - inicio < tempo_max:
        if cancel is not None and cancel.is_set():
            return
        pg.wait_for_timeout(1000)
        try:
            st = pg.evaluate("""() => { const vs = Array.from(document.querySelectorAll('video'));
                let v = vs.sort((a, b) => (b.clientWidth * b.clientHeight) - (a.clientWidth * a.clientHeight))[0];
                if (v) { try { if (v.paused && !v.ended) v.play(); if (v.playbackRate < 16) v.playbackRate = 16; } catch(e){} }
                const B = window.__bxv || {sb: [], blobs: []};
                return {t: v ? v.currentTime : 0, d: v && isFinite(v.duration) ? v.duration : 0, fim: v ? v.ended : false,
                        bytes: B.sb.reduce((s, x) => s + x.bytes, 0), blobs: (B.blobsObj || []).length, drm: B.drm.length}; }""")
        except Exception:
            break
        if st["drm"]:
            log("captura: a página pediu chave de DRM; a gravação dos bytes não serve (vêm cifrados)")
            out["obs"]["drm"] = ["(pedido durante a captura)"]
            return
        if st["d"]:
            progresso(min(99.0, st["t"] / st["d"] * 100), f"capturando o que o player recebe · {st['t']:.0f}/{st['d']:.0f} s")
        else:
            progresso(None, f"capturando o que o player recebe · {st['bytes'] / 1e6:.1f} MB")
        total = st["bytes"] + st["blobs"]
        if total != ultimo_total:
            ultimo_total = total
            parado_desde = time.time()
        if st["fim"] or (st["d"] and st["t"] >= st["d"] - 0.3):
            pg.wait_for_timeout(800)
            break
        if time.time() - parado_desde > 20 and (st["bytes"] or st["blobs"]):
            log("captura: o player parou de receber dados há 20 s; fechando o que já tenho")
            break
        if time.time() - parado_desde > 25 and not (st["bytes"] or st["blobs"]):
            log("captura: o player não recebeu nada (sem MSE/Blob nesta página)")
            break
    # tira os bytes da página
    try:
        info = pg.evaluate("() => (window.__bxv ? window.__bxv.sb.map(x => ({mime: x.mime, n: x.chunks.length, bytes: x.bytes})) : [])")
    except Exception:
        info = []
    for i, sb in enumerate(info):
        if not sb["n"]:
            continue
        ext = "webm" if "webm" in sb["mime"] else ("ts" if "mp2t" in sb["mime"] else "mp4")
        arq = pasta / f"captura_sb{i}.{ext}"
        with open(arq, "wb") as f:
            for j in range(sb["n"]):
                try:
                    f.write(base64.b64decode(pg.evaluate("([i, j]) => window.__bxv_ler(i, j)", [i, j])))
                except Exception:
                    break
        out["capturas"].append({"arquivo": str(arq), "mime": sb["mime"], "bytes": arq.stat().st_size, "fonte": "mse"})
    try:
        n_blobs = pg.evaluate("() => (window.__bxv && window.__bxv.blobsObj) ? window.__bxv.blobsObj.length : 0")
    except Exception:
        n_blobs = 0
    for i in range(n_blobs):
        try:
            dados = base64.b64decode(pg.evaluate("(i) => window.__bxv_blob(i)", i))
        except Exception:
            continue
        arq = pasta / f"captura_blob{i}.bin"
        arq.write_bytes(dados)
        out["capturas"].append({"arquivo": str(arq), "mime": "blob", "bytes": len(dados), "fonte": "blob"})
