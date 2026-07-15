# -*- coding: utf-8 -*-
"""
BAIXADOR DE VIDEO -> MP4  (v5)

O QUE MUDOU (as suas ideias, feitas de forma REAL):

1) CONEXAO PROPRIA: o programa nunca usou a VPN do navegador (extensao de VPN
   so afeta o navegador). Ele tem DNS proprio (DoH). Com a VPN do navegador
   DESLIGADA, a conexao direta ja deve funcionar.

2) ESCADA DE ROTAS (o "muda e tenta de novo" que voce quer):
   rota 1 = conexao direta
   rota 2 = cada proxy da sua lista (um por linha)
   rota 3 = Tor, trocando de PAIS 3x (se o Tor estiver rodando)
   Ele so troca de rota quando os motores esgotam na rota atual.

3) TRES MOTORES DE PLANTAO (sua ideia do "outro baixador de reserva"):
   motor 1 = yt-dlp (com suas 7 estrategias de formato)
   motor 2 = streamlink (bom em HLS onde o yt-dlp falha)
   motor 3 = ffmpeg direto (para links .m3u8/.mpd/.mp4 diretos)
   Se um falha, entra o proximo. So depois dos 3 falharem ele troca de rota.

4) FAVORITAR QUALIDADE de volta: botao "favoritar como padrao".
   + feedback de qualidade por download + memoria que aprende por site.

HONESTIDADE: nenhum programa "cria VPN sozinho". Tor e a unica forma real e
gratis de trocar de pais -- por isso ele e a rota 3. Se ate o Tor/proxies
falharem, o bloqueio e mais forte que qualquer software resolve: so VPN paga.
"""

import os
import sys
import json
import time
import socket
import threading
import subprocess
from urllib.parse import urlparse


def _ensure(pkg, imp=None):
    imp = imp or pkg
    try:
        __import__(imp)
    except ImportError:
        print(f"[SETUP] instalando {pkg} ...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--upgrade", pkg])


_ensure("yt-dlp", "yt_dlp")
_ensure("requests")
try:
    import socks  # noqa
except ImportError:
    subprocess.call([sys.executable, "-m", "pip", "install", "--upgrade",
                     "PySocks", "requests[socks]"])

import requests  # noqa: E402
import yt_dlp  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk, scrolledtext, messagebox  # noqa: E402


PASTA = os.getcwd()
DB = os.path.join(PASTA, "estrategias_memoria.json")
CFG = os.path.join(PASTA, "config.json")

TOR_SOCKS = "socks5://127.0.0.1:9050"
TOR_CTRL = ("127.0.0.1", 9051)


# ===========================================================================
# 1) DNS proprio (DoH) -- independente do sistema
# ===========================================================================
DOH = ["https://1.1.1.1/dns-query", "https://1.0.0.1/dns-query",
       "https://8.8.8.8/resolve", "https://8.8.4.4/resolve",
       "https://9.9.9.9/dns-query", "https://149.112.112.112/dns-query"]

_dns_cache = {}
_dns_lock = threading.Lock()
_orig_gai = socket.getaddrinfo


def resolver_doh(host, proxy=None, log=None):
    with _dns_lock:
        if host in _dns_cache:
            return _dns_cache[host]
    px = {"http": proxy, "https": proxy} if proxy else None
    for url in DOH:
        try:
            r = requests.get(url, params={"name": host, "type": "A"},
                             headers={"accept": "application/dns-json"},
                             timeout=6, proxies=px)
            for ans in r.json().get("Answer", []):
                if ans.get("type") == 1:
                    ip = ans["data"]
                    with _dns_lock:
                        _dns_cache[host] = ip
                    if log:
                        log(f"[DoH] {host} -> {ip}")
                    return ip
        except Exception as e:
            if log:
                log(f"[DoH] {url.split('/')[2]} falhou: {str(e)[:40]}")
    return None


def _gai_patched(host, port, *a, **k):
    try:
        return _orig_gai(host, port, *a, **k)
    except socket.gaierror:
        ip = resolver_doh(host)
        if ip:
            return _orig_gai(ip, port, *a, **k)
        raise


socket.getaddrinfo = _gai_patched


def tcp_ok(ip, port=443, timeout=5):
    try:
        socket.create_connection((ip, port), timeout=timeout).close()
        return True
    except Exception:
        return False


# ===========================================================================
# 2) Tor: deteccao + troca de circuito (pais)
# ===========================================================================
def tor_ligado():
    return tcp_ok("127.0.0.1", 9050, timeout=3)


def tor_novo_circuito(log=None):
    """Pede um novo circuito (novo pais de saida, best-effort)."""
    try:
        s = socket.create_connection(TOR_CTRL, timeout=5)
        s.sendall(b'AUTHENTICATE ""\r\n')
        if b"250" not in s.recv(256):
            s.close()
            return False
        s.sendall(b"SIGNAL NEWNYM\r\n")
        ok = b"250" in s.recv(256)
        s.close()
        if ok and log:
            log("[Tor] novo circuito solicitado (troca de pais)")
        time.sleep(4)  # deixa o circuito assentar
        return ok
    except Exception as e:
        if log:
            log(f"[Tor] nao consegui trocar circuito: {str(e)[:40]}")
        return False


def ip_publico(proxy=None):
    px = {"http": proxy, "https": proxy} if proxy else None
    for u in ["https://api.ipify.org?format=json",
              "https://ifconfig.co/json"]:
        try:
            r = requests.get(u, timeout=8, proxies=px)
            d = r.json()
            return d.get("ip") or d.get("query"), d.get("country", "?")
        except Exception:
            continue
    return None, "?"


# ===========================================================================
# 3) Config + memoria
# ===========================================================================
def _load(p, d):
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return d


def _save(p, o):
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


CONFIG = _load(CFG, {"qualidade_padrao": "Melhor disponivel",
                     "proxies": "", "usar_tor": True})
MEM = _load(DB, {})
MEM_LOCK = threading.Lock()


def dominio(url):
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "?"


def registrar(dom, sid, ok=0, fail=0):
    with MEM_LOCK:
        r = MEM.setdefault(dom, {}).setdefault(sid, {"ok": 0, "fail": 0})
        r["ok"] += ok
        r["fail"] += fail
        _save(DB, MEM)


def score(rec):
    return (rec.get("ok", 0) + 1.0) / (rec.get("ok", 0) + rec.get("fail", 0) + 2.0)


POOL = [
    {"id": "mp4_prioritario",
     "fmt": "bv*[ext=mp4][height<=?{Q}]+ba[ext=m4a]/b[ext=mp4][height<=?{Q}]/"
            "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
    {"id": "melhor_qualquer", "fmt": "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
    {"id": "unico_melhor", "fmt": "b[height<=?{Q}]/b"},
    {"id": "hls_nativo", "fmt": "bv*[height<=?{Q}]+ba/b",
     "extra": {"hls_prefer_native": True}},
    {"id": "hls_ffmpeg", "fmt": "bv*[height<=?{Q}]+ba/b",
     "extra": {"hls_prefer_native": False}},
    {"id": "generico_best", "fmt": "best"},
    {"id": "ultimo_recurso", "fmt": "worst/b"},
]


def montar(dom, q):
    base = [dict(s) for s in POOL]
    for s in base:
        s["fmt"] = s["fmt"].replace("{Q}", str(q))
    with MEM_LOCK:
        m = MEM.get(dom, {})
    base.sort(key=lambda s: score(m.get(s["id"], {})), reverse=True)
    return base


FATAIS = ["getaddrinfo failed", "failed to resolve", "name or service not known",
          "temporary failure in name resolution", "connection refused",
          "network is unreachable", "timed out", "certificate verify failed",
          "connection reset", "unable to connect to proxy", "tunnel connection"]


def erro_fatal(m):
    m = m.lower()
    return any(f in m for f in FATAIS)


# ===========================================================================
# 4) Qualidade / conversao
# ===========================================================================
def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tem(prog):
    try:
        _run([prog, "-version"], timeout=10)
        return True
    except Exception:
        try:
            _run([prog, "--version"], timeout=10)
            return True
        except Exception:
            return False


def altura_video(path):
    try:
        out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=height", "-of", "csv=p=0", path],
                   timeout=30)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return None


def para_mp4(path, log):
    if path.lower().endswith(".mp4"):
        return path
    saida = os.path.splitext(path)[0] + ".mp4"
    log("convertendo p/ mp4 (remux)...")
    r = _run(["ffmpeg", "-y", "-i", path, "-c", "copy",
              "-movflags", "+faststart", saida])
    if r.returncode == 0 and os.path.exists(saida) and os.path.getsize(saida) > 0:
        try:
            os.remove(path)
        except Exception:
            pass
        return saida
    log("remux falhou -> re-encodando H.264")
    r = _run(["ffmpeg", "-y", "-i", path, "-c:v", "libx264", "-crf", "20",
              "-preset", "medium", "-c:a", "aac", "-b:a", "192k",
              "-movflags", "+faststart", saida])
    if r.returncode == 0 and os.path.exists(saida) and os.path.getsize(saida) > 0:
        try:
            os.remove(path)
        except Exception:
            pass
        return saida
    return path


# ===========================================================================
# 5) OS TRES MOTORES
# ===========================================================================
def motor_ytdlp(url, q, proxy, log):
    dom = dominio(url)
    for i, s in enumerate(montar(dom, q), 1):
        log(f"yt-dlp [{i}/7] {s['id']}")

        def hook(d, sid=s["id"]):
            if d.get("status") == "downloading":
                log(f"{sid} {(d.get('_percent_str') or '').strip()} "
                    f"{(d.get('_speed_str') or '').strip()}")

        opts = {
            "format": s["fmt"], "merge_output_format": "mp4",
            "outtmpl": os.path.join(PASTA, "%(title).150B.%(ext)s"),
            "restrictfilenames": True, "noplaylist": True,
            "quiet": True, "no_warnings": True, "retries": 2,
            "fragment_retries": 5, "concurrent_fragment_downloads": 4,
            "geo_bypass": True, "progress_hooks": [hook],
            "postprocessors": [{"key": "FFmpegVideoRemuxer",
                                "preferedformat": "mp4"}],
        }
        if proxy:
            opts["proxy"] = proxy
        opts.update(s.get("extra", {}))
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fp = (info.get("requested_downloads") or [{}])[0].get("filepath") \
                    or ydl.prepare_filename(info)
            if fp and os.path.exists(fp):
                if not fp.lower().endswith(".mp4"):
                    fp = para_mp4(fp, log)
                registrar(dom, s["id"], ok=1)
                return {"ok": True, "arquivo": fp, "altura": altura_video(fp),
                        "strat_id": s["id"]}
        except Exception as e:
            msg = str(e).split("\n")[0]
            if erro_fatal(msg):
                return {"ok": False, "fatal": True, "erro": msg}
            registrar(dom, s["id"], fail=1)
            log(f"{s['id']} x: {msg[:55]}")
    return {"ok": False, "erro": "yt-dlp esgotou estrategias"}


def motor_streamlink(url, q, proxy, log):
    if not tem("streamlink"):
        log("streamlink nao instalado (pulei)")
        return {"ok": False, "erro": "streamlink ausente"}
    log("streamlink tentando...")
    saida = os.path.join(PASTA, f"streamlink_{int(time.time())}.mp4")
    cmd = ["streamlink", "--force", "-o", saida]
    if proxy:
        cmd += ["--http-proxy", proxy]
    cmd += [url, "best"]
    try:
        r = _run(cmd, timeout=1800)
        if os.path.exists(saida) and os.path.getsize(saida) > 1000:
            saida = para_mp4(saida, log)
            return {"ok": True, "arquivo": saida, "altura": altura_video(saida),
                    "strat_id": "streamlink"}
        m = (r.stderr or r.stdout or "").split("\n")[0]
        if erro_fatal(m):
            return {"ok": False, "fatal": True, "erro": m}
        return {"ok": False, "erro": "streamlink nao gerou arquivo"}
    except Exception as e:
        return {"ok": False, "erro": f"streamlink: {str(e)[:60]}"}


def motor_ffmpeg(url, q, proxy, log):
    low = url.lower()
    if not any(x in low for x in [".m3u8", ".mpd", ".mp4", "manifest"]):
        return {"ok": False, "erro": "url nao e midia direta (ffmpeg pulado)"}
    log("ffmpeg direto tentando...")
    saida = os.path.join(PASTA, f"ffmpeg_{int(time.time())}.mp4")
    env = dict(os.environ)
    if proxy:
        env["http_proxy"] = env["https_proxy"] = proxy
    cmd = ["ffmpeg", "-y"]
    if proxy and proxy.startswith("http"):
        cmd += ["-http_proxy", proxy]
    cmd += ["-i", url, "-c", "copy", "-movflags", "+faststart", saida]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=1800, env=env)
        if os.path.exists(saida) and os.path.getsize(saida) > 1000:
            return {"ok": True, "arquivo": saida, "altura": altura_video(saida),
                    "strat_id": "ffmpeg"}
        m = (r.stderr or "").split("\n")[-2] if r.stderr else "sem saida"
        return {"ok": False, "erro": f"ffmpeg: {m[:60]}"}
    except Exception as e:
        return {"ok": False, "erro": f"ffmpeg: {str(e)[:60]}"}


MOTORES = [("yt-dlp", motor_ytdlp), ("streamlink", motor_streamlink),
           ("ffmpeg", motor_ffmpeg)]


# ===========================================================================
# 6) ESCADA DE ROTAS
# ===========================================================================
def montar_rotas(proxies_txt, usar_tor):
    rotas = [{"nome": "conexao direta", "proxy": None, "tor": False}]
    for p in [x.strip() for x in proxies_txt.splitlines() if x.strip()]:
        rotas.append({"nome": f"proxy {p[:30]}", "proxy": p, "tor": False})
    if usar_tor and tor_ligado():
        for i in range(3):  # 3 paises
            rotas.append({"nome": f"Tor pais #{i+1}", "proxy": TOR_SOCKS,
                          "tor": True})
    return rotas


def baixar(url, q, proxies_txt, usar_tor, log):
    rotas = montar_rotas(proxies_txt, usar_tor)
    log(f"=== {len(rotas)} rota(s) disponiveis ===")

    for ri, rota in enumerate(rotas, 1):
        if rota["tor"]:
            tor_novo_circuito(log)
            ip, pais = ip_publico(rota["proxy"])
            log(f"[rota {ri}] {rota['nome']}  saida: {ip} ({pais})")
        else:
            log(f"[rota {ri}] {rota['nome']}")

        rota_morta = False
        for nome, motor in MOTORES:
            res = motor(url, q, rota["proxy"], log)
            if res.get("ok"):
                res["rota"] = rota["nome"]
                return res
            if res.get("fatal"):
                log(f"  {nome}: bloqueio de rede nesta rota -> troco de rota")
                rota_morta = True
                break  # os outros motores morrem igual nesta rota
            log(f"  {nome} falhou: {res.get('erro', '')[:50]}")
        if not rota_morta:
            log(f"  todos os motores falharam na rota {ri}")

    return {"ok": False, "erro": "acabaram TODAS as rotas e motores. "
            "Provavel bloqueio forte -> use uma VPN paga."}


# ===========================================================================
# 7) Interface
# ===========================================================================
Q_OPCOES = {"Melhor disponivel": 4320, "2160p (4K)": 2160, "1440p": 1440,
            "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


class Card:
    def __init__(self, parent, i, url, app):
        self.app, self.url, self.strat_id = app, url, None
        self.dom = dominio(url)
        self.frame = tk.Frame(parent, bg="#fff", bd=1, relief="solid")
        self.frame.pack(fill="x", padx=6, pady=4)
        top = tk.Frame(self.frame, bg="#fff")
        top.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(top, text=f"#{i}", font=("Segoe UI", 9, "bold"),
                 bg="#fff", fg="#6d28d9").pack(side="left")
        u = url if len(url) < 64 else url[:61] + "..."
        tk.Label(top, text=u, font=("Segoe UI", 9), bg="#fff",
                 fg="#111827").pack(side="left", padx=6)
        mid = tk.Frame(self.frame, bg="#fff")
        mid.pack(fill="x", padx=8)
        self.status = tk.StringVar(value="na fila...")
        tk.Label(mid, textvariable=self.status, font=("Segoe UI", 8),
                 bg="#fff", fg="#374151", anchor="w", justify="left",
                 wraplength=500).pack(side="left")
        self.qual = tk.StringVar(value="")
        tk.Label(mid, textvariable=self.qual, font=("Segoe UI", 9, "bold"),
                 bg="#fff", fg="#059669").pack(side="right")
        self.bot = tk.Frame(self.frame, bg="#fff")
        self.lbl = tk.Label(self.bot, text="qualidade do arquivo:",
                            font=("Segoe UI", 8), bg="#fff", fg="#6b7280")
        self.b1 = tk.Button(self.bot, text="boa", font=("Segoe UI", 8),
                            bg="#dcfce7", command=self.ok)
        self.b2 = tk.Button(self.bot, text="ruim/errada", font=("Segoe UI", 8),
                            bg="#fee2e2", command=self.ruim)
        self.b3 = tk.Button(self.bot, text="abrir pasta", font=("Segoe UI", 8),
                            command=lambda: os.startfile(PASTA))

    def log(self, t):
        self.app.ui(lambda: self.status.set(t))

    def fim(self, res):
        def _a():
            if res.get("ok"):
                h = res.get("altura")
                self.qual.set(f"{h}p" if h else "OK")
                self.status.set(f"baixado via {res.get('strat_id')} "
                                f"({res.get('rota', '')})")
                self.strat_id = res.get("strat_id")
                self.bot.pack(fill="x", padx=8, pady=(0, 6))
                self.lbl.pack(side="left", padx=(0, 6))
                self.b1.pack(side="left", padx=(0, 4))
                self.b2.pack(side="left")
                self.b3.pack(side="right")
            else:
                self.qual.set("ERRO")
                self.status.set(res.get("erro", "falhou"))
        self.app.ui(_a)

    def ok(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, ok=3)
        self.status.set("qualidade validada (estrategia reforcada)")
        self.b1.config(state="disabled")
        self.b2.config(state="disabled")

    def ruim(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, fail=3)
        self.status.set("marcado ruim (estrategia rebaixada)")
        self.b1.config(state="disabled")
        self.b2.config(state="disabled")


class App:
    def __init__(self, root):
        self.root = root
        self.cards = []
        self.stop = False
        self.rodando = False
        root.title("Baixador de Video -> MP4  (v5)")
        root.geometry("820x820")
        root.configure(bg="#f5f3ff")

        tk.Label(root, text="Baixador de Video -> MP4  (v5)",
                 font=("Segoe UI", 14, "bold"), bg="#f5f3ff",
                 fg="#5b21b6").pack(pady=(10, 0))
        tk.Label(root, text=f"salvando em: {PASTA}", font=("Segoe UI", 8),
                 bg="#f5f3ff", fg="#6b7280").pack()
        self.diag = tk.StringVar(value="checando ambiente...")
        tk.Label(root, textvariable=self.diag, font=("Segoe UI", 8),
                 bg="#f5f3ff", fg="#7c2d12").pack()
        threading.Thread(target=self._checar, daemon=True).start()

        # rotas
        rf = tk.LabelFrame(root, text=" Rotas de conexao (para furar bloqueio) ",
                           bg="#f5f3ff", font=("Segoe UI", 8))
        rf.pack(fill="x", padx=14, pady=6)
        self.tor_var = tk.BooleanVar(value=CONFIG.get("usar_tor", True))
        tk.Checkbutton(rf, text="usar Tor (troca de pais 3x se estiver rodando)",
                       variable=self.tor_var, bg="#f5f3ff",
                       font=("Segoe UI", 8)).pack(anchor="w")
        tk.Label(rf, text="Proxies (1 por linha, opcional):",
                 font=("Segoe UI", 8), bg="#f5f3ff").pack(anchor="w")
        self.proxies = scrolledtext.ScrolledText(rf, height=2,
                                                 font=("Consolas", 8))
        self.proxies.pack(fill="x", padx=4, pady=2)
        self.proxies.insert("1.0", CONFIG.get("proxies", ""))
        tk.Button(rf, text="Testar Rede / IP", bg="#0ea5e9", fg="white",
                  font=("Segoe UI", 8, "bold"),
                  command=self.testar).pack(side="left", padx=4, pady=2)
        tk.Label(rf, text="ex.: socks5://127.0.0.1:9050  |  "
                          "http://user:senha@ip:porta",
                 font=("Consolas", 7), bg="#f5f3ff", fg="#9ca3af").pack(
            side="left")

        # links
        top = tk.Frame(root, bg="#f5f3ff")
        top.pack(fill="x", padx=14, pady=(4, 2))
        tk.Label(top, text="Cole 1 link por linha:", font=("Segoe UI", 9),
                 bg="#f5f3ff").pack(anchor="w")
        self.txt = scrolledtext.ScrolledText(top, height=3,
                                             font=("Consolas", 9), wrap="none")
        self.txt.pack(fill="x", pady=2)

        ctl = tk.Frame(root, bg="#f5f3ff")
        ctl.pack(fill="x", padx=14)
        tk.Label(ctl, text="Qualidade:", font=("Segoe UI", 9),
                 bg="#f5f3ff").pack(side="left")
        padrao = CONFIG.get("qualidade_padrao", "Melhor disponivel")
        if padrao not in Q_OPCOES:
            padrao = "Melhor disponivel"
        self.q_var = tk.StringVar(value=padrao)
        ttk.Combobox(ctl, textvariable=self.q_var, values=list(Q_OPCOES),
                     state="readonly", width=15).pack(side="left", padx=6)
        tk.Button(ctl, text="★ favoritar como padrao", bg="#fde68a",
                  font=("Segoe UI", 8, "bold"),
                  command=self.favoritar).pack(side="left")
        tk.Button(ctl, text="Baixar Tudo", bg="#7c3aed", fg="white",
                  font=("Segoe UI", 10, "bold"),
                  command=self.iniciar).pack(side="left", padx=10)
        tk.Button(ctl, text="parar", bg="#ef4444", fg="white",
                  font=("Segoe UI", 9),
                  command=lambda: setattr(self, "stop", True)).pack(side="left")
        tk.Button(ctl, text="limpar", font=("Segoe UI", 9),
                  command=self.limpar).pack(side="right")

        tk.Label(root, text="Console:", font=("Segoe UI", 8), bg="#f5f3ff",
                 fg="#6b7280").pack(anchor="w", padx=14)
        self.console = scrolledtext.ScrolledText(root, height=8,
                                                font=("Consolas", 8),
                                                bg="#111827", fg="#a7f3d0")
        self.console.pack(fill="x", padx=14, pady=(0, 6))

        wrap = tk.Frame(root, bg="#e5e7eb", bd=1, relief="solid")
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        self.canvas = tk.Canvas(wrap, bg="#e5e7eb", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.lista = tk.Frame(self.canvas, bg="#e5e7eb")
        self.lista.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.lista, anchor="nw",
                                  width=770)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-e.delta / 120), "units"))

    def _checar(self):
        p = []
        p.append("Tor: LIGADO" if tor_ligado() else "Tor: desligado")
        p.append("FFmpeg: OK" if tem("ffmpeg") else "FFmpeg: FALTA")
        p.append("streamlink: OK" if tem("streamlink") else "streamlink: falta")
        self.ui(lambda: self.diag.set("  |  ".join(p)))

    def cout(self, t):
        def _a():
            self.console.insert("end", t + "\n")
            self.console.see("end")
        self.ui(_a)

    def ui(self, f):
        self.root.after(0, f)

    def testar(self):
        self.console.delete("1.0", "end")

        def _t():
            self.cout("=== TESTE DE REDE ===")
            self.cout("1.1.1.1:443 alcancavel? " +
                      ("SIM" if tcp_ok("1.1.1.1") else "NAO (sem net/firewall)"))
            ip, pais = ip_publico()
            self.cout(f"Seu IP direto: {ip} ({pais})")
            if self.tor_var.get() and tor_ligado():
                tor_novo_circuito(self.cout)
                tip, tp = ip_publico(TOR_SOCKS)
                self.cout(f"IP via Tor: {tip} ({tp})")
            else:
                self.cout("Tor: nao esta rodando (rota de pais indisponivel)")
            self.cout("=== fim ===")
        threading.Thread(target=_t, daemon=True).start()

    def favoritar(self):
        CONFIG["qualidade_padrao"] = self.q_var.get()
        _save(CFG, CONFIG)
        messagebox.showinfo("Favoritado",
                            f"Qualidade padrao: {self.q_var.get()}\n"
                            "Ja vem selecionada nas proximas vezes.")

    def _salvar_rotas(self):
        CONFIG["proxies"] = self.proxies.get("1.0", "end").strip()
        CONFIG["usar_tor"] = self.tor_var.get()
        _save(CFG, CONFIG)

    def limpar(self):
        if self.rodando:
            return
        for c in self.cards:
            c.frame.destroy()
        self.cards = []

    def iniciar(self):
        if self.rodando:
            return
        links = [l.strip() for l in self.txt.get("1.0", "end").splitlines()
                 if l.strip()]
        if not links:
            messagebox.showinfo("Ops", "Cole pelo menos 1 link.")
            return
        self._salvar_rotas()
        self.limpar()
        q = Q_OPCOES[self.q_var.get()]
        px = CONFIG.get("proxies", "")
        tor = self.tor_var.get()
        for i, u in enumerate(links, 1):
            self.cards.append(Card(self.lista, i, u, self))
        self.stop = False
        self.rodando = True
        threading.Thread(target=self._worker,
                         args=(list(self.cards), q, px, tor),
                         daemon=True).start()

    def _worker(self, cards, q, px, tor):
        for c in cards:
            if self.stop:
                c.log("cancelado")
                continue
            self.cout(f"--- {c.url[:55]}")
            res = baixar(c.url, q, px, tor,
                         lambda t: (self.cout("   " + t), c.log(t)))
            c.fim(res)
            self.cout("   >>> " + ("OK" if res.get("ok") else "FALHOU"))
        self.rodando = False


if __name__ == "__main__":
    print("[INFO] v5 - escada de rotas (direto/proxy/Tor) + 3 motores")
    r = tk.Tk()
    App(r)
    r.mainloop()
