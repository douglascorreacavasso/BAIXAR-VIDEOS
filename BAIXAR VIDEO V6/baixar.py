# -*- coding: utf-8 -*-
"""Baixador de Vídeo V6 — abre a janela. Erros de partida vão para dados\erro_inicio.txt e aparecem numa caixa."""
import os
import sys
import traceback

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
os.chdir(AQUI)

if __name__ == "__main__":
    try:
        from baixador.app import principal
        principal()
    except Exception:
        txt = traceback.format_exc()
        os.makedirs(os.path.join(AQUI, "dados"), exist_ok=True)
        with open(os.path.join(AQUI, "dados", "erro_inicio.txt"), "w", encoding="utf-8") as f:
            f.write(txt)
        try:
            import tkinter.messagebox as mb
            mb.showerror("Baixador de Vídeo", "Não consegui abrir. O erro foi salvo em dados\\erro_inicio.txt\n\n" + txt[-800:])
        except Exception:
            print(txt)
