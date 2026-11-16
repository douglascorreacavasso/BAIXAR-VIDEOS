# -*- coding: utf-8 -*-
"""Teste da "Marcar na tela": mostra numa janela um quadro de um dos vídeos (em momento sorteado, com tarjas
pretas e tamanho diferente, como num player), captura a área da tela de verdade e vê se a comparação aponta o vídeo
certo entre os candidatos. Também grava 3 s da área e confere o arquivo.

Uso: .venv\\Scripts\\python.exe testes\\teste_tela.py pasta_com_videos [n]
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from baixador.base import ffprobe_info  # noqa: E402
from baixador.detector import Candidato  # noqa: E402
from baixador.tela import GravadorArea, capturar, casar_com_tela, ligar_dpi  # noqa: E402


def quadro(arq: Path, t: float):
    import io
    from PIL import Image
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(arq), "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True, timeout=30)
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def main():
    pasta = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    videos = [p for p in sorted(pasta.glob("*.mp4")) if ffprobe_info(p).get("duracao", 0) > 2][:6]
    ligar_dpi()
    raiz = tk.Tk()
    raiz.overrideredirect(True)
    raiz.attributes("-topmost", True)
    r = random.Random(7)
    res = []
    from PIL import ImageTk, Image
    for i in range(n):
        alvo = r.choice(videos)
        dur = ffprobe_info(alvo)["duracao"]
        img = quadro(alvo, r.uniform(0.5, max(0.6, dur - 0.5)))
        larg = r.choice([480, 640, 800])
        img.thumbnail((larg, larg))
        # tarjas pretas como em player com proporção diferente
        alt_janela = int(larg * 9 / 16) + r.choice([0, 0, 60])
        fundo = Image.new("RGB", (larg, max(alt_janela, img.height)), (0, 0, 0))
        fundo.paste(img, ((larg - img.width) // 2, (fundo.height - img.height) // 2))
        foto = ImageTk.PhotoImage(fundo, master=raiz)
        x, y = 200 + r.randint(0, 200), 150 + r.randint(0, 100)
        for w in raiz.winfo_children():
            w.destroy()
        tk.Label(raiz, image=foto, bd=0).pack()
        raiz.geometry(f"{fundo.width}x{fundo.height}+{x}+{y}")
        raiz.update()
        time.sleep(0.4)
        raiz.update()
        cap = capturar((x, y, fundo.width, fundo.height))
        cands = []
        for k, v in enumerate(videos):
            c = Candidato(url=str(v), tipo="progressivo", ordem=k + 1, grupo=str(v))
            cands.append(c)
        notas = casar_com_tela([cap], cands)
        melhor = max(notas, key=notas.get)
        res.append({"alvo": alvo.name, "escolhido": Path(melhor).name, "ok": melhor == str(alvo),
                    "nota_alvo": notas[str(alvo)], "2a_nota": sorted(notas.values())[-2]})
        print(res[-1], flush=True)
    # gravação de 3 s da última área
    g = GravadorArea((x, y, fundo.width, fundo.height), AQUI / "saida" / "gravacao", "teste_gravacao")
    g.iniciar()
    t0 = time.time()
    while time.time() - t0 < 3.2:
        raiz.update()
        time.sleep(0.05)
    arq = g.parar()
    info = ffprobe_info(arq) if arq else {}
    raiz.destroy()
    resumo = {"acertos": sum(x["ok"] for x in res), "total": len(res), "gravacao": str(arq), "gravacao_info": info,
              "som": g.status_som, "erro_gravacao": g.erro, "casos": res}
    (AQUI / "saida" / "teste_tela.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in resumo.items() if k != "casos"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
