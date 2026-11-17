# -*- coding: utf-8 -*-
"""Técnica "assistir e gravar" (ideia do doug, 17/11): quando nada consegue baixar, o programa finge ser você
assistindo — abre a página, aperta play e grava o vídeo QUADRO A QUADRO COM O ÁUDIO, do começo ao fim, em tempo real.

Dois jeitos, nesta ordem:
  1) gravador do navegador (sem tela): o Edge escondido toca o vídeo e o próprio navegador grava o que o <video>
     (ou o <canvas>) mostra e o som que ele toca (captureStream + MediaRecorder). Não ocupa a sua tela nem o seu som.
  2) gravação da tela (igual ao "Gravar área" e ao observador de tela): se o site bloqueia o jeito 1 (DRM, vídeo de outro
     domínio), abre uma janela do Edge, deixa o vídeo do tamanho da janela, toca e grava aquela área com o som do PC.
     Nesse modo a janela precisa ficar visível e o som do PC entra junto.

Leva o tempo do vídeo (grava em velocidade normal, para o som sair certo).
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from .base import ffprobe_info

JS_INICIAR = r"""
async () => {
  const area = e => { const r = e.getBoundingClientRect(); return r.width * r.height; };
  const els = [...document.querySelectorAll('video, canvas')].filter(e => area(e) > 2000).sort((a, b) => area(b) - area(a));
  const el = els[0];
  if (!el) return {erro: 'sem video nem canvas na pagina'};
  if (el.tagName === 'VIDEO') {
    if (!el.currentSrc && !el.srcObject) return {erro: 'o <video> da página não tem vídeo carregado'};
    if (el.readyState < 1) { try { await el.play(); } catch (e) {} await new Promise(r => setTimeout(r, 3000)); }
    if (el.readyState < 1 || !(el.duration > 0)) return {erro: 'o vídeo não carregou na página (nada para assistir)'};
  }
  window.__bxg = {chunks: [], fim: false, erro: null, tipo: el.tagName};
  let stream;
  try {
    if (el.tagName === 'VIDEO') {
      el.muted = false; el.volume = 1;
      try { el.pause(); el.currentTime = 0; } catch (e) {}
      stream = el.captureStream ? el.captureStream() : el.mozCaptureStream();
    } else {
      stream = el.captureStream(30);
    }
  } catch (e) { return {erro: 'captura bloqueada: ' + e}; }
  const tipos = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'];
  const mime = tipos.find(t => MediaRecorder.isTypeSupported(t)) || '';
  let rec;
  try { rec = new MediaRecorder(stream, mime ? {mimeType: mime, videoBitsPerSecond: 8000000} : {}); }
  catch (e) { return {erro: 'gravador recusado: ' + e}; }
  rec.ondataavailable = ev => { if (ev.data && ev.data.size) window.__bxg.chunks.push(ev.data); };
  rec.onstop = () => { window.__bxg.fim = true; };
  rec.onerror = ev => { window.__bxg.erro = String(ev.error || ev); };
  window.__bxg.rec = rec;
  window.__bxg.el = el;
  rec.start(1000);
  if (el.tagName === 'VIDEO') {
    el.addEventListener('ended', () => { try { rec.stop(); } catch (e) {} }, {once: true});
    try { await el.play(); } catch (e) { el.muted = true; try { await el.play(); } catch (e2) {} }
  }
  return {tipo: el.tagName, dur: el.tagName === 'VIDEO' && isFinite(el.duration) ? el.duration : 0, mime};
}
"""

JS_ESTADO = r"""
() => { const g = window.__bxg; if (!g) return null; const el = g.el;
  let assinatura = 0;
  if (el && el.tagName === 'CANVAS') { try { const c = el.getContext('2d'); if (c) { const d = c.getImageData(0, 0, Math.min(64, el.width), Math.min(64, el.height)).data;
      for (let i = 0; i < d.length; i += 97) assinatura = (assinatura * 31 + d[i]) % 1000000007; } } catch (e) {} }
  return {t: el && el.tagName === 'VIDEO' ? el.currentTime : 0, d: el && el.tagName === 'VIDEO' && isFinite(el.duration) ? el.duration : 0,
          fim: g.fim, ended: el && el.tagName === 'VIDEO' ? el.ended : false, n: g.chunks.length,
          bytes: g.chunks.reduce((s, c) => s + c.size, 0), erro: g.erro, assinatura,
          pausado: el && el.tagName === 'VIDEO' ? el.paused : false}; }
"""

JS_LER = r"""
async ([a, b]) => { const g = window.__bxg; if (!g.blob) g.blob = new Blob(g.chunks, {type: 'video/webm'});
  const buf = new Uint8Array(await g.blob.slice(a, b).arrayBuffer()); let s = ''; const P = 0x8000;
  for (let k = 0; k < buf.length; k += P) s += String.fromCharCode.apply(null, buf.subarray(k, k + P));
  return [btoa(s), g.blob.size]; }
"""

JS_ENCHER_JANELA = r"""
() => { const area = e => { const r = e.getBoundingClientRect(); return r.width * r.height; };
  const el = [...document.querySelectorAll('video, canvas')].filter(e => area(e) > 2000).sort((a, b) => area(b) - area(a))[0];
  if (!el) return false;
  const s = document.createElement('style');
  s.textContent = 'html,body{overflow:hidden!important;background:#000!important}';
  document.head.appendChild(s);
  Object.assign(el.style, {position: 'fixed', left: '0', top: '0', width: '100vw', height: '100vh', zIndex: '2147483647',
                           background: '#000', objectFit: 'contain', margin: '0', maxWidth: 'none', maxHeight: 'none'});
  if (el.tagName === 'VIDEO') { el.controls = false; el.muted = false; el.volume = 1; try { el.currentTime = 0; } catch (e) {} }
  window.__bxg_el = el; return true; }
"""


def _novo_navegador(p, headless: bool, proxy, extra_args=()):
    args = {"headless": headless, "args": ["--autoplay-policy=no-user-gesture-required",
                                            "--disable-blink-features=AutomationControlled", *extra_args]}
    if proxy:
        args["proxy"] = {"server": proxy.replace("socks5h://", "socks5://")}
    try:
        return p.chromium.launch(channel="msedge", **args)
    except Exception:
        return p.chromium.launch(**args)


def _abrir(nav, url, headers, cookies_extra, viewport=(1280, 720)):
    ctx = nav.new_context(viewport={"width": viewport[0], "height": viewport[1]}, locale="pt-BR",
                          extra_http_headers={k: v for k, v in (headers or {}).items() if k.lower() not in ("user-agent", "cookie")})
    if cookies_extra:
        try:
            ctx.add_cookies(cookies_extra)
        except Exception:
            pass
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        pass
    try:
        pg.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    from .navegador import _apertar_play
    _apertar_play(pg)
    pg.wait_for_timeout(2500)
    return ctx, pg


def _quadros_validos(arq: Path) -> bool:
    """Gravou imagem de verdade? (DRM costuma dar quadros pretos)."""
    import subprocess
    from .base import SEM_JANELA, achar_programa
    info = ffprobe_info(arq)
    if not info.get("tem_video"):
        return False
    ff = achar_programa("ffmpeg")
    if not ff:
        return True
    dur = info.get("duracao") or 2
    for pos in (dur * 0.3, dur * 0.6):
        try:
            r = subprocess.run([ff, "-v", "error", "-ss", f"{pos:.2f}", "-i", str(arq), "-frames:v", "1", "-vf", "scale=32:18",
                                "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=60, creationflags=SEM_JANELA)
            b = r.stdout
            if b and (max(b) - min(b) > 12 or sum(b) / len(b) > 20):
                return True
        except Exception:
            continue
    return False


def gravar_pelo_navegador(url: str, pasta: Path, proxy=None, headers=None, cookies_extra=None, cancel=None,
                          log=lambda t: None, progresso=lambda p, t: None, tempo_max: float = 3 * 3600) -> dict:
    """Jeito 1: o navegador escondido grava o próprio <video>/<canvas> com o som (MediaRecorder)."""
    from playwright.sync_api import sync_playwright
    pasta.mkdir(parents=True, exist_ok=True)
    out = {"arquivo": None, "erro": "", "modo": "gravador do navegador"}
    with sync_playwright() as p:
        nav = _novo_navegador(p, True, proxy)
        try:
            ctx, pg = _abrir(nav, url, headers, cookies_extra)
            ini = pg.evaluate(JS_INICIAR)
            if ini.get("erro"):
                out["erro"] = ini["erro"]
                log(f"assistir: {ini['erro']}")
                return out
            log(f"assistir: gravando o {ini['tipo'].lower()} da página em tempo real"
                + (f" ({ini['dur']:.0f} s de vídeo)" if ini.get("dur") else ""))
            inicio = time.time()
            ultimo_t, ultimo_mov, ultima_ass, parado_ass = -1.0, time.time(), None, time.time()
            while time.time() - inicio < tempo_max:
                if cancel is not None and cancel.is_set():
                    break
                pg.wait_for_timeout(1000)
                st = pg.evaluate(JS_ESTADO)
                if not st:
                    break
                if st.get("erro"):
                    out["erro"] = st["erro"]
                    break
                if st["fim"] or st["ended"]:
                    break
                if ini["tipo"] == "VIDEO":
                    if st["d"]:
                        progresso(min(99.0, st["t"] / st["d"] * 100), f"assistindo e gravando · {st['t']:.0f}/{st['d']:.0f} s")
                    if st["t"] > ultimo_t + 0.05:
                        ultimo_t, ultimo_mov = st["t"], time.time()
                    elif st["pausado"]:
                        pg.evaluate("() => { const e = window.__bxg.el; try { e.play(); } catch (x) {} }")
                    if time.time() - ultimo_mov > 45:
                        log("assistir: o vídeo parou de andar há 45 s; fechando a gravação")
                        break
                else:  # canvas: sem duração, para quando a imagem para de mudar
                    progresso(None, f"assistindo e gravando o canvas · {time.time() - inicio:.0f} s")
                    if st["assinatura"] != ultima_ass:
                        ultima_ass, parado_ass = st["assinatura"], time.time()
                    elif time.time() - parado_ass > 10:
                        break
            pg.evaluate("() => { try { window.__bxg.rec.stop(); } catch (e) {} }")
            for _ in range(20):
                if pg.evaluate("() => window.__bxg.fim"):
                    break
                pg.wait_for_timeout(500)
            arq = pasta / "assistido.webm"
            pos, tam, bloco = 0, None, 4 * 1024 * 1024
            with open(arq, "wb") as f:
                while tam is None or pos < tam:
                    b64, tam = pg.evaluate(JS_LER, [pos, pos + bloco])
                    if not b64:
                        break
                    f.write(base64.b64decode(b64))
                    pos += bloco
            if arq.stat().st_size < 2000 or not _quadros_validos(arq):
                out["erro"] = "a gravação do navegador saiu vazia ou preta (proteção do site)"
                log(f"assistir: {out['erro']}")
                return out
            out["arquivo"] = arq
            return out
        except Exception as e:
            out["erro"] = f"{type(e).__name__}: {str(e)[:160]}"
            log(f"assistir: {out['erro']}")
            return out
        finally:
            try:
                nav.close()
            except Exception:
                pass


def gravar_pela_tela(url: str, pasta: Path, proxy=None, headers=None, cookies_extra=None, cancel=None,
                     log=lambda t: None, progresso=lambda p, t: None, tempo_max: float = 3 * 3600) -> dict:
    """Jeito 2: janela visível do Edge com o vídeo ocupando tudo + gravação daquela área da tela com o som do PC."""
    from playwright.sync_api import sync_playwright
    from .tela import GravadorArea
    pasta.mkdir(parents=True, exist_ok=True)
    out = {"arquivo": None, "erro": "", "modo": "gravação da tela"}
    with sync_playwright() as p:
        nav = _novo_navegador(p, False, proxy, ("--window-position=40,40", "--window-size=1280,800", "--start-maximized=false"))
        g = None
        try:
            ctx, pg = _abrir(nav, url, headers, cookies_extra, viewport=None or (1280, 720))
            if not pg.evaluate(JS_ENCHER_JANELA):
                out["erro"] = "não achei o vídeo na página"
                return out
            pg.bring_to_front()
            pg.wait_for_timeout(800)
            geo = pg.evaluate("""() => ({sx: window.screenX, sy: window.screenY, ow: window.outerWidth, oh: window.outerHeight,
                                        iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio || 1})""")
            borda = max(0, (geo["ow"] - geo["iw"]) / 2)
            x = (geo["sx"] + borda) * geo["dpr"]
            y = (geo["sy"] + geo["oh"] - geo["ih"] - borda) * geo["dpr"]
            area = (int(x), int(y), int(geo["iw"] * geo["dpr"]), int(geo["ih"] * geo["dpr"]))
            # confere que a área da tela é MESMO a janela do vídeo (nunca gravar outra coisa da sua tela)
            try:
                import io
                from PIL import Image
                from .tela import capturar, semelhanca
                foto_pagina = Image.open(io.BytesIO(pg.screenshot())).convert("RGB")
                foto_tela = capturar(area)
                sim = semelhanca(foto_pagina, foto_tela)
            except Exception as e:
                sim = 0.0
                log(f"assistir pela tela: não consegui conferir a janela ({type(e).__name__})")
            if sim < 0.6:
                out["erro"] = f"a janela do Edge não estava visível no lugar certo (parecença {sim:.0%}); não gravei para não pegar outra coisa da tela"
                log("assistir pela tela: " + out["erro"])
                return out
            if not pg.evaluate("() => { const e = window.__bxg_el; return !!e && (e.tagName === 'CANVAS' || (e.readyState >= 1 && e.duration > 0)); }"):
                out["erro"] = "o vídeo não carregou na página (nada para assistir)"
                return out
            log(f"assistir pela tela: janela do Edge em {area}; não cubra a janela enquanto grava (o som do PC entra junto)")
            g = GravadorArea(area, pasta, "assistido_tela")
            g.iniciar()
            pg.evaluate("() => { const e = window.__bxg_el; if (e && e.play) { e.muted = false; e.play(); } }")
            inicio, ultimo_t, ultimo_mov = time.time(), -1.0, time.time()
            while time.time() - inicio < tempo_max:
                if cancel is not None and cancel.is_set():
                    break
                pg.wait_for_timeout(1000)
                st = pg.evaluate("""() => { const e = window.__bxg_el; return e && e.tagName === 'VIDEO' ?
                    {t: e.currentTime, d: isFinite(e.duration) ? e.duration : 0, fim: e.ended} : {t: 0, d: 0, fim: false}; }""")
                if st["d"]:
                    progresso(min(99.0, st["t"] / st["d"] * 100), f"assistindo pela tela · {st['t']:.0f}/{st['d']:.0f} s · {g.status_som}")
                if st["fim"]:
                    break
                if st["t"] > ultimo_t + 0.05:
                    ultimo_t, ultimo_mov = st["t"], time.time()
                elif time.time() - ultimo_mov > 45:
                    log("assistir pela tela: o vídeo parou de andar; fechando")
                    break
            arq = g.parar()
            g = None
            if not arq or not _quadros_validos(Path(arq)):
                out["erro"] = "a gravação da tela saiu vazia ou preta"
                return out
            out["arquivo"] = Path(arq)
            return out
        except Exception as e:
            out["erro"] = f"{type(e).__name__}: {str(e)[:160]}"
            return out
        finally:
            if g is not None:
                try:
                    g.parar()
                except Exception:
                    pass
            try:
                nav.close()
            except Exception:
                pass


def assistir_e_gravar(url: str, pasta: Path, usar_tela: bool = True, **kw) -> dict:
    r = gravar_pelo_navegador(url, pasta, **kw)
    if r["arquivo"] or not usar_tela or (kw.get("cancel") is not None and kw["cancel"].is_set()):
        return r
    if "nada para assistir" in r.get("erro", "") or "não tem vídeo" in r.get("erro", ""):
        return r  # sem vídeo carregado, a tela também não teria o que gravar
    kw.get("log", lambda t: None)("assistir: o site bloqueou o gravador do navegador; indo para a gravação da tela")
    r2 = gravar_pela_tela(url, pasta, **kw)
    if not r2["arquivo"]:
        r2["erro"] = f"{r['erro']} / {r2['erro']}"
    return r2
