# -*- coding: utf-8 -*-
"""Sites que NÃO deixam baixar, simulados em 127.0.0.1 (nada sai do computador), para treinar e conferir o modo difícil.

Cada cenário imita uma proteção de site de verdade. O vídeo certo tem cor própria (conferida no arquivo baixado).
  token_header      a mídia só sai com o cabeçalho X-Token que o JavaScript pede a /api/token (vale 2 minutos)
  token_uso_unico   igual, mas o token só vale UMA vez (repetir o pedido não adianta) -> captura do player
  mse_embaralhado   os bytes do vídeo vêm embaralhados (XOR) e o JS desembaralha antes de tocar (MSE) -> captura
  manifesto_post    o JS faz POST em /api/play e recebe o endereço assinado (vale 15 s)
  cookie_js         o JS cria o cookie de acesso; sem ele a mídia dá 403
  anti_robo         1ª visita = desafio em JS que grava cookie e recarrega; sem navegador = 403
  assinado_curto    o JS monta o endereço com expires=agora+10 s (o servidor confere a hora)
  blob_inteiro      o JS baixa o vídeo inteiro com fetch e toca por Blob (URL.createObjectURL)
  drm_falso         a página pede chave de DRM (EME). O certo é o programa DIAGNOSTICAR DRM e não fingir que baixou
  referer_origem    a mídia exige Referer E Origin da página

Uso: .venv\\Scripts\\python.exe testes\\sites_dificeis.py [repeticoes]
"""
from __future__ import annotations

import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AQUI = Path(__file__).resolve().parent
SAIDA = AQUI / "saida"
SAIDA.mkdir(exist_ok=True)
os.environ["BAIXADOR_DADOS"] = str(SAIDA / "dados_dificeis")
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

from baixador.base import SEM_JANELA, achar_programa  # noqa: E402
from midias import CORES, PASTA as MIDIAS, cor_media, gerar_tudo, qual_cor  # noqa: E402

CENARIOS = ["token_header", "token_uso_unico", "mse_embaralhado", "manifesto_post", "cookie_js", "anti_robo",
            "assinado_curto", "blob_inteiro", "drm_falso", "referer_origem"]
ESPERADO = {c: "video" for c in CENARIOS}
ESPERADO["drm_falso"] = "drm"
CODEC = 'video/mp4; codecs="avc1.42E01E, mp4a.40.2"'


def fmp4(cor: str) -> Path:
    """MP4 fragmentado (o formato que players MSE tocam) com perfil baseline 3.0 = avc1.42E01E."""
    alvo = MIDIAS / f"{cor}.frag.mp4"
    if not alvo.exists():
        r, g, b = CORES[cor]
        subprocess.run([achar_programa("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"color=c=0x{r:02x}{g:02x}{b:02x}:s=320x180:d=4:r=24",
                        "-f", "lavfi", "-i", "sine=frequency=500:duration=4", "-c:v", "libx264", "-profile:v", "baseline",
                        "-level", "3.0", "-pix_fmt", "yuv420p", "-g", "24", "-c:a", "aac", "-b:a", "64k", "-shortest",
                        "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(alvo)],
                       capture_output=True, creationflags=SEM_JANELA)
    return alvo


ESTADO: dict = {}   # por cenário: cor, tokens, cookie...
LOCK = threading.Lock()


def pagina(cen: str, cid: str) -> str:
    base = f"/{cid}"
    titulo = f"<title>Vídeo protegido {cid}</title>"
    if cen in ("token_header", "token_uso_unico"):
        js = f"""const t = await (await fetch('{base}/api/token')).json();
                 const r = await fetch('{base}/media/v.mp4', {{headers: {{'X-Token': t.token}}}});
                 const b = await r.blob(); v.src = URL.createObjectURL(b); v.play();"""
    elif cen == "mse_embaralhado":
        js = f"""const ms = new MediaSource(); v.src = URL.createObjectURL(ms);
                 ms.addEventListener('sourceopen', async () => {{
                   const sb = ms.addSourceBuffer('{CODEC}');
                   const raw = new Uint8Array(await (await fetch('{base}/seg/dados.bin')).arrayBuffer());
                   for (let i = 0; i < raw.length; i++) raw[i] ^= 0x5A;
                   sb.addEventListener('updateend', () => {{ try {{ ms.endOfStream(); }} catch(e){{}} }}, {{once: true}});
                   sb.appendBuffer(raw);
                 }}); v.play();"""
    elif cen == "manifesto_post":
        js = f"""const j = await (await fetch('{base}/api/play', {{method: 'POST', body: JSON.stringify({{id: '{cid}'}})}})).json();
                 v.src = j.url; v.play();"""
    elif cen == "cookie_js":
        js = f"""document.cookie = 'acesso=' + btoa('{cid}') + '; path=/'; v.src = '{base}/media/v.mp4'; v.play();"""
    elif cen == "anti_robo":
        js = f"""v.src = '{base}/media/v.mp4'; v.play();"""
    elif cen == "assinado_curto":
        js = f"""const e = Math.floor(Date.now() / 1000) + 10; v.src = '{base}/media/v.mp4?expires=' + e + '&sig=' + (e * 7 % 9973); v.play();"""
    elif cen == "blob_inteiro":
        js = f"""const b = await (await fetch('{base}/media/v.mp4?raw=1')).blob(); v.src = URL.createObjectURL(b); v.play();"""
    elif cen == "drm_falso":
        js = """try { await navigator.requestMediaKeySystemAccess('com.widevine.alpha', [{initDataTypes: ['cenc'],
                 videoCapabilities: [{contentType: 'video/mp4; codecs="avc1.42E01E"'}]}]); } catch(e) {}
                 try { await navigator.requestMediaKeySystemAccess('org.w3.clearkey', [{initDataTypes: ['cenc'],
                 videoCapabilities: [{contentType: 'video/mp4; codecs="avc1.42E01E"'}]}]); } catch(e) {}"""
    else:  # referer_origem
        js = f"""const b = await (await fetch('{base}/media/v.mp4', {{mode: 'cors'}})).blob(); v.src = URL.createObjectURL(b); v.play();"""
    return (f"<!doctype html><html><head><meta charset='utf-8'>{titulo}</head><body><h1>Vídeo</h1>"
            f"<video id='v' controls muted width='640' height='360'></video>"
            f"<script>(async () => {{ const v = document.getElementById('v'); {js} }})();</script></body></html>")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def env(self, cod, corpo=b"", tipo="text/plain", extra=None):
        self.send_response(cod)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def do_POST(self):
        self.do_GET()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._get()
        except (ConnectionError, OSError):
            pass

    def _get(self):
        p = urlparse(self.path)
        partes = p.path.strip("/").split("/", 1)
        cid = partes[0]
        resto = "/" + (partes[1] if len(partes) > 1 else "")
        with LOCK:
            st = ESTADO.get(cid)
        if not st:
            return self.env(404, b"?")
        cen = st["cen"]
        if self.command == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
        ck = self.headers.get("Cookie", "")
        if resto in ("/", "/watch"):
            if cen == "anti_robo" and f"desafio=ok{cid}" not in ck:
                corpo = (f"<!doctype html><html><head><title>Just a moment...</title></head><body>Checking your browser"
                         f"<script>setTimeout(() => {{ document.cookie = 'desafio=ok{cid}; path=/'; location.reload(); }}, 1500);"
                         f"</script></body></html>").encode()
                return self.env(403, corpo, "text/html; charset=utf-8")
            return self.env(200, pagina(cen, cid).encode(), "text/html; charset=utf-8")
        if resto == "/api/token":
            tok = secrets.token_hex(8)
            with LOCK:
                st["tokens"][tok] = time.time() + 120
            return self.env(200, json.dumps({"token": tok}).encode(), "application/json")
        if resto == "/api/play":
            if self.command != "POST":
                return self.env(405, b"use POST")
            e = int(time.time()) + 15
            return self.env(200, json.dumps({"url": f"/{cid}/media/v.mp4?expires={e}&sig={e * 7 % 9973}"}).encode(), "application/json")
        if resto == "/seg/dados.bin":
            dados = bytes(b ^ 0x5A for b in fmp4(st["cor"]).read_bytes())
            return self.env(200, dados, "application/octet-stream")
        if resto == "/media/v.mp4":
            q = parse_qs(p.query)
            if cen in ("token_header", "token_uso_unico"):
                tok = self.headers.get("X-Token", "")
                with LOCK:
                    val = st["tokens"].get(tok, 0)
                    if cen == "token_uso_unico" and val:
                        st["tokens"].pop(tok, None)
                if val < time.time():
                    return self.env(403, b"token")
            if cen == "cookie_js" and "acesso=" not in ck:
                return self.env(403, b"cookie")
            if cen == "anti_robo" and f"desafio=ok{cid}" not in ck:
                return self.env(403, b"robo")
            if cen in ("assinado_curto", "manifesto_post"):
                try:
                    e = int(q.get("expires", ["0"])[0])
                    ok = int(q.get("sig", ["-1"])[0]) == e * 7 % 9973 and e >= time.time()
                except ValueError:
                    ok = False
                if not ok:
                    return self.env(403, b"assinatura vencida")
            if cen == "referer_origem":
                ref = self.headers.get("Referer", "")
                ori = self.headers.get("Origin", "")
                if f"/{cid}" not in ref or not ori.startswith("http://127.0.0."):
                    return self.env(403, b"referer/origin")
            corpo = MIDIAS.joinpath(f"{st['cor']}.mp4").read_bytes()
            h, pt = self.server.server_address
            return self.env(200, corpo, "video/mp4", {"Access-Control-Allow-Origin": f"http://{h}:{pt}"})
        return self.env(404, b"nao")


def rodar(repeticoes: int = 2):
    gerar_tudo()
    shutil.rmtree(SAIDA / "dados_dificeis", ignore_errors=True)
    destino = SAIDA / "baixados_dificeis"
    shutil.rmtree(destino, ignore_errors=True)
    from baixador.pipeline import Motor, Tarefa
    cfg = {"pasta_destino": str(destino), "usar_tor": False, "proxies": "", "usar_navegador": True, "perguntar_na_duvida": False,
           "mp4_compativel": True, "cookies_navegador": "nenhum", "_q": 720, "simultaneos": 1}
    motor = Motor(cfg)
    # um "site" por proteção: cada cenário num endereço próprio (127.0.0.2, .3 ...), para o aprendizado por site valer
    servidores = {}
    for i, cen in enumerate(CENARIOS):
        s = ThreadingHTTPServer((f"127.0.0.{2 + i}", 0), H)
        s.daemon_threads = True
        threading.Thread(target=s.serve_forever, daemon=True).start()
        servidores[cen] = s
    resultados = []
    r = random.Random(3)
    for rep in range(repeticoes):
        for cen in CENARIOS:
            cid = f"c{rep}{CENARIOS.index(cen):02d}{secrets.token_hex(2)}"
            cor = r.choice(list(CORES)[:4])
            fmp4(cor)
            with LOCK:
                ESTADO[cid] = {"cen": cen, "cor": cor, "tokens": {}}
            host, porta = servidores[cen].server_address
            t = Tarefa(url=f"http://{host}:{porta}/{cid}/watch")
            timer = threading.Timer(240, t.cancel.set)
            timer.start()
            ini = time.time()
            motor.processar(t)
            timer.cancel()
            cor_b = qual_cor(cor_media(t.arquivo)) if t.arquivo and os.path.exists(t.arquivo) else None
            if ESPERADO[cen] == "drm":
                res = "ok" if t.estado == "erro" and t.categoria_erro == "drm" else f"falhou ({t.estado}/{t.categoria_erro})"
            else:
                res = "ok" if t.estado == "pronto" and cor_b == cor else f"falhou ({t.estado}/{t.categoria_erro}/{cor_b})"
            linha = {"repeticao": rep + 1, "cenario": cen, "resultado": res, "tecnica": t.tecnica, "caminho": t.caminho,
                     "obstaculos": list(t.obstaculos), "tentou": t.tecnicas_tentadas, "segundos": round(time.time() - ini, 1),
                     "erro": t.erro[:240], "log": t.logs[-12:]}
            resultados.append(linha)
            print(f"{rep + 1} {cen:16s} {res:28s} técnica={t.tecnica or '-':16s} obstáculos={','.join(t.obstaculos) or '-'} "
                  f"({linha['segundos']} s)", flush=True)
            if t.arquivo and os.path.exists(t.arquivo):
                os.remove(t.arquivo)
    for s in servidores.values():
        s.shutdown()
    resumo = Counter(x["cenario"] for x in resultados if x["resultado"] == "ok")
    tot = Counter(x["cenario"] for x in resultados)
    saida = {"acertos": sum(resumo.values()), "total": len(resultados), "por_cenario": {c: f"{resumo[c]}/{tot[c]}" for c in CENARIOS},
             "resultados": resultados}
    (SAIDA / "sites_dificeis.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in saida.items() if k != "resultados"}, ensure_ascii=False, indent=1))
    shutil.rmtree(destino, ignore_errors=True)
    return saida


if __name__ == "__main__":
    rodar(int(sys.argv[1]) if len(sys.argv) > 1 else 2)
