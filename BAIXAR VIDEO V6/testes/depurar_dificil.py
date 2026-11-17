# -*- coding: utf-8 -*-
"""Depuração de um site difícil: roda um cenário e mostra os candidatos com as características que o modelo viu.
Uso: .venv\\Scripts\\python.exe testes\\depurar_dificil.py cookie_js"""
import secrets
import sys
import threading
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
import sites_dificeis as S  # noqa: E402

cen = sys.argv[1] if len(sys.argv) > 1 else "cookie_js"
S.gerar_tudo()
from baixador.pipeline import Motor, Tarefa  # noqa: E402
cfg = {"pasta_destino": str(S.SAIDA / "baixados_depurar"), "usar_tor": False, "proxies": "", "usar_navegador": True,
       "perguntar_na_duvida": False, "mp4_compativel": True, "cookies_navegador": "nenhum", "_q": 720, "simultaneos": 1}
m = Motor(cfg)
srv = S.Quieto(("127.0.0.20", 0), S.H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
cid = "d" + secrets.token_hex(3)
S.ESTADO[cid] = {"cen": cen, "cor": "azul", "tokens": {}}
orig = m._video_do_player


def espiao(c):
    r = orig(c)
    print(f"  _video_do_player={r} prob={c.prob:.3f} origens={sorted(c.origens)} tipo={c.tipo}")
    print("     ctx:", {k: v for k, v in c.ctx.items() if k not in ("_f",)})
    print("     f:", {k: round(v, 2) for k, v in (c.ctx.get("_f") or {}).items()})
    return r


m._video_do_player = espiao
t = Tarefa(url=f"http://127.0.0.20:{srv.server_address[1]}/{cid}/watch")
m.processar(t)
print(t.estado, t.erro[:200])
srv.shutdown()
