# -*- coding: utf-8 -*-
"""Detector de vídeo em páginas: lê o HTML (e os scripts) e lista TODOS os candidatos a vídeo com o contexto
de onde cada um apareceu. Quem decide qual é o vídeo certo é o aprendizado (aprendizado.py), não este arquivo.

Cobre: <video>/<source>, og:video, twitter:player:stream, JSON-LD (VideoObject), <iframe> de players conhecidos,
configurações de players (JW Player, Video.js, Plyr, Flowplayer, Clappr, hls.js, dash.js, Shaka, Fluid, Art,
DPlayer...), chaves de JSON de redes sociais (hd_src, playback_url...), atributos data-*, links diretos,
texto escapado (\\/ , \\u002F, &amp;, %2F) e base64 dentro de atob().
"""
from __future__ import annotations

import base64
import binascii
import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, unquote, parse_qs

EXT_PROGRESSIVO = {"mp4", "m4v", "webm", "mov", "mkv", "flv", "ts", "ogv", "3gp", "avi", "wmv", "mpg", "mpeg", "f4v"}
EXT_AUDIO = {"m4a", "mp3", "aac", "opus", "oga", "ogg", "wav", "flac", "weba"}
EXT_IGNORAR = {"jpg", "jpeg", "png", "gif", "webp", "svg", "avif", "ico", "css", "js", "json", "vtt", "srt", "ass",
               "woff", "woff2", "ttf", "html", "htm", "php", "xml", "txt", "map"}

# hosts de players embutidos que o yt-dlp sabe baixar
EMBEDS = ["youtube.com/embed", "youtube-nocookie.com/embed", "youtube.com/watch", "youtu.be/", "player.vimeo.com",
          "vimeo.com/", "dailymotion.com/embed", "dailymotion.com/video", "geo.dailymotion.com", "facebook.com/plugins/video",
          "facebook.com/watch", "streamable.com/", "rumble.com/embed", "ok.ru/videoembed", "vk.com/video_ext",
          "player.twitch.tv", "clips.twitch.tv", "bitchute.com/embed", "odysee.com/$/embed", "fast.wistia",
          "players.brightcove.net", "cdn.jwplayer.com/players", "content.jwplatform.com", "kaltura.com",
          "iframe.mediadelivery.net", "videodelivery.net", "iframe.videodelivery.net", "stream.mux.com",
          "play.vidyard.com", "loom.com/embed", "embed.ted.com", "tiktok.com/embed", "instagram.com/p/",
          "instagram.com/reel", "twitter.com/i/videos", "platform.twitter.com/embed", "drive.google.com/file",
          "archive.org/embed", "peertube", "/videos/embed/", "player.bilibili.com", "embed.podcasts.apple"]

PLAYERS = {"jwplayer": ["jwplayer", "jw-player", "jwplatform"], "videojs": ["video-js", "videojs", "vjs-"],
           "plyr": ["plyr"], "flowplayer": ["flowplayer"], "clappr": ["clappr"], "hlsjs": ["hls.js", "hls.min.js", "new hls(", "hls.loadsource"],
           "dashjs": ["dash.all", "dashjs"], "shaka": ["shaka-player", "shaka.player"], "mediaelement": ["mediaelement", "mejs-"],
           "brightcove": ["brightcove"], "kaltura": ["kaltura"], "wistia": ["wistia"], "fluid": ["fluidplayer"],
           "artplayer": ["artplayer"], "dplayer": ["dplayer"], "xgplayer": ["xgplayer"], "mux": ["mux-player", "stream.mux"],
           "vidstack": ["vidstack", "media-player"], "bitmovin": ["bitmovin"], "theo": ["theoplayer"],
           "youtube": ["youtube.com/embed", "youtube.com/iframe_api"], "vimeo": ["player.vimeo.com"],
           "facebook": ["facebook.com/plugins/video", "fbcdn"], "html5": ["<video"]}

# palavras de contexto que indicam "não é o vídeo principal"
CTX_RUIM = re.compile(r"related|recommend|sugest|suggest|sidebar|thumb|preview|teaser|hover|promo|sponsor|banner|"
                      r"advert|\bads?\b|ad-|-ad\b|ad_|outstream|preroll|footer|more-videos|carousel|playlist-item|"
                      r"card|grid-item|tile|background|bg-video|hero", re.I)
CTX_BOM = re.compile(r"main|player|primary|watch|content|article|post-video|video-container|featured-video|movie", re.I)

ATTR_DADOS = {"data-src", "data-video", "data-video-src", "data-hls", "data-mp4", "data-file", "data-url",
              "data-stream", "data-source", "data-video-url", "data-hls-url", "data-dash", "data-mpd",
              "data-media", "data-href", "data-original", "data-webm", "data-high", "data-low", "data-hd", "data-sd"}

CHAVES_JSON = ["file", "src", "url", "hlsUrl", "hls_url", "dashUrl", "dash_url", "videoUrl", "video_url", "mp4", "mp4Url",
               "playback_url", "playbackUrl", "contentUrl", "stream_url", "streamUrl", "hd_src", "sd_src", "hd_src_no_ratelimit",
               "sd_src_no_ratelimit", "browser_native_hd_url", "browser_native_sd_url", "playable_url",
               "playable_url_quality_hd", "manifest", "manifestUrl", "manifest_url", "source", "videoSrc", "video_src",
               "fallback_url", "download_url", "downloadUrl", "file_url", "media_url", "mediaUrl", "m3u8", "hls", "dash",
               "progressive", "secure_url", "720p", "1080p", "480p", "360p", "high", "low", "hd", "sd"]

RE_QUALIDADE = re.compile(r"(?<![a-z0-9])(2160|1440|1080|720|540|480|360|240|144)[pP]?(?![0-9])")


@dataclass
class Candidato:
    url: str
    tipo: str = "desconhecido"          # progressivo | hls | dash | embed | audio | desconhecido
    origens: set = field(default_factory=set)
    ctx: dict = field(default_factory=dict)
    ordem: int = 0
    ocorrencias: int = 1
    grupo: str = ""
    altura: int = 0
    rotulo: str = ""
    headers: dict = field(default_factory=dict)
    prob: float = 0.0

    def resumo(self) -> str:
        q = f" {self.altura}p" if self.altura else ""
        return f"{self.tipo}{q} · {', '.join(sorted(self.origens))}"


def extensao(url: str) -> str:
    try:
        p = urlparse(url)
    except Exception:
        return ""
    caminho = unquote(p.path or "").lower()
    m = re.search(r"\.([a-z0-9]{2,5})$", caminho)
    if m:
        return m.group(1)
    q = (p.query or "").lower()
    for k in ("format=", "ext=", "type=", "container=", "mime="):
        i = q.find(k)
        if i >= 0:
            v = re.match(r"[a-z0-9%/]+", q[i + len(k):])
            if v:
                val = unquote(v.group(0)).split("/")[-1]
                if val in ("m3u8", "mpegurl", "x-mpegurl", "hls"):
                    return "m3u8"
                if val in ("mpd", "dash", "dash+xml", "mpd-time-csf"):
                    return "mpd"
                if val in EXT_PROGRESSIVO or val in EXT_AUDIO:
                    return val
    return ""


def tipo_da_url(url: str, mime: str = "") -> str:
    mime = (mime or "").lower()
    low = url.lower()
    if any(h in low for h in EMBEDS) and not re.search(r"\.(m3u8|mpd|mp4|webm)(\?|$)", low):
        return "embed"
    if "mpegurl" in mime or "m3u" in mime:
        return "hls"
    if "dash+xml" in mime:
        return "dash"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/") and "mpegurl" not in mime:
        return "progressivo"   # <source type="video/ogg" src="x.ogg"> é vídeo, mesmo com extensão de áudio
    ext = extensao(url)
    if ext == "m3u8" or ".m3u8" in low:
        return "hls"
    if ext == "mpd" or ".mpd" in low:
        return "dash"
    if ext in EXT_PROGRESSIVO:
        return "progressivo"
    if ext in EXT_AUDIO:
        return "audio"
    if mime.startswith("video/"):
        return "progressivo"
    if ext in EXT_IGNORAR:
        return "ignorar"
    if re.search(r"/(hls|dash)/|manifest|playlist\.m3u|videoplayback|/video/|/stream/|mime=video", low):
        return "desconhecido"
    return "desconhecido"


def chave_grupo(url: str) -> str:
    """Mesmo vídeo em qualidades diferentes cai no mesmo grupo (tira 720p/1080/hd/sd do caminho)."""
    try:
        p = urlparse(url)
    except Exception:
        return url
    caminho = unquote(p.path or "").lower()
    caminho = RE_QUALIDADE.sub("{q}", caminho)
    caminho = re.sub(r"(?<![a-z])(hd|sd|high|low|med|medium|mobile|full|lq|hq)(?![a-z])", "{q}", caminho)
    caminho = re.sub(r"\.(mp4|webm|m3u8|mpd|mov|m4v|mkv|flv|ts|ogv|ogg|3gp|avi|wmv)$", "", caminho)
    caminho = re.sub(r"/(index|master|playlist|manifest|chunklist|prog_index)(_[a-z0-9{}]+)?$", "", caminho)
    caminho = re.sub(r"[_\-.]?\{q\}", "", caminho)
    return (p.hostname or "") + caminho.rstrip("/")


def altura_da_url(url: str, rotulo: str = "") -> int:
    for texto in (rotulo or "", unquote(urlparse(url).path or "")):
        m = RE_QUALIDADE.search(texto)
        if m:
            return int(m.group(1))
    return 0


def molde_da_url(url: str) -> str:
    """Forma do endereço, para o aprendizado reconhecer o padrão de um site: /v/{n}/{h}/index.m3u8"""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    partes = []
    for s in unquote(p.path or "").split("/"):
        if not s:
            continue
        nome, _, ext = s.rpartition(".") if re.search(r"\.[a-z0-9]{2,4}$", s, re.I) else (s, "", "")
        if re.fullmatch(r"\d+", nome):
            nome = "{n}"
        elif re.fullmatch(r"[0-9a-fA-F\-]{12,}", nome) or (len(nome) >= 16 and re.search(r"\d", nome)):
            nome = "{h}"
        elif RE_QUALIDADE.fullmatch(nome):
            nome = "{q}"
        elif len(nome) > 24:
            nome = "{t}"
        partes.append(nome + ("." + ext.lower() if ext else ""))
    return "/" + "/".join(partes[-4:])


# ------------------------------------------------------------------ leitura do HTML
class _Leitor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pilha: list[tuple[str, str]] = []   # (tag, classes+id)
        self.achados: list[tuple[str, str, dict]] = []  # (url, origem, ctx)
        self.scripts: list[tuple[str, str]] = []  # (type, texto)
        self._script_tipo = None
        self._script_buf: list[str] = []
        self.video_atual: dict | None = None
        self.n_videos = 0
        self.titulo = ""
        self._em_titulo = False
        self.metas: dict[str, str] = {}
        self.dentro_link = 0

    VAZIAS = {"source", "meta", "link", "img", "br", "hr", "input", "embed", "param", "track", "area", "base", "col", "wbr"}

    def _ctx(self, extra: dict | None = None) -> dict:
        classes = " ".join(c for _, c in self.pilha[-8:])
        d = {"ctx_ruim": bool(CTX_RUIM.search(classes)), "ctx_bom": bool(CTX_BOM.search(classes)),
             "dentro_link": self.dentro_link > 0, "aside": any(t in ("aside", "footer", "nav") for t, _ in self.pilha)}
        if extra:
            d.update(extra)
        return d

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        rot = " ".join([a.get("class", ""), a.get("id", ""), a.get("data-role", ""), a.get("aria-label", "")])
        if tag not in self.VAZIAS:
            self.pilha.append((tag, rot))
        if tag == "a":
            self.dentro_link += 1
            href = a.get("href", "")
            if href and re.search(r"\.(mp4|m4v|webm|mov|mkv|m3u8|mpd|flv)(\?|#|$)", href, re.I):
                self.achados.append((href, "link", self._ctx({"download_attr": "download" in a})))
        elif tag == "title":
            self._em_titulo = True
        elif tag == "video":
            self.n_videos += 1
            v = {"autoplay": "autoplay" in a, "muted": "muted" in a, "loop": "loop" in a, "controls": "controls" in a,
                 "poster": bool(a.get("poster")), "largura": _num(a.get("width")), "altura_tag": _num(a.get("height")),
                 "indice_video": self.n_videos, "playsinline": "playsinline" in a, "id_video": rot}
            self.video_atual = v
            for k in ("src", *ATTR_DADOS):
                if a.get(k):
                    self.achados.append((a[k], "video_tag" if k == "src" else "data_attr", self._ctx(dict(v))))
        elif tag == "source":
            src = a.get("src") or a.get("data-src") or ""
            if src:
                extra = dict(self.video_atual or {})
                extra.update({"mime": a.get("type", ""), "rotulo": a.get("label") or a.get("res") or a.get("size")
                              or a.get("data-res") or a.get("data-quality") or a.get("title") or ""})
                origem = "source_tag" if self.video_atual is not None else "source_solto"
                self.achados.append((src, origem, self._ctx(extra)))
        elif tag == "meta":
            prop = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            cont = a.get("content", "")
            if prop:
                self.metas[prop] = cont
            if cont and prop in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream",
                                 "contenturl", "embedurl", "twitter:player"):
                origem = {"twitter:player:stream": "twitter_stream", "contenturl": "jsonld", "embedurl": "iframe",
                          "twitter:player": "iframe"}.get(prop, "og_video")
                self.achados.append((cont, origem, self._ctx({"mime": self.metas.get("og:video:type", "")})))
        elif tag in ("iframe", "embed", "object"):
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or a.get("data") or ""
            if src:
                extra = {"largura": _num(a.get("width")), "altura_tag": _num(a.get("height"))}
                self.achados.append((src, "iframe", self._ctx(extra)))
                # player.php?file=https%3A%2F%2Fcdn/.../video.mp4 : o vídeo está dentro do parâmetro
                try:
                    for k, vs in parse_qs(urlparse(src).query).items():
                        for v in vs:
                            if re.match(r"(https?:)?//", v) and re.search(r"\.(m3u8|mpd|mp4|webm|mov|m4v)", v, re.I):
                                self.achados.append((v, "config_player", self._ctx(dict(extra, chave="file"))))
                except Exception:
                    pass
            fv = a.get("flashvars", "")
            if fv:
                self._flashvars(fv)
        elif tag == "param" and a.get("name", "").lower() in ("flashvars", "movie", "src"):
            self._flashvars(a.get("value", ""))
        elif tag == "link" and a.get("as", "").lower() == "video" and a.get("href"):
            self.achados.append((a["href"], "preload", self._ctx()))
        elif tag == "script":
            self._script_tipo = a.get("type", "").lower()
            self._script_buf = []
        # data-* em qualquer elemento
        if tag not in ("video",):
            for k in ATTR_DADOS:
                val = a.get(k, "")
                if val and (tag != "img" or re.search(r"\.(mp4|webm|m3u8|mpd)", val, re.I)):
                    self.achados.append((val, "data_attr", self._ctx()))
            if a.get("data-setup"):  # video.js
                self.scripts.append(("data-setup", a["data-setup"]))
            for k in ("data-config", "data-player", "data-options", "data-video-config", "data-props", "data-settings"):
                if a.get(k):
                    self.scripts.append(("data-json", a[k]))

    def _flashvars(self, fv: str):
        for k, vs in parse_qs(fv).items():
            for v in vs:
                if v.startswith(("http", "//", "/")):
                    self.achados.append((v, "config_player", self._ctx({"chave": k})))

    def handle_endtag(self, tag):
        if tag == "a" and self.dentro_link:
            self.dentro_link -= 1
        if tag == "title":
            self._em_titulo = False
        if tag == "video":
            self.video_atual = None
        if tag == "script" and self._script_tipo is not None:
            self.scripts.append((self._script_tipo, "".join(self._script_buf)))
            self._script_tipo = None
        if tag in self.VAZIAS:
            return
        # desempilha até a tag (HTML mal fechado não quebra a pilha)
        for i in range(len(self.pilha) - 1, max(-1, len(self.pilha) - 12), -1):
            if self.pilha[i][0] == tag:
                del self.pilha[i:]
                break

    def handle_data(self, data):
        if self._script_tipo is not None:
            self._script_buf.append(data)
        elif self._em_titulo:
            self.titulo += data


def _num(v) -> int:
    try:
        return int(re.match(r"\d+", str(v)).group(0))
    except Exception:
        return 0


# ------------------------------------------------------------------ scripts e texto
# Absoluto (http://, https:// ou //) em qualquer lugar; relativo (/pasta/x.mp4) só logo depois de aspas/parêntese/=,
# para não testar cada barra do texto (fica linear mesmo em páginas de vários MB).
RE_URL_MIDIA = re.compile(
    r"""(?i)(?:(?:https?:)?//[^\s"'<>()\\]{1,1500}?|(?<=["'(=])/[^\s"'<>()\\]{1,1500}?)"""
    r"""\.(?:m3u8|mpd|mp4|m4v|webm|mov|flv|mkv)(?:\?[^\s"'<>\\]{0,1500})?(?=["'\s<>\\),;]|$)""")
RE_CHAVE = re.compile(r"""["']?(%s)["']?\s*[:=]\s*["']((?:https?:)?(?:\\?/)[^"']+)["']""" % "|".join(map(re.escape, CHAVES_JSON)), re.I)
RE_LOADSRC = re.compile(r"""(?:loadSource|\.load|\.src|setSrc|attachSource|\.initialize\([^,]*,)\s*\(?\s*["']((?:https?:)?/[^"']+)["']""", re.I)
RE_ATOB = re.compile(r"""atob\(\s*["']([A-Za-z0-9+/=]{16,})["']\s*\)""")
RE_B64_SOLTO = re.compile(r"""["']((?:aHR0c|Ly9)[A-Za-z0-9+/=]{20,})["']""")


def desescapar(texto: str) -> str:
    t = texto.replace("\\/", "/")
    t = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), t)
    t = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), t)
    t = html.unescape(t)
    return t


def _url_valida(u: str) -> bool:
    low = u.lower()
    return not (low.startswith(("blob:", "data:", "javascript:", "about:", "mailto:", "#")) or len(u) > 4000 or len(u) < 4)


_CHAVES_RUINS = (r"related\w*|recommend\w*|suggest\w*|up_?next|next_?video\w*|autoplay_?next|more_?videos|playlist\w*|"
                 r"ads?|ad_?config|ad_?tag|adverts?\w*|vast\w*|preroll\w*|midroll|sponsor\w*|promo\w*|preview\w*|thumb\w*|"
                 r"sprite\w*|storyboard\w*|trailer\w*|teaser\w*|audio\w*|outros\w*|relacionados\w*|sugest\w*")
# "chave": valor  ou  chave = valor  ou  chave: valor — só NOMES de chave/variável, não palavras soltas no texto
RE_CHAVE_RUIM = re.compile(r"""["']?\b(%s)\b["']?\s*[:=]\s*(?=[\[{"'])""" % _CHAVES_RUINS, re.I)
RE_CHAVE_BOA = re.compile(r"""\b(setup|sources?|clip|playlist_?item|player\w*|playback\w*|hd_src|sd_src|playable\w*|"""
                          r"""current_?video|main_?video|video)\b["']?\s*[:=(]\s*(?=[\[{"'(])""", re.I)


def _dentro(t: str, ini: int, pos: int) -> bool:
    """O endereço em 'pos' está dentro do valor que começa em 'ini' (lista/objeto ainda aberto, ou a própria string)?"""
    if ini >= len(t):
        return False
    c = t[ini]
    if c in "\"'":
        fim = t.find(c, ini + 1)
        return fim == -1 or fim >= pos
    nivel = 0
    for ch in t[ini:pos]:
        if ch in "[{(":
            nivel += 1
        elif ch in "]})":
            nivel -= 1
            if nivel <= 0:
                return False
    return nivel > 0


def _ctx_script(t: str, pos: int) -> dict:
    """De quem é o valor em que o endereço está? Dentro de "related": [ ... ] é outro vídeo; dentro de
    player.setup({ ... }) é o da página. Vale o contêiner aberto mais próximo (precisão em vez de palavra solta)."""
    janela_ini = max(0, pos - 4000)
    janela = t[janela_ini:pos]
    melhor = None  # (posição, tipo)
    for rx, tipo in ((RE_CHAVE_RUIM, "script_ruim"), (RE_CHAVE_BOA, "script_bom")):
        for m in rx.finditer(janela):
            ini = janela_ini + m.end()
            if _dentro(t, ini, pos) and (melhor is None or ini > melhor[0]):
                melhor = (ini, tipo)
    return {melhor[1]: True} if melhor else {}

def _de_scripts(scripts: list[tuple[str, str]]) -> list[tuple[str, str, dict]]:
    out = []
    for tipo, texto in scripts:
        if not texto or tipo in ("text/template", "text/x-template"):
            continue
        if tipo == "application/ld+json":
            for u, rot in _jsonld(texto):
                out.append((u, "jsonld" if rot == "contentUrl" else "iframe", {"chave": rot}))
            continue
        t = desescapar(texto)
        origem_cfg = "config_player"
        for m in RE_CHAVE.finditer(t):
            out.append((m.group(2), origem_cfg, {"chave": m.group(1).lower(), **_ctx_script(t, m.start())}))
        for m in RE_LOADSRC.finditer(t):
            out.append((m.group(1), origem_cfg, {"chave": "load", **_ctx_script(t, m.start())}))
        for m in RE_URL_MIDIA.finditer(t):
            out.append((m.group(0), "script", _ctx_script(t, m.start())))
        for rx in (RE_ATOB, RE_B64_SOLTO):
            for m in rx.finditer(texto):
                try:
                    dec = base64.b64decode(m.group(1) + "===").decode("utf-8", "ignore")
                except (binascii.Error, ValueError):
                    continue
                if re.match(r"(https?:)?//", dec):
                    out.append((dec.strip(), "script_base64", {}))
        # %2F codificado dentro de parâmetros (flashvars, iframes de redirecionamento)
        for m in re.finditer(r"https?%3A%2F%2F[^\s\"'&<>]+", texto, re.I):
            dec = unquote(m.group(0))
            if re.search(r"\.(m3u8|mpd|mp4|webm|mov)", dec, re.I):
                out.append((dec, "script", {"codificado": True}))
    return out


def _jsonld(texto: str):
    try:
        dados = json.loads(texto.strip().rstrip(";"))
    except Exception:
        try:
            dados = json.loads(desescapar(texto))
        except Exception:
            return
    pilha = [dados]
    vistos = 0
    while pilha and vistos < 5000:
        vistos += 1
        d = pilha.pop()
        if isinstance(d, list):
            pilha.extend(d)
        elif isinstance(d, dict):
            tipo = d.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if "VideoObject" in tipos or "Clip" in tipos or "Movie" in tipos:
                for k in ("contentUrl", "embedUrl"):
                    v = d.get(k)
                    if isinstance(v, str) and v:
                        yield v, k
            for v in d.values():
                if isinstance(v, (dict, list)):
                    pilha.append(v)


def players_da_pagina(html_txt: str) -> list[str]:
    low = html_txt[:2_000_000].lower()
    return [nome for nome, marcas in PLAYERS.items() if any(m in low for m in marcas)]


@dataclass
class Pagina:
    url: str
    titulo: str = ""
    candidatos: list = field(default_factory=list)
    players: list = field(default_factory=list)
    n_videos_tag: int = 0
    og_video: str = ""
    tamanho_html: int = 0


def analisar_html(html_txt: str, url_base: str) -> Pagina:
    """Lê a página e devolve todos os candidatos (sem decidir). Nunca levanta erro por HTML ruim."""
    html_txt = html_txt or ""
    if len(html_txt) > 12_000_000:
        html_txt = html_txt[:12_000_000]
    leitor = _Leitor()
    try:
        leitor.feed(html_txt)
        leitor.close()
    except Exception:
        pass
    # <base href> muda a raiz dos relativos
    m = re.search(r"<base[^>]+href=[\"']([^\"']+)", html_txt[:200000], re.I)
    base = urljoin(url_base, html.unescape(m.group(1))) if m else url_base
    brutos = list(leitor.achados) + _de_scripts(leitor.scripts)
    # varredura geral do texto (pega URLs em JSON solto, comentários, atributos estranhos)
    if len(brutos) < 400:
        resto = desescapar(re.sub(r"<script[^>]*>.*?</script>", " ", html_txt[:3_000_000], flags=re.S | re.I))
        for mm in RE_URL_MIDIA.finditer(resto):
            brutos.append((mm.group(0), "texto", {}))
    pag = Pagina(url=url_base, titulo=html.unescape(leitor.titulo.strip())[:300], players=players_da_pagina(html_txt),
                 n_videos_tag=leitor.n_videos, og_video=leitor.metas.get("og:video", "") or leitor.metas.get("og:video:url", ""),
                 tamanho_html=len(html_txt))
    pag.titulo = leitor.metas.get("og:title") or pag.titulo
    pag.candidatos = juntar_candidatos(brutos, base)
    return pag


def normalizar_url(u: str, base: str) -> str | None:
    u = desescapar(u.strip().strip("\"'"))
    u = u.replace(" ", "%20")
    if not _url_valida(u):
        return None
    if u.startswith("//"):
        u = (urlparse(base).scheme or "https") + ":" + u
    try:
        u = urljoin(base, u)
    except Exception:
        return None
    if not u.lower().startswith(("http://", "https://")):
        return None
    return u.split("#")[0]


def juntar_candidatos(brutos, base: str) -> list[Candidato]:
    por_url: dict[str, Candidato] = {}
    ordem = 0
    for u, origem, ctx in brutos:
        nu = normalizar_url(u, base)
        if not nu:
            continue
        tipo = tipo_da_url(nu, ctx.get("mime", ""))
        if tipo == "ignorar":
            continue
        if tipo == "desconhecido" and origem in ("texto", "script", "link", "data_attr") and not re.search(
                r"video|stream|media|hls|dash|play|mp4|cdn", nu, re.I):
            continue
        c = por_url.get(nu)
        if c is None:
            ordem += 1
            c = Candidato(url=nu, tipo=tipo, ordem=ordem, grupo=chave_grupo(nu))
            c.rotulo = str(ctx.get("rotulo") or "")
            c.altura = altura_da_url(nu, c.rotulo)
            c.ctx = {}
            c.ocorrencias = 0
            por_url[nu] = c
        c.ocorrencias += 1
        if origem == "texto" and len(c.origens) and c.ocorrencias > 1:
            continue  # a varredura geral só repete o que os leitores específicos já acharam
        c.origens.add(origem)
        for k, v in ctx.items():
            if k not in c.ctx or (isinstance(v, bool) and v and not c.ctx.get(k)):
                c.ctx[k] = v
            elif k in ("ctx_bom",) and v:
                c.ctx[k] = True
        # mais de uma aparição: "ruim" só fica se TODAS forem ruins
        if c.ocorrencias > 1 and not ctx.get("ctx_ruim", False) and "ctx_ruim" in ctx:
            c.ctx["ctx_ruim_parcial"] = True
    cands = list(por_url.values())
    # quantos candidatos de cada grupo (várias qualidades do mesmo vídeo = vídeo importante)
    cont: dict[str, int] = {}
    for c in cands:
        cont[c.grupo] = cont.get(c.grupo, 0) + 1
    contar_moldes(cands)
    for c in cands:
        c.ctx["n_grupo"] = cont[c.grupo]
        c.ctx["n_total"] = len(cands)
    return cands


def contar_moldes(cands: list) -> None:
    """Quantos OUTROS vídeos da página têm o mesmo formato de endereço (lista de relacionados tem muitos iguais)."""
    grupos_por_molde: dict[str, set] = {}
    for c in cands:
        grupos_por_molde.setdefault(molde_da_url(c.url), set()).add(c.grupo)
    for c in cands:
        c.ctx["molde_rep"] = len(grupos_por_molde.get(molde_da_url(c.url), ())) - 1
