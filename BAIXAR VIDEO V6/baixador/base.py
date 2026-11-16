# -*- coding: utf-8 -*-
"""Caminhos, configuração, gravação segura de JSON e utilidades de texto/URL."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

APP_DIR = Path(__file__).resolve().parent.parent
DADOS_DIR = Path(os.environ.get("BAIXADOR_DADOS") or (APP_DIR / "dados"))  # os testes usam outra pasta
DADOS_DIR.mkdir(parents=True, exist_ok=True)
CFG_PATH = DADOS_DIR / "config.json"
MEMORIA_PATH = DADOS_DIR / "memoria_sites.json"
MODELO_PATH = DADOS_DIR / "modelo_aprendido.json"
HISTORICO_PATH = DADOS_DIR / "historico.jsonl"
MODELO_INICIAL_PATH = Path(__file__).resolve().parent / "modelo_inicial.json"

QUALIDADES = {"Melhor disponível": 4320, "2160p (4K)": 2160, "1440p": 1440,
              "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}

NAVEGADORES = ["nenhum", "opera", "chrome", "edge", "firefox", "brave"]

PADRAO = {
    "qualidade_padrao": "720p",
    "proxies": "",
    "usar_tor": True,
    "pasta_destino": str(APP_DIR / "videos_baixados"),
    "tema": "escuro",
    "cookies_navegador": "nenhum",
    "mp4_compativel": True,
    "usar_navegador": True,
    "perguntar_na_duvida": True,
    "simultaneos": 2,
}

_lock_json = threading.Lock()


def ler_json(path: Path, padrao):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar_json(path: Path, obj) -> bool:
    """Grava em arquivo temporário e troca no fim: um travamento no meio nunca deixa o JSON pela metade."""
    with _lock_json:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                os.remove(tmp)  # type: ignore[name-defined]
            except Exception:
                pass
            return False


def carregar_config() -> dict:
    cfg = dict(PADRAO)
    cfg.update({k: v for k, v in ler_json(CFG_PATH, {}).items() if k in PADRAO})
    if cfg["qualidade_padrao"] not in QUALIDADES:
        # nomes antigos da V5 ("Melhor disponivel" sem acento)
        cfg["qualidade_padrao"] = "Melhor disponível" if "melhor" in str(cfg["qualidade_padrao"]).lower() else "720p"
    if cfg["cookies_navegador"] not in NAVEGADORES:
        cfg["cookies_navegador"] = "nenhum"
    try:
        cfg["simultaneos"] = max(1, min(4, int(cfg["simultaneos"])))
    except Exception:
        cfg["simultaneos"] = 2
    return cfg


def salvar_config(cfg: dict) -> bool:
    return gravar_json(CFG_PATH, {k: cfg.get(k, v) for k, v in PADRAO.items()})


# ---------------------------------------------------------------- domínio
_SEGUNDO_NIVEL = {"com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "co.uk", "org.uk", "ac.uk",
                  "com.au", "co.jp", "com.mx", "com.ar", "co.in", "com.tr", "co.kr", "com.cn", "com.pt"}
_ALIAS = {"youtu.be": "youtube.com", "youtube-nocookie.com": "youtube.com", "fb.watch": "facebook.com",
          "fb.com": "facebook.com", "x.com": "twitter.com", "t.co": "twitter.com", "vm.tiktok.com": "tiktok.com",
          "instagr.am": "instagram.com", "dai.ly": "dailymotion.com", "redd.it": "reddit.com",
          "v.redd.it": "reddit.com", "player.vimeo.com": "vimeo.com", "vimeo.com": "vimeo.com"}


def dominio(url: str) -> str:
    """Nome do site sem www/m./pt. etc., para a memória juntar youtu.be com youtube.com."""
    try:
        host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower().strip(".")
    except Exception:
        return "?"
    if not host:
        return "?"
    if host in _ALIAS:
        return _ALIAS[host]
    if re.fullmatch(r"[\d.]+|\[[0-9a-f:]+\]", host):
        return host
    partes = host.split(".")
    n = 3 if ".".join(partes[-2:]) in _SEGUNDO_NIVEL else 2
    base = ".".join(partes[-n:]) if len(partes) >= n else host
    return _ALIAS.get(base, base)


# ---------------------------------------------------------------- links colados
_RE_URL = re.compile(
    r"""(?ix)
    \b(
      (?:https?://|www\.)[^\s<>"'`]+            # com esquema ou www.
      |
      (?:[a-z0-9-]+\.)+(?:com|net|org|tv|be|ly|me|io|br|watch|co|app|to|cc)(?:\.[a-z]{2})?/[^\s<>"'`]+
    )""")
_FIM_RUIM = ".,;:!?)]}>'\"…»”’"


def extrair_links(texto: str) -> list[str]:
    """Acha links em qualquer texto colado (conversa do WhatsApp, lista, frase) e tira repetidos."""
    vistos, out = set(), []
    for m in _RE_URL.finditer(texto or ""):
        u = m.group(1)
        # tira pontuação colada no fim, mas mantém parênteses equilibrados (wikipedia)
        while u and u[-1] in _FIM_RUIM:
            if u[-1] == ")" and u.count("(") >= u.count(")"):
                break
            u = u[:-1]
        if not u:
            continue
        if not u.lower().startswith(("http://", "https://")):
            u = "https://" + u
        u = limpar_url(u)
        chave = u.rstrip("/")
        if chave not in vistos:
            vistos.add(chave)
            out.append(u)
    return out


# Só parâmetros que nunca mudam o vídeo. (mibextid do Facebook fica: tirar não ajuda e já foi testado.)
_RASTREIO = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "igshid"}


def limpar_url(u: str) -> str:
    """Remove parâmetros de rastreio e, no YouTube, a playlist (o pedido é o vídeo específico)."""
    try:
        p = urlparse(u.strip())
        q = parse_qsl(p.query, keep_blank_values=True)
        dom = dominio(u)
        if dom == "youtube.com" and any(k == "v" for k, _ in q):
            q = [(k, v) for k, v in q if k in ("v", "t")]
        elif dom == "youtube.com":
            q = [(k, v) for k, v in q if k not in ("si", "feature", "pp")]
        else:
            q = [(k, v) for k, v in q if k.lower() not in _RASTREIO]
        return urlunparse(p._replace(query=urlencode(q, doseq=True)))
    except Exception:
        return u.strip()


# ---------------------------------------------------------------- nomes de arquivo
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def nome_seguro(titulo: str, limite: int = 120) -> str:
    t = unicodedata.normalize("NFC", str(titulo or "")).strip()
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    if not t:
        t = "video"
    if len(t) > limite:
        t = t[:limite].rstrip(" .")
    if t.split(".")[0].upper() in _RESERVADOS:
        t = "_" + t
    return t


def caminho_livre(pasta: Path, nome: str, ext: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    p = pasta / f"{nome}.{ext}"
    i = 2
    while p.exists():
        p = pasta / f"{nome} ({i}).{ext}"
        i += 1
    return p


# ---------------------------------------------------------------- programas externos
_SCRIPTS = Path(sys.executable).parent


_ACHADOS: dict[str, str] = {}


def achar_programa(nome: str) -> str | None:
    """Acha um programa de fora. Se o PATH do processo estiver velho (ex.: aberto antes do winget instalar), procura
    também nas pastas onde o winget e os instaladores comuns põem o FFmpeg."""
    if nome in _ACHADOS and Path(_ACHADOS[nome]).exists():
        return _ACHADOS[nome]
    for cand in (_SCRIPTS / f"{nome}.exe", _SCRIPTS / nome):
        if cand.exists():
            _ACHADOS[nome] = str(cand)
            return str(cand)
    achado = shutil.which(nome)
    if not achado and sys.platform.startswith("win"):
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        fixos = [local / "Microsoft" / "WinGet" / "Links" / f"{nome}.exe", Path("C:/ffmpeg/bin") / f"{nome}.exe",
                 Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / f"{nome}.exe"]
        for cand in fixos:
            if cand.exists():
                achado = str(cand)
                break
        if not achado:
            pac = local / "Microsoft" / "WinGet" / "Packages"
            try:
                acho = sorted(pac.glob(f"*FFmpeg*/*/bin/{nome}.exe"), key=lambda x: x.stat().st_mtime, reverse=True)
                achado = str(acho[0]) if acho else None
            except Exception:
                achado = None
    if achado:
        _ACHADOS[nome] = achado
    return achado


SEM_JANELA = 0x08000000 if sys.platform.startswith("win") else 0  # CREATE_NO_WINDOW


def rodar(cmd: list, timeout=None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=SEM_JANELA, **kw)


def ffprobe_info(path: str | Path) -> dict:
    """Informações reais do arquivo: tem vídeo? codec, altura, duração. Base da verificação de cada download."""
    exe = achar_programa("ffprobe")
    if not exe:
        return {}
    try:
        r = rodar([exe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)], timeout=60)
        d = json.loads(r.stdout or "{}")
    except Exception:
        return {}
    vs = [s for s in d.get("streams", []) if s.get("codec_type") == "video"
          and (s.get("disposition") or {}).get("attached_pic", 0) == 0]
    aus = [s for s in d.get("streams", []) if s.get("codec_type") == "audio"]
    dur = 0.0
    try:
        dur = float((d.get("format") or {}).get("duration") or 0)
    except Exception:
        pass
    v = vs[0] if vs else {}
    return {"tem_video": bool(vs), "tem_audio": bool(aus), "vcodec": v.get("codec_name"),
            "acodec": aus[0].get("codec_name") if aus else None, "altura": v.get("height"),
            "largura": v.get("width"), "duracao": dur, "formato": (d.get("format") or {}).get("format_name", "")}
