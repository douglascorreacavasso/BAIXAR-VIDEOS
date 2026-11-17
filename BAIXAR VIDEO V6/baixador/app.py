# -*- coding: utf-8 -*-
"""Janela do Baixador de Vídeo V6 (design novo, tema escuro e claro).

Regra de ouro herdada do NERAEL: NADA de Tk fora da thread da janela. Os downloads correm em threads e só
mandam avisos por uma fila; a janela lê a fila a cada 120 ms."""
from __future__ import annotations

import ctypes
import io
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font  # noqa: F401  (tk.font.Font)
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import VERSAO, navegador, tema
from .base import (APP_DIR, NAVEGADORES, QUALIDADES, SEM_JANELA, achar_programa, carregar_config, dominio,
                   extrair_links, salvar_config)
from .pipeline import Motor, Tarefa
from .rede import ip_publico, ligar_doh, tcp_ok, tor_ligado, tor_novo_circuito, TOR_SOCKS
from .tela import GravadorArea, capturar, ligar_dpi, selecionar_area
from .tema import C, FONTE, FONTE_FORTE, I, ICONES, MONO
from .widgets import Botao, Chave, Dica, Pilula, Progresso, Rolagem

PLACEHOLDER = "Cole aqui um ou vários links…  (pode colar a conversa inteira do WhatsApp: eu acho os links sozinho)"
COR_ESTADO = {"fila": "fraco", "analisando": "destaque", "escolher": "amarelo", "baixando": "destaque", "pronto": "verde",
              "erro": "vermelho", "cancelado": "fraco"}
NOME_ESTADO = {"fila": "na fila", "analisando": "analisando", "escolher": "aguardando você", "baixando": "baixando",
               "pronto": "pronto", "erro": "não deu", "cancelado": "cancelado"}


def para_lixeira(caminho: str) -> bool:
    """Manda para a Lixeira (dá para recuperar), não apaga de vez."""
    if not sys.platform.startswith("win"):
        return False

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(caminho) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, 0, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


def miniatura_do_arquivo(caminho: str, larg=224):
    from PIL import Image
    ff = achar_programa("ffmpeg")
    if not ff:
        return None
    for t in ("2", "0.3"):
        try:
            r = subprocess.run([ff, "-v", "error", "-ss", t, "-i", caminho, "-frames:v", "1", "-vf", f"scale={larg}:-2",
                                "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=20, creationflags=SEM_JANELA)
            if r.stdout:
                return Image.open(io.BytesIO(r.stdout)).convert("RGB")
        except Exception:
            continue
    return None


class Cartao:
    """Um download na lista."""
    LARG_MINI, ALT_MINI = 120, 68

    def __init__(self, app: "App", t: Tarefa):
        self.app, self.t = app, t
        self.foto = None
        self.mini_pronta = False
        self.f = tk.Frame(app.lista.dentro, bg=C["painel"], highlightthickness=1, highlightbackground=C["borda"])
        self.f.pack(fill="x", padx=(0, 6), pady=5)
        self.faixa = tk.Frame(self.f, bg=C["fraco"], width=4)
        self.faixa.pack(side="left", fill="y")
        self.mini = tk.Canvas(self.f, width=self.LARG_MINI, height=self.ALT_MINI, bg=C["painel3"], highlightthickness=0)
        self.mini.pack(side="left", padx=12, pady=12)
        self._mini_vazia()
        meio = tk.Frame(self.f, bg=C["painel"])
        meio.pack(side="left", fill="both", expand=True, pady=10)
        linha1 = tk.Frame(meio, bg=C["painel"])
        linha1.pack(fill="x")
        self.estado = tk.Label(linha1, text="", font=(FONTE_FORTE, 9), bg=C["painel"], fg=C["suave"])
        self.estado.pack(side="left")
        Pilula(linha1, dominio(t.url)).pack(side="left", padx=8)
        self.titulo = tk.Label(meio, text=t.url, font=(FONTE_FORTE, 11), bg=C["painel"], fg=C["texto"], anchor="w")
        self.titulo.pack(fill="x", pady=(3, 0))
        self.texto = tk.Label(meio, text="", font=(FONTE, 9), bg=C["painel"], fg=C["suave"], anchor="w", justify="left")
        self.texto.pack(fill="x", pady=(1, 6))
        self.barra = Progresso(meio, fundo=C["painel"])
        self.barra.pack(fill="x", padx=(0, 12))
        self.botoes = tk.Frame(self.f, bg=C["painel"])
        self.botoes.pack(side="right", padx=12)
        self._estado_botoes = None
        self.f.bind("<Configure>", lambda e: self._ajustar_textos())
        self.atualizar()

    def _mini_vazia(self):
        self.mini.delete("all")
        self.mini.create_text(self.LARG_MINI / 2, self.ALT_MINI / 2, text=I["play"], font=(ICONES, 20), fill=C["fraco"])

    def _ajustar_textos(self):
        largura = max(200, self.f.winfo_width() - self.LARG_MINI - 60 - self.botoes.winfo_width())
        self.texto.configure(wraplength=largura)
        tit = self.t.titulo or self.t.url
        f = tk.font.Font(family=FONTE_FORTE, size=11)
        if f.measure(tit) > largura:
            while tit and f.measure(tit + "…") > largura:
                tit = tit[:-1]
            tit += "…"
        self.titulo.configure(text=tit)

    def atualizar(self):
        t = self.t
        cor = C[COR_ESTADO.get(t.estado, "fraco")]
        self.faixa.configure(bg=cor)
        self.estado.configure(text=NOME_ESTADO.get(t.estado, t.estado).upper(), fg=cor)
        self.texto.configure(text=t.texto, fg=C["vermelho"] if t.estado == "erro" else C["suave"])
        if t.estado in ("pronto",):
            self.barra.definir(100, C["verde"])
        elif t.estado in ("erro", "cancelado"):
            self.barra.definir(0)
        elif t.estado == "fila":
            self.barra.definir(0)
        else:
            self.barra.definir(t.progresso, C["destaque"])
        self._ajustar_textos()
        chave = (t.estado, bool(t.candidatos), t.categoria_erro)
        if chave != self._estado_botoes:
            self._estado_botoes = chave
            for w in self.botoes.winfo_children():
                w.destroy()
            b = []
            if t.estado in ("fila", "analisando", "baixando", "escolher"):
                b.append(Botao(self.botoes, "Cancelar", I["x"], lambda: t.cancel.set(), "fantasma", tamanho=9))
            elif t.estado == "pronto":
                b.append(Botao(self.botoes, "Assistir", I["play"], self.abrir, "primario", tamanho=9))
                b.append(Botao(self.botoes, "", I["pasta"], self.pasta, "secundario", tamanho=10, dica="Mostrar na pasta"))
                b.append(Botao(self.botoes, "", I["ok"], self.certo, "sucesso", tamanho=10,
                               dica="Era esse vídeo mesmo (ensina o programa)"))
                b.append(Botao(self.botoes, "", I["x"], self.errado, "perigo", tamanho=10,
                               dica="Não era esse vídeo: escolher o certo (o errado vai para a Lixeira e o programa aprende)"))
            else:
                b.append(Botao(self.botoes, "Tentar de novo", I["repetir"], self.repetir, "secundario", tamanho=9))
                if t.categoria_erro in ("nao_achei", "formato", "bloqueio", "drm", "outro") and not t.url.startswith("tela://"):
                    b.append(Botao(self.botoes, "", I["gravar"], self.app.gravar_area, "secundario", tamanho=10,
                                   dica="Gravar a área da tela enquanto o vídeo toca (último recurso)"))
                if t.categoria_erro in ("login", "cookies_travados"):
                    b.append(Botao(self.botoes, "", I["config"], self.app.abrir_config, "secundario", tamanho=10,
                                   dica="Escolher o navegador para usar os cookies"))
            b.append(Botao(self.botoes, "", I["lista"], self.detalhes, "fantasma", tamanho=10, dica="Ver o passo a passo"))
            for w in b:
                w.configure(bg=C["painel"])
                w.fundo_pai = C["painel"]
                w._desenhar()
                w.pack(side="left", padx=3)
        if t.estado == "pronto" and not self.mini_pronta and t.arquivo:
            self.mini_pronta = True
            threading.Thread(target=self._fazer_mini, daemon=True).start()

    def _fazer_mini(self):
        img = miniatura_do_arquivo(self.t.arquivo)
        if img is not None:
            self.app.na_janela(lambda: self._por_mini(img))

    def _por_mini(self, img):
        from PIL import ImageTk
        try:
            img = img.copy()
            img.thumbnail((self.LARG_MINI, self.ALT_MINI))
            self.foto = ImageTk.PhotoImage(img, master=self.mini)
            self.mini.delete("all")
            self.mini.create_image(self.LARG_MINI / 2, self.ALT_MINI / 2, image=self.foto)
        except Exception:
            pass

    def abrir(self):
        if self.t.arquivo and os.path.exists(self.t.arquivo):
            os.startfile(self.t.arquivo)

    def pasta(self):
        if self.t.arquivo and os.path.exists(self.t.arquivo):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.t.arquivo)])
        else:
            os.startfile(self.app.cfg["pasta_destino"])

    def certo(self):
        self.app.motor.foi_bom(self.t)
        self.t.texto = "obrigado! anotei que era esse (" + self.t.caminho + ")"
        self.atualizar()
        self.app.rodape()

    def errado(self):
        self.app.nao_era_esse(self)

    def repetir(self):
        self.app.adicionar([self.t.url], substituir=self)

    def detalhes(self):
        self.app.mostrar_detalhes(self.t)

    def destruir(self):
        self.f.destroy()


class App:
    def __init__(self, raiz: tk.Tk):
        self.raiz = raiz
        self.cfg = carregar_config()
        tema.trocar_tema(self.cfg.get("tema", "escuro"))
        self.ui_q: queue.Queue = queue.Queue()
        self.trabalho: queue.Queue = queue.Queue()
        self.cartoes: dict[str, Cartao] = {}
        self.ordem: list[str] = []
        self.area = None
        self.capturas: list = []
        self.gravador = None
        self.ativos = 0
        self._cond = threading.Condition()
        self.motor = Motor(self.cfg, ao_mudar=lambda t: self.ui_q.put(("tarefa", t.id)), escolher=self._escolher_bloqueante)
        self._importar_v5()
        raiz.title(f"Baixador de Vídeo {VERSAO}")
        raiz.geometry("1120x780")
        raiz.minsize(880, 600)
        tema.aplicar_icone(raiz)
        self.montar()
        for _ in range(4):
            threading.Thread(target=self._operario, daemon=True).start()
        threading.Thread(target=self._checar_ambiente, daemon=True).start()
        raiz.after(120, self._ciclo)
        raiz.protocol("WM_DELETE_WINDOW", self.fechar)

    # ---------------------------------------------------------------- montagem (refeita ao trocar o tema)
    def montar(self):
        r = self.raiz
        for w in r.winfo_children():
            w.destroy()
        r.configure(bg=C["fundo"])
        tema.estilo_ttk(r)
        tema.barra_titulo(r)
        self.cartoes_antigos = dict(self.cartoes)
        self.cartoes = {}

        # ---- topo
        topo = tk.Frame(r, bg=C["painel"], height=70)
        topo.pack(fill="x")
        tk.Frame(r, bg=C["borda"], height=1).pack(fill="x")
        self._icone = tema.imagem_icone(40, topo)
        tk.Label(topo, image=self._icone, bg=C["painel"]).pack(side="left", padx=(20, 12), pady=14)
        txt = tk.Frame(topo, bg=C["painel"])
        txt.pack(side="left")
        l1 = tk.Frame(txt, bg=C["painel"])
        l1.pack(anchor="w")
        tk.Label(l1, text="Baixador de Vídeo", font=(FONTE_FORTE, 16), bg=C["painel"], fg=C["texto"]).pack(side="left")
        Pilula(l1, f"V{VERSAO}", C["destaque"]).pack(side="left", padx=8, pady=(4, 0))
        self.lbl_pasta = tk.Label(txt, text=f"salvando em  {self.cfg['pasta_destino']}", font=(FONTE, 9), bg=C["painel"],
                                  fg=C["suave"], cursor="hand2")
        self.lbl_pasta.pack(anchor="w")
        self.lbl_pasta.bind("<Button-1>", lambda e: self.abrir_pasta())
        Dica(self.lbl_pasta, "Abrir a pasta dos vídeos (troque em Configurações)")
        dir_ = tk.Frame(topo, bg=C["painel"])
        dir_.pack(side="right", padx=16)
        Botao(dir_, "", I["config"], self.abrir_config, "fantasma", tamanho=12, dica="Configurações").pack(side="right", padx=2)
        Botao(dir_, "", I["sol"] if tema.escuro() else I["lua"], self.trocar_tema, "fantasma", tamanho=12,
              dica="Tema claro / escuro").pack(side="right", padx=2)
        self.status_amb = tk.Frame(dir_, bg=C["painel"])
        self.status_amb.pack(side="right", padx=10)
        self._pintar_ambiente(getattr(self, "_amb", None))

        # ---- entrada
        corpo = tk.Frame(r, bg=C["fundo"])
        corpo.pack(fill="both", expand=True, padx=22, pady=(18, 0))
        cart = tk.Frame(corpo, bg=C["painel"], highlightthickness=1, highlightbackground=C["borda"])
        cart.pack(fill="x")
        cab = tk.Frame(cart, bg=C["painel"])
        cab.pack(fill="x", padx=18, pady=(14, 6))
        tk.Label(cab, text="Links", font=(FONTE_FORTE, 12), bg=C["painel"], fg=C["texto"]).pack(side="left")
        self.lbl_contagem = tk.Label(cab, text="", font=(FONTE, 9), bg=C["painel"], fg=C["suave"])
        self.lbl_contagem.pack(side="left", padx=10)
        Botao(cab, "Colar", I["colar"], self.colar, "fantasma", tamanho=9).pack(side="right")
        Botao(cab, "Limpar", I["limpar"], self.limpar_caixa, "fantasma", tamanho=9).pack(side="right", padx=4)
        tk.Frame(cab, bg=C["borda"], width=1, height=20).pack(side="right", padx=12)
        self.v_perg = tk.BooleanVar(value=self.cfg.get("perguntar_na_duvida", True))
        Chave(cab, "Perguntar na dúvida", self.v_perg, lambda v: self._guardar("perguntar_na_duvida", v),
              dica="Quando a página tem dois vídeos parecidos, mostra os dois para você escolher.").pack(side="right", padx=(16, 0))
        self.v_compat = tk.BooleanVar(value=self.cfg.get("mp4_compativel", True))
        Chave(cab, "MP4 que toca em tudo", self.v_compat, lambda v: self._guardar("mp4_compativel", v),
              dica="Converte vídeos AV1/VP9 para H.264 (toca no celular, TV e Windows). Demora mais em vídeos longos.").pack(side="right")
        borda_txt = tk.Frame(cart, bg=C["borda"], padx=1, pady=1)
        borda_txt.pack(fill="x", padx=18)
        self.caixa = tk.Text(borda_txt, height=4, font=(FONTE, 11), bg=C["entrada"], fg=C["texto"], insertbackground=C["texto"],
                             relief="flat", wrap="word", padx=12, pady=10, selectbackground=C["selecao"], undo=True)
        self.caixa.pack(fill="x")
        self._placeholder(True)
        self.caixa.bind("<FocusIn>", lambda e: self._placeholder(False))
        self.caixa.bind("<FocusOut>", lambda e: self._placeholder(True))
        self.caixa.bind("<KeyRelease>", lambda e: self._contar())
        self.caixa.bind("<<Paste>>", lambda e: self.raiz.after(50, self._contar))
        self.caixa.bind("<Control-Return>", lambda e: (self.baixar(), "break")[1])
        self.caixa.bind("<FocusIn>", lambda e: borda_txt.configure(bg=C["destaque"]), add="+")
        self.caixa.bind("<FocusOut>", lambda e: borda_txt.configure(bg=C["borda"]), add="+")

        ctl = tk.Frame(cart, bg=C["painel"])
        ctl.pack(fill="x", padx=18, pady=14)
        tk.Label(ctl, text="Qualidade", font=(FONTE, 10), bg=C["painel"], fg=C["suave"]).pack(side="left")
        self.q_var = tk.StringVar(value=self.cfg["qualidade_padrao"])
        cb = ttk.Combobox(ctl, textvariable=self.q_var, values=list(QUALIDADES), state="readonly", width=16, font=(FONTE, 10))
        cb.pack(side="left", padx=(8, 4))
        Botao(ctl, "", I["estrela"], self.favoritar, "fantasma", tamanho=11,
              dica="Favoritar esta qualidade como padrão").pack(side="left")

        self.b_baixar = Botao(ctl, "Baixar", I["baixar"], self.baixar, "primario", tamanho=11, largura=130,
                              dica="Baixar todos os links (Ctrl+Enter)")
        self.b_baixar.pack(side="right")
        self.b_gravar = Botao(ctl, "Gravar área", I["gravar"], self.gravar_area, "secundario", tamanho=10,
                              dica="Último recurso: grava a área marcada da tela, com o som do computador")
        self.b_gravar.pack(side="right", padx=8)
        Botao(ctl, "Marcar na tela", I["alvo"], self.marcar_tela, "secundario", tamanho=10,
              dica="Arraste sobre o vídeo que está tocando: ajuda a achar o vídeo certo na página").pack(side="right")

        self.faixa_area = tk.Frame(corpo, bg=C["fundo"])
        self.faixa_area.pack(fill="x")
        self._pintar_area()

        # ---- lista
        cab2 = tk.Frame(corpo, bg=C["fundo"])
        cab2.pack(fill="x", pady=(16, 6))
        tk.Label(cab2, text="Downloads", font=(FONTE_FORTE, 12), bg=C["fundo"], fg=C["texto"]).pack(side="left")
        self.lbl_resumo = tk.Label(cab2, text="", font=(FONTE, 9), bg=C["fundo"], fg=C["suave"])
        self.lbl_resumo.pack(side="left", padx=10)
        Botao(cab2, "Abrir pasta", I["pasta"], self.abrir_pasta, "fantasma", tamanho=9, fundo=C["fundo"]).pack(side="right")
        Botao(cab2, "Tirar os prontos", I["limpar"], self.tirar_prontos, "fantasma", tamanho=9, fundo=C["fundo"]).pack(side="right", padx=4)
        self.lista = Rolagem(corpo, fundo=C["fundo"])
        self.lista.pack(fill="both", expand=True)
        self.vazio = tk.Frame(self.lista.dentro, bg=C["fundo"])
        tk.Label(self.vazio, text=I["baixar"], font=(ICONES, 34), bg=C["fundo"], fg=C["borda"]).pack(pady=(50, 8))
        tk.Label(self.vazio, text="Nenhum download ainda", font=(FONTE_FORTE, 12), bg=C["fundo"], fg=C["suave"]).pack()
        tk.Label(self.vazio, text="Cole um link acima e aperte Baixar. Se a página tiver vários vídeos, use Marcar na tela\n"
                                  "sobre o vídeo que você quer: eu comparo a imagem e baixo aquele.",
                 font=(FONTE, 10), bg=C["fundo"], fg=C["fraco"], justify="center").pack(pady=6)

        # ---- rodapé
        tk.Frame(r, bg=C["borda"], height=1).pack(fill="x", side="bottom")
        self.lbl_rodape = tk.Label(r, text="", font=(FONTE, 9), bg=C["painel"], fg=C["fraco"], anchor="w", padx=22, pady=6)
        self.lbl_rodape.pack(fill="x", side="bottom", before=corpo)
        for tid in self.ordem:
            velho = self.cartoes_antigos.get(tid)
            if velho:
                self.cartoes[tid] = Cartao(self, velho.t)
        self._mostrar_vazio()
        self.rodape()
        self._contar()

    def _placeholder(self, mostrar: bool):
        txt = self.caixa.get("1.0", "end").strip()
        if mostrar and not txt:
            self.caixa.insert("1.0", PLACEHOLDER)
            self.caixa.configure(fg=C["fraco"])
            self._ph = True
        elif not mostrar and getattr(self, "_ph", False):
            self.caixa.delete("1.0", "end")
            self.caixa.configure(fg=C["texto"])
            self._ph = False

    def _texto_caixa(self) -> str:
        return "" if getattr(self, "_ph", False) else self.caixa.get("1.0", "end")

    def _contar(self):
        n = len(extrair_links(self._texto_caixa()))
        self.lbl_contagem.configure(text=(f"{n} link{'s' if n != 1 else ''} encontrado{'s' if n != 1 else ''}" if n else ""))

    def _guardar(self, k, v):
        self.cfg[k] = v
        salvar_config(self.cfg)

    def _pintar_area(self):
        for w in self.faixa_area.winfo_children():
            w.destroy()
        if not self.area:
            return
        x, y, w, h = self.area
        f = tk.Frame(self.faixa_area, bg=C["painel2"], highlightthickness=1, highlightbackground=C["destaque"])
        f.pack(fill="x", pady=(10, 0))
        tk.Label(f, text=I["alvo"], font=(ICONES, 12), bg=C["painel2"], fg=C["destaque"]).pack(side="left", padx=(14, 8), pady=8)
        if self.capturas:
            from PIL import ImageTk
            im = self.capturas[0].copy()
            im.thumbnail((84, 48))
            self._foto_area = ImageTk.PhotoImage(im, master=f)
            tk.Label(f, image=self._foto_area, bg=C["painel2"]).pack(side="left", padx=(0, 10))
        tk.Label(f, text=f"Área marcada: {w}×{h} em ({x}, {y}).  Os próximos downloads usam esta imagem para achar o vídeo certo,"
                         " e \"Gravar área\" grava aqui.", font=(FONTE, 9), bg=C["painel2"], fg=C["texto"]).pack(side="left")
        b = Botao(f, "Tirar", I["x"], self.limpar_area, "fantasma", tamanho=9, fundo=C["painel2"])
        b.pack(side="right", padx=8)

    def _mostrar_vazio(self):
        if self.cartoes:
            self.vazio.pack_forget()
        else:
            self.vazio.pack(fill="x")

    def _pintar_ambiente(self, amb):
        self._amb = amb
        for w in self.status_amb.winfo_children():
            w.destroy()
        if not amb:
            Pilula(self.status_amb, "conferindo…", fundo=C["painel2"]).pack(side="left")
            return
        for nome, ok, dica in amb:
            p = Pilula(self.status_amb, ("● " if ok else "○ ") + nome, C["verde"] if ok else C["fraco"])
            p.pack(side="left", padx=3)
            Dica(p, dica)

    # ---------------------------------------------------------------- ações
    def trocar_tema(self):
        self.cfg["tema"] = "claro" if tema.escuro() else "escuro"
        salvar_config(self.cfg)
        tema.trocar_tema(self.cfg["tema"])
        texto = self._texto_caixa()
        self.montar()
        if texto.strip():
            self._placeholder(False)
            self.caixa.insert("1.0", texto.strip())
            self._contar()

    def favoritar(self):
        self._guardar("qualidade_padrao", self.q_var.get())
        self.toast(f"Qualidade padrão: {self.q_var.get()}")

    def colar(self):
        try:
            txt = self.raiz.clipboard_get()
        except Exception:
            return
        self._placeholder(False)
        self.caixa.insert("end", ("\n" if self._texto_caixa().strip() else "") + txt)
        self._contar()

    def limpar_caixa(self):
        self.caixa.delete("1.0", "end")
        self._ph = False
        self._placeholder(True)
        self._contar()

    def abrir_pasta(self):
        p = Path(self.cfg["pasta_destino"])
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(p))

    def baixar(self):
        links = extrair_links(self._texto_caixa())
        if not links:
            self.toast("Não achei nenhum link no texto colado.", erro=True)
            return
        self.adicionar(links)
        self.limpar_caixa()

    def adicionar(self, links: list[str], substituir: Cartao | None = None, **extra):
        self.cfg["_q"] = QUALIDADES.get(self.q_var.get(), 720)
        for u in links:
            t = Tarefa(url=u)
            t.capturas = list(self.capturas)
            for k, v in extra.items():
                setattr(t, k, v)
            if substituir is not None:
                i = self.ordem.index(substituir.t.id)
                substituir.destruir()
                del self.cartoes[substituir.t.id]
                self.ordem[i] = t.id
                substituir = None
                c = Cartao(self, t)
                # recoloca na mesma posição
                irmaos = [self.cartoes[x].f for x in self.ordem if x in self.cartoes]
                if irmaos:
                    c.f.pack_configure(before=irmaos[0]) if i == 0 else c.f.pack_configure(after=self.cartoes[self.ordem[i - 1]].f)
            else:
                self.ordem.append(t.id)
                c = Cartao(self, t)
            self.cartoes[t.id] = c
            self.trabalho.put(t)
        self._mostrar_vazio()
        self.rodape()

    def tirar_prontos(self):
        for tid in list(self.ordem):
            c = self.cartoes.get(tid)
            if c and c.t.estado in ("pronto", "cancelado"):
                c.destruir()
                del self.cartoes[tid]
                self.ordem.remove(tid)
        self._mostrar_vazio()
        self.rodape()

    def marcar_tela(self, depois=None):
        self.raiz.iconify()

        def fim(area):
            if not area:
                self.raiz.deiconify()
                return
            self.area = area

            def tirar():
                caps = []
                for _ in range(3):
                    try:
                        caps.append(capturar(area))
                    except Exception:
                        pass
                    time.sleep(0.45)
                self.capturas = caps
                self.na_janela(lambda: (self.raiz.deiconify(), self._pintar_area(), depois and depois()))
            threading.Thread(target=tirar, daemon=True).start()
        self.raiz.after(350, lambda: selecionar_area(self.raiz, fim))

    def limpar_area(self):
        self.area = None
        self.capturas = []
        self._pintar_area()

    def gravar_area(self):
        if self.gravador:
            return
        if not self.area:
            self.toast("Primeiro marque na tela a área do vídeo.")
            self.marcar_tela(depois=self.gravar_area)
            return
        if not achar_programa("ffmpeg"):
            self.toast("Falta o FFmpeg para gravar a tela.", erro=True)
            return
        self.gravador = GravadorArea(self.area, Path(self.cfg["pasta_destino"]))
        try:
            self.gravador.iniciar()
        except Exception as e:
            self.gravador = None
            self.toast(f"Não consegui gravar: {e}", erro=True)
            return
        self.raiz.iconify()
        self._janela_gravando()

    def _janela_gravando(self):
        x, y, w, h = self.area
        top = tk.Toplevel(self.raiz)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=C["painel"], highlightthickness=1, highlightbackground=C["vermelho"])
        ponto = tk.Label(top, text="●", font=(FONTE, 14), bg=C["painel"], fg=C["vermelho"])
        ponto.pack(side="left", padx=(12, 4), pady=6)
        rel = tk.Label(top, text="Gravando 00:00", font=(FONTE_FORTE, 10), bg=C["painel"], fg=C["texto"])
        rel.pack(side="left")
        som = tk.Label(top, text="", font=(FONTE, 8), bg=C["painel"], fg=C["suave"])
        som.pack(side="left", padx=8)
        Botao(top, "Parar", I["parar"], lambda: self._parar_gravacao(top), "perigo", tamanho=9).pack(side="left", padx=8, pady=6)
        top.update_idletasks()
        vx, vy, vw, vh = __import__("baixador.tela", fromlist=["tela_virtual"]).tela_virtual()
        px = min(x + w - top.winfo_reqwidth(), vx + vw - top.winfo_reqwidth())
        py = y - top.winfo_reqheight() - 8 if y - top.winfo_reqheight() - 8 > vy else y + h + 8
        if py + top.winfo_reqheight() > vy + vh:  # a área ocupa a tela toda: canto de cima, por cima
            py = vy + 4
        top.geometry(f"+{max(vx, px)}+{py}")
        ini = time.time()

        def tique():
            if not self.gravador:
                return
            s = int(time.time() - ini)
            rel.configure(text=f"Gravando {s // 60:02d}:{s % 60:02d}")
            ponto.configure(fg=C["vermelho"] if s % 2 == 0 else C["painel3"])
            som.configure(text=self.gravador.status_som)
            top.after(500, tique)
        tique()

    def _parar_gravacao(self, top):
        g = self.gravador
        top.destroy()
        self.raiz.deiconify()
        t = Tarefa(url="tela://area")
        t.titulo = "Gravação da área da tela"
        t.estado = "baixando"
        t.texto = "finalizando a gravação…"
        t.progresso = None
        self.ordem.append(t.id)
        self.cartoes[t.id] = Cartao(self, t)
        self._mostrar_vazio()

        def fim():
            arq = g.parar()
            self.gravador = None
            if arq:
                from .base import ffprobe_info
                inf = ffprobe_info(arq)
                t.arquivo, t.estado = str(arq), "pronto"
                t.texto = f"gravado: {inf.get('largura')}×{inf.get('altura')} · {int((inf.get('duracao') or 0) // 60)}:" \
                          f"{int((inf.get('duracao') or 0) % 60):02d} · {g.status_som}"
            else:
                t.estado, t.texto = "erro", "a gravação falhou: " + (g.erro or "?")
            self.ui_q.put(("tarefa", t.id))
        threading.Thread(target=fim, daemon=True).start()

    def nao_era_esse(self, cartao: Cartao):
        t = cartao.t
        outros = [c for c in t.candidatos if t.escolhido is None or c.grupo != t.escolhido.grupo]
        if not outros:
            if messagebox.askyesno("Não era esse", "Este link foi resolvido direto pelo site (não havia outros vídeos na lista).\n\n"
                                   "Quer que eu procure de novo na página inteira, abrindo no navegador escondido,\n"
                                   "e mostre tudo o que achar para você escolher?\n\n(O arquivo baixado vai para a Lixeira.)",
                                   parent=self.raiz):
                if t.arquivo and os.path.exists(t.arquivo):
                    para_lixeira(t.arquivo)
                self.adicionar([t.url], substituir=cartao, pular_ytdlp=True, sempre_perguntar=True)
            return
        self.janela_escolha(t, outros, titulo="Qual era o vídeo certo?",
                            ao_escolher=lambda c: self._refazer_com(cartao, c))

    def _refazer_com(self, cartao: Cartao, certo):
        t = cartao.t
        if certo is None:
            return
        self.motor.nao_era_esse(t, certo)
        if t.arquivo and os.path.exists(t.arquivo):
            para_lixeira(t.arquivo)
        self.adicionar([t.url], substituir=cartao, forcar=certo, pagina_url=t.pagina_url, candidatos=t.candidatos,
                       titulo=t.titulo, cookies_nav=t.cookies_nav)
        self.toast("Anotei o erro, mandei o arquivo errado para a Lixeira e estou baixando o certo.")

    # ---------------------------------------------------------------- escolha entre candidatos
    def _escolher_bloqueante(self, t: Tarefa, cands: list):
        """Chamado pela thread do download: pede à janela e espera a resposta."""
        ev = threading.Event()
        res = {"c": None}

        def responder(c):
            res["c"] = c
            ev.set()
        miniaturas = self._miniaturas_cands(t, cands[:6])
        self.ui_q.put(("escolher", (t, cands[:6], miniaturas, responder)))
        while not ev.wait(0.3):
            if t.cancel.is_set():
                return None
        return res["c"]

    def _miniaturas_cands(self, t, cands):
        from PIL import Image
        from .tela import quadros_do_endereco
        out = {}
        for c in cands:
            img = None
            if c.url in t.fotos:
                try:
                    img = Image.open(io.BytesIO(t.fotos[c.url])).convert("RGB")
                except Exception:
                    img = None
            if img is None and c.tipo in ("progressivo", "hls", "dash", "desconhecido"):
                q = quadros_do_endereco(c.url, c.headers or {"Referer": t.pagina_url or t.url}, n=1, limite_s=8)
                img = q[0] if q else None
            out[c.url] = img
        return out

    def janela_escolha(self, t: Tarefa, cands: list, miniaturas: dict | None = None, titulo="Qual destes é o vídeo?",
                       ao_escolher=None):
        top = tk.Toplevel(self.raiz)
        top.title(titulo)
        top.configure(bg=C["fundo"])
        top.transient(self.raiz)
        tema.aplicar_icone(top)
        tema.barra_titulo(top)
        tk.Label(top, text=titulo, font=(FONTE_FORTE, 14), bg=C["fundo"], fg=C["texto"]).pack(anchor="w", padx=20, pady=(16, 2))
        tk.Label(top, text=(t.titulo or t.url)[:110], font=(FONTE, 9), bg=C["fundo"], fg=C["suave"]).pack(anchor="w", padx=20)
        corpo = tk.Frame(top, bg=C["fundo"])
        corpo.pack(fill="both", expand=True, padx=20, pady=12)
        escolhido = {"feito": False}
        fotos = []

        def escolher(c):
            if escolhido["feito"]:
                return
            escolhido["feito"] = True
            top.destroy()
            if ao_escolher:
                ao_escolher(c)
        for c in cands:
            linha = tk.Frame(corpo, bg=C["painel"], highlightthickness=1, highlightbackground=C["borda"], cursor="hand2")
            linha.pack(fill="x", pady=4)
            mini = tk.Canvas(linha, width=128, height=72, bg=C["painel3"], highlightthickness=0)
            mini.pack(side="left", padx=10, pady=8)
            img = (miniaturas or {}).get(c.url)
            if img is not None:
                from PIL import ImageTk
                im = img.copy()
                im.thumbnail((128, 72))
                f = ImageTk.PhotoImage(im, master=mini)
                fotos.append(f)
                mini.create_image(64, 36, image=f)
            else:
                mini.create_text(64, 36, text=I["play"], font=(ICONES, 18), fill=C["fraco"])
            info = tk.Frame(linha, bg=C["painel"])
            info.pack(side="left", fill="x", expand=True)
            q = f"{c.altura}p" if c.altura else "qualidade ?"
            dur = c.ctx.get("duracao") or 0
            extra = f" · {int(dur // 60)}:{int(dur % 60):02d}" if dur else ""
            casou = t.casamento.get(c.url)
            tk.Label(info, text=f"{c.tipo.upper()} · {q}{extra} · chance {c.prob:.0%}" + (f" · tela {casou:.0%}" if casou is not None else ""),
                     font=(FONTE_FORTE, 10), bg=C["painel"], fg=C["texto"]).pack(anchor="w")
            tk.Label(info, text=c.url[:120], font=(MONO, 8), bg=C["painel"], fg=C["suave"]).pack(anchor="w")
            tk.Label(info, text="achado em: " + ", ".join(sorted(c.origens)), font=(FONTE, 8), bg=C["painel"], fg=C["fraco"]).pack(anchor="w")
            b = Botao(linha, "É este", I["ok"], lambda c=c: escolher(c), "primario", tamanho=9)
            b.pack(side="right", padx=12)
            for w in (linha, mini, info):
                w.bind("<Double-Button-1>", lambda e, c=c: escolher(c))
        rod = tk.Frame(top, bg=C["fundo"])
        rod.pack(fill="x", padx=20, pady=(0, 16))
        Botao(rod, "Nenhum destes", I["x"], lambda: escolher(None), "fantasma", tamanho=10, fundo=C["fundo"]).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", lambda: escolher(None))
        top._fotos = fotos  # type: ignore[attr-defined]
        top.update_idletasks()
        w, h = max(720, top.winfo_reqwidth()), min(top.winfo_reqheight(), 760)
        x = self.raiz.winfo_rootx() + (self.raiz.winfo_width() - w) // 2
        y = self.raiz.winfo_rooty() + 60
        top.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")
        top.grab_set()
        top.focus_force()
        return top

    # ---------------------------------------------------------------- detalhes
    def mostrar_detalhes(self, t: Tarefa):
        top = tk.Toplevel(self.raiz)
        top.title("Passo a passo")
        top.configure(bg=C["fundo"])
        tema.aplicar_icone(top)
        tema.barra_titulo(top)
        top.geometry("820x520")
        tk.Label(top, text=t.titulo or t.url, font=(FONTE_FORTE, 12), bg=C["fundo"], fg=C["texto"], wraplength=780,
                 justify="left").pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(top, text=t.url, font=(MONO, 8), bg=C["fundo"], fg=C["suave"]).pack(anchor="w", padx=16)
        txt = tk.Text(top, font=(MONO, 9), bg=C["entrada"], fg=C["texto"], relief="flat", padx=10, pady=8, wrap="word")
        txt.pack(fill="both", expand=True, padx=16, pady=12)
        linhas = list(t.logs)
        if t.candidatos:
            linhas.append("")
            linhas.append("candidatos (do mais provável ao menos):")
            for c in t.candidatos[:15]:
                linhas.append(f"  {c.prob:6.1%}  {c.resumo():40s}  {c.url[:110]}")
        if t.arquivo:
            linhas += ["", f"arquivo: {t.arquivo}", f"info: {t.info_arquivo}"]
        txt.insert("1.0", "\n".join(linhas))
        txt.configure(state="disabled")

    # ---------------------------------------------------------------- configurações
    def abrir_config(self):
        top = tk.Toplevel(self.raiz)
        top.title("Configurações")
        top.configure(bg=C["fundo"])
        top.transient(self.raiz)
        tema.aplicar_icone(top)
        tema.barra_titulo(top)

        def secao(titulo, sub=""):
            f = tk.Frame(top, bg=C["painel"], highlightthickness=1, highlightbackground=C["borda"])
            f.pack(fill="x", padx=18, pady=(12, 0))
            tk.Label(f, text=titulo, font=(FONTE_FORTE, 11), bg=C["painel"], fg=C["texto"]).pack(anchor="w", padx=14, pady=(10, 0))
            if sub:
                tk.Label(f, text=sub, font=(FONTE, 9), bg=C["painel"], fg=C["suave"], wraplength=600, justify="left").pack(anchor="w", padx=14)
            dentro = tk.Frame(f, bg=C["painel"])
            dentro.pack(fill="x", padx=14, pady=10)
            return dentro

        s = secao("Pasta dos vídeos")
        lbl = tk.Label(s, text=self.cfg["pasta_destino"], font=(FONTE, 9), bg=C["painel"], fg=C["texto"])
        lbl.pack(side="left")

        def escolher_pasta():
            p = filedialog.askdirectory(parent=top, initialdir=self.cfg["pasta_destino"])
            if p:
                self._guardar("pasta_destino", os.path.normpath(p))
                lbl.configure(text=self.cfg["pasta_destino"])
                self.lbl_pasta.configure(text=f"salvando em  {self.cfg['pasta_destino']}")
        Botao(s, "Escolher…", I["pasta"], escolher_pasta, "secundario", tamanho=9).pack(side="right")

        s = secao("Cookies do navegador (para vídeos que pedem login)",
                  "Use o navegador em que você está logado no site. O Opera e o Chrome trancam os cookies enquanto estão abertos: "
                  "feche por alguns segundos quando o download começar.")
        v_nav = tk.StringVar(value=self.cfg.get("cookies_navegador", "nenhum"))
        cb = ttk.Combobox(s, textvariable=v_nav, values=NAVEGADORES, state="readonly", width=14)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (self._guardar("cookies_navegador", v_nav.get()), setattr(self.motor, "_jar", None)))

        s = secao("Achar o vídeo", "Quando a página esconde o vídeo no JavaScript, abro ela no Microsoft Edge escondido, aperto play e "
                                  "escuto a rede (perfil limpo, sem as suas senhas).")
        v_nav2 = tk.BooleanVar(value=self.cfg.get("usar_navegador", True))
        Chave(s, "Usar o Edge escondido quando precisar", v_nav2, lambda v: self._guardar("usar_navegador", v)).pack(side="left")
        v_ass = tk.BooleanVar(value=self.cfg.get("assistir_gravar", True))
        Chave(s, "Assistir e gravar quando nada baixar", v_ass, lambda v: self._guardar("assistir_gravar", v),
              dica="Último recurso: toca o vídeo e grava quadro a quadro com o som, em tempo real. Se o site bloquear, "
                   "grava a janela do Edge (não cubra a janela; o som do PC entra junto).").pack(side="left", padx=(16, 0))
        tk.Label(s, text="Downloads ao mesmo tempo", font=(FONTE, 10), bg=C["painel"], fg=C["texto"]).pack(side="left", padx=(24, 6))
        v_sim = tk.StringVar(value=str(self.cfg.get("simultaneos", 2)))
        cb2 = ttk.Combobox(s, textvariable=v_sim, values=["1", "2", "3", "4"], state="readonly", width=3)
        cb2.pack(side="left")
        cb2.bind("<<ComboboxSelected>>", lambda e: self._guardar("simultaneos", int(v_sim.get())))

        s = secao("Rotas de conexão (furar bloqueio)",
                  "Ordem: conexão direta → cada proxy da lista → Tor trocando de país 3 vezes (se o Tor estiver rodando). "
                  "Só troca de rota quando o erro é de rede, de país ou de bloqueio.")
        v_tor = tk.BooleanVar(value=self.cfg.get("usar_tor", True))
        Chave(s, "Usar o Tor quando estiver ligado", v_tor, lambda v: self._guardar("usar_tor", v)).pack(anchor="w")
        tk.Label(s, text="Proxies, um por linha (ex.: socks5://127.0.0.1:1080  ou  http://usuario:senha@ip:porta)",
                 font=(FONTE, 9), bg=C["painel"], fg=C["suave"]).pack(anchor="w", pady=(8, 2))
        px = tk.Text(s, height=3, font=(MONO, 9), bg=C["entrada"], fg=C["texto"], insertbackground=C["texto"], relief="flat", padx=8, pady=6)
        px.pack(fill="x")
        px.insert("1.0", self.cfg.get("proxies", ""))
        res = tk.Label(s, text="", font=(MONO, 9), bg=C["painel"], fg=C["texto"], justify="left")

        def testar():
            res.configure(text="testando…")

            def t():
                linhas = ["1.1.1.1 alcançável: " + ("sim" if tcp_ok("1.1.1.1") else "NÃO (sem internet ou firewall)")]
                ip, pais = ip_publico()
                linhas.append(f"seu IP direto: {ip} ({pais})")
                if tor_ligado():
                    tor_novo_circuito()
                    tip, tp = ip_publico(TOR_SOCKS)
                    linhas.append(f"pelo Tor: {tip} ({tp})")
                else:
                    linhas.append("Tor: desligado")
                self.na_janela(lambda: res.configure(text="\n".join(linhas)))
            threading.Thread(target=t, daemon=True).start()
        lin = tk.Frame(s, bg=C["painel"])
        lin.pack(fill="x", pady=(8, 0))
        Botao(lin, "Testar rede", I["rede"], testar, "secundario", tamanho=9).pack(side="left")
        res.pack(anchor="w", pady=(6, 0))

        s = secao("O que ele já aprendeu (grafo de nós e arestas, no formato do cérebro)",
                  "Tudo o que ele aprende com o uso fica num grafo no contrato GEP V1 (E:\\NERAEL_GEP\\baixador.gep.sqlite): sites, players, formas de endereço, "
                  "motores, erros, cada download e o seu retorno. É esse pedaço que depois vai para o cérebro do Nerael.")
        g = self.motor.memoria.grafo.contagem()
        regioes = " · ".join(f"{k} {v}" for k, v in sorted(g["regioes"].items()))
        tk.Label(s, text=f"{g['nos']} nós · {g['arestas']} arestas · {self.motor.memoria.n_sites()} sites · "
                         f"{self.motor.modelo.n_atualizacoes} ajustes do modelo\n{regioes}",
                 font=(FONTE, 10), bg=C["painel"], fg=C["texto"], justify="left").pack(side="left")

        def exportar():
            from .base import DADOS_DIR
            destino = DADOS_DIR / "grafo_exportado"
            r = self.motor.memoria.grafo.exportar(destino)
            self.toast(f"Grafo exportado ({r['nos']} nós, {r['arestas']} arestas, {r['episodios']} episódios); "
                       f"validador do contrato: {'PASSOU' if r['passou'] else 'REPROVADO'}. Arquivo: {r['arquivo']}", erro=not r["passou"])
        Botao(s, "Exportar grafo", I["abrir"], exportar, "secundario", tamanho=9).pack(side="right")

        def fechar():
            self._guardar("proxies", px.get("1.0", "end").strip())
            top.destroy()
        rod = tk.Frame(top, bg=C["fundo"])
        rod.pack(fill="x", padx=18, pady=14)
        Botao(rod, "Fechar", I["ok"], fechar, "primario", tamanho=10, fundo=C["fundo"]).pack(side="right")
        top.protocol("WM_DELETE_WINDOW", fechar)
        top.update_idletasks()
        top.geometry(f"700x{min(top.winfo_reqheight(), 860)}+{self.raiz.winfo_rootx() + 80}+{self.raiz.winfo_rooty() + 30}")
        top.grab_set()

    # ---------------------------------------------------------------- avisos
    def toast(self, texto, erro=False):
        top = tk.Toplevel(self.raiz)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        cor = C["vermelho"] if erro else C["destaque"]
        f = tk.Frame(top, bg=C["painel3"], highlightthickness=1, highlightbackground=cor)
        f.pack()
        tk.Label(f, text=texto, font=(FONTE, 10), bg=C["painel3"], fg=C["texto"], padx=16, pady=10, wraplength=420).pack()
        top.update_idletasks()
        x = self.raiz.winfo_rootx() + self.raiz.winfo_width() - top.winfo_reqwidth() - 24
        y = self.raiz.winfo_rooty() + self.raiz.winfo_height() - top.winfo_reqheight() - 50
        top.geometry(f"+{x}+{y}")
        top.after(3800, top.destroy)

    def rodape(self):
        n = {"fila": 0, "trabalhando": 0, "pronto": 0, "erro": 0}
        for c in self.cartoes.values():
            e = c.t.estado
            n["trabalhando" if e in ("analisando", "baixando", "escolher") else e if e in n else "fila"] += 1
        partes = []
        if n["trabalhando"]:
            partes.append(f"{n['trabalhando']} em andamento")
        if n["fila"]:
            partes.append(f"{n['fila']} na fila")
        if n["pronto"]:
            partes.append(f"{n['pronto']} pronto{'s' if n['pronto'] > 1 else ''}")
        if n["erro"]:
            partes.append(f"{n['erro']} com problema")
        self.lbl_resumo.configure(text=" · ".join(partes))
        mem = self.motor.memoria.dados
        g = self.motor.memoria.grafo.contagem()
        self.lbl_rodape.configure(text=f"Aprendizado: grafo com {g['nos']} nós e {g['arestas']} arestas · {self.motor.memoria.n_sites()} sites · {self.motor.modelo.n_atualizacoes} ajustes com o seu uso"
                                       f"   ·   motores: yt-dlp, HTTP direto, ffmpeg, streamlink, Edge escondido")

    # ---------------------------------------------------------------- threads
    def na_janela(self, f):
        self.ui_q.put(("chamar", f))

    def _ciclo(self):
        try:
            mudou = False
            while True:
                tipo, dado = self.ui_q.get_nowait()
                if tipo == "tarefa":
                    c = self.cartoes.get(dado)
                    if c:
                        c.atualizar()
                        mudou = True
                elif tipo == "chamar":
                    try:
                        dado()
                    except Exception:
                        pass
                elif tipo == "escolher":
                    t, cands, minis, responder = dado
                    self.raiz.deiconify()
                    self.janela_escolha(t, cands, minis, ao_escolher=responder)
                elif tipo == "ambiente":
                    self._pintar_ambiente(dado)
        except queue.Empty:
            pass
        except tk.TclError:
            pass
        for c in self.cartoes.values():
            if c.t.estado in ("analisando", "baixando") and c.t.progresso is None:
                c.barra.animar()
        if 'mudou' in locals() and mudou:
            self.rodape()
        self.raiz.after(120, self._ciclo)

    def _operario(self):
        while True:
            t = self.trabalho.get()
            with self._cond:
                while self.ativos >= int(self.cfg.get("simultaneos", 2)):
                    self._cond.wait(0.5)
                self.ativos += 1
            try:
                if not t.cancel.is_set():
                    self.motor.processar(t)
                else:
                    t.estado, t.texto = "cancelado", "cancelado"
                    self.ui_q.put(("tarefa", t.id))
            finally:
                with self._cond:
                    self.ativos -= 1
                    self._cond.notify_all()

    def _checar_ambiente(self):
        ff = bool(achar_programa("ffmpeg"))
        amb = [("FFmpeg", ff, "Junta áudio e vídeo, converte para MP4 e grava a tela" if ff else "Falta o FFmpeg: rode o BAIXAR_VIDEO.bat"),
               ("Edge", navegador.disponivel(), "Navegador escondido para páginas que escondem o vídeo"),
               ("Tor", tor_ligado(), "Rota para trocar de país (opcional; só se o Tor estiver rodando)")]
        self.ui_q.put(("ambiente", amb))

    def _importar_v5(self):
        """Na primeira vez, traz o que a V5 aprendeu e a qualidade favorita dela."""
        if self.motor.memoria.dados.get("_importado_v5"):
            return
        # cópia do que a V5 aprendeu (a pasta da V5 foi apagada; o original está no backup do E:)
        arq = Path(__file__).resolve().parent / "memoria_v5.json"
        n = 0
        if arq.exists():
            n = self.motor.memoria.importar_v5(arq)
        self.motor.memoria.dados["_importado_v5"] = {"quando": time.strftime("%Y-%m-%d %H:%M"), "registros": n}
        self.motor.memoria._sujo = True
        self.motor.memoria.salvar()

    def fechar(self):
        ativos = [c for c in self.cartoes.values() if c.t.estado in ("analisando", "baixando", "escolher", "fila")]
        if ativos and not messagebox.askyesno("Sair", f"Há {len(ativos)} download(s) em andamento. Sair mesmo assim?\n"
                                              "(O que já baixou fica em .parciais e continua de onde parou na próxima vez.)",
                                              parent=self.raiz):
            return
        for c in ativos:
            c.t.cancel.set()
        if self.gravador:
            try:
                self.gravador.parar()
            except Exception:
                pass
        self.motor.memoria.salvar()
        self.motor.modelo.salvar()
        self.raiz.destroy()


def principal():
    ligar_dpi()
    ligar_doh()
    tema.identidade_windows()
    raiz = tk.Tk()
    raiz.tk.call("tk", "scaling", raiz.winfo_fpixels("1i") / 72.0)
    App(raiz)
    raiz.mainloop()
