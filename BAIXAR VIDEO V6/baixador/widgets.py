# -*- coding: utf-8 -*-
"""Peças de interface desenhadas à mão (Tk puro): botão arredondado, chave liga/desliga, barra de progresso,
etiqueta tipo pílula e área com rolagem. Todas leem as cores de tema.C."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from .tema import C, FONTE, FONTE_FORTE, ICONES


def _ret_arredondado(cv, x0, y0, x1, y1, r, **kw):
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r,
           x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class Botao(tk.Canvas):
    """Botão arredondado com ícone opcional. tipo: primario | secundario | fantasma | perigo | sucesso."""

    def __init__(self, mestre, texto="", icone="", comando=None, tipo="secundario", tamanho=10, altura=None,
                 largura=None, raio=9, fundo=None, dica=""):
        self.fundo_pai = fundo or C["painel"]
        self.f_txt = tkfont.Font(family=FONTE_FORTE if tipo == "primario" else FONTE, size=tamanho)
        self.f_ico = tkfont.Font(family=ICONES, size=tamanho)
        larg_txt = self.f_txt.measure(texto) if texto else 0
        larg_ico = self.f_ico.measure(icone) if icone else 0
        espaco = 8 if (texto and icone) else 0
        alt = altura or int(tamanho * 3.3)
        larg = largura or (larg_txt + larg_ico + espaco + int(tamanho * 2.6))
        super().__init__(mestre, width=larg, height=alt, highlightthickness=0, bd=0, bg=self.fundo_pai, cursor="hand2")
        self.texto, self.icone, self.comando, self.tipo, self.raio = texto, icone, comando, tipo, raio
        self.larg, self.alt, self.esp, self.l_ico, self.l_txt = larg, alt, espaco, larg_ico, larg_txt
        self.ativo = True
        self.sobre = False
        self.dica = dica
        self._desenhar()
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._clique)
        if dica:
            Dica(self, dica)

    def _cores(self):
        t = self.tipo
        if not self.ativo:
            return C["painel2"], C["fraco"], C["borda"]
        if t == "primario":
            return (C["destaque_hover"] if self.sobre else C["destaque"]), C["destaque_txt"], None
        if t == "perigo":
            return (C["vermelho"] if self.sobre else C["painel2"]), (C["destaque_txt"] if self.sobre else C["vermelho"]), C["borda"]
        if t == "sucesso":
            return (C["verde"] if self.sobre else C["painel2"]), (C["destaque_txt"] if self.sobre else C["verde"]), C["borda"]
        if t == "fantasma":
            return (C["painel3"] if self.sobre else self.fundo_pai), C["texto"] if self.sobre else C["suave"], None
        return (C["painel3"] if self.sobre else C["painel2"]), C["texto"], C["borda"]

    def _desenhar(self):
        self.delete("all")
        fundo, txt, borda = self._cores()
        _ret_arredondado(self, 1, 1, self.larg - 1, self.alt - 1, self.raio, fill=fundo, outline=borda or fundo)
        total = self.l_ico + self.esp + self.l_txt
        x = (self.larg - total) / 2
        if self.icone:
            self.create_text(x + self.l_ico / 2, self.alt / 2 + 1, text=self.icone, font=self.f_ico, fill=txt)
            x += self.l_ico + self.esp
        if self.texto:
            self.create_text(x + self.l_txt / 2, self.alt / 2, text=self.texto, font=self.f_txt, fill=txt)

    def _hover(self, v):
        self.sobre = v and self.ativo
        self._desenhar()

    def _clique(self, e):
        if self.ativo and self.comando and 0 <= e.x <= self.larg and 0 <= e.y <= self.alt:
            self.comando()

    def habilitar(self, v: bool):
        self.ativo = v
        self.configure(cursor="hand2" if v else "arrow")
        self._desenhar()

    def mudar(self, texto=None, icone=None, tipo=None):
        if texto is not None:
            self.texto = texto
        if icone is not None:
            self.icone = icone
        if tipo is not None:
            self.tipo = tipo
        self._desenhar()


class Chave(tk.Frame):
    """Chave liga/desliga com texto ao lado."""

    def __init__(self, mestre, texto, variavel: tk.BooleanVar, ao_mudar=None, fundo=None, dica=""):
        fundo = fundo or C["painel"]
        super().__init__(mestre, bg=fundo, cursor="hand2")
        self.var = variavel
        self.ao_mudar = ao_mudar
        self.cv = tk.Canvas(self, width=38, height=22, highlightthickness=0, bg=fundo, cursor="hand2")
        self.cv.pack(side="left")
        self.lbl = tk.Label(self, text=texto, bg=fundo, fg=C["texto"], font=(FONTE, 10), cursor="hand2")
        self.lbl.pack(side="left", padx=(6, 0))
        for w in (self, self.cv, self.lbl):
            w.bind("<Button-1>", self._trocar)
        self._desenhar()
        if dica:
            Dica(self, dica)

    def _desenhar(self):
        cv = self.cv
        cv.delete("all")
        on = bool(self.var.get())
        _ret_arredondado(cv, 1, 2, 37, 20, 9, fill=C["destaque"] if on else C["trilho"], outline=C["destaque"] if on else C["borda"])
        x = 27 if on else 11
        cv.create_oval(x - 7, 4, x + 7, 18, fill="#FFFFFF" if on else C["suave"], outline="")

    def _trocar(self, _e=None):
        self.var.set(not self.var.get())
        self._desenhar()
        if self.ao_mudar:
            self.ao_mudar(self.var.get())


class Progresso(tk.Canvas):
    def __init__(self, mestre, largura=300, altura=6, fundo=None):
        super().__init__(mestre, height=altura, highlightthickness=0, bg=fundo or C["painel"])
        self.alt = altura
        self.valor: float | None = 0.0
        self.cor = C["destaque"]
        self._fase = 0
        self.bind("<Configure>", lambda e: self._desenhar())

    def definir(self, valor: float | None, cor: str | None = None):
        self.valor = valor
        if cor:
            self.cor = cor
        self._desenhar()

    def animar(self):
        """Faixa andando quando não se sabe a porcentagem."""
        if self.valor is None:
            self._fase = (self._fase + 6) % 1000
            self._desenhar()

    def _desenhar(self):
        self.delete("all")
        w = max(self.winfo_width(), 10)
        h = self.alt
        _ret_arredondado(self, 0, 0, w, h, h / 2, fill=C["trilho"], outline=C["trilho"])
        if self.valor is None:
            seg = w * 0.25
            x = (self._fase / 1000) * (w + seg) - seg
            x0, x1 = max(0, x), min(w, x + seg)
            if x1 - x0 > 2:
                _ret_arredondado(self, x0, 0, x1, h, h / 2, fill=self.cor, outline=self.cor)
        elif self.valor > 0:
            x1 = max(h, w * min(self.valor, 100) / 100)
            _ret_arredondado(self, 0, 0, x1, h, h / 2, fill=self.cor, outline=self.cor)


class Pilula(tk.Label):
    def __init__(self, mestre, texto, cor=None, fundo=None):
        super().__init__(mestre, text=texto, font=(FONTE, 9), fg=cor or C["suave"], bg=fundo or C["painel2"], padx=8, pady=1)


class Rolagem(tk.Frame):
    """Lista com rolagem pela roda do mouse (só quando o mouse está em cima)."""

    def __init__(self, mestre, fundo=None):
        fundo = fundo or C["fundo"]
        super().__init__(mestre, bg=fundo)
        from tkinter import ttk
        self.cv = tk.Canvas(self, bg=fundo, highlightthickness=0, bd=0)
        self.barra = ttk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.dentro = tk.Frame(self.cv, bg=fundo)
        self._janela = self.cv.create_window((0, 0), window=self.dentro, anchor="nw")
        self.cv.configure(yscrollcommand=self.barra.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.dentro.bind("<Configure>", lambda e: self._medir())
        self.cv.bind("<Configure>", lambda e: (self.cv.itemconfigure(self._janela, width=e.width), self._medir()))
        self.bind_all("<MouseWheel>", self._roda, add="+")

    def _medir(self):
        self.cv.configure(scrollregion=self.cv.bbox("all"))
        precisa = self.dentro.winfo_reqheight() > self.cv.winfo_height()
        if precisa and not self.barra.winfo_ismapped():
            self.barra.pack(side="right", fill="y")
        elif not precisa and self.barra.winfo_ismapped():
            self.barra.pack_forget()
            self.cv.yview_moveto(0)

    def _roda(self, e):
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        while w is not None:
            if w is self:
                if self.dentro.winfo_height() > self.cv.winfo_height():
                    self.cv.yview_scroll(int(-e.delta / 120), "units")
                return
            w = getattr(w, "master", None)


class Dica:
    """Balão de ajuda ao parar o mouse em cima."""

    def __init__(self, w, texto):
        self.w, self.texto, self.top, self._id = w, texto, None, None
        w.bind("<Enter>", self._agendar, add="+")
        w.bind("<Leave>", self._esconder, add="+")
        w.bind("<ButtonPress>", self._esconder, add="+")

    def _agendar(self, _e=None):
        self._id = self.w.after(550, self._mostrar)

    def _mostrar(self):
        if self.top:
            return
        x = self.w.winfo_rootx() + 10
        y = self.w.winfo_rooty() + self.w.winfo_height() + 6
        self.top = tk.Toplevel(self.w)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        tk.Label(self.top, text=self.texto, bg=C["painel3"], fg=C["texto"], font=(FONTE, 9), padx=10, pady=6,
                 justify="left", wraplength=320, bd=0).pack()
        self.top.geometry(f"+{x}+{y}")

    def _esconder(self, _e=None):
        if self._id:
            try:
                self.w.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
        if self.top:
            self.top.destroy()
            self.top = None
