# -*- coding: utf-8 -*-
"""Rede: DNS próprio (DoH) quando o do sistema falha, Tor e a escada de rotas (ideia da V5 do doug)."""
from __future__ import annotations

import socket
import threading
import time

import requests

TOR_SOCKS = "socks5h://127.0.0.1:9050"
TOR_CTRL = ("127.0.0.1", 9051)

DOH = ["https://1.1.1.1/dns-query", "https://8.8.8.8/resolve", "https://9.9.9.9/dns-query",
       "https://1.0.0.1/dns-query", "https://8.8.4.4/resolve"]

_dns_cache: dict[str, tuple[str, float]] = {}
_dns_lock = threading.Lock()
_orig_gai = socket.getaddrinfo


def resolver_doh(host: str, proxy: str | None = None) -> str | None:
    agora = time.time()
    with _dns_lock:
        c = _dns_cache.get(host)
        if c and c[1] > agora:
            return c[0]
    px = {"http": proxy, "https": proxy} if proxy else None
    for url in DOH:
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"},
                             timeout=5, proxies=px)
            for ans in r.json().get("Answer", []):
                if ans.get("type") == 1:
                    ip = ans["data"]
                    ttl = max(60, min(3600, int(ans.get("TTL", 300))))
                    with _dns_lock:
                        _dns_cache[host] = (ip, agora + ttl)
                    return ip
        except Exception:
            continue
    return None


def _gai_com_doh(host, port, *a, **k):
    try:
        return _orig_gai(host, port, *a, **k)
    except socket.gaierror:
        if not isinstance(host, str) or host in ("localhost",) or host.replace(".", "").isdigit():
            raise
        ip = resolver_doh(host)
        if ip:
            return _orig_gai(ip, port, *a, **k)
        raise


def ligar_doh():
    socket.getaddrinfo = _gai_com_doh


def tcp_ok(host: str, port: int = 443, timeout: float = 4) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except Exception:
        return False


def tor_ligado() -> bool:
    return tcp_ok("127.0.0.1", 9050, timeout=1.5)


def tor_novo_circuito() -> bool:
    """Pede circuito novo ao Tor (normalmente troca o país de saída)."""
    try:
        s = socket.create_connection(TOR_CTRL, timeout=4)
        s.sendall(b'AUTHENTICATE ""\r\n')
        if b"250" not in s.recv(256):
            s.close()
            return False
        s.sendall(b"SIGNAL NEWNYM\r\n")
        ok = b"250" in s.recv(256)
        s.close()
        time.sleep(3)
        return ok
    except Exception:
        return False


def ip_publico(proxy: str | None = None) -> tuple[str | None, str]:
    px = {"http": proxy, "https": proxy} if proxy else None
    for u in ("https://ipinfo.io/json", "https://api.ipify.org?format=json"):
        try:
            d = requests.get(u, timeout=8, proxies=px).json()
            return d.get("ip"), d.get("country", "?")
        except Exception:
            continue
    return None, "?"


def montar_rotas(proxies_txt: str, usar_tor: bool) -> list[dict]:
    rotas = [{"nome": "conexão direta", "proxy": None, "tor": False}]
    for p in [x.strip() for x in (proxies_txt or "").splitlines() if x.strip() and not x.strip().startswith("#")]:
        if "://" not in p:
            p = "http://" + p
        rotas.append({"nome": f"proxy {p.split('@')[-1][:40]}", "proxy": p, "tor": False})
    if usar_tor and tor_ligado():
        for i in range(3):
            rotas.append({"nome": f"Tor (país #{i + 1})", "proxy": TOR_SOCKS, "tor": True})
    return rotas


def servidor_existe(host: str, proxy: str | None = None) -> bool | None:
    """True = existe; False = o DNS do próprio domínio diz que NÃO existe (NXDOMAIN, conferido em 2 DNS seguros);
    None = não deu para saber. (Achado no caso real do doug, 17/11: o site mandava o vídeo para g1.swiftsrv9.com,
    servidor que não existe mais; nem o navegador tocava.)"""
    px = {"http": proxy, "https": proxy} if proxy else None
    respostas = []
    for url in ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve"):
        try:
            d = requests.get(url, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"},
                             timeout=6, proxies=px).json()
            respostas.append(d.get("Status"))
            if d.get("Status") == 0 and d.get("Answer"):
                return True
        except Exception:
            continue
    if respostas and all(s == 3 for s in respostas):
        return False
    return None