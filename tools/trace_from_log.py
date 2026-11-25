#!/usr/bin/env python3
"""Trace de topologia por rodada a partir do simulation.log (Fase B do simulador web).

Contrato: docs/plano-fase-b.md (inclui as decisões do usuário ao fim do arquivo).

- `build_trace(log_path, cols, n, g, r, thS1)` lê o log (só leitura), monta o trace e o
  verifica contra as 17 colunas do IntuitiveStats.csv (`cols`, na ordem de
  build_site_data.COLS). Devolve (trace, None) ou (None, motivo).
- `reconstruct(trace)` reaplica os eventos do trace e devolve, para cada ciclo k, o estado
  NO INÍCIO do ciclo (antes das ações; o mesmo instante de QI/SR/pThreat). É a mesma
  máquina de estados que site/index.html implementa em JavaScript.
- `role_counts(trace)` resume esses estados por ciclo; usado pela verificação e pelo e2e.

Uso na linha de comando (para o e2e):
    python3 tools/trace_from_log.py --counts site/data/trace_<grupo>_r<NNN>.json
"""
import json
import math
import re
import sys
from collections import defaultdict

T0, DT, NC = 170, 5, 145
UNKNOWN = -2
STATES = {"EXISTING_VIABLE": 0, "ORPHAN_ONLY": 1, "NO_MATCH": 2}
ACTS = {"DO_NOTHING": 0, "REALLOCATE": 1, "RECLUSTER": 2}
SIS = {"S1": 0, "S1+S2": 1, "S2": 2}
# papéis no início do ciclo, em ordem de precedência
ROLES = ["morto", "orfao", "novo_lider", "lider", "reclusterizado", "realocado", "membro", "fora"]
DEAD, ORPHAN, NEWLEADER, LEADER, RECL, REALLOC, MEMBER, OUTSIDER = range(8)

# índices das colunas de build_site_data.COLS usadas aqui
C_SR, C_ORF, C_REA, C_REC, C_DN, C_S1, C_S2 = 1, 7, 8, 9, 10, 11, 12

R_IP = re.compile(r"^fe80::ff:fe00:([0-9a-f]+)$")
R_ELECT = re.compile(r"^N: (\S+) CLSize \d+ elects (\S+)$")
R_LS = re.compile(r"^N: LS (\S+)$")
R_LR = re.compile(r"^LR: (\S+) at [\d.]+$")
R_IPD = re.compile(r"^N: IP (\S+) D ([\d.eE+-]+)$")
R_AP = re.compile(r"^AP: (fe80::\S+)$")
R_CYC = re.compile(r"^INTUITIVE: t=([\d.]+) ")
R_WSTART = re.compile(r"^FAILURE_WAVE: === Onda (\d+) de (\d+) no tempo ([\d.]+)s ===$")
R_WELIG = re.compile(r"^FAILURE_WAVE_ELIGIBLE: Leader (\S+) with (\d+) real members")
R_WSKIP = re.compile(r"^FAILURE_WAVE_SKIPPED: Leader (\S+) has 0 real members")
R_WFAIL = re.compile(r"^FAILURE_WAVE: Líder (\S+) falhou na onda (\d+) no tempo ([\d.]+) - (\d+) nós órfãos")
R_WDONE = re.compile(r"^FAILURE_WAVE: Onda (\d+) completa — (\d+) líderes falharam, (\d+) órfãos reais$")
R_ORF = re.compile(r"^INTUITIVE_ORPHAN: (\S+) \[(\w+)\] → (REALLOCATE|RECLUSTER|DO_NOTHING)"
                   r"(?: para (\S+))?(?: \([^)]*\))? \[(S1\+S2|S1|S2)\]$")
R_NEW = re.compile(r"^INTUITIVE_RECLUSTER: Novo cluster - Líder (\S+) com (\d+) seguidores$")
R_FB = re.compile(r"^INTUITIVE_RECLUSTER_FALLBACK: (\S+) não agrupado → pendente$")
R_BL = re.compile(r"^N: BecomeLeader (\S+) at ")
R_SUM = re.compile(r"^INTUITIVE_ORPHANS: Processados (\d+) órfãos \|.*\| Pendentes=(\d+) \|")


class SemTrace(Exception):
    pass


def grid(n):
    """Parâmetros do GridPositionAllocator de contaski.cc (RowFirst)."""
    sq = math.ceil(math.sqrt(40000 / n))
    per = math.ceil(200 / sq)
    return sq, per


def positions(n):
    sq, per = grid(n)
    return [((i % per) * sq, (i // per) * sq) for i in range(n)]


# ---------------------------------------------------------------- leitura do log

def parse_log(log_path, n):
    """Lê o simulation.log e devolve os fatos usados pelo trace. Levanta SemTrace."""
    def idx(ip, allow_ap=False):
        m = R_IP.match(ip)
        if not m:
            raise SemTrace(f"IP fora do domínio: {ip}")
        v = int(m.group(1), 16) - 1
        if not (0 <= v < n or (allow_ap and v == n)):
            raise SemTrace(f"índice de nó fora de [0, {n}): {ip}")
        return v

    elects, ls, lr, ipd, ap = {}, set(), set(), {}, None
    cycles, waves, pending_waves, cur_wave = [], [], [], None

    with open(log_path, "r", errors="replace") as f:
        for ln in f:
            if ln.startswith(("N: MS", "N: C ", "AP: MS", "AP: T")):
                continue
            ln = ln.rstrip("\n")
            if ln.startswith("N: "):
                if " elects " in ln:
                    m = R_ELECT.match(ln)
                    if not m:
                        raise SemTrace(f"linha de eleição malformada: {ln[:80]}")
                    a = idx(m.group(1))
                    if a in elects:
                        raise SemTrace(f"eleição duplicada para o nó {a}")
                    elects[a] = idx(m.group(2))
                elif ln.startswith("N: LS "):
                    m = R_LS.match(ln)
                    if not m:
                        raise SemTrace(f"linha LS malformada: {ln[:80]}")
                    ls.add(idx(m.group(1)))
                elif ln.startswith("N: IP "):
                    m = R_IPD.match(ln)
                    if not m:
                        raise SemTrace(f"linha N: IP malformada: {ln[:80]}")
                    ipd[idx(m.group(1))] = float(m.group(2))
                elif ln.startswith("N: BecomeLeader "):
                    m = R_BL.match(ln)
                    if not m or not cycles:
                        raise SemTrace(f"BecomeLeader fora de ciclo ou malformado: {ln[:80]}")
                    cycles[-1]["bl"].append(idx(m.group(1)))
                continue
            if ln.startswith("LR: "):
                m = R_LR.match(ln)
                if not m:
                    raise SemTrace(f"linha LR malformada: {ln[:80]}")
                lr.add(idx(m.group(1)))
                continue
            if ln.startswith("AP: fe80"):
                m = R_AP.match(ln)
                if m and ap is None:
                    ap = idx(m.group(1), allow_ap=True)
                continue
            if ln.startswith("INTUITIVE: t="):
                m = R_CYC.match(ln)
                if not m:
                    raise SemTrace(f"linha de ciclo malformada: {ln[:80]}")
                k = len(cycles)
                cycles.append({"t": float(m.group(1)), "o": [], "new": [], "fb": [], "bl": [], "sum": None})
                for w in pending_waves:
                    w["k"] = k
                waves.extend(pending_waves)
                pending_waves, cur_wave = [], None
                continue
            if ln.startswith("INTUITIVE_ORPHAN: "):
                m = R_ORF.match(ln)
                if not m or not cycles:
                    raise SemTrace(f"linha de órfão malformada ou fora de ciclo: {ln[:90]}")
                if m.group(2) not in STATES:
                    raise SemTrace(f"estado de órfão desconhecido: {m.group(2)}")
                act = ACTS[m.group(3)]
                if (act == 1) != (m.group(4) is not None):
                    raise SemTrace(f"destino incoerente com a ação: {ln[:90]}")
                dest = idx(m.group(4)) if m.group(4) else -1
                cycles[-1]["o"].append([idx(m.group(1)), STATES[m.group(2)], act, SIS[m.group(5)], dest])
                continue
            if ln.startswith("INTUITIVE_RECLUSTER: Novo"):
                m = R_NEW.match(ln)
                if not m or not cycles:
                    raise SemTrace(f"linha de novo cluster malformada: {ln[:80]}")
                cycles[-1]["new"].append([idx(m.group(1)), int(m.group(2))])
                continue
            if ln.startswith("INTUITIVE_RECLUSTER_FALLBACK"):
                m = R_FB.match(ln)
                if not m or not cycles:
                    raise SemTrace(f"linha de fallback malformada: {ln[:80]}")
                cycles[-1]["fb"].append(idx(m.group(1)))
                continue
            if ln.startswith("INTUITIVE_ORPHANS: Processados"):
                m = R_SUM.match(ln)
                if not m or not cycles:
                    raise SemTrace(f"resumo de órfãos malformado: {ln[:80]}")
                cycles[-1]["sum"] = (int(m.group(1)), int(m.group(2)))
                continue
            if ln.startswith("FAILURE_WAVE"):
                if (m := R_WSTART.match(ln)):
                    cur_wave = {"w": int(m.group(1)), "t": float(m.group(3)), "elig": {}, "skip": set(),
                                "fail": [], "done": None, "k": None}
                    pending_waves.append(cur_wave)
                elif (m := R_WELIG.match(ln)):
                    if cur_wave is None:
                        raise SemTrace("FAILURE_WAVE_ELIGIBLE fora de onda")
                    cur_wave["elig"][idx(m.group(1))] = int(m.group(2))
                elif (m := R_WSKIP.match(ln)):
                    if cur_wave is None:
                        raise SemTrace("FAILURE_WAVE_SKIPPED fora de onda")
                    cur_wave["skip"].add(idx(m.group(1)))
                elif (m := R_WFAIL.match(ln)):
                    if cur_wave is None or int(m.group(2)) != cur_wave["w"]:
                        raise SemTrace(f"falha de líder fora da onda corrente: {ln[:80]}")
                    cur_wave["fail"].append((idx(m.group(1)), int(m.group(4))))
                elif (m := R_WDONE.match(ln)):
                    if cur_wave is None or int(m.group(1)) != cur_wave["w"]:
                        raise SemTrace("fechamento de onda sem onda corrente")
                    cur_wave["done"] = (int(m.group(2)), int(m.group(3)))
                elif ln.startswith(("FAILURE_WAVE: Líder", "FAILURE_WAVE: Onda", "FAILURE_WAVE: ===",
                                    "FAILURE_WAVE_ELIGIBLE", "FAILURE_WAVE_SKIPPED")):
                    raise SemTrace(f"linha de onda malformada: {ln[:80]}")
                continue
    if pending_waves:
        raise SemTrace("onda de falha depois do último ciclo")
    return {"elects": elects, "ls": ls, "lr": lr, "ipd": ipd, "ap": ap, "cycles": cycles, "waves": waves}


# ---------------------------------------------------------------- trace

def make_trace(p, n, g, r, thS1):
    """Checagens estruturais 1–3 e montagem do JSON do trace (sem posições)."""
    if len(p["elects"]) != n:
        raise SemTrace(f"eleição: {len(p['elects'])} linhas para N={n}")
    if p["ls"] != p["lr"]:
        raise SemTrace("conjunto LS diferente do conjunto LR")
    if p["ap"] != n:
        raise SemTrace("IP do AP diferente do esperado")
    for leader in p["ls"]:
        if p["elects"][leader] != leader:
            raise SemTrace(f"líder {leader} não elegeu a si mesmo")
    pos = positions(n)
    if len(p["ipd"]) != n:
        raise SemTrace(f"distâncias ao AP registradas para {len(p['ipd'])} de {n} nós")
    for i, (x, y) in enumerate(pos):
        d = math.hypot(x - 100, y - 100)
        if abs(p["ipd"][i] - d) > 1e-5 * d + 1e-4:
            raise SemTrace(f"posição da grade do nó {i} não confere com a distância do log")
    cyc = p["cycles"]
    if len(cyc) != NC or any(abs(c["t"] - (T0 + DT * k)) > 1e-9 for k, c in enumerate(cyc)):
        raise SemTrace(f"ciclos do log: {len(cyc)} (esperado {NC}, t = 170 + 5k)")
    ev = []
    for k, c in enumerate(cyc):
        if c["o"] or c["new"] or c["fb"]:
            ev.append({"k": k, "o": c["o"], "p": c["new"], "pend": c["fb"]})
    ondas = [{"w": w["w"], "t": round(w["t"], 3), "k": w["k"], "lf": [x for x, _ in w["fail"]]}
             for w in p["waves"]]
    return {"model": "synapt", "fonte": "log", "v": 1, "g": g, "r": r, "n": n,
            "t0": T0, "dt": DT, "nc": NC, "ap": [100, 100], "thS1": thS1,
            "lid0": [p["elects"][i] for i in range(n)], "lideres0": sorted(p["ls"]),
            "ondas": ondas, "ev": ev}


def reconstruct(tr, hook=None):
    """Estados no início de cada ciclo. hook(evento, dados, lid, lead, dead) é chamado
    antes de cada onda ("wave") e antes de cada falha de líder ("fail")."""
    n = tr["n"]
    lid = list(tr["lid0"])
    lead = [0] * n
    for i in tr["lideres0"]:
        lead[i] = 1
    dead = [False] * n
    realoc = [False] * n
    pending = set()
    waves_by_k = defaultdict(list)
    for w in tr["ondas"]:
        waves_by_k[w["k"]].append(w)
    ev_by_k = {e["k"]: e for e in tr["ev"]}
    out = []
    for k in range(tr["nc"]):
        for w in waves_by_k[k]:
            if hook:
                hook("wave", w, lid, lead, dead)
            for x in w["lf"]:
                if hook:
                    hook("fail", (w, x), lid, lead, dead)
                lead[x] = 0
                dead[x] = True
        e = ev_by_k.get(k, {"o": [], "p": [], "pend": []})
        O = {o[0] for o in e["o"]}
        roles = []
        for i in range(n):
            if dead[i]:
                ro = DEAD
            elif i in O:
                ro = ORPHAN
            elif lead[i] == 2:
                ro = NEWLEADER
            elif lead[i] == 1:
                ro = LEADER
            elif lid[i] == UNKNOWN:
                ro = RECL
            elif lead[lid[i]] > 0 and not dead[lid[i]]:
                ro = REALLOC if realoc[i] else MEMBER
            else:
                ro = OUTSIDER
            roles.append(ro)
        out.append({"k": k, "roles": roles, "lid": list(lid), "dead": list(dead), "lead": list(lead),
                    "O": O, "pending": set(pending), "o": e["o"], "p": e["p"], "pend": e["pend"]})
        cand, newp = [], set()
        for i, st, a, s, d in e["o"]:
            if a == 1:
                lid[i] = d
                realoc[i] = True
            elif a == 2:
                cand.append(i)
            else:
                newp.add(i)
        newleaders = {x for x, _ in e["p"]}
        for x in newleaders:
            lead[x] = 2
            lid[x] = x
            realoc[x] = False
        fb = set(e["pend"])
        for i in cand:
            if i in newleaders or i in fb:
                continue
            lid[i] = UNKNOWN
            realoc[i] = False
        pending = newp | fb
    return out


def role_counts(tr):
    res = []
    for st in reconstruct(tr):
        c = {name: 0 for name in ROLES}
        for ro in st["roles"]:
            c[ROLES[ro]] += 1
        c["s1"] = sum(1 for o in st["o"] if o[3] == 0)
        c["s2"] = sum(1 for o in st["o"] if o[3] != 0)
        res.append(c)
    return res


# ---------------------------------------------------------------- verificação

def verify(tr, p, cols):
    """Verificações 4–7, A1 e A2 do plano. Devolve a lista de divergências."""
    probs = []
    n = tr["n"]

    def members(x, lid, dead):
        return sum(1 for i in range(n) if not dead[i] and i != x and lid[i] == x)

    def unknown_alive(lid, dead):
        return sum(1 for i in range(n) if not dead[i] and lid[i] == UNKNOWN)

    def check_count(x, logged, lid, lead, dead, what):
        known = members(x, lid, dead)
        if lead[x] == 1:
            if known != logged:
                probs.append(f"A2: {what} do líder {x}: log {logged} × reconstruído {known}")
        elif not (known <= logged <= known + unknown_alive(lid, dead)):
            probs.append(f"A2: {what} do novo líder {x}: log {logged} fora de [{known}, "
                         f"{known + unknown_alive(lid, dead)}]")

    waves_parsed = {(w["w"], w["k"]): w for w in p["waves"]}

    def hook(kind, data, lid, lead, dead):
        if kind == "wave":
            w = waves_parsed[(data["w"], data["k"])]
            alive_leaders = {i for i in range(n) if lead[i] > 0 and not dead[i]}
            if set(w["elig"]) | w["skip"] != alive_leaders:
                probs.append(f"A2: onda {w['w']}: líderes elegíveis+ignorados ≠ líderes vivos reconstruídos")
            for x, cnt in w["elig"].items():
                check_count(x, cnt, lid, lead, dead, f"membros (elegível, onda {w['w']})")
            for x in w["skip"]:
                check_count(x, 0, lid, lead, dead, f"membros (ignorado, onda {w['w']})")
            if w["done"] is None or w["done"][0] != len(w["fail"]) or \
                    w["done"][1] != sum(c for _, c in w["fail"]):
                probs.append(f"5: onda {w['w']}: totais de 'completa' ≠ soma das falhas")
        else:
            wave, x = data
            w = waves_parsed[(wave["w"], wave["k"])]
            if lead[x] == 0 or dead[x]:
                probs.append(f"5: líder {x} caiu na onda {wave['w']} sem ser líder vivo")
                return
            logged = dict(w["fail"])[x]
            check_count(x, logged, lid, lead, dead, f"órfãos (falha, onda {wave['w']})")

    states = reconstruct(tr, hook)
    cyc = p["cycles"]
    for k, st in enumerate(states):
        o, c = st["o"], cyc[k]
        acts = [sum(1 for x in o if x[2] == a) for a in (1, 2, 0)]
        s1 = sum(1 for x in o if x[3] == 0)
        if len(o) != cols[C_ORF][k]:
            probs.append(f"4: ciclo {k}: {len(o)} órfãos × CSV {cols[C_ORF][k]}")
        if acts != [cols[C_REA][k], cols[C_REC][k], cols[C_DN][k]]:
            probs.append(f"4: ciclo {k}: ações {acts} × CSV")
        if s1 != cols[C_S1][k] or len(o) - s1 != cols[C_S2][k]:
            probs.append(f"4: ciclo {k}: S1/S2 {s1}/{len(o) - s1} × CSV {cols[C_S1][k]}/{cols[C_S2][k]}")
        dead, lid, lead = st["dead"], st["lid"], st["lead"]
        alive = n - sum(dead)
        covered = sum(1 for ro in st["roles"] if ro in (NEWLEADER, LEADER, RECL, REALLOC, MEMBER))
        if round(cols[C_SR][k] * alive) != covered:
            probs.append(f"A1: ciclo {k}: round(SR·vivos) = {round(cols[C_SR][k] * alive)} × "
                         f"vivos − órfãos − fora = {covered}")
        derived = {i for i in range(n) if not dead[i] and lid[i] >= 0 and dead[lid[i]]} | st["pending"]
        O = st["O"]
        if not derived <= O:
            probs.append(f"7: ciclo {k}: {len(derived - O)} órfão(s) esperados ausentes do log")
        if any(dead[i] for i in O):
            probs.append(f"7: ciclo {k}: órfão morto")
        if any(lid[i] != UNKNOWN for i in O - derived):
            probs.append(f"7: ciclo {k}: órfão sem líder morto nem pertencimento desconhecido")
        cand = [x[0] for x in o if x[2] == 2]
        newl = [x for x, _ in st["p"]]
        if sorted(c["bl"]) != sorted(newl):
            probs.append(f"6: ciclo {k}: BecomeLeader ≠ Novo cluster")
        if not set(newl) <= set(cand) or not set(st["pend"]) <= set(cand):
            probs.append(f"6: ciclo {k}: novo líder ou pendente que não foi marcado para RECLUSTER")
        if sum(m for _, m in st["p"]) != len(cand) - len(st["pend"]) - len(newl):
            probs.append(f"6: ciclo {k}: seguidores ≠ RECLUSTER − pendentes − novos líderes")
        for i, s_, a, si, d in o:
            if a == 1 and (lead[d] == 0 or dead[d]):
                probs.append(f"6: ciclo {k}: REALLOCATE de {i} para {d}, que não é líder vivo")
        if o:
            dn = sum(1 for x in o if x[2] == 0)
            if c["sum"] != (len(o), dn + len(st["pend"])):
                probs.append(f"6: ciclo {k}: resumo 'Processados/Pendentes' {c['sum']} × "
                             f"{(len(o), dn + len(st['pend']))}")
    return probs


def build_trace(log_path, cols, n, g, r, thS1):
    try:
        p = parse_log(log_path, n)
        tr = make_trace(p, n, g, r, thS1)
    except SemTrace as e:
        return None, str(e)
    probs = verify(tr, p, cols)
    if probs:
        more = f" (+{len(probs) - 1} outra(s))" if len(probs) > 1 else ""
        return None, probs[0] + more
    return tr, None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--counts":
        with open(sys.argv[2]) as f:
            print(json.dumps(role_counts(json.load(f)), separators=(",", ":")))
    else:
        print(__doc__)
        sys.exit(2)
