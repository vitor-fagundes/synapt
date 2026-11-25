#!/usr/bin/env python3
"""Gera site/data/*.json (Fase A do simulador web do SYNAPT) a partir de results-synapt/.

Uso:
    python3 tools/build_site_data.py [--results DIR] [--out DIR] [--readme ARQ]
                                     [--failure-response ARQ] [--max-bytes N]
                                     [--check-only] [--allow-dirty]

Rode sempre dentro do guard, que protege results-synapt/:
    tools/with_results_guard.sh python3 tools/build_site_data.py

Contrato: docs/plano-simulador-web.md. Resumo:
- Só lê results-synapt/ (arquivos abertos em modo "r"); nunca grava ali.
- Gera em .site-build-tmp/data/ (raiz do repositório) e troca site/data por
  dois os.rename. A troca não é atômica; é recuperável com
  `git checkout -- site/data`.
- Códigos de saída: 0 ok; 2 erro de entrada/formato/ambiente/publicação;
  3 divergência de validação não aceita; 4 tamanho acima de --max-bytes.
"""
import argparse
import csv
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys

T0, DT, NC = 170, 5, 145
NRUNS = 35

CSV_HEADER = ["timestamp", "qi", "sr", "tii", "arf", "meanRho", "netLearning", "pThreat",
              "totalOrphans", "actionsReallocate", "actionsRecluster", "actionsDoNothing",
              "cycleS1", "cycleS2", "anomalies", "qDoNothing", "qReallocate", "qRecluster"]
COLS = ["qi", "sr", "tii", "arf", "rho", "nl", "pt", "orf", "rea", "rec", "dn",
        "s1", "s2", "an", "qd", "qr", "qc"]
INT_COLS = {"orf", "rea", "rec", "dn", "s1", "s2", "an"}
DEC = {c: (6 if c == "arf" else 4) for c in COLS}

STATES = ["EXISTING_VIABLE", "ORPHAN_ONLY", "NO_MATCH"]
ACTIONS = ["DO_NOTHING", "REALLOCATE_TO_EXISTING", "RECLUSTER_ORPHANS"]

# (id, N, cenário) dos grupos publicados, na ordem de exibição.
GROUPS = [
    ("200_no_failures", 200, "no"),
    ("200_with_failures", 200, "with"),
    ("250_no_failures", 250, "no"),
    ("250_with_failures", 250, "with"),
    ("250_random_failures", 250, "random"),
    ("300_no_failures", 300, "no"),
    ("300_with_failures", 300, "with"),
]
VALIDATION_ONLY = [("300_with_failures_OLD_backup", 300, "with")]

RUN_EVID_RUNNER = "runner versionado (run_simulations.sh, sectional@c5e78e0)"
RUN_EVID_DECL = ("--run=0 declarado pelo autor; consistente com a sequência idêntica "
                 "de eventos do NS-3")
RUN_EVID = {
    "200_no_failures": RUN_EVID_RUNNER, "200_with_failures": RUN_EVID_RUNNER,
    "250_no_failures": RUN_EVID_RUNNER, "250_with_failures": RUN_EVID_RUNNER,
    "300_no_failures": RUN_EVID_RUNNER,
    "250_random_failures": RUN_EVID_DECL, "300_with_failures": RUN_EVID_DECL,
}
RUN_EVID_REF = {"250_random_failures": "250_no_failures", "300_with_failures": "300_no_failures"}
PREFIX_MIN_LINES = 1000

NOTAS = {
    "200_no_failures": "Sem ondas de falha; sem órfãos, portanto sem decisões S1/S2.",
    "200_with_failures": "3 ondas × 30% dos líderes elegíveis, configuradas para 300/450/600 s; "
                         "disparam em 310/460/610 s.",
    "250_no_failures": "Sem ondas de falha; sem órfãos, portanto sem decisões S1/S2.",
    "250_with_failures": "3 ondas × 30% dos líderes elegíveis, configuradas para 300/450/600 s; "
                         "disparam em 310/460/610 s.",
    "250_random_failures": "3 ondas por rodada com tempo sorteado em U(200, 800) s (disparo = "
                           "sorteio + 10 s) e percentual sorteado em U(10, 30), via "
                           "std::random_device; o sorteio de cada rodada vem do log.",
    "300_no_failures": "Pré-calibragem: gerado antes de synapt@901ed81, sem DENSITY_OVERRIDE "
                       "(limiar S1 0,20; ξ −0,05). Sem órfãos, portanto sem decisões S1/S2.",
    "300_with_failures": "Pós-calibragem (synapt@901ed81): limiar S1 0,245 e ξ −0,033. "
                         "A linha “300 nós — com falhas” da tabela do README corresponde ao "
                         "grupo 300_with_failures_OLD_backup (pré-calibragem), não a este.",
}
ORIGENS = [("/scratch/sectional/results-intuitive/", "sectional@c5e78e0 (results-intuitive)"),
           ("/scratch/synapt/results-synapt/", "synapt (results-synapt)")]

RE_WAVE_FIRE = re.compile(r"^FAILURE_WAVE: === Onda (\d+) de (\d+) no tempo ([\d.]+)s ===$")
RE_WAVE_DONE = re.compile(r"^FAILURE_WAVE: Onda (\d+) completa — (\d+) líderes falharam, "
                          r"(\d+) órfãos reais$")
RE_CFG_RANDOM = re.compile(r"^FAILURE_CONFIG_RANDOM: Onda (\d+) t=([\d.]+)s, ([\d.]+)%$")
RE_CFG_FIXED = re.compile(r"^FAILURE_CONFIG: Onda (\d+) agendada para t=([\d.]+)s$")
RE_CENARIO4 = re.compile(r"^CENARIO4: (\d+) ondas de falha configuradas, ([\d.]+)% cada$")
RE_OVERRIDE = re.compile(r"^DENSITY_OVERRIDE: N=(\d+) → ξ_anomalous=(-?[\d.]+), "
                         r"THRESHOLD_S1=([\d.]+)$")
RE_PERSIST_PATH = re.compile(r"^INTUITIVE_PERSIST: .*?(/\S+knowledge\.dat)")
RE_PERSIST_ZERO = re.compile(r"^INTUITIVE_PERSIST: Arquivo .* não encontrado")
RE_PREFIX_DROP = re.compile(r"^(\d+\+|CENARIO|FAILURE_CONFIG|INTUITIVE_PERSIST|DENSITY_OVERRIDE)")
RE_MS_SIZE = re.compile(r"^(N: MS \(.*), \d+\)$")

README_QI = re.compile(r"^\| (\d+) nós — (sem|com) falhas \| ([\d,]+)% ± ([\d,]+)% \| "
                       r"([\d,]+)% ± ([\d,]+)% \|\s*$")
README_S1 = re.compile(r"^\| (\d+) nós \| (\d+)% \| (\d+)% \|\s*$")


class BuildError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def fail(code, msg):
    raise BuildError(code, msg)


# ---------------------------------------------------------------- parsers

def parse_stats(path):
    """Lê IntuitiveStats.csv e devolve 17 colunas × 145 ciclos (ordem de COLS)."""
    try:
        with open(path, "r", newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        fail(2, f"não consegui ler {path}: {e}")
    if not rows or rows[0] != CSV_HEADER:
        fail(2, f"{path}: cabeçalho diferente do esperado")
    body = rows[1:]
    if len(body) != NC:
        fail(2, f"{path}: {len(body)} linhas de dados (esperado {NC})")
    cols = [[] for _ in COLS]
    for k, row in enumerate(body):
        if len(row) != len(CSV_HEADER):
            fail(2, f"{path}: linha {k + 2} com {len(row)} campos")
        try:
            vals = [float(x) for x in row]
        except ValueError:
            fail(2, f"{path}: valor não numérico na linha {k + 2}")
        if any(not math.isfinite(v) for v in vals):
            fail(2, f"{path}: valor não finito na linha {k + 2}")
        if vals[0] != T0 + DT * k:
            fail(2, f"{path}: timestamp {vals[0]} na linha {k + 2} (esperado {T0 + DT * k})")
        for i, c in enumerate(COLS):
            v = vals[i + 1]
            if c in INT_COLS:
                if v != int(v):
                    fail(2, f"{path}: contagem não inteira em {c}, linha {k + 2}")
                v = int(v)
            cols[i].append(v)
    return cols


def parse_qvalues(path):
    try:
        with open(path, "r") as f:
            lines = [ln.rstrip("\n") for ln in f]
    except OSError as e:
        fail(2, f"não consegui ler {path}: {e}")
    q, state, eps, s1, s2 = {}, None, None, None, None
    for ln in lines:
        m = re.match(r"^Estado (\w+):$", ln)
        if m:
            state = m.group(1)
            if state not in STATES:
                fail(2, f"{path}: estado desconhecido {state}")
            q[state] = {}
            continue
        m = re.match(r"^  (\w+): (-?[\d.eE+-]+)$", ln)
        if m and state is not None:
            if m.group(1) not in ACTIONS:
                fail(2, f"{path}: ação desconhecida {m.group(1)}")
            q[state][m.group(1)] = float(m.group(2))
            continue
        m = re.match(r"^Epsilon final: ([\d.eE+-]+)$", ln)
        if m:
            eps = float(m.group(1))
        m = re.match(r"^S1 count total: (\d+)$", ln)
        if m:
            s1 = int(m.group(1))
        m = re.match(r"^S2 count total: (\d+)$", ln)
        if m:
            s2 = int(m.group(1))
    for s in STATES:
        if s not in q or set(q[s]) != set(ACTIONS):
            fail(2, f"{path}: Q-table incompleta no estado {s}")
    if eps is None or s1 is None or s2 is None:
        fail(2, f"{path}: faltam Epsilon final / S1 count total / S2 count total")
    return {"Q": [[q[s][a] for a in ACTIONS] for s in STATES], "eps": eps, "s1tot": s1, "s2tot": s2}


def parse_log(path, cen):
    """Extrai ondas, override, origem e se a rodada começou do zero."""
    fire, done, cfg_rand, cfg_fixed, cen4, override = [], [], [], [], [], []
    persist_paths, zero = set(), False
    try:
        with open(path, "r", errors="replace") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln.startswith(("FAILURE_", "CENARIO4", "DENSITY_OVERRIDE", "INTUITIVE_PERSIST")):
                    continue
                for rx, acc in ((RE_WAVE_FIRE, fire), (RE_WAVE_DONE, done), (RE_CFG_RANDOM, cfg_rand),
                                (RE_CFG_FIXED, cfg_fixed), (RE_CENARIO4, cen4), (RE_OVERRIDE, override)):
                    m = rx.match(ln)
                    if m:
                        acc.append(m.groups())
                        break
                m = RE_PERSIST_PATH.match(ln)
                if m:
                    persist_paths.add(m.group(1))
                if RE_PERSIST_ZERO.match(ln):
                    zero = True
    except OSError as e:
        fail(2, f"não consegui ler {path}: {e}")

    counts = (len(cfg_fixed), len(cen4), len(cfg_rand), len(fire), len(done))
    expected = {"no": (0, 0, 0, 0, 0), "with": (3, 1, 0, 3, 3), "random": (3, 0, 3, 3, 3)}[cen]
    if counts != expected:
        fail(2, f"{path}: cardinalidade de linhas de onda {counts} (esperado {expected} "
                f"para cenário '{cen}')")
    waves = []
    if cen != "no":
        if [int(g[0]) for g in fire] != [1, 2, 3] or [int(g[0]) for g in done] != [1, 2, 3]:
            fail(2, f"{path}: ondas fora de ordem")
        if cen == "with":
            cfg = sorted((int(g[0]), float(g[1])) for g in cfg_fixed)
            pct = [float(cen4[0][1])] * 3
        else:
            cfg = sorted((int(g[0]), float(g[1])) for g in cfg_rand)
            pct = [float(g[2]) for g in sorted(cfg_rand, key=lambda g: int(g[0]))]
        if [w for w, _ in cfg] != [1, 2, 3]:
            fail(2, f"{path}: numeração das ondas configuradas")
        times = [float(g[2]) for g in fire]
        if times != sorted(times):
            fail(2, f"{path}: ondas não estão em ordem crescente de tempo")
        for i in range(3):
            t, cfg_t = times[i], cfg[i][1]
            if abs(t - (cfg_t + 10.0)) > 0.001:
                fail(2, f"{path}: onda {i + 1} disparou em {t} s, esperado cfg_t + 10 = {cfg_t + 10}")
            waves.append({"w": i + 1, "t": round(t, 3), "cfg_t": round(cfg_t, 3),
                          "pct": round(pct[i], 3), "lf": int(done[i][1]), "orf": int(done[i][2])})
    if len(override) > 1:
        fail(2, f"{path}: mais de uma linha DENSITY_OVERRIDE")
    origem = set()
    for p in persist_paths:
        hit = [label for frag, label in ORIGENS if frag in p]
        if len(hit) != 1:
            fail(2, f"{path}: knowledgePath de origem desconhecida: {p}")
        origem.add(hit[0])
    if len(origem) != 1:
        fail(2, f"{path}: origem do knowledgePath indefinida ({sorted(origem)})")
    ov = None
    if override:
        ov = {"xi": float(override[0][1]), "thS1": float(override[0][2])}
    return {"waves": waves, "override": ov, "origem": origem.pop(), "zero": zero}


def log_prefix(path):
    """Prefixo do log até a 1ª linha 'N: C ' (exclusive), sem linhas de configuração e
    sem o tamanho dos pacotes 'N: MS (..., tamanho)'."""
    out = []
    try:
        with open(path, "r", errors="replace") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if RE_PREFIX_DROP.match(ln):
                    continue
                if ln.startswith("N: C "):
                    break
                out.append(RE_MS_SIZE.sub(r"\1)", ln))
    except OSError as e:
        fail(2, f"não consegui ler {path}: {e}")
    return out


def prefix_match(a, b):
    n = min(len(a), len(b))
    return n >= PREFIX_MIN_LINES and a[:n] == b[:n]


# ---------------------------------------------------------------- grupos

def run_dir(results, gid, r):
    return os.path.join(results, gid, f"run_{r:03d}")


def check_layout(results):
    expected = {g for g, _, _ in GROUPS} | {g for g, _, _ in VALIDATION_ONLY}
    try:
        present = {e for e in os.listdir(results) if os.path.isdir(os.path.join(results, e))}
    except OSError as e:
        fail(2, f"não consegui listar {results}: {e}")
    if present != expected:
        fail(2, f"grupos em {results} diferem do esperado: faltam {sorted(expected - present)}, "
                f"sobram {sorted(present - expected)}")
    for g in sorted(expected):
        runs = sorted(e for e in os.listdir(os.path.join(results, g)) if e.startswith("run_"))
        if runs != [f"run_{r:03d}" for r in range(1, NRUNS + 1)]:
            fail(2, f"{g}: rodadas diferentes de run_001..run_{NRUNS:03d}")


def build_group(results, gid, n, cen):
    """Devolve (json_do_grupo, metadados, dados_brutos_para_validação)."""
    runs, raw, overrides, origens = [], [], set(), set()
    prev_s1 = prev_s2 = 0
    for r in range(1, NRUNS + 1):
        d = run_dir(results, gid, r)
        cols = parse_stats(os.path.join(d, "IntuitiveStats.csv"))
        qv = parse_qvalues(os.path.join(d, "IntuitiveQValues.txt"))
        lg = parse_log(os.path.join(d, "simulation.log"), cen)
        s1_run = sum(cols[COLS.index("s1")])
        s2_run = sum(cols[COLS.index("s2")])
        if qv["s1tot"] != prev_s1 + s1_run or qv["s2tot"] != prev_s2 + s2_run:
            fail(3, f"{gid} run_{r:03d}: totais S1/S2 acumulados ({qv['s1tot']}/{qv['s2tot']}) "
                    f"≠ anterior + soma da rodada ({prev_s1 + s1_run}/{prev_s2 + s2_run})")
        prev_s1, prev_s2 = qv["s1tot"], qv["s2tot"]
        overrides.add(json.dumps(lg["override"], sort_keys=True))
        origens.add(lg["origem"])
        runs.append({"r": r, "zero": lg["zero"], "d": cols, "ondas": lg["waves"],
                     "fim": {"eps": qv["eps"], "s1tot": qv["s1tot"], "s2tot": qv["s2tot"],
                             "Q": qv["Q"]}})
        raw.append(cols)
    if len(overrides) != 1:
        fail(2, f"{gid}: DENSITY_OVERRIDE presente em parte das rodadas")
    if len(origens) != 1:
        fail(2, f"{gid}: origem do knowledgePath varia entre rodadas")
    ov = json.loads(overrides.pop())
    meta = {"id": gid, "n": n, "cen": cen, "runs": NRUNS,
            "thS1": ov["thS1"] if ov else 0.20, "xi": ov["xi"] if ov else -0.05,
            "override": ov is not None, "origem": origens.pop()}
    agg_m, agg_sd = [], []
    for i in range(len(COLS)):
        m_row, sd_row = [], []
        for k in range(NC):
            vals = [raw[j][i][k] for j in range(NRUNS)]
            m_row.append(statistics.fmean(vals))
            sd_row.append(statistics.stdev(vals))
        agg_m.append(m_row)
        agg_sd.append(sd_row)
    return {"id": gid, "runs": runs, "agg": {"m": agg_m, "sd": agg_sd}}, meta, raw


def run_evid_check(results, gid):
    ref = RUN_EVID_REF[gid]
    iguais = 0
    for r in range(1, NRUNS + 1):
        a = log_prefix(os.path.join(run_dir(results, gid, r), "simulation.log"))
        b = log_prefix(os.path.join(run_dir(results, ref, r), "simulation.log"))
        if prefix_match(a, b):
            iguais += 1
    return {"ref": ref, "iguais": iguais, "total": NRUNS}


# ---------------------------------------------------------------- validação

def _pct(s):
    return float(s.replace(",", "."))


def validate_readme(readme_path, raw_by_group):
    """Compara QI/SR finais e S1/S2 com o README. Devolve (divergências, linhas de relatório)."""
    try:
        with open(readme_path, "r") as f:
            text = f.read().splitlines()
    except OSError as e:
        fail(2, f"não consegui ler {readme_path}: {e}")
    qi_rows, s1_rows, in_s1 = {}, {}, False
    for ln in text:
        if ln.startswith("### "):
            in_s1 = ln.strip() == "### Distribuição S1/S2"
        m = README_QI.match(ln)
        if m:
            qi_rows[(int(m.group(1)), m.group(2))] = tuple(_pct(m.group(i)) for i in range(3, 7))
        m = README_S1.match(ln)
        if m and in_s1:
            s1_rows[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    if len(qi_rows) != 6 or len(s1_rows) != 3:
        fail(2, f"{readme_path}: tabelas não encontradas (QI/SR: {len(qi_rows)} linhas, "
                f"S1/S2: {len(s1_rows)} linhas)")

    def final_stats(gid):
        raw = raw_by_group[gid]
        qi = [100 * cols[COLS.index("qi")][-1] for cols in raw]
        sr = [100 * cols[COLS.index("sr")][-1] for cols in raw]
        return (statistics.fmean(qi), statistics.stdev(qi), statistics.fmean(sr), statistics.stdev(sr))

    def close(calc, ref):
        return all(abs(c - r) <= 0.05 + 1e-9 for c, r in zip(calc, ref))

    div, rep = [], []
    rep.append("QI/SR finais (t=890 s), média ± DP amostral ao longo das 35 rodadas, em %:")
    for (n, kind), ref in sorted(qi_rows.items()):
        gid = f"{n}_{'no' if kind == 'sem' else 'with'}_failures"
        calc = final_stats(gid)
        ok = close(calc, ref)
        line = (f"  {n} nós — {kind} falhas: dados {calc[0]:.1f} ± {calc[1]:.1f} / {calc[2]:.1f} ± "
                f"{calc[3]:.1f}; README {ref[0]} ± {ref[1]} / {ref[2]} ± {ref[3]}")
        if ok:
            rep.append(line + "  [ok]")
        elif gid == "300_with_failures":
            old = final_stats("300_with_failures_OLD_backup")
            if close(old, ref):
                rep.append(line + "  [divergência aceita]")
                rep.append(f"    300_with_failures_OLD_backup: {old[0]:.1f} ± {old[1]:.1f} / "
                           f"{old[2]:.1f} ± {old[3]:.1f}  [bate com o README]")
            else:
                div.append(line + f" — nem o OLD_backup bate ({old[0]:.1f} ± {old[1]:.1f} / "
                                  f"{old[2]:.1f} ± {old[3]:.1f})")
                rep.append(line + "  [DIVERGE]")
        else:
            div.append(line)
            rep.append(line + "  [DIVERGE]")
    rep.append("Distribuição S1/S2 das decisões por órfão (soma das 35 rodadas dos grupos com "
               "falhas fixas; S1+S2 contado como S2):")
    for n, (r1, r2) in sorted(s1_rows.items()):
        raw = raw_by_group[f"{n}_with_failures"]
        s1 = sum(sum(c[COLS.index("s1")]) for c in raw)
        s2 = sum(sum(c[COLS.index("s2")]) for c in raw)
        p1 = 100 * s1 / (s1 + s2)
        ok = abs(p1 - r1) <= 0.5 and abs((100 - p1) - r2) <= 0.5
        line = (f"  {n} nós: dados S1 {p1:.1f}% / S2 {100 - p1:.1f}% (S1={s1}, S2={s2}); "
                f"README {r1}% / {r2}%")
        rep.append(line + ("  [ok]" if ok else "  [DIVERGE]"))
        if not ok:
            div.append(line)
    raw = raw_by_group["250_random_failures"]
    s1 = sum(sum(c[COLS.index("s1")]) for c in raw)
    s2 = sum(sum(c[COLS.index("s2")]) for c in raw)
    rep.append(f"  (informativo) 250 aleatório: S1 {100 * s1 / (s1 + s2):.1f}% / "
               f"S2 {100 * s2 / (s1 + s2):.1f}% (S1={s1}, S2={s2}); não está no README")
    return div, rep


def crosscheck_failure_response(csv_path, waves_by_group):
    """Confere plots/data/failure_response.csv contra as ondas do log. Só avisa."""
    avisos, seen = [], set()
    try:
        with open(csv_path, "r", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        return [f"não consegui ler {csv_path}: {e}"], 0
    for row in rows:
        key = (row["group"], int(row["run"]), int(row["failure_wave"]))
        seen.add(key)
        g, r, w = key
        if g not in waves_by_group or not (1 <= r <= NRUNS) or not (1 <= w <= 3):
            avisos.append(f"chave extra no CSV: {key}")
            continue
        wave = waves_by_group[g][r - 1][w - 1]
        exp_t = int(wave["t"]) if g.startswith("300_") else int(wave["cfg_t"])
        for campo, csvv, logv in (("failure_time", int(float(row["failure_time"])), exp_t),
                                  ("n_leaders_failed", int(row["n_leaders_failed"]), wave["lf"]),
                                  ("n_orphans", int(row["n_orphans"]), wave["orf"])):
            if csvv != logv:
                avisos.append(f"{key} {campo}: CSV {csvv} × log {logv}")
    for g in ("200_with_failures", "250_with_failures", "300_with_failures"):
        for r in range(1, NRUNS + 1):
            for w in (1, 2, 3):
                if (g, r, w) not in seen:
                    avisos.append(f"chave ausente no CSV: {(g, r, w)}")
    return avisos, len(rows)


# ---------------------------------------------------------------- serialização

def _num(v, dec):
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if not math.isfinite(v):
        fail(2, "valor NaN/infinito na serialização")
    x = round(v, dec)
    if x == 0:
        return 0
    if x == int(x):
        return int(x)
    return x


def compact_group(g):
    runs = []
    for run in g["runs"]:
        d = [[_num(v, DEC[c]) for v in run["d"][i]] for i, c in enumerate(COLS)]
        fim = {"eps": _num(run["fim"]["eps"], 6), "s1tot": run["fim"]["s1tot"],
               "s2tot": run["fim"]["s2tot"],
               "Q": [[_num(v, 4) for v in row] for row in run["fim"]["Q"]]}
        runs.append({"r": run["r"], "zero": run["zero"], "d": d, "ondas": run["ondas"], "fim": fim})
    agg = {k: [[_num(float(v), DEC[c]) for v in g["agg"][k][i]] for i, c in enumerate(COLS)]
           for k in ("m", "sd")}
    return {"id": g["id"], "runs": runs, "agg": agg}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


# ---------------------------------------------------------------- git e publicação

def git(root, *args):
    p = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    if p.returncode != 0:
        fail(2, f"git {' '.join(args)} falhou: {p.stderr.strip()}")
    return p.stdout


def check_results_clean(root, results):
    out = git(root, "status", "--porcelain", "--", os.path.relpath(results, root))
    if out.strip():
        fail(2, "git status --porcelain -- results-synapt não está vazio:\n" + out[:2000])


def known_names():
    return {"index.json"} | {f"{g}.json" for g, _, _ in GROUPS}


def check_out_dir(root, out):
    """site/data só pode ter arquivos conhecidos, todos commitados e sem alteração."""
    if not os.path.lexists(out):
        return
    if os.path.islink(out) or not os.path.isdir(out):
        fail(2, f"{out} não é um diretório real")
    unknown = []
    for e in sorted(os.listdir(out)):
        p = os.path.join(out, e)
        if os.path.islink(p) or not os.path.isfile(p):
            fail(2, f"{p} não é arquivo regular (symlink ou diretório)")
        if e not in known_names() and not re.fullmatch(r"trace_[\w.-]+\.json", e):
            unknown.append(e)
    if unknown:
        fail(2, f"arquivos desconhecidos em {out}: {unknown}. Nada foi apagado; remova-os ou "
                f"mova-os manualmente.")
    st = git(root, "status", "--porcelain", "--untracked-files=all", "--",
             os.path.relpath(out, root))
    if st.strip():
        fail(2, f"{out} tem arquivos não commitados ou alterados:\n{st[:2000]}"
                f"Faça commit ou remova-os manualmente antes de regerar.")


def publish(tmp_root, tmp_data, out):
    """Troca out por tmp_data com dois renames. Não é atômico; é recuperável com
    `git checkout -- site/data`."""
    old = os.path.join(tmp_root, "old")
    had_old = os.path.lexists(out)
    if had_old:
        os.rename(out, old)
    try:
        os.rename(tmp_data, out)
    except OSError as e:
        if had_old:
            try:
                os.rename(old, out)
            except OSError:
                pass
        raise BuildError(2, f"falha ao publicar {out}: {e}. Recupere com: git checkout -- site/data")


def main(argv=None):
    root = os.path.realpath(git(os.path.dirname(os.path.abspath(__file__)),
                                "rev-parse", "--show-toplevel").strip())
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", default=os.path.join(root, "results-synapt"))
    ap.add_argument("--out", default=os.path.join(root, "site", "data"))
    ap.add_argument("--readme", default=os.path.join(root, "README.md"))
    ap.add_argument("--failure-response", default=os.path.join(root, "plots", "data",
                                                                "failure_response.csv"))
    ap.add_argument("--max-bytes", type=int, default=5_000_000)
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true")
    a = ap.parse_args(argv)

    results = os.path.realpath(a.results)
    out = os.path.abspath(a.out)
    tmp_root = os.path.join(root, ".site-build-tmp")
    created_tmp = False
    try:
        if os.path.realpath(out) != os.path.join(root, "site", "data") or \
                out != os.path.join(root, "site", "data"):
            fail(2, f"--out tem de ser {os.path.join(root, 'site', 'data')} (recebido {a.out})")
        for p in (out, tmp_root):
            rp = os.path.realpath(p)
            if rp == results or rp.startswith(results + os.sep):
                fail(2, f"{p} fica dentro de {results}")
        check_results_clean(root, results)
        site = os.path.join(root, "site")
        if os.path.islink(site):
            fail(2, f"{site} é symlink")
        if not a.check_only:
            check_out_dir(root, out)
            if os.path.lexists(tmp_root):
                fail(2, f"{tmp_root} já existe; remova-o manualmente (nada foi apagado)")
        gen_rel = os.path.relpath(os.path.abspath(__file__), root)
        dirty = git(root, "status", "--porcelain", "--", gen_rel).strip()
        if dirty and not a.allow_dirty:
            fail(2, f"{gen_rel} tem alteração não commitada; faça commit ou use --allow-dirty")
        commit = git(root, "rev-parse", "HEAD").strip() + ("-dirty" if dirty else "")

        check_layout(results)
        groups, metas, raw_by_group = {}, [], {}
        for gid, n, cen in GROUPS + VALIDATION_ONLY:
            g, meta, raw = build_group(results, gid, n, cen)
            raw_by_group[gid] = raw
            if (gid, n, cen) in GROUPS:
                groups[gid] = g
                meta["run_evid"] = RUN_EVID[gid]
                if gid in RUN_EVID_REF:
                    meta["run_evid_check"] = run_evid_check(results, gid)
                meta["nota"] = NOTAS[gid]
                metas.append(meta)
            print(f"[ok] {gid}: {NRUNS} rodadas lidas", file=sys.stderr)

        divs, rep = validate_readme(a.readme, raw_by_group)
        print("\n".join(rep))
        for m in metas:
            if "run_evid_check" in m:
                c = m["run_evid_check"]
                print(f"run_evid_check {m['id']} × {c['ref']}: prefixo idêntico em "
                      f"{c['iguais']}/{c['total']} rodadas")
        waves = {gid: [run["ondas"] for run in g["runs"]] for gid, g in groups.items()}
        avisos, nrows = crosscheck_failure_response(a.failure_response, waves)
        print(f"Conferência com failure_response.csv ({nrows} linhas; cobre 200/250/300 com falhas "
              f"fixas; 250_random_failures não consta e não é conferido): {len(avisos)} aviso(s)")
        for av in avisos[:40]:
            print("  aviso: " + av)
        if len(avisos) > 40:
            print(f"  … mais {len(avisos) - 40} aviso(s)")
        if divs:
            fail(3, "divergência com o README além do arredondamento:\n" + "\n".join(divs))

        index = {"v": 1, "commit_codigo": commit,
                 "nota_commit": "dados gerados de results-synapt/ nesse commit",
                 "t0": T0, "dt": DT, "nc": NC, "cols": COLS, "grupos": metas, "traces": []}
        files = {"index.json": dumps(index)}
        for gid, g in groups.items():
            files[f"{gid}.json"] = dumps(compact_group(g))
        sizes = {k: len(v.encode("utf-8")) for k, v in files.items()}

        if a.check_only:
            total = sum(sizes.values())
            print(f"--check-only: nada gravado; tamanho previsto {total} bytes")
            if total > a.max_bytes:
                fail(4, f"tamanho previsto {total} > --max-bytes {a.max_bytes}")
            return 0

        try:
            st_root = os.stat(root).st_dev
            os.makedirs(site, exist_ok=True)
            if os.stat(site).st_dev != st_root:
                fail(2, f"{site} está em outro sistema de arquivos")
            os.mkdir(tmp_root)
        except FileExistsError:
            fail(2, f"{tmp_root} já existe; remova-o manualmente (nada foi apagado)")
        created_tmp = True
        tmp_data = os.path.join(tmp_root, "data")
        os.mkdir(tmp_data)
        for name, text in files.items():
            with open(os.path.join(tmp_data, name), "x", encoding="utf-8") as f:
                f.write(text)
        if os.path.isdir(out):
            for e in sorted(os.listdir(out)):
                if re.fullmatch(r"trace_[\w.-]+\.json", e):
                    src = os.path.join(out, e)
                    if os.path.islink(src) or not os.path.isfile(src):
                        fail(2, f"{src} não é arquivo regular")
                    shutil.copyfile(src, os.path.join(tmp_data, e))
        total = sum(os.path.getsize(os.path.join(tmp_data, e)) for e in os.listdir(tmp_data))
        if total > a.max_bytes:
            fail(4, f"tamanho total {total} > --max-bytes {a.max_bytes}")
        publish(tmp_root, tmp_data, out)
        check_results_clean(root, results)
        print(f"Publicado em {out}: {len(os.listdir(out))} arquivos, {total} bytes "
              f"({total / 1e6:.2f} MB)")
        for k in sorted(sizes):
            print(f"  {k}: {sizes[k]} bytes")
        return 0
    except BuildError as e:
        print(f"ERRO ({e.code}): {e}", file=sys.stderr)
        return e.code
    finally:
        if created_tmp and os.path.isdir(tmp_root):
            shutil.rmtree(tmp_root)


if __name__ == "__main__":
    sys.exit(main())
