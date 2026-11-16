# -*- coding: utf-8 -*-
"""Temas escuro e claro, ícone do programa e barra de título escura.
Mesmo jeito do OTA-001 (ota/theme.py): um dicionário C trocado no lugar e cores só aqui. Adaptado, sem alterar o OTA."""
from __future__ import annotations

import ctypes
import sys

from .base import DADOS_DIR

ESCURO = {
    "fundo": "#0D1117", "painel": "#151B23", "painel2": "#1C2430", "painel3": "#243041", "borda": "#2C394A",
    "texto": "#E6EDF3", "suave": "#9AA7B6", "fraco": "#66758A", "destaque": "#4C9BFF", "destaque_hover": "#6DB0FF",
    "destaque_txt": "#06101C", "verde": "#3DD68C", "amarelo": "#F2B84B", "vermelho": "#F06A6A", "roxo": "#B48CFF",
    "trilho": "#243041", "selecao": "#27486E", "entrada": "#0F151D", "sombra": "#0A0E13",
}
CLARO = {
    "fundo": "#EEF2F8", "painel": "#FFFFFF", "painel2": "#F4F7FB", "painel3": "#E4EAF3", "borda": "#D3DCE8",
    "texto": "#14243A", "suave": "#55677F", "fraco": "#8A99AD", "destaque": "#2563D9", "destaque_hover": "#3B78EA",
    "destaque_txt": "#FFFFFF", "verde": "#13864F", "amarelo": "#B7791F", "vermelho": "#C8464A", "roxo": "#7046C8",
    "trilho": "#E1E8F2", "selecao": "#CFE0F7", "entrada": "#FAFBFD", "sombra": "#D9E0EA",
}
TEMAS = {"escuro": ESCURO, "claro": CLARO}
C = dict(ESCURO)
ATUAL = {"nome": "escuro"}
FONTE = "Segoe UI"
FONTE_FORTE = "Segoe UI Semibold"
MONO = "Consolas"
ICONES = "Segoe MDL2 Assets"
I = {"baixar": "", "pasta": "", "config": "", "sol": "", "lua": "", "alvo": "",
     "gravar": "", "parar": "", "ok": "", "x": "", "repetir": "", "info": "",
     "play": "", "estrela": "", "colar": "", "limpar": "", "lista": "", "rede": "",
     "cerebro": "", "abrir": ""}

ICONE_ICO = DADOS_DIR / "icone.ico"
ICONE_PNG = DADOS_DIR / "icone.png"


def trocar_tema(nome: str) -> str:
    nome = nome if nome in TEMAS else "escuro"
    C.clear()
    C.update(TEMAS[nome])
    ATUAL["nome"] = nome
    return nome


def escuro() -> bool:
    return ATUAL["nome"] == "escuro"


def _desenhar_icone(tam: int):
    from PIL import Image, ImageDraw
    s = tam * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s * 0.05
    # quadrado arredondado com degradê azul -> roxo
    for i in range(int(s - 2 * pad)):
        t = i / (s - 2 * pad)
        cor = (int(37 + (124 - 37) * t), int(99 + (92 - 99) * t), int(235 + (246 - 235) * t), 255)
        d.line([(pad, pad + i), (s - pad, pad + i)], fill=cor)
    mascara = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mascara).rounded_rectangle([pad, pad, s - pad, s - pad], radius=s * 0.24, fill=255)
    img.putalpha(mascara)
    d = ImageDraw.Draw(img)
    # triângulo de play + seta para baixo + base
    cx = s / 2
    d.polygon([(cx - s * 0.13, s * 0.22), (cx - s * 0.13, s * 0.50), (cx + s * 0.13, s * 0.36)], fill=(255, 255, 255, 255))
    d.rectangle([cx - s * 0.035, s * 0.52, cx + s * 0.035, s * 0.66], fill=(255, 255, 255, 235))
    d.polygon([(cx - s * 0.12, s * 0.62), (cx + s * 0.12, s * 0.62), (cx, s * 0.76)], fill=(255, 255, 255, 235))
    d.rounded_rectangle([s * 0.24, s * 0.79, s * 0.76, s * 0.84], radius=s * 0.02, fill=(255, 255, 255, 200))
    return img.resize((tam, tam), Image.Resampling.LANCZOS)


def garantir_icone():
    try:
        if not ICONE_ICO.exists() or not ICONE_PNG.exists():
            g = _desenhar_icone(256)
            g.save(ICONE_PNG)
            g.save(ICONE_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception:
        pass


def identidade_windows():
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("doug.BaixadorDeVideo.V6")
        except Exception:
            pass


def aplicar_icone(win):
    garantir_icone()
    try:
        if ICONE_ICO.exists():
            win.iconbitmap(default=str(ICONE_ICO))
    except Exception:
        pass


def imagem_icone(tam: int, master):
    from PIL import ImageTk
    foto = ImageTk.PhotoImage(_desenhar_icone(tam), master=master)
    return foto


def barra_titulo(win):
    """Barra de título escura/clara no Windows 10 1809+ e 11 (igual ao OTA)."""
    if not sys.platform.startswith("win"):
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1 if escuro() else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                break
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)
    except Exception:
        pass


def estilo_ttk(raiz):
    from tkinter import ttk
    st = ttk.Style(raiz)
    st.theme_use("clam")
    st.configure(".", background=C["painel"], foreground=C["texto"], fieldbackground=C["entrada"], bordercolor=C["borda"],
                 lightcolor=C["painel"], darkcolor=C["painel"], troughcolor=C["painel2"], focuscolor=C["destaque"], font=(FONTE, 10))
    st.configure("TCombobox", fieldbackground=C["entrada"], background=C["painel3"], foreground=C["texto"], arrowcolor=C["texto"],
                 padding=5, borderwidth=1)
    st.map("TCombobox", fieldbackground=[("readonly", C["entrada"])], foreground=[("readonly", C["texto"])],
           selectbackground=[("readonly", C["entrada"])], selectforeground=[("readonly", C["texto"])],
           bordercolor=[("focus", C["destaque"])])
    st.configure("Vertical.TScrollbar", background=C["painel3"], troughcolor=C["fundo"], arrowcolor=C["suave"], borderwidth=0,
                 gripcount=0, relief="flat")
    st.map("Vertical.TScrollbar", background=[("active", C["borda"])])
    raiz.option_add("*TCombobox*Listbox.background", C["painel2"])
    raiz.option_add("*TCombobox*Listbox.foreground", C["texto"])
    raiz.option_add("*TCombobox*Listbox.selectBackground", C["selecao"])
    raiz.option_add("*TCombobox*Listbox.selectForeground", C["texto"])
    raiz.option_add("*TCombobox*Listbox.font", (FONTE, 10))
    return st
