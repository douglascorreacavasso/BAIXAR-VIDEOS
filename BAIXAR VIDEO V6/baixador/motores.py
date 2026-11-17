# -*- coding: utf-8 -*-
"""Motores de download: yt-dlp, HTTP direto (com retomada), ffmpeg (HLS/DASH) e streamlink.
Cada motor devolve o caminho do arquivo baixado ou levanta ErroMotor com a CATEGORIA do erro, para o
pipeline decidir: tentar outro formato, outro motor, outra rota, pedir cookies... (na V5 o mesmo erro de
extração era repetido nas 7 estratégias de formato; aqui cada categoria tem sua saída)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from .base import SEM_JANELA, achar_programa, caminho_livre, ffprobe_info, nome_seguro, rodar

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


class Cancelado(Exception):
    pass


class ErroMotor(Exception):
    def __init__(self, categoria: str, msg: str):
        super().__init__(msg)
        self.categoria = categoria
        self.msg = msg


@dataclass
class Contexto:
    destino: Path
    q: int = 720
    proxy: str | None = None
    cookies_navegador: str = "nenhum"
    compat: bool = True
    cancel: threading.Event = field(default_factory=threading.Event)
    progresso: Callable[[float | None, str], None] = lambda p, t: None
    log: Callable[[str], None] = lambda t: None
    tempo_limite: int = 3 * 3600

    def checar(self):
        if self.cancel.is_set():
            raise Cancelado("cancelado")


# ------------------------------------------------------------------ erros
_CATEGORIAS = [
    ("ffmpeg", r"ffmpeg not found|ffprobe not found|ffmpeg is not installed|ffmpeg não encontrado"),
    ("servidor_morto", r"servidor que não existe mais"),
    ("drm", r"drm protected|\bdrm\b|widevine|playready|fairplay|encrypted media"),
    ("cookies_travados", r"could not copy .*cookie|cookie database|database is locked|failed to decrypt|cookies? .*(?:lock|perm)"),
    ("rede", r"getaddrinfo failed|failed to resolve|name or service not known|temporary failure in name|"
             r"connection refused|network is unreachable|unable to connect to proxy|tunnel connection failed|"
             r"no route to host|certificate verify failed|winerror 10060|winerror 10061|winerror 10051|"
             r"connection reset|remote end closed|read timed out|connect timeout|timed out"),
    ("geo", r"geo.?restrict|not available in your country|this video is not available in your|geoblock|in your region"),
    ("login", r"login required|sign in to confirm|log in|cookies? (?:are )?(?:needed|required)|private video|"
              r"this video is private|age.?restricted|confirm your age|members.only|requires authentication|"
              r"use --cookies|registered users|account|you need to log"),
    ("removido", r"video unavailable|has been removed|no longer available|does not exist|deleted|not found|http error 404|"
                 r"this content isn.t available|content is not available|http error 410"),
    ("bloqueio", r"http error 403|forbidden|cloudflare|captcha|access denied|http error 429|too many requests|"
                 r"rate.?limit|unable to download webpage: http error 5\d\d"),
    ("formato", r"requested format is not available|no video formats found|format .* not available|"
                r"requested merging|merge|postprocessing|conversion failed|ffmpeg exited"),
    ("nao_suportado", r"unsupported url|unable to extract|no video could be found|did not find any|no media found|"
                      r"is not a valid url|generic|unable to recognize"),

]


def classificar_erro(msg: str) -> str:
    m = (msg or "").lower()
    for cat, rx in _CATEGORIAS:
        if re.search(rx, m):
            return cat
    return "outro"


# ------------------------------------------------------------------ yt-dlp
# A qualidade escolhida entra pelo format_sort "res:Q" (o yt-dlp mede pelo LADO MENOR: vídeo em pé 720x1280 conta
# como 720p). Filtrar por altura (como na V5) baixava 1280p em reels quando o pedido era 720p.
ESTRATEGIAS = [  # mesmos nomes da V5 (a memória dela continua valendo)
    ("mp4_prioritario", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", {}),
    ("melhor_qualquer", "bv*+ba/b", {}),
    ("unico_melhor", "b/bv*", {}),
    ("hls_nativo", "bv*+ba/b", {"hls_prefer_native": True}),
    ("hls_ffmpeg", "bv*+ba/b", {"hls_prefer_native": False}),
    ("generico_best", "best/bv*+ba", {}),
    ("ultimo_recurso", "worst/b/bv*", {}),
]


class _LogYtdlp:
    def __init__(self, ctx: Contexto):
        self.ctx = ctx
        self.avisos: list[str] = []

    def debug(self, msg):
        if msg.startswith("[download] Destination") or "Merging formats" in msg:
            self.ctx.log(msg.replace("[download] ", ""))

    def info(self, msg):
        pass

    def warning(self, msg):
        self.avisos.append(msg)

    def error(self, msg):
        self.avisos.append(msg)


def opcoes_ytdlp(ctx: Contexto, headers: dict | None = None, impersonar: bool = False, pasta_tmp: Path | None = None) -> dict:
    ffmpeg = achar_programa("ffmpeg")
    log = _LogYtdlp(ctx)

    def gancho(d):
        ctx.checar()
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = (d.get("downloaded_bytes", 0) / tot * 100) if tot else None
            vel = (d.get("_speed_str") or "").strip()
            ctx.progresso(pct, f"baixando {vel}".strip())
        elif d.get("status") == "finished":
            ctx.progresso(100.0, "juntando áudio e vídeo...")

    o = {
        "quiet": True, "no_warnings": False, "noplaylist": True, "logger": log,
        "socket_timeout": 25, "retries": 4, "fragment_retries": 12, "extractor_retries": 2,
        "concurrent_fragment_downloads": 4, "windowsfilenames": True, "trim_file_name": 150,
        "paths": {"home": str(ctx.destino), "temp": str(pasta_tmp or (ctx.destino / ".parciais"))},
        "outtmpl": "%(title).110B [%(id)s].%(ext)s", "merge_output_format": "mp4",
        "progress_hooks": [gancho], "geo_bypass": True, "overwrites": False, "continuedl": True,
        "http_headers": {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8", **(headers or {})},
    }
    if ffmpeg:
        o["ffmpeg_location"] = str(Path(ffmpeg).parent)
    if ctx.proxy:
        o["proxy"] = ctx.proxy
    if ctx.cookies_navegador and ctx.cookies_navegador != "nenhum":
        o["cookiesfrombrowser"] = (ctx.cookies_navegador,)
    if impersonar:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget
            o["impersonate"] = ImpersonateTarget("chrome")
        except Exception:
            pass
    if ctx.compat:
        # prefere H.264 + AAC (toca em qualquer aparelho) quando houver na mesma qualidade
        o["format_sort"] = [f"res:{ctx.q}", "vcodec:h264", "acodec:aac"]
    else:
        o["format_sort"] = [f"res:{ctx.q}"]
    o["_log"] = log
    return o


def ytdlp_info(url: str, ctx: Contexto, headers: dict | None = None, impersonar: bool = False) -> dict:
    import yt_dlp
    o = opcoes_ytdlp(ctx, headers, impersonar)
    log = o.pop("_log")
    o["noplaylist"] = True
    o["extract_flat"] = "in_playlist"
    try:
        with yt_dlp.YoutubeDL(o) as y:
            info = y.extract_info(url, download=False)
            return y.sanitize_info(info) if info else {}
    except Cancelado:
        raise
    except Exception as e:
        msg = _msg(e, log)
        raise ErroMotor(classificar_erro(msg), msg)


def ytdlp_baixar(url: str, ctx: Contexto, estrategia: tuple, headers: dict | None = None,
                 impersonar: bool = False, nome: str | None = None, checar_formatos: bool = False) -> Path:
    import yt_dlp
    sid, fmt, extra = estrategia
    o = opcoes_ytdlp(ctx, headers, impersonar)
    log = o.pop("_log")
    if nome:  # endereço cru de vídeo (master.m3u8...): o arquivo leva o título da página, com nome livre
        base = nome_seguro(nome)
        livre = caminho_livre(ctx.destino, base, "mp4").stem
        o["outtmpl"] = livre.replace("%", "%%") + ".%(ext)s"
    o["format"] = fmt.replace("{Q}", str(ctx.q))
    o.update(extra)
    if checar_formatos:  # pula formatos com DRM / quebrados testando cada um antes (mais lento)
        o["check_formats"] = True
    try:
        with yt_dlp.YoutubeDL(o) as y:
            info = y.extract_info(url, download=True)
            if info and info.get("_type") == "playlist":
                ents = [e for e in (info.get("entries") or []) if e]
                info = ents[0] if ents else info
            arquivos = [d.get("filepath") for d in (info.get("requested_downloads") or []) if d.get("filepath")]
            fp = arquivos[0] if arquivos else y.prepare_filename(info)
            if fp and not os.path.exists(fp):
                base = os.path.splitext(fp)[0]
                for ext in ("mp4", "mkv", "webm", "mov"):
                    if os.path.exists(f"{base}.{ext}"):
                        fp = f"{base}.{ext}"
                        break
            if not fp or not os.path.exists(fp):
                raise ErroMotor("formato", "yt-dlp terminou sem arquivo final")
            return Path(fp)
    except (Cancelado, ErroMotor):
        raise
    except Exception as e:
        if "cancelado" in str(e).lower() or ctx.cancel.is_set():
            raise Cancelado("cancelado")
        msg = _msg(e, log)
        raise ErroMotor(classificar_erro(msg), msg)


def _msg(e: Exception, log: _LogYtdlp) -> str:
    m = str(e).replace("ERROR: ", "").strip()
    m = re.sub(r"\x1b\[[0-9;]*m", "", m)
    extra = " | ".join(a for a in log.avisos[-3:] if a not in m)
    return (m + (" | " + extra if extra else ""))[:600]


# ------------------------------------------------------------------ HTTP direto
def baixar_direto(url: str, ctx: Contexto, nome: str, headers: dict | None = None, cookies=None) -> Path:
    """Download de arquivo de vídeo comum com retomada (Range), novas tentativas e checagem de que é vídeo."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "pt-BR,pt;q=0.9", **(headers or {})})
    if cookies:
        try:
            for ck in cookies:
                s.cookies.set(ck["name"], ck["value"], domain=ck.get("domain", ""), path=ck.get("path", "/"))
        except Exception:
            pass
    if ctx.proxy:
        s.proxies = {"http": ctx.proxy, "https": ctx.proxy}
    tmpdir = ctx.destino / ".parciais"
    tmpdir.mkdir(parents=True, exist_ok=True)
    parcial = tmpdir / (nome_seguro(nome, 90) + f".{abs(hash(url)) % 10**8}.part")
    feito = parcial.stat().st_size if parcial.exists() else 0
    total = None
    ext = "mp4"
    tentativas = 0
    inicio = time.time()
    while True:
        ctx.checar()
        h = {"Range": f"bytes={feito}-"} if feito else {}
        try:
            r = s.get(url, stream=True, timeout=(15, 40), headers=h, allow_redirects=True)
        except requests.RequestException as e:
            tentativas += 1
            m_host = re.search(r"host='([^']+)'", str(e))
            if m_host and re.search(r"NameResolution|getaddrinfo|Failed to resolve|Max retries", str(e), re.I):
                from .rede import servidor_existe
                if servidor_existe(m_host.group(1), ctx.proxy) is False:
                    raise ErroMotor("servidor_morto", f"o site manda o vídeo para {m_host.group(1)}, servidor que não existe mais "
                                                      f"(o DNS do próprio domínio responde que ele não existe)")
            if tentativas > 6:
                raise ErroMotor(classificar_erro(str(e)) if classificar_erro(str(e)) != "outro" else "rede", str(e)[:300])
            time.sleep(min(2 ** tentativas, 20))
            continue
        if r.status_code == 429 or r.status_code >= 500:
            tentativas += 1
            if tentativas > 6:
                raise ErroMotor("bloqueio", f"HTTP {r.status_code} repetido")
            espera = r.headers.get("Retry-After", "")
            time.sleep(min(int(espera) if espera.isdigit() else 2 ** tentativas, 30))
            continue
        if r.status_code == 416 and feito:  # já estava completo
            break
        if r.status_code in (401, 403):
            raise ErroMotor("bloqueio", f"HTTP {r.status_code} (o site recusou; talvez precise de Referer/cookies)")
        if r.status_code == 404 or r.status_code == 410:
            raise ErroMotor("removido", f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise ErroMotor("outro", f"HTTP {r.status_code}")
        ct = r.headers.get("Content-Type", "").lower()
        if r.status_code == 200 and feito:
            feito = 0  # servidor ignorou o Range: recomeça
            parcial.unlink(missing_ok=True)
        it = r.iter_content(chunk_size=256 * 1024)
        try:
            primeiro = next(it, b"")
        except requests.RequestException:
            primeiro = b""
        if feito == 0:
            cab = primeiro[:600].lstrip().lower()
            if ct.startswith("text/html") or cab.startswith((b"<!doctype html", b"<html")) or (b"<html" in cab[:200]):
                raise ErroMotor("nao_suportado", "o endereço devolveu uma página, não um vídeo")
            if cab.startswith(b"#extm3u"):
                raise ErroMotor("formato", "é uma lista HLS (m3u8), não um arquivo direto")
            ext = _ext_por_assinatura(primeiro, ct, url)
        cl = r.headers.get("Content-Length")
        cr = r.headers.get("Content-Range", "")
        if cr and "/" in cr and cr.split("/")[-1].isdigit():
            total = int(cr.split("/")[-1])
        elif cl and cl.isdigit():
            total = int(cl) + (feito if r.status_code == 206 else 0)
        ultimo_aviso = 0.0
        try:
            with open(parcial, "ab") as f:
                if primeiro:
                    f.write(primeiro)
                    feito += len(primeiro)
                for bloco in it:
                    ctx.checar()
                    if not bloco:
                        continue
                    f.write(bloco)
                    feito += len(bloco)
                    agora = time.time()
                    if agora - ultimo_aviso > 0.4:
                        ultimo_aviso = agora
                        vel = feito / max(agora - inicio, 0.1) / 1e6
                        ctx.progresso(feito / total * 100 if total else None, f"direto {feito / 1e6:.1f} MB · {vel:.1f} MB/s")
                    if time.time() - inicio > ctx.tempo_limite:
                        raise ErroMotor("rede", "tempo limite do download")
        except (requests.RequestException, ConnectionError, OSError) as e:
            tentativas += 1
            if tentativas > 8:
                raise ErroMotor("rede", f"conexão caiu muitas vezes: {str(e)[:200]}")
            ctx.log(f"conexão caiu em {feito / 1e6:.1f} MB; retomando...")
            time.sleep(min(1.5 * tentativas, 10))
            continue
        if total and feito < total:
            tentativas += 1
            if tentativas > 8:
                raise ErroMotor("rede", f"arquivo incompleto ({feito} de {total} bytes)")
            ctx.log(f"veio incompleto ({feito}/{total}); retomando...")
            continue
        break
    if feito == 0:
        raise ErroMotor("outro", "arquivo vazio")
    final = caminho_livre(ctx.destino, nome_seguro(nome), ext)
    shutil.move(str(parcial), str(final))
    return final


def _ext_por_assinatura(b: bytes, ct: str, url: str) -> str:
    if b[4:8] == b"ftyp":
        return "mov" if b[8:10] == b"qt" else "mp4"
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return "webm" if b"webm" in b[:64] else "mkv"
    if b[:3] == b"FLV":
        return "flv"
    if b[:1] == b"\x47" and len(b) > 188 and b[188:189] == b"\x47":
        return "ts"
    if b[:4] == b"RIFF":
        return "avi"
    if b[:4] == b"OggS":
        return "ogv"
    if "webm" in ct:
        return "webm"
    m = re.search(r"\.(mp4|webm|mov|mkv|flv|m4v|ts)(\?|$)", url.lower())
    return m.group(1) if m else "mp4"


# ------------------------------------------------------------------ ffmpeg
def _cab_ffmpeg(headers: dict | None) -> list[str]:
    h = {"User-Agent": UA, **(headers or {})}
    linhas = "".join(f"{k}: {v}\r\n" for k, v in h.items() if k.lower() != "user-agent")
    out = ["-user_agent", h["User-Agent"]]
    if linhas:
        out += ["-headers", linhas]
    return out


def baixar_ffmpeg(url: str, ctx: Contexto, nome: str, headers: dict | None = None) -> Path:
    exe = achar_programa("ffmpeg")
    if not exe:
        raise ErroMotor("ffmpeg", "ffmpeg não encontrado")
    tmp = caminho_livre(ctx.destino / ".parciais", nome_seguro(nome, 90) + ".ffmpeg", "mp4")
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-stats",
           "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "8",
           "-rw_timeout", "30000000"] + _cab_ffmpeg(headers)
    if ctx.proxy and ctx.proxy.startswith("http"):
        cmd += ["-http_proxy", ctx.proxy]
    cmd += ["-i", url, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-bsf:a", "aac_adtstoasc",
            "-movflags", "+faststart", str(tmp)]
    env = dict(os.environ)
    if ctx.proxy:
        env["http_proxy"] = env["https_proxy"] = ctx.proxy
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                         errors="replace", creationflags=SEM_JANELA, env=env)
    erros: list[str] = []
    inicio = time.time()
    try:
        buf = ""
        while True:
            ch = p.stderr.read(1) if p.stderr else ""
            if not ch:
                break
            if ch in "\r\n":
                linha, buf = buf.strip(), ""
                if linha.startswith("frame=") or "time=" in linha:
                    m = re.search(r"time=(\S+)", linha)
                    ctx.progresso(None, f"ffmpeg {m.group(1) if m else ''}")
                elif linha:
                    erros.append(linha)
                if ctx.cancel.is_set():
                    p.kill()
                    raise Cancelado("cancelado")
                if time.time() - inicio > ctx.tempo_limite:
                    p.kill()
                    raise ErroMotor("rede", "tempo limite do ffmpeg")
            else:
                buf += ch
        p.wait(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    if p.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True)
        msg = " | ".join(erros[-3:]) or f"ffmpeg saiu com código {p.returncode}"
        cat = classificar_erro(msg)
        raise ErroMotor(cat if cat != "outro" else "formato", msg[:400])
    final = caminho_livre(ctx.destino, nome_seguro(nome), "mp4")
    shutil.move(str(tmp), str(final))
    return final


# ------------------------------------------------------------------ streamlink
def baixar_streamlink(url: str, ctx: Contexto, nome: str, headers: dict | None = None) -> Path:
    exe = achar_programa("streamlink")
    if not exe:
        raise ErroMotor("outro", "streamlink não instalado")
    tmp = caminho_livre(ctx.destino / ".parciais", nome_seguro(nome, 90) + ".streamlink", "ts")
    cmd = [exe, "--force", "--retry-open", "2", "-o", str(tmp)]
    for k, v in {"User-Agent": UA, **(headers or {})}.items():
        cmd += ["--http-header", f"{k}={v}"]
    if ctx.proxy:
        cmd += ["--http-proxy", ctx.proxy]
    cmd += [url, "best"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", creationflags=SEM_JANELA)
    saida = []
    try:
        for linha in p.stdout:  # type: ignore[union-attr]
            saida.append(linha.strip())
            if ctx.cancel.is_set():
                p.kill()
                raise Cancelado("cancelado")
        p.wait(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    if not tmp.exists() or tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True)
        msg = " | ".join(x for x in saida[-3:] if x) or "streamlink não gerou arquivo"
        raise ErroMotor(classificar_erro(msg) if classificar_erro(msg) != "outro" else "nao_suportado", msg[:300])
    final = caminho_livre(ctx.destino, nome_seguro(nome), "ts")
    shutil.move(str(tmp), str(final))
    return final


# ------------------------------------------------------------------ verificação e MP4
def _apagar(p: Path):
    try:
        Path(p).unlink(missing_ok=True)
    except Exception:
        pass


def decodifica(path: Path, segundo: float = 0.0) -> bool:
    """O arquivo abre e dá imagem de verdade? (um quadro decodificado; achado na bateria: arquivo com cabeçalho
    certo mas sem imagem decodificável passava como pronto)."""
    exe = achar_programa("ffmpeg")
    if not exe:
        return True
    # 1º pulando para o meio; se não vier quadro (MPEG-TS começa em 1,4 s e o pulo falha), o 1º quadro mesmo
    for antes in (["-ss", f"{max(0.0, segundo):.2f}"], []):
        try:
            r = subprocess.run([exe, "-v", "error", *antes, "-i", str(path), "-frames:v", "1",
                                "-vf", "scale=16:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                               capture_output=True, timeout=60, creationflags=SEM_JANELA)
            if len(r.stdout) > 0:
                return True
        except Exception:
            continue
    return False


def verificar_e_converter(path: Path, ctx: Contexto) -> tuple[Path, dict]:
    """Confere com o ffprobe que o arquivo TEM vídeo e deixa em MP4 (remux; recodifica só se precisar)."""
    info = ffprobe_info(path)
    if not info:
        _apagar(path)
        raise ErroMotor("formato", "não consegui ler o arquivo (ffprobe)")
    if not info.get("tem_video"):
        _apagar(path)
        raise ErroMotor("formato", "o arquivo baixado não tem imagem (só áudio ou vazio)")
    if not decodifica(path, (info.get("duracao") or 0) / 2):
        _apagar(path)
        raise ErroMotor("formato", "o arquivo baixado não decodifica (veio quebrado)")
    ffmpeg = achar_programa("ffmpeg")
    vc = (info.get("vcodec") or "").lower()
    ac = (info.get("acodec") or "").lower()
    precisa_video = ctx.compat and vc not in ("h264",)
    precisa_audio = bool(ac) and ac not in ("aac", "mp3", "alac", "ac3", "eac3", "opus") or (ctx.compat and ac == "opus")
    if path.suffix.lower() == ".mp4" and not precisa_video and not precisa_audio:
        return path, info
    if not ffmpeg:
        return path, info
    saida = caminho_livre(path.parent, path.stem, "mp4") if path.suffix.lower() != ".mp4" else \
        path.with_name(path.stem + ".h264.tmp.mp4")
    if not precisa_video:
        ctx.progresso(None, "passando para MP4 (sem perder qualidade)...")
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-c:v", "copy", "-c:a", "aac" if precisa_audio else "copy", "-b:a", "192k", "-movflags", "+faststart", str(saida)]
    else:
        ctx.progresso(None, f"convertendo {vc or '?'} para H.264 (toca em tudo)...")
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709,scale=trunc(iw/2)*2:trunc(ih/2)*2",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(saida)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=ctx.tempo_limite, creationflags=SEM_JANELA)
        ok = r.returncode == 0 and saida.exists() and saida.stat().st_size > 1000
    except subprocess.TimeoutExpired:
        ok = False
    if not ok:
        saida.unlink(missing_ok=True)
        if precisa_video and path.suffix.lower() != ".mp4":
            # último caso: remux simples
            saida = caminho_livre(path.parent, path.stem, "mp4")
            r = rodar([ffmpeg, "-y", "-loglevel", "error", "-i", str(path), "-c", "copy", "-movflags", "+faststart", str(saida)], timeout=3600)
            ok = r.returncode == 0 and saida.exists() and saida.stat().st_size > 1000
        if not ok:
            saida.unlink(missing_ok=True)
            return path, info
    novo = ffprobe_info(saida)
    if not novo.get("tem_video"):
        saida.unlink(missing_ok=True)
        return path, info
    path.unlink(missing_ok=True)
    if saida.name.endswith(".h264.tmp.mp4"):
        destino_final = path.with_suffix(".mp4")
        os.replace(saida, destino_final)
        saida = destino_final
    return saida, novo
