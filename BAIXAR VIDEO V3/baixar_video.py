# -*- coding: utf-8 -*-
"""
BAIXADOR DE VIDEO -> MP4  (v3)

NOVIDADES DA V3:
1) CAMADA DNS (o bug real da v1/v2):
   O erro "getaddrinfo failed" era DNS bloqueado, nao problema de extracao.
   Agora, se o DNS normal falhar, o programa resolve o dominio via
   DNS-over-HTTPS (Cloudflare/Google), acessando o resolvedor pelo IP direto
   -- portanto sem depender do DNS bloqueado -- e injeta o IP no socket.
2) RETRY 100% AUTOMATICO: percorre TODAS as estrategias sozinho, sem
   perguntar nada. So para quando acabam as opcoes.
3) DETECCAO DE ERRO FATAL: se o erro for de rede/DNS (nao adianta trocar
   formato), ele para na hora e diz a causa real, em vez de repetir 5x o
   mesmo erro.
4) QUALIDADE PADRAO salva em config.json (fica escolhida na proxima vez).
5) FEEDBACK so aparece no que BAIXOU (avalia a qualidade do resultado).
"""

import os
import sys
import json
import time
import socket
import threading
import subprocess
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# 0) Dependencias
# ---------------------------------------------------------------------------
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

import requests  # noqa: E402
import yt_dlp  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk, scrolledtext, messagebox  # noqa: E402


PASTA = os.getcwd()
DB = os.path.join(PASTA, "estrategias_memoria.json")
CFG = os.path.join(PASTA, "config.json")


# ===========================================================================
# 1) CAMADA DNS-over-HTTPS  (a correcao do erro real)
# ===========================================================================
# Resolvedores acessados por IP: NAO precisam de DNS para serem alcancados.
DOH = [
    ("https://1.1.1.1/dns-query", "cloudflare-dns.com"),
    ("https://8.8.8.8/resolve", "dns.google"),
    ("https://9.9.9.9:5053/dns-query", "dns.quad9.net"),
]

_dns_cache = {}
_dns_lock = threading.Lock()
_orig_getaddrinfo = socket.getaddrinfo


def resolver_doh(host):
    """Resolve um host via DNS-over-HTTPS. Retorna IP ou None."""
    with _dns_lock:
        if host in _dns_cache:
            return _dns_cache[host]

    for url, sni in DOH:
        try:
            r = requests.get(
                url,
                params={"name": host, "type": "A"},
                headers={"accept": "application/dns-json"},
                timeout=8,
                verify=True,
            )
            data = r.json()
            for ans in data.get("Answer", []):
                if ans.get("type") == 1:  # registro A
                    ip = ans["data"]
                    with _dns_lock:
                        _dns_cache[host] = ip
                    print(f"[DNS] {host} -> {ip} (via DoH)")
                    return ip
        except Exception:
            continue
    return None


def getaddrinfo_patched(host, port, *args, **kwargs):
    """
    Tenta o DNS normal. Se falhar (bloqueio do provedor), cai no DoH.
    """
    try:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    except socket.gaierror:
        ip = resolver_doh(host)
        if ip:
            return _orig_getaddrinfo(ip, port, *args, **kwargs)
        raise


socket.getaddrinfo = getaddrinfo_patched


def testar_dns():
    """Diagnostico rapido: DNS normal funciona? DoH funciona?"""
    normal, doh = False, False
    try:
        _orig_getaddrinfo("www.google.com", 443)
        normal = True
    except Exception:
        pass
    try:
        doh = resolver_doh("www.google.com") is not None
    except Exception:
        pass
    return normal, doh


# ===========================================================================
# 2) Config (qualidade padrao) + memoria de estrategias
# ===========================================================================
def _load(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


CONFIG = _load(CFG, {"qualidade_padrao": "Melhor disponivel"})
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
    ok, fail = rec.get("ok", 0), rec.get("fail", 0)
    return (ok + 1.0) / (ok + fail + 2.0)


POOL = [
    {"id": "mp4_prioritario",
     "fmt": "bv*[ext=mp4][height<=?{Q}]+ba[ext=m4a]/b[ext=mp4][height<=?{Q}]/"
            "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
    {"id": "melhor_qualquer",
     "fmt": "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
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


# Erros onde trocar de estrategia e INUTIL (o problema e a rede, nao o formato)
FATAIS = [
    "getaddrinfo failed",
    "failed to resolve",
    "name or service not known",
    "temporary failure in name resolution",
    "connection refused",
    "network is unreachable",
    "timed out",
    "certificate verify failed",
]


def erro_fatal(msg):
    m = msg.lower()
    return any(f in m for f in FATAIS)


# ===========================================================================
# 3) Qualidade / conversao
# ===========================================================================
def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tem_ffmpeg():
    try:
        _run(["ffmpeg", "-version"], timeout=10)
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

    log("convertendo p/ mp4 (remux sem perda)...")
    r = _run(["ffmpeg", "-y", "-i", path, "-c", "copy",
              "-movflags", "+faststart", saida])
    if r.returncode == 0 and os.path.exists(saida) and os.path.getsize(saida) > 0:
        try:
            os.remove(path)
        except Exception:
            pass
        return saida

    log("remux falhou -> re-encodando H.264 (mais lento, garantido)")
    r = _run(["ffmpeg", "-y", "-i", path,
              "-c:v", "libx264", "-crf", "20", "-preset", "medium",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", saida])
    if r.returncode == 0 and os.path.exists(saida) and os.path.getsize(saida) > 0:
        try:
            os.remove(path)
        except Exception:
            pass
        return saida

    log("[aviso] conversao falhou; mantendo formato original")
    return path


# ===========================================================================
# 4) Motor: percorre TODAS as estrategias sozinho
# ===========================================================================
def baixar_com_motor(url, q, log):
    dom = dominio(url)
    estrategias = montar(dom, q)
    total = len(estrategias)
    ultimo = ""

    for i, s in enumerate(estrategias, 1):
        log(f"[{i}/{total}] tentando: {s['id']}")

        def hook(d, sid=s["id"]):
            if d.get("status") == "downloading":
                pct = (d.get("_percent_str") or "").strip()
                vel = (d.get("_speed_str") or "").strip()
                log(f"{sid}  {pct}  {vel}")
            elif d.get("status") == "finished":
                log(f"{sid}  juntando/convertendo...")

        opts = {
            "format": s["fmt"],
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(PASTA, "%(title).150B.%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "fragment_retries": 6,
            "concurrent_fragment_downloads": 4,
            "geo_bypass": True,
            "progress_hooks": [hook],
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }
        opts.update(s.get("extra", {}))

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                fp = None
                if info.get("requested_downloads"):
                    fp = info["requested_downloads"][0].get("filepath")
                if not fp:
                    fp = ydl.prepare_filename(info)

            if not fp or not os.path.exists(fp):
                raise RuntimeError("arquivo final nao encontrado")

            if not fp.lower().endswith(".mp4"):
                fp = para_mp4(fp, log)

            registrar(dom, s["id"], ok=1)
            return {"ok": True, "arquivo": fp, "altura": altura_video(fp),
                    "strat_id": s["id"], "erro": ""}

        except Exception as e:
            ultimo = str(e).split("\n")[0]

            # <<< a grande diferenca: nao martelar contra uma parede >>>
            if erro_fatal(ultimo):
                normal, doh = testar_dns()
                diag = ("PROBLEMA DE REDE/DNS - trocar de formato nao resolve.\n"
                        f"DNS normal: {'OK' if normal else 'FALHOU'} | "
                        f"DNS-over-HTTPS: {'OK' if doh else 'FALHOU'}\n")
                if not normal and doh:
                    diag += ("O DoH funciona mas o site segue bloqueado. "
                             "Provavel bloqueio do provedor. Tente uma VPN.")
                elif not normal and not doh:
                    diag += ("Sem internet ou firewall bloqueando tudo. "
                             "Cheque a conexao / antivirus.")
                else:
                    diag += "O site pode estar fora do ar ou bloqueando seu IP."
                return {"ok": False, "arquivo": None, "altura": None,
                        "strat_id": None, "erro": diag, "fatal": True}

            registrar(dom, s["id"], fail=1)
            log(f"{s['id']} falhou: {ultimo[:70]}")
            time.sleep(0.3)
            continue  # AUTOMATICO: proxima estrategia, sem perguntar nada

    return {"ok": False, "arquivo": None, "altura": None, "strat_id": None,
            "erro": f"acabaram as {total} estrategias. Ultimo: {ultimo[:150]}"}


# ===========================================================================
# 5) Interface
# ===========================================================================
Q_OPCOES = {
    "Melhor disponivel": 4320,
    "2160p (4K)": 2160,
    "1440p": 1440,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}


class Card:
    def __init__(self, parent, i, url, app):
        self.app, self.url = app, url
        self.strat_id = None
        self.dom = dominio(url)

        self.frame = tk.Frame(parent, bg="#fff", bd=1, relief="solid")
        self.frame.pack(fill="x", padx=6, pady=4)

        top = tk.Frame(self.frame, bg="#fff")
        top.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(top, text=f"#{i}", font=("Segoe UI", 9, "bold"),
                 bg="#fff", fg="#6d28d9").pack(side="left")
        u = url if len(url) < 68 else url[:65] + "..."
        tk.Label(top, text=u, font=("Segoe UI", 9), bg="#fff",
                 fg="#111827").pack(side="left", padx=6)

        mid = tk.Frame(self.frame, bg="#fff")
        mid.pack(fill="x", padx=8)
        self.status = tk.StringVar(value="na fila...")
        tk.Label(mid, textvariable=self.status, font=("Segoe UI", 8),
                 bg="#fff", fg="#374151", anchor="w",
                 justify="left", wraplength=520).pack(side="left")
        self.qual = tk.StringVar(value="")
        tk.Label(mid, textvariable=self.qual, font=("Segoe UI", 9, "bold"),
                 bg="#fff", fg="#059669").pack(side="right")

        # feedback so aparece no que BAIXOU
        self.bot = tk.Frame(self.frame, bg="#fff")
        self.lbl_fb = tk.Label(self.bot, text="qualidade do arquivo:",
                               font=("Segoe UI", 8), bg="#fff", fg="#6b7280")
        self.btn_ok = tk.Button(self.bot, text="boa", font=("Segoe UI", 8),
                                bg="#dcfce7", command=self.marcar_ok)
        self.btn_ruim = tk.Button(self.bot, text="ruim / errada",
                                  font=("Segoe UI", 8), bg="#fee2e2",
                                  command=self.marcar_ruim)
        self.btn_pasta = tk.Button(self.bot, text="abrir pasta",
                                   font=("Segoe UI", 8),
                                   command=lambda: os.startfile(PASTA))

    def log(self, txt):
        self.app.ui(lambda: self.status.set(txt))

    def concluido(self, res):
        def _apply():
            if res["ok"]:
                h = res["altura"]
                self.qual.set(f"{h}p" if h else "OK")
                self.status.set(f"baixado ({res['strat_id']})")
                self.strat_id = res["strat_id"]
                self.bot.pack(fill="x", padx=8, pady=(0, 6))
                self.lbl_fb.pack(side="left", padx=(0, 6))
                self.btn_ok.pack(side="left", padx=(0, 4))
                self.btn_ruim.pack(side="left")
                self.btn_pasta.pack(side="right")
            else:
                self.qual.set("ERRO")
                self.status.set(res["erro"])
        self.app.ui(_apply)

    def marcar_ok(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, ok=3)
        self.status.set("qualidade validada (estrategia reforcada p/ esse site)")
        self._off()

    def marcar_ruim(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, fail=3)
        self.status.set("marcado como ruim (essa estrategia foi rebaixada)")
        self._off()

    def _off(self):
        self.btn_ok.config(state="disabled")
        self.btn_ruim.config(state="disabled")


class App:
    def __init__(self, root):
        self.root = root
        self.cards = []
        self.stop = False
        self.rodando = False

        root.title("Baixador de Video -> MP4  (v3)")
        root.geometry("780x700")
        root.configure(bg="#f5f3ff")

        tk.Label(root, text="Baixador de Video -> MP4",
                 font=("Segoe UI", 15, "bold"), bg="#f5f3ff",
                 fg="#5b21b6").pack(pady=(12, 0))
        tk.Label(root, text=f"salvando em: {PASTA}", font=("Segoe UI", 8),
                 bg="#f5f3ff", fg="#6b7280").pack()

        self.diag = tk.StringVar(value="checando rede...")
        tk.Label(root, textvariable=self.diag, font=("Segoe UI", 8),
                 bg="#f5f3ff", fg="#b91c1c").pack()
        threading.Thread(target=self._checar, daemon=True).start()

        top = tk.Frame(root, bg="#f5f3ff")
        top.pack(fill="x", padx=14, pady=(8, 4))
        tk.Label(top, text="Cole 1 link por linha (baixa a lista toda):",
                 font=("Segoe UI", 9), bg="#f5f3ff").pack(anchor="w")
        self.txt = scrolledtext.ScrolledText(top, height=5,
                                             font=("Consolas", 9), wrap="none")
        self.txt.pack(fill="x", pady=4)

        ctl = tk.Frame(root, bg="#f5f3ff")
        ctl.pack(fill="x", padx=14)
        tk.Label(ctl, text="Qualidade:", font=("Segoe UI", 9),
                 bg="#f5f3ff").pack(side="left")

        padrao = CONFIG.get("qualidade_padrao", "Melhor disponivel")
        if padrao not in Q_OPCOES:
            padrao = "Melhor disponivel"
        self.q_var = tk.StringVar(value=padrao)
        ttk.Combobox(ctl, textvariable=self.q_var, values=list(Q_OPCOES),
                     state="readonly", width=17).pack(side="left", padx=6)
        tk.Button(ctl, text="salvar como padrao", font=("Segoe UI", 8),
                  command=self.salvar_padrao).pack(side="left")

        tk.Button(ctl, text="Baixar Tudo", bg="#7c3aed", fg="white",
                  font=("Segoe UI", 10, "bold"),
                  command=self.iniciar).pack(side="left", padx=10)
        tk.Button(ctl, text="parar", bg="#ef4444", fg="white",
                  font=("Segoe UI", 9),
                  command=lambda: setattr(self, "stop", True)).pack(side="left")
        tk.Button(ctl, text="limpar", font=("Segoe UI", 9),
                  command=self.limpar).pack(side="right")

        wrap = tk.Frame(root, bg="#e5e7eb", bd=1, relief="solid")
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        self.canvas = tk.Canvas(wrap, bg="#e5e7eb", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.lista = tk.Frame(self.canvas, bg="#e5e7eb")
        self.lista.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.lista, anchor="nw",
                                  width=730)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-e.delta / 120), "units"))

    def _checar(self):
        normal, doh = testar_dns()
        ff = tem_ffmpeg()
        p = []
        p.append("DNS: OK" if normal else "DNS: BLOQUEADO")
        p.append("DoH: ativo (contorno ligado)" if doh else "DoH: indisponivel")
        p.append("FFmpeg: OK" if ff else "FFmpeg: FALTANDO (conversao limitada)")
        self.ui(lambda: self.diag.set("  |  ".join(p)))

    def ui(self, f):
        self.root.after(0, f)

    def salvar_padrao(self):
        CONFIG["qualidade_padrao"] = self.q_var.get()
        _save(CFG, CONFIG)
        messagebox.showinfo("Salvo",
                            f"Padrao definido: {self.q_var.get()}\n"
                            "Ja vem selecionado na proxima vez.")

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

        self.limpar()
        q = Q_OPCOES[self.q_var.get()]
        for i, u in enumerate(links, 1):
            self.cards.append(Card(self.lista, i, u, self))

        self.stop = False
        self.rodando = True
        threading.Thread(target=self._worker,
                         args=(list(self.cards), q), daemon=True).start()

    def _worker(self, cards, q):
        for c in cards:
            if self.stop:
                c.log("cancelado")
                continue
            c.concluido(baixar_com_motor(c.url, q, c.log))
        self.rodando = False


if __name__ == "__main__":
    print("[INFO] Camada DNS-over-HTTPS ativa (contorna DNS bloqueado)")
    r = tk.Tk()
    App(r)
    r.mainloop()
