# -*- coding: utf-8 -*-
"""Rodada "depois": com a trava pesada pega -> retreina o modelo de fábrica, mede 95 mil (nível 1) e 5 mil ponta a ponta.
Só fica o modelo novo se não cair no nível 1."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path

V6 = Path(r"C:\Users\Douglas\Documents\GitHub\BAIXAR VIDEOS\BAIXAR VIDEO V6")
T = V6 / "testes"
S = T / "saida"
os.chdir(V6)
sys.path.insert(0, str(V6)); sys.path.insert(0, str(T))
import bateria_100k as B  # noqa: E402  (também fixa BAIXADOR_DADOS para dados de teste)

B.pegar_trava("Baixador V6: retreino do modelo + bateria de 100 mil (1 processo, 4 threads, Edge escondido às vezes)")
try:
    mod = V6 / "baixador" / "modelo_inicial.json"
    shutil.copy2(mod, S / "modelo_inicial_ANTES.json")
    t0 = time.time()
    r = subprocess.run([sys.executable, str(T / "treinar_modelo.py"), "20000", "3000"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    (S / "treino.log").write_text(r.stdout + "\n" + r.stderr, encoding="utf-8")
    print("treino ok" if r.returncode == 0 else "treino FALHOU", round(time.time() - t0), "s", flush=True)
    antes = json.loads((S / "nivel1_ANTES.json").read_text(encoding="utf-8"))["taxa_ok"]
    r1 = B.nivel1(95000)
    if r1["taxa_ok"] < antes:
        print(f"modelo novo pior ({r1['taxa_ok']} < {antes}): volta o antigo", flush=True)
        (S / "nivel1_modelo_novo_recusado.json").write_text(json.dumps(r1, ensure_ascii=False, indent=1), encoding="utf-8")
        shutil.copy2(S / "modelo_inicial_ANTES.json", mod)
        import importlib, baixador.aprendizado as A
        importlib.reload(A)
        r1 = B.nivel1(95000)
        r1["modelo"] = "antigo (o novo caiu)"
    else:
        r1["modelo"] = "novo"
    (S / "nivel1_DEPOIS.json").write_text(json.dumps(r1, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in r1.items() if k not in ("por", "falhas")}, ensure_ascii=False), flush=True)
    from bateria_e2e import rodar
    r2 = rodar(5000, 100000, 4, prefixo="e2e100k_DEPOIS")
    resumo = {"quando": time.strftime("%Y-%m-%d %H:%M"), "nivel1": {k: v for k, v in r1.items() if k not in ("por", "falhas")},
              "e2e": {k: v for k, v in r2.items() if k not in ("por", "falhas")}, "total_testes": r1["total"] + r2["total"]}
    (S / "resultado_100k_DEPOIS.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1), flush=True)
finally:
    B.soltar_trava()
