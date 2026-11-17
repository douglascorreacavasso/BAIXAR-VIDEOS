# -*- coding: utf-8 -*-
"""Conferência depois das correções da bateria de 100 mil: 1.000 cenários de ponta a ponta NOVOS (sementes 200000+)
e os 10 sites difíceis. Grava testes/saida/conferencia_final.json."""
import json
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
sys.path.insert(0, str(AQUI))

import bateria_e2e  # noqa: E402  (fixa a pasta de dados de teste)

r1 = bateria_e2e.rodar(1000, 200000, 4, prefixo="conf")
import sites_dificeis  # noqa: E402
r2 = sites_dificeis.rodar(1)
resumo = {"quando": time.strftime("%Y-%m-%d %H:%M"),
          "ponta_a_ponta": {k: v for k, v in r1.items() if k not in ("por", "falhas")},
          "sites_dificeis": {k: v for k, v in r2.items() if k != "resultados"}}
(AQUI / "saida" / "conferencia_final.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(resumo, ensure_ascii=False, indent=1))
