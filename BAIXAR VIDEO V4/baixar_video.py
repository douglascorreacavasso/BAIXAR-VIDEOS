# -*- coding: utf-8 -*-
"""
BAIXADOR DE VIDEO -> MP4  (com motor de tentativas que aprende)

- Cole 1 ou VARIOS links (um por linha). Baixa a lista toda, um por um.
- Motor de tentativas: testa N estrategias de formato/protocolo. Guarda numa
  memoria (estrategias_memoria.json) quais funcionam melhor POR SITE e
  prioriza elas nas proximas vezes. Melhora com o uso.
- Deteccao de qualidade (altura em p) via ffprobe.
- Se nao vier MP4, converte pra MP4 (remux sem perda; se nao der, re-encoda H.264).
- Botoes de feedback por item: OK (baixou certo) / ERRO (reforca/penaliza a memoria
  e permite tentar outra forma na hora).
- Salva tudo na pasta onde o programa foi executado.

Quem faz a extracao real dos sites e o yt-dlp. A "IA" aqui e a camada que
escolhe/reordena estrategias e aprende com o feedback.
"""

import os
import sys
import json
import time
import threading
import subprocess
from urllib.parse import urlparse


# ----------------------------------------------------------------------------
# 0) Auto-instalacao de dependencias (rede)
# ----------------------------------------------------------------------------
def _ensure(pkg, imp=None):
    imp = imp or pkg
    try:
        __import__(imp)
    except ImportError:
        print(f"[SETUP] instalando {pkg} ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", pkg])


_ensure("yt-dlp", "yt_dlp")

import yt_dlp  # noqa: E402
import tkinter as tk  # noqa: E402
from tkinter import ttk, scrolledtext, messagebox  # noqa: E402


PASTA = os.getcwd()
DB = os.path.join(PASTA, "estrategias_memoria.json")


# ----------------------------------------------------------------------------
# 1) Memoria de estrategias (a "aprendizagem")
# ----------------------------------------------------------------------------
def carregar_memoria():
    if os.path.exists(DB):
        try:
            with open(DB, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_memoria(mem):
    try:
        with open(DB, "w", encoding="utf-8") as f:
            json.dump(mem, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


MEM = carregar_memoria()
MEM_LOCK = threading.Lock()


def dominio(url):
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "?"


def registrar(dom, strat_id, ok=0, fail=0):
    with MEM_LOCK:
        d = MEM.setdefault(dom, {})
        r = d.setdefault(strat_id, {"ok": 0, "fail": 0})
        r["ok"] += ok
        r["fail"] += fail
        salvar_memoria(MEM)


def score(rec):
    ok = rec.get("ok", 0)
    fail = rec.get("fail", 0)
    return (ok + 1.0) / (ok + fail + 2.0)  # suavizado (Laplace)


# Pool base de estrategias. {Q} vira a altura maxima desejada.
POOL = [
    {"id": "mp4_prioritario",
     "fmt": "bv*[ext=mp4][height<=?{Q}]+ba[ext=m4a]/b[ext=mp4][height<=?{Q}]/"
            "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
    {"id": "melhor_qualquer",
     "fmt": "bv*[height<=?{Q}]+ba/b[height<=?{Q}]/bv*+ba/b"},
    {"id": "unico_melhor",
     "fmt": "b[height<=?{Q}]/b"},
    {"id": "hls_nativo",
     "fmt": "bv*[height<=?{Q}]+ba/b", "extra": {"hls_prefer_native": True}},
    {"id": "generico_best",
     "fmt": "best"},
]

MENOR = {"id": "menor_rapido", "fmt": "wv*+wa/w/worst"}


def montar_estrategias(dom, qheight, menor=False):
    base = [dict(s) for s in POOL]
    if menor:
        base.insert(0, dict(MENOR))
    for s in base:
        s["fmt"] = s["fmt"].replace("{Q}", str(qheight))
    with MEM_LOCK:
        m = MEM.get(dom, {})
    base.sort(key=lambda s: score(m.get(s["id"], {})), reverse=True)
    return base


# ----------------------------------------------------------------------------
# 2) Ferramentas de qualidade / conversao (ffmpeg / ffprobe)
# ----------------------------------------------------------------------------
def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def tem_ffmpeg():
    try:
        _run(["ffmpeg", "-version"], timeout=10)
        return True
    except Exception:
        return False


def altura_video(path):
    """Detecta a resolucao (altura em p) via ffprobe."""
    try:
        out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=height", "-of", "csv=p=0", path],
                   timeout=30)
        linha = out.stdout.strip().splitlines()[0]
        return int(linha)
    except Exception:
        return None


def para_mp4(path, log):
    """Garante MP4. Tenta remux (sem perda). Se falhar, re-encoda H.264/AAC."""
    if path.lower().endswith(".mp4"):
        return path
    base = os.path.splitext(path)[0]
    saida = base + ".mp4"

    log(f"convertendo p/ mp4 (remux): {os.path.basename(path)}")
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

    log("[aviso] nao consegui converter; mantendo formato original")
    return path


# ----------------------------------------------------------------------------
# 3) Motor de download (tenta estrategias em ordem aprendida)
# ----------------------------------------------------------------------------
def baixar_com_motor(url, qheight, menor, on_status, on_progress, evitar=None):
    """
    Retorna dict: {ok, arquivo, altura, strat_id, erro}
    'evitar' = id de estrategia para pular (usado apos feedback de ERRO).
    """
    dom = dominio(url)
    estrategias = montar_estrategias(dom, qheight, menor)

    ultimo_erro = ""
    for s in estrategias:
        if evitar and s["id"] == evitar:
            continue

        on_status(f"tentando: {s['id']}")

        def hook(d, sid=s["id"]):
            if d.get("status") == "downloading":
                pct = (d.get("_percent_str") or "").strip()
                vel = (d.get("_speed_str") or "").strip()
                on_progress(f"{sid}  {pct}  {vel}")
            elif d.get("status") == "finished":
                on_progress(f"{sid}  juntando/convertendo...")

        opts = {
            "format": s["fmt"],
            "merge_output_format": "mp4",
            "outtmpl": os.path.join(PASTA, "%(title).150B.%(ext)s"),
            "restrictfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 4,
            "fragment_retries": 6,
            "concurrent_fragment_downloads": 4,
            "geo_bypass": True,
            "progress_hooks": [hook],
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
            ],
        }
        if "extra" in s:
            opts.update(s["extra"])

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
                fp = para_mp4(fp, on_progress)

            altura = altura_video(fp)
            registrar(dom, s["id"], ok=1)  # sucesso provisorio (gerou arquivo)
            return {"ok": True, "arquivo": fp, "altura": altura,
                    "strat_id": s["id"], "erro": ""}

        except Exception as e:
            ultimo_erro = str(e).split("\n")[0][:200]
            registrar(dom, s["id"], fail=1)
            on_progress(f"{s['id']} falhou: {ultimo_erro[:60]}")
            time.sleep(0.4)
            continue

    return {"ok": False, "arquivo": None, "altura": None,
            "strat_id": None, "erro": ultimo_erro or "todas as estrategias falharam"}


# ----------------------------------------------------------------------------
# 4) Interface grafica
# ----------------------------------------------------------------------------
Q_OPCOES = {
    "Melhor disponivel": (4320, False),
    "1080p": (1080, False),
    "720p": (720, False),
    "480p": (480, False),
    "360p": (360, False),
    "Menor (rapido)": (480, True),
}


class Card:
    """Uma linha da fila, com status e botoes de feedback."""
    def __init__(self, parent, indice, url, app):
        self.app = app
        self.url = url
        self.strat_id = None
        self.dom = dominio(url)

        self.frame = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid")
        self.frame.pack(fill="x", padx=6, pady=4)

        top = tk.Frame(self.frame, bg="#ffffff")
        top.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(top, text=f"#{indice}", font=("Segoe UI", 9, "bold"),
                 bg="#ffffff", fg="#6d28d9").pack(side="left")
        u = url if len(url) < 70 else url[:67] + "..."
        tk.Label(top, text=u, font=("Segoe UI", 9), bg="#ffffff",
                 fg="#111827").pack(side="left", padx=6)

        mid = tk.Frame(self.frame, bg="#ffffff")
        mid.pack(fill="x", padx=8, pady=(0, 2))
        self.status = tk.StringVar(value="na fila...")
        tk.Label(mid, textvariable=self.status, font=("Segoe UI", 8),
                 bg="#ffffff", fg="#374151").pack(side="left")
        self.qual = tk.StringVar(value="")
        tk.Label(mid, textvariable=self.qual, font=("Segoe UI", 8, "bold"),
                 bg="#ffffff", fg="#059669").pack(side="right")

        bot = tk.Frame(self.frame, bg="#ffffff")
        bot.pack(fill="x", padx=8, pady=(0, 6))
        self.btn_ok = tk.Button(bot, text="OK baixou certo", state="disabled",
                                font=("Segoe UI", 8), bg="#dcfce7",
                                command=self.marcar_ok)
        self.btn_ok.pack(side="left", padx=(0, 6))
        self.btn_err = tk.Button(bot, text="X deu errado", state="disabled",
                                 font=("Segoe UI", 8), bg="#fee2e2",
                                 command=self.marcar_erro)
        self.btn_err.pack(side="left")
        self.btn_pasta = tk.Button(bot, text="abrir pasta", state="disabled",
                                   font=("Segoe UI", 8),
                                   command=lambda: os.startfile(PASTA))
        self.btn_pasta.pack(side="right")

    def set_status(self, txt):
        self.app.ui(lambda: self.status.set(txt))

    def concluido(self, res):
        def _apply():
            if res["ok"]:
                h = res["altura"]
                self.qual.set(f"{h}p" if h else "OK")
                self.status.set("baixado. Confira e valide abaixo:")
                self.strat_id = res["strat_id"]
                self.btn_ok.config(state="normal")
                self.btn_err.config(state="normal")
                self.btn_pasta.config(state="normal")
            else:
                self.status.set(f"FALHOU: {res['erro']}")
                self.qual.set("erro")
                self.btn_err.config(state="normal")
        self.app.ui(_apply)

    def marcar_ok(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, ok=2)  # reforco extra
        self.status.set("validado (reforcei essa estrategia p/ esse site)")
        self.btn_ok.config(state="disabled")
        self.btn_err.config(state="disabled")

    def marcar_erro(self):
        if self.strat_id:
            registrar(self.dom, self.strat_id, fail=2)  # penalidade forte
        self.btn_ok.config(state="disabled")
        self.btn_err.config(state="disabled")
        r = messagebox.askyesno(
            "Feedback",
            "Marquei como errado e penalizei essa estrategia.\n\n"
            "Quer tentar OUTRA forma de baixar agora?")
        if r:
            self.status.set("re-tentando com outra estrategia...")
            self.app.reenfileirar(self.url, evitar=self.strat_id)


class App:
    def __init__(self, root):
        self.root = root
        self.cards = []
        self.stop_flag = False
        self.rodando = False

        root.title("Baixador de Video -> MP4 (motor que aprende)")
        root.geometry("760x680")
        root.configure(bg="#f5f3ff")

        tk.Label(root, text="Baixador de Video -> MP4",
                 font=("Segoe UI", 15, "bold"), bg="#f5f3ff",
                 fg="#5b21b6").pack(pady=(12, 0))
        tk.Label(root, text=f"salvando em: {PASTA}",
                 font=("Segoe UI", 8), bg="#f5f3ff", fg="#6b7280").pack()
        if not tem_ffmpeg():
            tk.Label(root, text="[aviso] FFmpeg nao encontrado - "
                                "mesclagem/conversao limitada",
                     font=("Segoe UI", 8), bg="#f5f3ff", fg="#b91c1c").pack()

        top = tk.Frame(root, bg="#f5f3ff")
        top.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(top, text="Cole 1 link por linha (a lista toda de uma vez):",
                 font=("Segoe UI", 9), bg="#f5f3ff", fg="#111827").pack(anchor="w")
        self.txt = scrolledtext.ScrolledText(top, height=5, font=("Consolas", 9),
                                             wrap="none")
        self.txt.pack(fill="x", pady=4)

        ctl = tk.Frame(root, bg="#f5f3ff")
        ctl.pack(fill="x", padx=14)
        tk.Label(ctl, text="Qualidade:", font=("Segoe UI", 9),
                 bg="#f5f3ff").pack(side="left")
        self.q_var = tk.StringVar(value="Melhor disponivel")
        ttk.Combobox(ctl, textvariable=self.q_var, values=list(Q_OPCOES.keys()),
                     state="readonly", width=18).pack(side="left", padx=6)

        self.btn_go = tk.Button(ctl, text="Baixar Tudo", bg="#7c3aed",
                                fg="white", font=("Segoe UI", 10, "bold"),
                                command=self.iniciar)
        self.btn_go.pack(side="left", padx=8)
        self.btn_stop = tk.Button(ctl, text="parar", bg="#ef4444", fg="white",
                                  font=("Segoe UI", 9), command=self.parar)
        self.btn_stop.pack(side="left")
        tk.Button(ctl, text="limpar lista", font=("Segoe UI", 9),
                  command=self.limpar).pack(side="right")

        wrap = tk.Frame(root, bg="#e5e7eb", bd=1, relief="solid")
        wrap.pack(fill="both", expand=True, padx=14, pady=10)
        self.canvas = tk.Canvas(wrap, bg="#e5e7eb", highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.lista = tk.Frame(self.canvas, bg="#e5e7eb")
        self.lista.bind("<Configure>",
                        lambda e: self.canvas.configure(
                            scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.lista, anchor="nw", width=710)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(
                                 int(-e.delta / 120), "units"))

    def ui(self, func):
        self.root.after(0, func)

    def limpar(self):
        if self.rodando:
            return
        for c in self.cards:
            c.frame.destroy()
        self.cards = []

    def parar(self):
        self.stop_flag = True

    def iniciar(self):
        if self.rodando:
            return
        raw = self.txt.get("1.0", "end").strip()
        links = [l.strip() for l in raw.splitlines() if l.strip()]
        if not links:
            messagebox.showinfo("Ops", "Cole pelo menos 1 link.")
            return

        self.limpar()
        qh, menor = Q_OPCOES[self.q_var.get()]
        for i, url in enumerate(links, 1):
            self.cards.append(Card(self.lista, i, url, self))

        self.stop_flag = False
        self.rodando = True
        self.btn_go.config(state="disabled")
        t = threading.Thread(target=self._worker,
                             args=(list(self.cards), qh, menor), daemon=True)
        t.start()

    def reenfileirar(self, url, evitar=None):
        qh, menor = Q_OPCOES[self.q_var.get()]
        card = Card(self.lista, len(self.cards) + 1, url, self)
        self.cards.append(card)
        t = threading.Thread(target=self._um,
                             args=(card, qh, menor, evitar), daemon=True)
        t.start()

    def _um(self, card, qh, menor, evitar):
        res = baixar_com_motor(
            card.url, qh, menor,
            on_status=card.set_status,
            on_progress=card.set_status,
            evitar=evitar)
        card.concluido(res)

    def _worker(self, cards, qh, menor):
        for card in cards:
            if self.stop_flag:
                card.set_status("cancelado")
                continue
            self._um(card, qh, menor, None)
        self.ui(lambda: self.btn_go.config(state="normal"))
        self.rodando = False


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
