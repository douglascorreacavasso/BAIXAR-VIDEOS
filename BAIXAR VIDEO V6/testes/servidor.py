# -*- coding: utf-8 -*-
"""Servidor de sites falsos em 127.0.0.1 (só nesta máquina) para a bateria de ponta a ponta.

Cada cenário é uma página do gerador com todos os endereços trocados para  http://127.0.0.1:PORTA/c<ID>/<host>/...
O vídeo PRINCIPAL é servido numa cor do grupo A; qualquer isca, numa cor do grupo B (assim dá para saber, pela
imagem, se o programa baixou o vídeo certo). Cada cenário também liga "manias" de servidor de verdade:
  ref     exige Referer da página (403 sem)          ck      exige o cookie que a página entrega
  norange ignora Range (sempre 200 inteiro)          drop    derruba a conexão no meio na 1ª vez
  slow    entrega devagar                            chunked sem Content-Length
  redir   2 redirecionamentos até o arquivo          octet   Content-Type application/octet-stream
  r429    1ª resposta 429 Retry-After                r503    1ª resposta 503
  gzip    página com gzip                            ua      recusa User-Agent de robô (python-requests)
  seg404  um pedaço do HLS dá 404 na 1ª vez          mentira o .mp4 é na verdade webm/mkv/flv/ts/AV1/HEVC
"""
from __future__ import annotations

import gzip
import hashlib
import mimetypes
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

from midias import PASTA as MIDIAS

GRUPO_A = ["vermelho", "verde", "azul", "amarelo"]
GRUPO_B = ["roxo", "ciano", "laranja", "rosa"]
FMT_PROG = ["mp4", "mp4", "mp4", "webm", "mov", "mkv", "flv", "ts", "av1.mp4", "hevc.mp4", "mudo.mp4", "360.mp4"]
FMT_HLS = ["hls", "hls", "hlsaes", "hlsfmp4"]
MANIAS = ["ref", "ck", "norange", "drop", "slow", "chunked", "redir", "octet", "r429", "r503", "gzip", "ua", "seg404"]
RE_HOST = re.compile(r"(https?:)?((?:\\?/|\\u002F){2})([a-z0-9-]+(?:\.[a-z0-9-]+)*\.test)", re.I)


class Cenario:
    def __init__(self, cid: int, pagina, r: random.Random, porta: int):
        self.id = cid
        self.pg = pagina
        self.cor = r.choice(GRUPO_A)
        self.fmt_prog = r.choice(FMT_PROG)
        self.fmt_hls = r.choice(FMT_HLS)
        self.manias = set(m for m in MANIAS if r.random() < 0.09)
        self.porta = porta
        self.vezes: dict[str, int] = {}
        self.lock = threading.Lock()
        self.prefixos: list[tuple[str, Path]] = []   # (caminho da pasta HLS/DASH, pasta da mídia)
        self.pedidos: list[str] = []
        base = f"http://127.0.0.1:{porta}/c{cid}/"
        self.html = self._trocar(pagina.html, base)
        self.url = self._trocar_url(pagina.url, base)
        self.verdade = {self._trocar_url(u, base) for u in pagina.verdade}
        self.caminhos_verdade = {unquote(urlparse(u).path) for u in self.verdade}

    @staticmethod
    def _trocar_url(u: str, base: str) -> str:
        p = urlparse(u)
        return base + p.netloc + (p.path or "/") + (("?" + p.query) if p.query else "")

    def _trocar(self, html: str, base: str) -> str:
        def sub(m):
            barras = m.group(2)
            esc = "\\/" if "\\/" in barras else ("\\u002F" if "u002F" in barras else "/")
            b = base.replace("/", esc)
            return b + m.group(3)
        html = RE_HOST.sub(sub, html)
        # %2F codificado (player.php?file=...)
        html = re.sub(r"https?%3A%2F%2F([a-z0-9.-]+\.test)", lambda m: base.replace(":", "%3A").replace("/", "%2F") + m.group(1), html, flags=re.I)
        # base64 de atob()
        import base64

        def b64(m):
            try:
                dec = base64.b64decode(m.group(1)).decode()
            except Exception:
                return m.group(0)
            novo = RE_HOST.sub(lambda mm: base + mm.group(3), dec)
            return f'atob("{base64.b64encode(novo.encode()).decode()}")'
        html = re.sub(r'atob\("([A-Za-z0-9+/=]+)"\)', b64, html)
        # host solto em JS (família que monta o endereço na hora)
        html = re.sub(r'(?<=\[")([a-z0-9-]+(?:\.[a-z0-9-]+)*\.test)(?=")', lambda m: f"127.0.0.1:{self.porta}/c{self.id}/" + m.group(1), html)
        return html

    def contar(self, chave: str) -> int:
        with self.lock:
            self.vezes[chave] = self.vezes.get(chave, 0) + 1
            return self.vezes[chave]


class Registro:
    def __init__(self):
        self.cenarios: dict[int, Cenario] = {}
        self.lock = threading.Lock()

    def add(self, c: Cenario):
        with self.lock:
            self.cenarios[c.id] = c

    def get(self, cid: int) -> Cenario | None:
        with self.lock:
            return self.cenarios.get(cid)

    def tirar(self, cid: int):
        with self.lock:
            self.cenarios.pop(cid, None)


REG = Registro()


def _cor_isca(chave: str) -> str:
    return GRUPO_B[int(hashlib.md5(chave.encode()).hexdigest(), 16) % len(GRUPO_B)]


class Manipulador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _enviar(self, codigo, corpo=b"", tipo="text/plain", extra=None, sem_tamanho=False):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if not sem_tamanho:
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(corpo)
        else:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if self.command != "HEAD":
                for i in range(0, len(corpo), 16384):
                    pedaco = corpo[i:i + 16384]
                    self.wfile.write(f"{len(pedaco):x}\r\n".encode() + pedaco + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._get()
        except (ConnectionError, OSError):
            pass

    def _get(self):
        p = urlparse(self.path)
        caminho = unquote(p.path)
        m = re.match(r"/c(\d+)/([^/]+)(/.*)?$", caminho)
        if not m:
            return self._enviar(404, b"nada")
        c = REG.get(int(m.group(1)))
        if c is None:
            return self._enviar(404, b"cenario acabou")
        c.pedidos.append(caminho)
        ua = self.headers.get("User-Agent", "")
        pagina_path = urlparse(c.url).path
        # ---------------- página
        if caminho == pagina_path or caminho.rstrip("/") == pagina_path.rstrip("/"):
            if "ua" in c.manias and re.search(r"python-requests|curl/|wget", ua, re.I):
                return self._enviar(403, b"robo nao")
            corpo = c.html.encode("utf-8")
            extra = {"Set-Cookie": f"sess=ok{c.id}; Path=/"}
            if "gzip" in c.manias and "gzip" in self.headers.get("Accept-Encoding", ""):
                corpo = gzip.compress(corpo)
                extra["Content-Encoding"] = "gzip"
            return self._enviar(200, corpo, "text/html; charset=utf-8", extra)
        # ---------------- manias que valem para toda mídia
        if "ref" in c.manias and f"/c{c.id}/" not in self.headers.get("Referer", ""):
            return self._enviar(403, b"sem referer")
        if "ck" in c.manias and f"sess=ok{c.id}" not in self.headers.get("Cookie", ""):
            return self._enviar(403, b"sem cookie")
        if "redir" in c.manias and "r=2" not in (p.query or ""):
            n = 1 if "r=1" in (p.query or "") else 0
            q = re.sub(r"(^|&)r=\d", "", p.query or "")
            q = (q + "&" if q else "") + f"r={n + 1}"
            return self._enviar(302, b"", extra={"Location": p.path + "?" + q.lstrip("&")})
        for mania, cod in (("r429", 429), ("r503", 503)):
            if mania in c.manias and c.contar(mania + caminho) == 1:
                return self._enviar(cod, b"tente depois", extra={"Retry-After": "1"})
        # ---------------- HLS / DASH (pasta registrada)
        for pref, pasta in c.prefixos:
            if caminho.startswith(pref):
                return self._arquivo_pasta(c, pasta, caminho[len(pref):])
        low = caminho.lower()
        e_verdade = caminho in c.caminhos_verdade
        if low.endswith(".m3u8") or ".m3u8" in low:
            cor = c.cor if e_verdade else _cor_isca(caminho)
            pasta = MIDIAS / f"{c.fmt_hls if e_verdade else 'hls'}_{cor}"
            pref = caminho.rsplit("/", 1)[0] + "/"
            c.prefixos.append((pref, pasta))
            return self._arquivo_pasta(c, pasta, "master.m3u8")
        if low.endswith(".mpd"):
            cor = c.cor if e_verdade else _cor_isca(caminho)
            pasta = MIDIAS / f"dash_{cor}"
            pref = caminho.rsplit("/", 1)[0] + "/"
            c.prefixos.append((pref, pasta))
            return self._arquivo_pasta(c, pasta, "manifest.mpd")
        if low.endswith((".m4a", ".mp3", ".aac")):
            return self._arquivo(c, MIDIAS / f"{_cor_isca(caminho)}.m4a", "audio/mp4")
        if re.search(r"\.(mp4|webm|mov|mkv|flv|m4v)$", low):
            if e_verdade:
                arq = MIDIAS / f"{c.cor}.{c.fmt_prog}"
            else:
                arq = MIDIAS / f"{_cor_isca(caminho)}.mp4"
            return self._arquivo(c, arq, None)
        if low.endswith((".jpg", ".png", ".vtt", ".xml", ".swf", ".js", ".css")):
            return self._enviar(200, b"x", mimetypes.guess_type(low)[0] or "application/octet-stream")
        return self._enviar(404, b"nao existe")

    def _arquivo_pasta(self, c: Cenario, pasta: Path, resto: str):
        resto = resto.split("?")[0]
        if resto.endswith(".m3u8") and not (pasta / resto).exists():
            resto = "master.m3u8"
        if resto.endswith(".mpd") and not (pasta / resto).exists():
            resto = "manifest.mpd"
        alvo = (pasta / resto).resolve()
        if pasta.resolve() not in alvo.parents or not alvo.exists():
            return self._enviar(404, b"sem pedaco")
        if "seg404" in c.manias and resto.endswith((".ts", ".m4s")) and c.contar("seg" + str(alvo)) == 1 and "1" in resto:
            return self._enviar(404, b"pedaco sumiu")
        tipo = {"m3u8": "application/vnd.apple.mpegurl", "mpd": "application/dash+xml", "ts": "video/mp2t",
                "m4s": "video/iso.segment", "mp4": "video/mp4", "key": "application/octet-stream"}.get(alvo.suffix.lstrip("."), "application/octet-stream")
        return self._arquivo(c, alvo, tipo, pequeno=True)

    def _arquivo(self, c: Cenario, arq: Path, tipo: str | None, pequeno=False):
        if not arq.exists():
            return self._enviar(404, b"sem midia")
        dados = arq.read_bytes()
        if tipo is None:
            tipo = "application/octet-stream" if "octet" in c.manias else (mimetypes.guess_type(arq.name)[0] or "video/mp4")
            if arq.name.endswith(".mp4"):
                tipo = "application/octet-stream" if "octet" in c.manias else "video/mp4"
        total = len(dados)
        ini, fim = 0, total - 1
        rng = self.headers.get("Range")
        parcial = False
        if rng and "norange" not in c.manias:
            mm = re.match(r"bytes=(\d*)-(\d*)", rng)
            if mm:
                if mm.group(1):
                    ini = int(mm.group(1))
                    if mm.group(2):
                        fim = min(int(mm.group(2)), total - 1)
                elif mm.group(2):
                    ini = max(0, total - int(mm.group(2)))
                if ini >= total:
                    return self._enviar(416, b"", extra={"Content-Range": f"bytes */{total}"})
                parcial = True
        corpo = dados[ini:fim + 1]
        extra = {"Accept-Ranges": "none" if "norange" in c.manias else "bytes"}
        if parcial:
            extra["Content-Range"] = f"bytes {ini}-{fim}/{total}"
        # derruba a conexão no meio (1ª vez, só arquivo inteiro)
        if "drop" in c.manias and not pequeno and c.contar("drop" + str(arq)) == 1 and len(corpo) > 20000:
            self.send_response(206 if parcial else 200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo[: len(corpo) // 3])
            self.wfile.flush()
            self.close_connection = True
            try:
                import socket
                self.connection.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            return
        if "slow" in c.manias and not pequeno:
            self.send_response(206 if parcial else 200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            for k, v in extra.items():
                self.send_header(k, v)
            self.end_headers()
            for i in range(0, len(corpo), 8192):
                self.wfile.write(corpo[i:i + 8192])
                time.sleep(0.02)
            return
        self._enviar(206 if parcial else 200, corpo, tipo, extra, sem_tamanho=("chunked" in c.manias and not pequeno))


class _SrvQuieto(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # conexões cortadas de propósito (mania "drop") não devem sujar a saída


class Servidor:
    def __init__(self):
        self.srv = _SrvQuieto(("127.0.0.1", 0), Manipulador)
        self.srv.daemon_threads = True
        self.porta = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self):
        self.th.start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()
