#!/usr/bin/env python3
"""Testes de tools/trace_from_log.py (Fase B).

Rode dentro do guard:
    tools/with_results_guard.sh python3 -m unittest tools/test_trace_from_log.py -v

O log sintético (N = 4) é escrito num diretório temporário fora do repositório. Os testes com
dados reais só leem results-synapt/.
"""
import copy
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trace_from_log as t  # noqa: E402
import build_site_data as b  # noqa: E402

REAL = os.path.join(os.path.dirname(HERE), "results-synapt")


def ip(i):
    return f"fe80::ff:fe00:{i + 1:x}"


def synthetic(mut=None):
    """Cenário N=4: líder 0 com membros 1 e 2; o nó 3 elege o 2 (que não é líder).
    Onda 1 antes do ciclo 10 derruba o 0; no ciclo 10, 1 e 2 vão para RECLUSTER (S1),
    2 vira novo líder com 1 seguidor; a partir do ciclo 11, o 3 é membro do 2."""
    m = mut or {}
    L = ["Contaski_V1", "AP: fe80::ff:fe00:5"]
    for i, d in enumerate([141.421, 100, 100, 0]):
        L.append(f"N: IP {ip(i)} D {d}")
    elects = m.get("elects", [0, 0, 0, 2])
    for i, j in enumerate(elects):
        L.append(f"N: {ip(i)} CLSize 3 elects {ip(j)}")
    L += ["N: LS " + ip(0), f"LR: {ip(0)} at 91.5"]
    for k in range(t.NC):
        if k in m.get("skip_cycles", ()):
            continue
        tt = 170 + 5 * k
        if k == 10:
            L += ["FAILURE_WAVE: === Onda 1 de 1 no tempo 220s ===",
                  f"FAILURE_WAVE_ELIGIBLE: Leader {ip(0)} with 2 real members (clusterList size=2)",
                  f"FAILURE_WAVE: Líder {ip(0)} falhou na onda 1 no tempo 220 - "
                  f"{m.get('logged_orphans', 2)} nós órfãos (clusterList size=2)",
                  f"FAILURE_WAVE: Onda 1 completa — 1 líderes falharam, {m.get('logged_orphans', 2)} órfãos reais"]
        L.append(f"INTUITIVE: t={tt} QI=1 SR=1 Pthr=0.3 TII=0.4 ARF=0.01 Anom=0 Orphans=0")
        if k == 10:
            L.append(m.get("orphan2", f"INTUITIVE_ORPHAN: {ip(2)} [ORPHAN_ONLY] → RECLUSTER (simOrphans=1 r=10) [S1]"))
            L.append(f"INTUITIVE_ORPHAN: {ip(1)} [ORPHAN_ONLY] → RECLUSTER (simOrphans=1 r=10) [S1]")
            L.append("INTUITIVE_RECLUSTER: 2 órfãos marcados para reclusterização")
            if not m.get("no_becomeleader"):
                L.append(f"N: BecomeLeader {ip(2)} at 220")
            L.append(f"INTUITIVE_RECLUSTER: Novo cluster - Líder {ip(2)} com 1 seguidores")
            L.append("INTUITIVE_ORPHANS: Processados 2 órfãos | REALLOCATE=0 RECLUSTER=2 DO_NOTHING=0 "
                     "| Pendentes=0 | S1=2 S2=0")
        else:
            L.append("INTUITIVE_ORPHANS: Nenhum órfão para processar")
    cols = [[0] * t.NC for _ in b.COLS]
    for k in range(t.NC):
        cols[t.C_SR][k] = 0.75 if k < 10 else (0.0 if k == 10 else 1.0)
    cols[t.C_ORF][10] = m.get("csv_orf", 2)
    cols[t.C_REC][10] = 2
    cols[t.C_S1][10] = 2
    if "sr" in m:
        cols[t.C_SR][m["sr"][0]] = m["sr"][1]
    return "\n".join(L) + "\n", cols


class SyntheticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="synapt-trace-test-")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, **mut):
        text, cols = synthetic(mut)
        path = os.path.join(self.tmp, "simulation.log")
        with open(path, "w") as f:
            f.write(text)
        return t.build_trace(path, cols, 4, "x", 1, 0.2)

    def test_happy_path_trace_and_roles(self):
        tr, motivo = self.build()
        self.assertIsNone(motivo)
        self.assertEqual(tr["lid0"], [0, 0, 0, 2])
        self.assertEqual(tr["lideres0"], [0])
        self.assertNotIn("pos", tr)
        self.assertEqual(tr["ondas"], [{"w": 1, "t": 220.0, "k": 10, "lf": [0]}])
        self.assertEqual(tr["ev"], [{"k": 10, "o": [[2, 1, 2, 0, -1], [1, 1, 2, 0, -1]],
                                     "p": [[2, 1]], "pend": []}])
        rc = t.role_counts(tr)
        self.assertEqual((rc[0]["lider"], rc[0]["membro"], rc[0]["fora"]), (1, 2, 1))
        self.assertEqual((rc[10]["morto"], rc[10]["orfao"], rc[10]["fora"], rc[10]["s1"]), (1, 2, 1, 2))
        self.assertEqual((rc[11]["morto"], rc[11]["novo_lider"], rc[11]["reclusterizado"], rc[11]["membro"]),
                         (1, 1, 1, 1))

    def test_positions_from_grid(self):
        self.assertEqual(t.positions(4), [(0, 0), (100, 0), (0, 100), (100, 100)])
        sq, per = t.grid(200)
        self.assertEqual((sq, per), (15, 14))

    def assertSem(self, prefix, **mut):
        tr, motivo = self.build(**mut)
        self.assertIsNone(tr)
        self.assertTrue(motivo.startswith(prefix), motivo)

    def test_count_mismatch(self):
        self.assertSem("4:", csv_orf=3)

    def test_missing_cycle(self):
        self.assertSem("ciclos do log", skip_cycles=(50,))

    def test_bad_ip(self):
        self.assertSem("IP fora do domínio", orphan2="INTUITIVE_ORPHAN: 2001::1 [ORPHAN_ONLY] → RECLUSTER (x) [S1]")

    def test_malformed_orphan_line(self):
        self.assertSem("linha de órfão malformada", orphan2="INTUITIVE_ORPHAN: quebrado")

    def test_becomeleader_missing(self):
        self.assertSem("6:", no_becomeleader=True)

    def test_A1_sr_identity(self):
        self.assertSem("A1:", sr=(20, 0.5))

    def test_A2_failure_orphans(self):
        self.assertSem("A2:", logged_orphans=3)

    def test_duplicate_or_short_election(self):
        self.assertSem("eleição", elects=[0, 0, 0])


@unittest.skipUnless(os.path.isdir(REAL), "results-synapt ausente")
class RealDataTests(unittest.TestCase):
    """Verificações não vazias: adulterar dados reais tem de gerar divergências."""

    def load(self, g, r):
        d = os.path.join(REAL, g, f"run_{r:03d}")
        cols = b.parse_stats(os.path.join(d, "IntuitiveStats.csv"))
        p = t.parse_log(os.path.join(d, "simulation.log"), int(g[:3]))
        tr = t.make_trace(p, int(g[:3]), g, r, 0.2)
        return p, tr, cols

    def test_real_run_passes_and_tampering_fails(self):
        p, tr, cols = self.load("200_with_failures", 6)
        self.assertEqual(t.verify(tr, p, cols), [])
        # um membro de um líder inicial passa a "eleger" outro líder: A1/A2 têm de acusar
        lid0 = tr["lid0"]
        leaders = tr["lideres0"]
        member = next(i for i in range(tr["n"]) if i not in leaders and lid0[i] in leaders)
        other = next(x for x in leaders if x != lid0[member])
        bad = copy.deepcopy(tr)
        bad["lid0"][member] = other
        probs = t.verify(bad, p, cols)
        self.assertTrue(any(x.startswith(("A1", "A2", "7")) for x in probs), probs[:3])

    def test_promoted_electors_become_members(self):
        """Rodada em que um nó eleito sem ser líder vira novo líder: seus eleitores viram membros."""
        p, tr, cols = self.load("200_with_failures", 6)
        lid0, leaders = tr["lid0"], set(tr["lideres0"])
        new_leaders = {x for e in tr["ev"] for x, _ in e["p"]}
        promoted = {lid0[i] for i in range(tr["n"]) if lid0[i] not in leaders} & new_leaders
        if not promoted:
            self.skipTest("rodada sem promoção de nó eleito")
        y = sorted(promoted)[0]
        k_prom = next(e["k"] for e in tr["ev"] if any(x == y for x, _ in e["p"]))
        states = t.reconstruct(tr)
        electors = [i for i in range(tr["n"]) if lid0[i] == y and i != y]
        before = states[k_prom]["roles"]
        after = states[k_prom + 1]["roles"]
        self.assertTrue(all(before[i] in (t.OUTSIDER, t.ORPHAN, t.DEAD) for i in electors))
        self.assertTrue(any(after[i] == t.MEMBER for i in electors))


if __name__ == "__main__":
    unittest.main()
