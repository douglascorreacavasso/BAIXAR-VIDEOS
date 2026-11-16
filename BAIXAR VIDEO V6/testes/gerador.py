# -*- coding: utf-8 -*-
"""Gerador de páginas simuladas para treinar e testar o detector + modelo (nada sai do computador).

Cada página tem UM vídeo principal (em 1..4 qualidades) escondido de um jeito de player real, e iscas:
propaganda (VAST/IMA), vídeo de fundo mudo em loop, prévias dos relacionados, trailer na lateral, faixa só
de áudio, legenda, miniaturas, storyboard... Também há páginas SEM vídeo e páginas em que o vídeo só existe
depois do JavaScript rodar (essas o certo é o programa admitir a dúvida e chamar o navegador).

conjunto="treino" e conjunto="teste" usam palavras/formas de iscas DIFERENTES (o teste é cego para os pesos).
"""
from __future__ import annotations

import base64
import json
import random
from urllib.parse import quote

FAMILIAS = ["html5_src", "html5_sources", "og_only", "jsonld", "jwplayer", "videojs", "plyr", "hlsjs", "dashjs",
            "shaka", "flowplayer", "clappr", "fluid", "social_json", "twitter_card", "iframe_embed", "flashvars",
            "atob", "encoded_param", "lazy_data", "download_link", "mse_blob_js", "sem_video", "artplayer", "nuxt_state"]

PESOS_FAMILIA = {"sem_video": 0.6, "mse_blob_js": 0.6}

EMBED_HOSTS = ["https://www.youtube.com/embed/{id}", "https://player.vimeo.com/video/{n}",
               "https://www.dailymotion.com/embed/video/x{id}", "https://streamable.com/e/{id}",
               "https://rumble.com/embed/v{id}/", "https://ok.ru/videoembed/{n}"]

ISCAS_TREINO = {"rel_cls": ["related", "sidebar", "thumb-grid", "suggest"], "ad_host": ["ads.adserver{n}.test", "pubads.g.doubleclick.net"],
                "prev": ["preview", "thumbs", "hover"], "bg": ["hero", "bg-video"], "trailer": ["trailer"], "promo": ["promo"]}
ISCAS_TESTE = {"rel_cls": ["recommendations", "more-videos", "carousel-row", "card tile"], "ad_host": ["vast.exoclick{n}.test", "imasdk.googleapis.com"],
               "prev": ["teaser", "storyboard", "sprite"], "bg": ["background", "hero-banner"], "trailer": ["teaser"], "promo": ["sponsor"]}


def _id(r, n=11):
    return "".join(r.choice("abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789_-") for _ in range(n))


class Pagina:
    def __init__(self):
        self.html = ""
        self.url = ""
        self.verdade: set[str] = set()     # endereços (absolutos) do vídeo principal, qualquer qualidade vale
        self.familia = ""
        self.espera = "video"               # video | nenhum | navegador
        self.iscas: list[str] = []
        self.peculiar: list[str] = []
        self.tipo_verdade = ""


def gerar(seed: int, conjunto: str = "treino", familia: str | None = None, local: bool = False) -> Pagina:
    """local=True: página para o servidor local (sem endereços relativos à raiz e sem players de outros sites)."""
    r = random.Random(seed * 7919 + (0 if conjunto == "treino" else 1_000_003))
    isc = ISCAS_TREINO if conjunto == "treino" else ISCAS_TESTE
    if familia is None:
        fams = [f for f in FAMILIAS if not (local and f == "iframe_embed")]
        pesos = [PESOS_FAMILIA.get(f, 1.0) for f in fams]
        familia = r.choices(fams, pesos)[0]
    pg = Pagina()
    pg.familia = familia
    site = f"{r.choice(['www.', '', 'm.'])}{r.choice(['tube', 'vids', 'cine', 'canal', 'video', 'media', 'clip', 'portal'])}{r.randint(1, 999)}.test"
    cdn = r.choice([f"cdn{r.randint(1, 9)}.{site.split('.', 1)[-1] if site.startswith(('www.', 'm.')) else site}",
                    f"media{r.randint(1, 50)}.cdnhost{r.randint(1, 99)}.test", f"vod.{r.choice(['akamai', 'cloudfront', 'edge'])}{r.randint(1, 9)}.test",
                    site])
    vid = r.randint(10000, 9999999)
    chave = _id(r, r.choice([8, 16, 32]))
    alturas = r.sample([240, 360, 480, 720, 1080, 1440, 2160], r.randint(1, 4))
    # endereço da página: às vezes leva o número do vídeo (como na maioria dos sites), às vezes só um título
    jeito = r.random()
    if jeito < 0.55:
        pagina_id = str(vid)
    elif jeito < 0.75:
        pagina_id = f"{r.randint(1000, 99999999)}"      # número que NÃO é o do arquivo
    else:
        pagina_id = r.choice(["titulo-do-video-de-hoje", "melhores-momentos", "entrevista-completa", "aula-01"])
    pg.url = f"https://{site}/{r.choice(['watch', 'video', 'v', 'videos', 'post', 'p'])}/{pagina_id}" + \
        r.choice(["", "/", f"/{_id(r, 8).lower()}-titulo-do-video", "?ref=home"])

    # ------------------------------------------------ como escrever um endereço (peculiaridades)
    def caminho_mp4(h=None, ext="mp4", pref="v"):
        partes = [pref, str(vid), chave]
        nome = r.choice([f"{h}p.{ext}" if h else f"video.{ext}", f"{vid}_{h}.{ext}" if h else f"{vid}.{ext}",
                         f"{chave[:6]}-{h}p.{ext}" if h else f"{chave[:6]}.{ext}"])
        if r.random() < 0.05:
            nome = nome.replace(f".{ext}", f".{ext.upper()}")
            pg.peculiar.append("ext_maiuscula")
        if r.random() < 0.04:
            nome = "vídeo final " + nome
            pg.peculiar.append("acento_espaco")
        return "/" + "/".join(partes) + "/" + nome

    def absoluto(caminho, host=None, assinar=None):
        host = host or cdn
        q = ""
        if assinar if assinar is not None else r.random() < 0.35:
            q = f"?token={_id(r, 24)}&expires={r.randint(1700000000, 1800000000)}"
            if r.random() < 0.3:
                q += f"&ip=10.0.{r.randint(0, 255)}.{r.randint(0, 255)}"
        return "https://" + host + quote(caminho, safe="/%._-~") + q

    def escrever(u_abs, contexto="attr"):
        """Mesmo endereço em formas diferentes: relativo, //, &amp;, \\/ , \\u002F."""
        forma = r.random()
        u = u_abs
        host_pag = site
        if contexto == "attr":
            if forma < 0.15 and not local and u.startswith("https://" + host_pag):
                u = u[len("https://" + host_pag):]
                pg.peculiar.append("relativo")
            elif forma < 0.25:
                u = u[len("https:"):]
                pg.peculiar.append("protocolo_relativo")
            if "&" in u and r.random() < 0.6:
                u = u.replace("&", "&amp;")
        else:  # dentro de JSON/JS
            if forma < 0.4:
                u = u.replace("/", "\\/")
                pg.peculiar.append("barra_escapada")
            elif forma < 0.5:
                u = u.replace("/", "\\u002F")
                pg.peculiar.append("unicode_escapado")
            elif forma < 0.55:
                u = u.replace("&", "\\u0026")
        return u

    principal: list[tuple[str, int]] = []
    hls_url = absoluto(f"/hls/{vid}/{chave}/" + r.choice(["master.m3u8", "index.m3u8", "playlist.m3u8", f"{vid}.m3u8"]))
    dash_url = absoluto(f"/dash/{vid}/{chave}/" + r.choice(["manifest.mpd", "stream.mpd", f"{vid}.mpd"]))
    corpo: list[str] = []
    head: list[str] = [f"<title>Vídeo {vid} | Canal {r.randint(1, 99)}</title>", '<meta charset="utf-8">']
    scripts: list[str] = []

    def mp4s(n=None):
        hs = sorted(alturas, reverse=True)[: n or len(alturas)]
        return [(absoluto(caminho_mp4(h)), h) for h in hs]

    f = familia
    if f == "html5_src":
        u, h = mp4s(1)[0]
        principal = [(u, h)]
        extra = r.choice(["controls", "controls preload=\"metadata\"", "controls playsinline", "controls autoplay"])
        corpo.append(f'<div class="{r.choice(["player", "video-wrap", "content", "main"])}"><video {extra} '
                     f'poster="https://{cdn}/thumb/{vid}.jpg" width="{r.choice([640, 854, 1280, "100%"])}" src="{escrever(u)}"></video></div>')
    elif f == "html5_sources":
        principal = mp4s()
        srcs = "".join(f'<source src="{escrever(u)}" type="video/mp4" {r.choice(["label", "res", "size", "data-res", "title"])}="{h}{r.choice(["p", ""])}">'
                       for u, h in principal)
        corpo.append(f'<div class="player-container"><video id="v{vid}" class="{r.choice(["video-js vjs-default-skin", "plyr", "mejs__player", "x"])}" controls>{srcs}</video></div>')
    elif f == "og_only":
        u, h = mp4s(1)[0]
        principal = [(u, h)]
        head.append(f'<meta property="og:video" content="{escrever(u)}">')
        head.append('<meta property="og:video:type" content="video/mp4">')
        if r.random() < 0.5:
            head.append(f'<meta property="og:video:secure_url" content="{escrever(u)}">')
        corpo.append('<div id="player-root"></div>')
    elif f == "jsonld":
        alvo = r.choice(["mp4", "hls"])
        u = mp4s(1)[0][0] if alvo == "mp4" else hls_url
        principal = [(u, 0)]
        ld = {"@context": "https://schema.org", "@type": "VideoObject", "name": "Vídeo", "contentUrl": u,
              "thumbnailUrl": f"https://{cdn}/t/{vid}.jpg", "uploadDate": "2025-01-01", "duration": "PT3M2S"}
        if r.random() < 0.4:
            ld = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage", "name": "x"}, ld]}
        txt = json.dumps(ld, ensure_ascii=r.random() < 0.5)
        if r.random() < 0.5:
            txt = txt.replace("/", "\\/")
        head.append(f'<script type="application/ld+json">{txt}</script>')
        corpo.append('<div class="video-player" data-player="custom"></div>')
    elif f == "jwplayer":
        if r.random() < 0.5:
            principal = [(hls_url, 0)]
            cfg = f'file: "{escrever(hls_url, "js")}"'
        else:
            principal = mp4s()
            cfg = "sources: [" + ",".join(f'{{file: "{escrever(u, "js")}", label: "{h}p"}}' for u, h in principal) + "]"
        corpo.append(f'<div id="jw{vid}"></div>')
        scripts.append(f'<script src="https://cdn.jwplayer.com/libraries/abc.js"></script><script>jwplayer("jw{vid}").setup({{{cfg}, image: "https://{cdn}/p/{vid}.jpg", width: "100%"}});</script>')
    elif f == "videojs":
        u = r.choice([hls_url, mp4s(1)[0][0]])
        principal = [(u, 0)]
        tipo = "application/x-mpegURL" if u.endswith(".m3u8") or ".m3u8?" in u else "video/mp4"
        setup = json.dumps({"sources": [{"src": u, "type": tipo}], "fluid": True}).replace('"', "&quot;")
        corpo.append(f'<video id="vjs{vid}" class="video-js" controls data-setup="{setup}"></video>')
    elif f == "plyr":
        principal = mp4s()
        fontes = ",".join(f'{{src: "{escrever(u, "js")}", type: "video/mp4", size: {h}}}' for u, h in principal)
        corpo.append('<video id="plyr" playsinline controls></video>')
        scripts.append(f'<script>const player = new Plyr("#plyr"); player.source = {{type: "video", sources: [{fontes}]}};</script>')
    elif f == "hlsjs":
        principal = [(hls_url, 0)]
        corpo.append('<video id="video" controls></video>')
        var = r.choice(["src", "videoSrc", "streamUrl"])
        scripts.append(f'<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script><script>var {var} = "{escrever(hls_url, "js")}"; '
                       f'if (Hls.isSupported()) {{ var hls = new Hls(); hls.loadSource({var}); hls.attachMedia(document.getElementById("video")); }}</script>')
    elif f == "dashjs":
        principal = [(dash_url, 0)]
        corpo.append('<video id="dv" controls></video>')
        scripts.append(f'<script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script><script>var p = dashjs.MediaPlayer().create(); '
                       f'p.initialize(document.querySelector("#dv"), "{escrever(dash_url, "js")}", true);</script>')
    elif f == "shaka":
        principal = [(dash_url, 0)]
        corpo.append('<video id="sv" controls></video>')
        scripts.append(f'<script>const pl = new shaka.Player(document.getElementById("sv")); pl.load("{escrever(dash_url, "js")}");</script>')
    elif f == "flowplayer":
        principal = mp4s(2)
        fontes = ",".join(f'{{type: "video/mp4", src: "{escrever(u, "js")}"}}' for u, _ in principal)
        corpo.append('<div class="flowplayer" id="fp"></div>')
        scripts.append(f'<script>flowplayer("#fp", {{clip: {{sources: [{fontes}]}}}});</script>')
    elif f == "clappr":
        u = r.choice([hls_url, mp4s(1)[0][0]])
        principal = [(u, 0)]
        corpo.append('<div id="clp"></div>')
        scripts.append(f'<script>var player = new Clappr.Player({{source: "{escrever(u, "js")}", parentId: "#clp"}});</script>')
    elif f == "fluid":
        principal = mp4s(1)
        corpo.append(f'<video id="fl"><source src="{escrever(principal[0][0])}" type="video/mp4"/></video>')
        scripts.append('<script src="https://cdn.fluidplayer.com/v3/current/fluidplayer.min.js"></script><script>fluidPlayer("fl");</script>')
    elif f == "artplayer":
        u = r.choice([hls_url, mp4s(1)[0][0]])
        principal = [(u, 0)]
        corpo.append('<div class="artplayer-app"></div>')
        scripts.append(f'<script>var art = new Artplayer({{container: ".artplayer-app", url: "{escrever(u, "js")}"}});</script>')
    elif f == "social_json":
        principal = mp4s(2)
        hd = principal[0][0]
        sd = principal[-1][0]
        blob = {"video": {"id": str(vid), "playable_url": sd, "playable_url_quality_hd": hd, "hd_src": hd, "sd_src": sd,
                          "thumbnail": f"https://{cdn}/t.jpg"}}
        txt = json.dumps(blob).replace("/", "\\/")
        if r.random() < 0.5:
            txt = txt.replace("&", "\\u0026")
        scripts.append(f'<script type="application/json" data-sjs>{{"require":[["ScheduledServerJS","handle",null,[{txt}]]]}}</script>')
        corpo.append('<div role="main"><div data-pagelet="video"></div></div>')
    elif f == "twitter_card":
        u = mp4s(1)[0][0]
        principal = [(u, 0)]
        head.append(f'<meta name="twitter:player:stream" content="{escrever(u)}">')
        head.append('<meta name="twitter:card" content="player">')
    elif f == "iframe_embed":
        em = r.choice(EMBED_HOSTS).format(id=_id(r), n=r.randint(100000, 99999999))
        principal = [(em, 0)]
        corpo.append(f'<div class="embed-responsive {r.choice(["video", "post-video", "content"])}"><iframe width="{r.choice([560, 640, 800])}" '
                     f'height="{r.choice([315, 360, 450])}" src="{em}" frameborder="0" allowfullscreen></iframe></div>')
    elif f == "flashvars":
        u = mp4s(1)[0][0]
        principal = [(u, 0)]
        corpo.append(f'<object type="application/x-shockwave-flash" data="/player.swf" width="640" height="360">'
                     f'<param name="flashvars" value="file={quote(u, safe="")}&autostart=false"></object>')
    elif f == "atob":
        u = mp4s(1)[0][0]
        principal = [(u, 0)]
        b = base64.b64encode(u.encode()).decode()
        corpo.append('<video id="ab" controls></video>')
        scripts.append(f'<script>document.getElementById("ab").src = atob("{b}");</script>')
    elif f == "encoded_param":
        u = r.choice([mp4s(1)[0][0], hls_url])
        principal = [(u, 0)]
        corpo.append(f'<iframe src="https://{site}/player.php?file={quote(u, safe="")}&amp;autoplay=0" width="720" height="405"></iframe>')
    elif f == "lazy_data":
        u = mp4s(1)[0][0]
        principal = [(u, 0)]
        attr = r.choice(["data-src", "data-video", "data-video-src", "data-mp4", "data-file"])
        corpo.append(f'<div class="lazy-player video-container" {attr}="{escrever(u)}" data-poster="/p.jpg"></div>')
    elif f == "download_link":
        u = mp4s(1)[0][0]
        principal = [(u, 0)]
        corpo.append(f'<div class="post-video"><p>Clique para baixar:</p><a class="btn" href="{escrever(u)}" download>Baixar vídeo</a></div>')
    elif f == "nuxt_state":
        u = r.choice([hls_url, mp4s(1)[0][0]])
        principal = [(u, 0)]
        estado = {"data": [{"video": {"id": vid, "title": "x", r.choice(["videoUrl", "streamUrl", "playbackUrl", "manifestUrl"]): u}}]}
        scripts.append(f'<script>window.__NUXT__={json.dumps(estado).replace("/", chr(92) + "u002F")};</script>')
        corpo.append('<div id="__nuxt"></div>')
    elif f == "mse_blob_js":
        pg.espera = "navegador"
        partes = hls_url.split("/")
        scripts.append(f'<script>var a=["{partes[2]}","{"/".join(partes[3:-1])}"]; var b="{partes[-1][::-1]}"; '
                       f'fetch(location.protocol+"//"+a[0]+"/"+a[1]+"/"+b.split("").reverse().join("")).then(r=>r.text());</script>')
        corpo.append('<video id="m" controls></video>')
        principal = [(hls_url, 0)]
    elif f == "sem_video":
        pg.espera = "nenhum"
        corpo.append("<article><h1>Notícia</h1>" + "<p>texto do artigo sem nenhum vídeo principal.</p>" * r.randint(3, 30) + "</article>")
    pg.verdade = {u for u, _ in principal}
    pg.tipo_verdade = familia

    # ------------------------------------------------ iscas
    n_iscas = r.choice([0, 1, 2, 2, 3, 4, 5, 6, 8])
    if familia == "sem_video":
        n_iscas = r.choice([0, 1, 2, 3])
    tipos = ["propaganda", "fundo", "relacionados", "trailer", "audio", "legenda", "previa_principal", "promo_rodape",
             "storyboard", "outro_artigo", "gif_mp4", "relacionados_json", "proximo_video"]
    for _ in range(n_iscas):
        t = r.choice(tipos)
        pg.iscas.append(t)
        if t == "propaganda":
            host = r.choice(isc["ad_host"]).format(n=r.randint(1, 99))
            ad = f"https://{host}/{r.choice(['vast', 'ads', 'creative', 'preroll'])}/{_id(r, 10)}.mp4"
            if r.random() < 0.5:
                scripts.append(f'<script>var adConfig = {{tag: "https://{host}/vast.xml?id={r.randint(1, 999)}", '
                               f'{r.choice(["file", "src", "mediaFile"])}: "{escrever(ad, "js")}", skip: 5}};</script>')
            else:
                corpo.append(f'<div class="{r.choice(["ad-slot", "banner", "advert", "ads"])}"><video autoplay muted src="{escrever(ad)}" width="300" height="250"></video></div>')
        elif t == "fundo":
            bg = f"https://{site}/assets/{r.choice(isc['bg'])}/{r.choice(['loop', 'intro', 'header', 'bg'])}{r.randint(1, 9)}.mp4"
            corpo.insert(0, f'<section class="{r.choice(isc["bg"])}"><video autoplay muted loop playsinline src="{escrever(bg)}"></video></section>')
        elif t == "relacionados":
            cls = r.choice(isc["rel_cls"])
            itens = []
            for _k in range(r.randint(2, 12)):
                o = r.randint(10000, 9999999)
                pv = f"https://{cdn}/{r.choice(isc['prev'])}/{o}/{_id(r, 8)}.mp4"
                itens.append(f'<a href="/watch/{o}"><img src="https://{cdn}/t/{o}.jpg"><video class="{r.choice(isc["prev"])}" muted '
                             f'{r.choice(["data-src", "src", "data-video"])}="{escrever(pv)}"></video></a>')
            pos = r.choice(["aside", "div", "section"])
            corpo.append(f'<{pos} class="{cls}">' + "".join(itens) + f"</{pos}>")
        elif t == "trailer":
            tr = f"https://{cdn}/{r.choice(isc['trailer'])}s/{r.randint(1, 9999)}/{r.choice(isc['trailer'])}_{_id(r, 6)}.mp4"
            corpo.append(f'<aside class="sidebar widget"><h3>Em breve</h3><video controls width="320" height="180" src="{escrever(tr)}"></video></aside>')
        elif t == "audio":
            au = f"https://{cdn}/audio/{vid}/{chave}/{r.choice(['audio', 'aac', 'track'])}.m4a"
            scripts.append(f'<script>var audioTrack = {{url: "{escrever(au, "js")}", lang: "pt"}};</script>')
        elif t == "legenda":
            corpo.append(f'<track kind="subtitles" src="https://{cdn}/subs/{vid}.pt.vtt" srclang="pt">')
        elif t == "previa_principal":
            pv = f"https://{cdn}/{r.choice(isc['prev'])}/{vid}/{r.choice(isc['prev'])}_{vid}.mp4"
            corpo.append(f'<div class="video-info"><video class="{r.choice(isc["prev"])}" muted loop autoplay width="160" height="90" src="{escrever(pv)}"></video></div>')
        elif t == "promo_rodape":
            pr = f"https://{site}/media/{r.choice(isc['promo'])}/{_id(r, 6)}.mp4"
            corpo.append(f'<footer><div class="{r.choice(isc["promo"])}"><video autoplay muted src="{escrever(pr)}"></video></div></footer>')
        elif t == "storyboard":
            scripts.append(f'<script>var sb = "https://{cdn}/{r.choice(["storyboard", "sprites", "thumbs"])}/{vid}/M$M.jpg";</script>')
        elif t == "outro_artigo":
            o = r.randint(10000, 9999999)
            ou = f"https://{cdn}/v/{o}/{_id(r, 10)}/{r.choice(alturas)}p.mp4"
            corpo.append(f'<div class="{r.choice(isc["rel_cls"])}"><a href="/watch/{o}" class="{r.choice(["card", "tile", "item"])}">'
                         f'<video preload="none" muted src="{escrever(ou)}" width="240" height="135"></video><span>Próximo vídeo</span></a></div>')
        elif t == "relacionados_json":
            # lista de relacionados no MESMO formato de endereço do principal (o caso mais difícil)
            lista = []
            for _k in range(r.randint(2, 8)):
                o = r.randint(10000, 9999999)
                lista.append({"id": o, "title": f"Vídeo {o}", r.choice(["url", "src", "video_url"]):
                              f"https://{cdn}/v/{o}/{_id(r, len(chave))}/{r.choice(alturas)}p.mp4", "thumb": f"https://{cdn}/t/{o}.jpg"})
            txt = json.dumps({r.choice(["related", "recommended", "upNext", "suggestions"]): lista})
            if r.random() < 0.5:
                txt = txt.replace("/", "\\/")
            scripts.append(f"<script>window.{r.choice(['pageData', 'INITIAL', 'cfgRel'])} = {txt};</script>")
        elif t == "proximo_video":
            o = r.randint(10000, 9999999)
            nx = f"https://{cdn}/v/{o}/{_id(r, len(chave))}/{r.choice(alturas)}p.mp4"
            scripts.append(f'<script>var {r.choice(["nextVideo", "autoplayNext", "upnext"])} = {{"file": "{escrever(nx, "js")}", "countdown": 10}};</script>')
        elif t == "gif_mp4":
            gm = f"https://{cdn}/gifs/{_id(r, 8)}.mp4"
            corpo.append(f'<p>Reação: <video autoplay muted loop playsinline width="200" src="{escrever(gm)}"></video></p>')

    # ------------------------------------------------ peculiaridades do HTML
    if r.random() < 0.1:
        corpo.append("<!-- " + "comentário " * r.randint(1, 50) + f" antigo: https://{cdn}/old/{vid}.mp4 -->")
        pg.peculiar.append("comentario")
    if r.random() < 0.08:
        corpo.append("<div><p>sem fechar <span>tags <b>bagunçadas")
        pg.peculiar.append("html_quebrado")
    if r.random() < 0.05:
        corpo.append("<p>" + ("texto longo de recheio " * r.randint(2000, 20000)) + "</p>")
        pg.peculiar.append("pagina_enorme")
    if not local and r.random() < 0.06:
        head.insert(0, f'<base href="https://{site}/">')
        pg.peculiar.append("base_href")
    r.shuffle(scripts)
    html = "<!doctype html><html lang=\"pt-br\"><head>" + "".join(head) + "</head><body>" + \
           "<header><nav><a href=\"/\">Início</a></nav></header>" + "".join(corpo) + "".join(scripts) + "</body></html>"
    if r.random() < 0.15:
        html = html.replace(">\n<", "><").replace("  ", " ")
    if r.random() < 0.3:
        html = html.replace("><", ">\n<")
    pg.html = html
    return pg
