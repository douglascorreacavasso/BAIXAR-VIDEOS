# -*- coding: utf-8 -*-
"""Selecionar a tela: você marca com o mouse a área onde o vídeo está tocando.

Serve para duas coisas:
 1) APONTAR O VÍDEO CERTO: quando a página tem vários vídeos, a imagem da área marcada é comparada com
    quadros de cada candidato (impressão digital dHash + cor); o que casa ganha força no modelo.
 2) GRAVAR A ÁREA (último recurso): quando nada consegue baixar (DRM, site fechado), grava aquela área da
    tela com o som do computador, em MP4.

O jeito de gravar o som do sistema (soundcard em loopback, preenchendo silêncio pelo relógio) foi copiado e
adaptado do OTA-001 (ota/devcapture.py), sem alterar o OTA.
"""
from __future__ import annotations

import ctypes
import io
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from .base import SEM_JANELA, achar_programa, caminho_livre, nome_seguro


def ligar_dpi():
    """Coordenadas do Tk = pixels reais (sem isso, em tela com zoom 125% a área sai deslocada)."""
    if sys.platform.startswith("win"):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def tela_virtual() -> tuple[int, int, int, int]:
    if sys.platform.startswith("win"):
        u = ctypes.windll.user32
        return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)
    return 0, 0, 1920, 1080


def capturar(area: tuple[int, int, int, int]):
    import mss
    from PIL import Image
    x, y, w, h = area
    with mss.mss() as s:
        g = s.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")


def selecionar_area(raiz, ao_terminar):
    """Abre a camada de seleção por cima de todas as telas. ao_terminar((x,y,w,h) | None)."""
    import tkinter as tk
    from PIL import Image, ImageEnhance, ImageTk
    vx, vy, vw, vh = tela_virtual()
    try:
        foto = capturar((vx, vy, vw, vh))
    except Exception:
        foto = Image.new("RGB", (vw, vh), (30, 30, 30))
    escura = ImageEnhance.Brightness(foto).enhance(0.45)
    top = tk.Toplevel(raiz)
    top.overrideredirect(True)
    top.geometry(f"{vw}x{vh}+{vx}+{vy}")
    top.attributes("-topmost", True)
    cv = tk.Canvas(top, width=vw, height=vh, highlightthickness=0, cursor="crosshair", bg="black")
    cv.pack(fill="both", expand=True)
    img_escura = ImageTk.PhotoImage(escura, master=top)
    cv.create_image(0, 0, image=img_escura, anchor="nw")
    cv._refs = [img_escura]  # type: ignore[attr-defined]
    dica = cv.create_text(vw // 2, 40, fill="#FFFFFF", font=("Segoe UI", 16, "bold"),
                          text="Arraste sobre o vídeo que está tocando  ·  Esc cancela")
    estado = {"ini": None, "ret": None, "clara": None, "ref": None}

    def mostrar(x0, y0, x1, y1):
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if estado["ret"]:
            cv.delete(estado["ret"])
        if estado["clara"]:
            cv.delete(estado["clara"])
        if x1 - x0 > 2 and y1 - y0 > 2:
            recorte = ImageTk.PhotoImage(foto.crop((x0, y0, x1, y1)), master=top)
            estado["ref"] = recorte
            estado["clara"] = cv.create_image(x0, y0, image=recorte, anchor="nw")
        estado["ret"] = cv.create_rectangle(x0, y0, x1, y1, outline="#4C9BFF", width=3)
        cv.itemconfigure(dica, text=f"{x1 - x0} × {y1 - y0}  ·  solte para confirmar  ·  Esc cancela")

    def apertou(e):
        estado["ini"] = (e.x, e.y)

    def arrastou(e):
        if estado["ini"]:
            mostrar(*estado["ini"], e.x, e.y)

    def soltou(e):
        if not estado["ini"]:
            return
        x0, y0 = estado["ini"]
        x0, x1 = sorted((x0, e.x))
        y0, y1 = sorted((y0, e.y))
        top.destroy()
        if x1 - x0 < 24 or y1 - y0 < 24:
            ao_terminar(None)
        else:
            ao_terminar((x0 + vx, y0 + vy, x1 - x0, y1 - y0))

    def cancelar(_e=None):
        top.destroy()
        ao_terminar(None)

    cv.bind("<ButtonPress-1>", apertou)
    cv.bind("<B1-Motion>", arrastou)
    cv.bind("<ButtonRelease-1>", soltou)
    top.bind("<Escape>", cancelar)
    cv.bind("<Button-3>", cancelar)
    top.focus_force()
    top.grab_set()
    return top


# ------------------------------------------------------------------ impressão digital da imagem
def dhash(img, n: int = 8) -> int:
    from PIL import Image
    g = img.convert("L").resize((n + 1, n), Image.Resampling.BILINEAR)
    px = list(g.getdata())
    bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (1 if px[y * (n + 1) + x] > px[y * (n + 1) + x + 1] else 0)
    return bits


def miniatura(img, n: int = 12):
    from PIL import Image
    return list(img.convert("RGB").resize((n, n), Image.Resampling.BILINEAR).getdata())


def _tirar_barras(img):
    """Corta bordas pretas (letterbox) para a área marcada e o quadro do vídeo ficarem comparáveis."""
    try:
        from PIL import ImageOps
        g = ImageOps.grayscale(img)
        caixa = g.point(lambda v: 255 if v > 18 else 0).getbbox()
        if caixa and (caixa[2] - caixa[0]) > img.width * 0.3 and (caixa[3] - caixa[1]) > img.height * 0.3:
            return img.crop(caixa)
    except Exception:
        pass
    return img


def semelhanca(a, b) -> float:
    """0..1: 1 = mesma imagem. Junta forma (dHash) e cor média por bloco."""
    a, b = _tirar_barras(a), _tirar_barras(b)
    ha, hb = dhash(a), dhash(b)
    forma = 1.0 - bin(ha ^ hb).count("1") / 64.0
    ma, mb = miniatura(a), miniatura(b)
    dif = sum(abs(p[i] - q[i]) for p, q in zip(ma, mb) for i in range(3)) / (len(ma) * 3 * 255.0)
    cor = max(0.0, 1.0 - dif * 2.2)
    return 0.55 * forma + 0.45 * cor


def quadros_do_endereco(url: str, headers: dict | None = None, n: int = 5, limite_s: float = 25.0) -> list:
    """Tira alguns quadros espalhados do vídeo (sem baixar ele inteiro) com o ffmpeg."""
    from PIL import Image
    ffmpeg = achar_programa("ffmpeg")
    ffprobe = achar_programa("ffprobe")
    if not ffmpeg:
        return []
    cab = []
    if headers:
        cab = ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    dur = 0.0
    if ffprobe:
        try:
            r = subprocess.run([ffprobe, "-v", "error", *cab, "-show_entries", "format=duration", "-of", "csv=p=0", url],
                               capture_output=True, text=True, timeout=12, creationflags=SEM_JANELA)
            dur = float((r.stdout or "0").strip().splitlines()[0])
        except Exception:
            dur = 0.0
    tempos = [0.5] if dur <= 1 else [max(0.2, dur * f) for f in (0.03, 0.2, 0.4, 0.6, 0.8)][:n]
    out = []
    t0 = time.time()
    for t in tempos:
        if time.time() - t0 > limite_s:
            break
        try:
            r = subprocess.run([ffmpeg, "-v", "error", *cab, "-ss", f"{t:.2f}", "-i", url, "-frames:v", "1",
                                "-vf", "scale=160:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                               capture_output=True, timeout=12, creationflags=SEM_JANELA)
            if r.stdout:
                out.append(Image.open(io.BytesIO(r.stdout)).convert("RGB"))
        except Exception:
            continue
    return out


def casar_com_tela(capturas: list, candidatos: list, fotos: dict | None = None, log=lambda t: None,
                   max_cands: int = 6) -> dict[str, float]:
    """Para cada candidato, a maior semelhança entre a área marcada e os quadros dele. Devolve {url: 0..1}."""
    from PIL import Image
    res: dict[str, float] = {}
    if not capturas:
        return res
    vistos_grupo = set()
    for c in candidatos:
        if len(res) >= max_cands:
            break
        if c.tipo in ("audio", "embed") or c.grupo in vistos_grupo:
            continue
        vistos_grupo.add(c.grupo)
        quadros = []
        if fotos and c.url in fotos:
            try:
                quadros.append(Image.open(io.BytesIO(fotos[c.url])).convert("RGB"))
            except Exception:
                pass
        quadros += quadros_do_endereco(c.url, c.headers or None)
        melhor = 0.0
        for a in capturas:
            for q in quadros:
                melhor = max(melhor, semelhanca(a, q))
        res[c.url] = round(melhor, 4)
        for outro in candidatos:  # o mesmo vídeo em outra qualidade recebe a mesma nota
            if outro.grupo == c.grupo and outro.url not in res:
                res[outro.url] = res[c.url]
        log(f"tela × {c.resumo()}: {melhor:.0%}")
    return res


# ------------------------------------------------------------------ gravar a área (último recurso)
class GravadorArea:
    def __init__(self, area: tuple[int, int, int, int], destino: Path, nome: str = "gravacao_da_tela", fps: int = 30,
                 com_som: bool = True):
        x, y, w, h = area
        self.area = (x, y, w - w % 2, h - h % 2)
        self.destino = Path(destino)
        self.nome = nome_seguro(nome)
        self.fps = fps
        self.com_som = com_som
        self.parar_ev = threading.Event()
        self.proc = None
        self.video_tmp = None
        self.audio_tmp = None
        self.t_video0 = None
        self.t_audio0 = None
        self.erro = ""
        self.status_som = "desligado"
        self._th_audio = None

    def iniciar(self):
        ffmpeg = achar_programa("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg não encontrado")
        tmp = self.destino / ".parciais"
        tmp.mkdir(parents=True, exist_ok=True)
        carimbo = time.strftime("%Y%m%d_%H%M%S")
        self.video_tmp = tmp / f"tela_{carimbo}.mkv"
        self.audio_tmp = tmp / f"som_{carimbo}.wav"
        x, y, w, h = self.area
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-stats", "-f", "gdigrab", "-framerate", str(self.fps),
               "-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}", "-draw_mouse", "0", "-i", "desktop",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-pix_fmt", "yuv420p", str(self.video_tmp)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     creationflags=SEM_JANELA)
        self.t_video0 = time.monotonic()
        threading.Thread(target=self._ler_stats, daemon=True).start()
        if self.com_som:
            self._th_audio = threading.Thread(target=self._som, daemon=True)
            self._th_audio.start()

    def _ler_stats(self):
        """Hora real do 1º quadro = quando o ffmpeg avisa o 1º frame menos o tempo desses quadros."""
        buf = b""
        while self.proc and self.proc.poll() is None:
            ch = self.proc.stderr.read(1)
            if not ch:
                break
            if ch in b"\r\n":
                linha = buf.decode("utf-8", "replace")
                buf = b""
                if linha.startswith("frame=") and self.t_video0 is not None and not getattr(self, "_ajustado", False):
                    try:
                        n = int(linha.split("=")[1].split()[0])
                        self.t_video0 = time.monotonic() - n / self.fps
                        self._ajustado = True
                    except Exception:
                        pass
                elif linha.strip() and not linha.startswith("frame="):
                    self.erro = linha.strip()[:200]
            else:
                buf += ch

    def _som(self):
        try:
            import numpy as np
            import soundcard as sc
        except Exception as e:
            self.status_som = f"sem som ({type(e).__name__})"
            return
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        taxa, bloco = 48000, 4800
        wav = wave.open(str(self.audio_tmp), "wb")
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(taxa)
        escritos = 0
        try:
            alto = sc.default_speaker()
            mic = sc.get_microphone(id=str(alto.name), include_loopback=True)
            with mic.recorder(samplerate=taxa, channels=2, blocksize=bloco) as rec:
                self.t_audio0 = time.monotonic()
                self.status_som = f"gravando o som de {alto.name}"
                while not self.parar_ev.is_set():
                    dados = rec.record(numframes=bloco)
                    if dados is None or len(dados) == 0:
                        continue
                    if dados.ndim == 1:
                        dados = np.stack([dados, dados], axis=1)
                    esperado = int((time.monotonic() - self.t_audio0) * taxa) - len(dados)
                    if esperado - escritos > taxa // 5:  # silêncio sem pacotes vira zeros
                        falta = esperado - escritos
                        wav.writeframes(b"\x00\x00\x00\x00" * falta)
                        escritos += falta
                    wav.writeframes((np.clip(dados[:, :2], -1, 1) * 32767).astype("<i2").tobytes())
                    escritos += len(dados)
        except Exception as e:
            self.status_som = f"sem som ({str(e)[:80]})"
        finally:
            wav.close()

    def parar(self) -> Path | None:
        self.parar_ev.set()
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._th_audio:
            self._th_audio.join(timeout=5)
        if not self.video_tmp or not self.video_tmp.exists() or self.video_tmp.stat().st_size < 1000:
            self.erro = self.erro or "a gravação não gerou vídeo"
            return None
        ffmpeg = achar_programa("ffmpeg")
        final = caminho_livre(self.destino, self.nome + time.strftime(" %Y-%m-%d %H-%M"), "mp4")
        tem_som = self.audio_tmp and self.audio_tmp.exists() and self.audio_tmp.stat().st_size > 48000
        if tem_som and self.t_audio0 and self.t_video0:
            atraso = self.t_audio0 - self.t_video0  # >0: o som começou depois do 1º quadro
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(self.video_tmp), "-itsoffset", f"{atraso:.3f}",
                   "-i", str(self.audio_tmp), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                   "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(final)]
        else:
            cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(self.video_tmp), "-c:v", "copy", "-movflags", "+faststart", str(final)]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=SEM_JANELA)
        if r.returncode != 0 or not final.exists():
            self.erro = (r.stderr or "")[-200:]
            return None
        for p in (self.video_tmp, self.audio_tmp):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        return final
