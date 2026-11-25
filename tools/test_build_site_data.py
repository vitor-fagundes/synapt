#!/usr/bin/env python3
"""Testes do gerador tools/build_site_data.py.

Rode dentro do guard (protege results-synapt/):
    tools/with_results_guard.sh python3 -m unittest tools/test_build_site_data.py -v

Uma única vez por sessão, monta num diretório temporário (fora do repositório) um
repositório de teste com git init: rodadas COPIADAS de results-synapt/ (CSV e
Q-values inteiros; simulation.log filtrado: as primeiras 1500 linhas do prefixo
mais as linhas de onda/override/persistência), README.md e failure_response.csv.
Cada teste trabalha numa cópia por hardlink desse repositório; arquivos alterados
são substituídos (unlink + escrita), nunca editados no lugar. Nada aqui grava em
results-synapt/ nem em site/ do repositório real.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_ROOT = os.path.dirname(HERE)
REAL_RESULTS = os.path.join(REAL_ROOT, "results-synapt")
GEN = os.path.join(HERE, "build_site_data.py")
PREFIX_KEEP = 1500
TAGS = ("FAILURE_", "CENARIO4", "DENSITY_OVERRIDE", "INTUITIVE", "LR: ", "AP: fe80", "N: LS ", "N: IP ",
        "N: BecomeLeader")


def tagged(ln):
    return ln.startswith(TAGS) or " elects " in ln

sys.path.insert(0, HERE)
import build_site_data as bsd  # noqa: E402


def filtered_log(src, dst):
    with open(src, "r", errors="replace") as f, open(dst, "w") as g:
        seen_c = False
        for i, ln in enumerate(f):
            if not seen_c:
                if ln.startswith("N: C ") or i >= PREFIX_KEEP:
                    seen_c = True
                    g.write("N: C fixture\n")
                    if tagged(ln):
                        g.write(ln)
                    continue
                g.write(ln)
            elif tagged(ln):
                g.write(ln)


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


def commit_all(repo, msg="fixture"):
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)


def snapshot(d):
    if not os.path.isdir(d):
        return None
    out = {}
    for e in sorted(os.listdir(d)):
        with open(os.path.join(d, e), "rb") as f:
            out[e] = f.read()
    return out


def replace_file(path, text):
    os.unlink(path)
    with open(path, "w") as f:
        f.write(text)


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_tmp = tempfile.mkdtemp(prefix="synapt-gen-test-")
        base = os.path.join(cls.base_tmp, "base")
        os.makedirs(os.path.join(base, "tools"))
        shutil.copyfile(GEN, os.path.join(base, "tools", "build_site_data.py"))
        shutil.copyfile(os.path.join(HERE, "trace_from_log.py"), os.path.join(base, "tools", "trace_from_log.py"))
        shutil.copyfile(os.path.join(REAL_ROOT, "README.md"), os.path.join(base, "README.md"))
        os.makedirs(os.path.join(base, "plots", "data"))
        shutil.copyfile(os.path.join(REAL_ROOT, "plots", "data", "failure_response.csv"),
                        os.path.join(base, "plots", "data", "failure_response.csv"))
        for g in sorted(os.listdir(REAL_RESULTS)):
            gdir = os.path.join(REAL_RESULTS, g)
            if not os.path.isdir(gdir):
                continue
            for r in sorted(os.listdir(gdir)):
                if not r.startswith("run_"):
                    continue
                src = os.path.join(gdir, r)
                dst = os.path.join(base, "results-synapt", g, r)
                os.makedirs(dst)
                for name in ("IntuitiveStats.csv", "IntuitiveQValues.txt"):
                    shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))
                filtered_log(os.path.join(src, "simulation.log"), os.path.join(dst, "simulation.log"))
        git(base, "init", "-q")
        commit_all(base, "base")
        cls.base = base
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base_tmp)

    def setUp(self):
        type(self).n += 1
        self.repo = os.path.join(self.base_tmp, f"t{self.n}")
        shutil.copytree(self.base, self.repo, copy_function=os.link, symlinks=True)
        self.out = os.path.join(self.repo, "site", "data")
        self.res = os.path.join(self.repo, "results-synapt")

    def tearDown(self):
        shutil.rmtree(self.repo)

    def run_gen(self, *args, env=None):
        p = subprocess.run([sys.executable, os.path.join(self.repo, "tools", "build_site_data.py"),
                            *args], capture_output=True, text=True, env=env)
        return p.returncode, p.stdout + p.stderr

    def seed_out(self, extra=None):
        """Cria um site/data commitado com um index.json antigo (e extras)."""
        os.makedirs(self.out)
        with open(os.path.join(self.out, "index.json"), "w") as f:
            f.write('{"antigo":true}')
        for name, text in (extra or {}).items():
            with open(os.path.join(self.out, name), "w") as f:
                f.write(text)
        commit_all(self.repo, "seed out")
        return snapshot(self.out)

    def assertIntact(self, code, out_text, expected_code, before):
        self.assertEqual(code, expected_code, out_text[-3000:])
        self.assertEqual(snapshot(self.out), before)
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".site-build-tmp")))

    def mutate_and_commit(self, rel, fn):
        p = os.path.join(self.repo, rel)
        with open(p) as f:
            text = f.read()
        replace_file(p, fn(text))
        commit_all(self.repo, "mutação")

    # ------------------------------------------------------------ caminho feliz

    def test_happy_path_regenerates_traces(self):
        before = self.seed_out({"trace_200_with_failures_r002.json": '{"antigo":true}'})
        code, txt = self.run_gen()
        self.assertEqual(code, 0, txt[-3000:])
        import json
        with open(os.path.join(self.out, "index.json")) as f:
            idx = json.load(f)
        self.assertEqual(len(idx["traces"]), 7 * 35, idx.get("sem_trace", [])[:3])
        self.assertEqual(idx["sem_trace"], [])
        names = set(os.listdir(self.out))
        self.assertEqual(names, {"index.json"} | {f"{g}.json" for g, _, _ in bsd.GROUPS} |
                         {x["f"] for x in idx["traces"]})
        with open(os.path.join(self.out, "trace_200_with_failures_r002.json")) as f:
            tr = json.load(f)
        self.assertEqual((tr["model"], tr["g"], tr["r"]), ("synapt", "200_with_failures", 2))
        self.assertNotEqual(snapshot(self.out)["index.json"], before["index.json"])
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".site-build-tmp")))
        self.assertIn("divergência aceita", txt)

    def test_check_only_writes_nothing(self):
        code, txt = self.run_gen("--check-only")
        self.assertEqual(code, 0, txt[-3000:])
        self.assertFalse(os.path.lexists(self.out))
        self.assertFalse(os.path.lexists(os.path.join(self.repo, "site")))
        self.assertFalse(os.path.lexists(os.path.join(self.repo, ".site-build-tmp")))

    # ------------------------------------------------------------ código 2: entrada

    def test_csv_header_changed(self):
        before = self.seed_out()
        self.mutate_and_commit("results-synapt/200_no_failures/run_003/IntuitiveStats.csv",
                               lambda t: t.replace("timestamp,qi", "timestamp,QI", 1))
        self.assertIntact(*self.run_gen(), 2, before)

    def test_csv_144_rows(self):
        before = self.seed_out()
        self.mutate_and_commit("results-synapt/200_no_failures/run_003/IntuitiveStats.csv",
                               lambda t: "\n".join(t.rstrip("\n").split("\n")[:-1]) + "\n")
        self.assertIntact(*self.run_gen(), 2, before)

    def test_csv_timestamp_off_grid(self):
        before = self.seed_out()
        self.mutate_and_commit("results-synapt/250_no_failures/run_010/IntuitiveStats.csv",
                               lambda t: t.replace("\n175,", "\n176,", 1))
        self.assertIntact(*self.run_gen(), 2, before)

    def test_qvalues_missing_state(self):
        before = self.seed_out()
        self.mutate_and_commit("results-synapt/250_with_failures/run_005/IntuitiveQValues.txt",
                               lambda t: t.replace("Estado NO_MATCH:", "Estado XX:"))
        self.assertIntact(*self.run_gen(), 2, before)

    def test_missing_run(self):
        before = self.seed_out()
        shutil.rmtree(os.path.join(self.res, "300_no_failures", "run_035"))
        commit_all(self.repo, "remove run")
        self.assertIntact(*self.run_gen(), 2, before)

    def test_missing_wave_completion(self):
        before = self.seed_out()
        self.mutate_and_commit(
            "results-synapt/200_with_failures/run_007/simulation.log",
            lambda t: "".join(ln for ln in t.splitlines(True) if "Onda 2 completa" not in ln))
        self.assertIntact(*self.run_gen(), 2, before)

    def test_results_untracked_file_even_with_allow_dirty(self):
        before = self.seed_out()
        with open(os.path.join(self.res, "200_no_failures", "novo.txt"), "w") as f:
            f.write("x")
        self.assertIntact(*self.run_gen("--allow-dirty"), 2, before)

    def test_dirty_generator_without_allow_dirty(self):
        before = self.seed_out()
        gen = os.path.join(self.repo, "tools", "build_site_data.py")
        with open(gen) as f:
            text = f.read()
        replace_file(gen, text + "\n# alteração local\n")
        self.assertIntact(*self.run_gen(), 2, before)

    # ------------------------------------------------------------ código 2: ambiente/publicação

    def test_preexisting_tmp_dir_kept(self):
        before = self.seed_out()
        tmp = os.path.join(self.repo, ".site-build-tmp")
        os.mkdir(tmp)
        with open(os.path.join(tmp, "algo"), "w") as f:
            f.write("preexistente")
        code, txt = self.run_gen()
        self.assertEqual(code, 2, txt[-3000:])
        self.assertEqual(snapshot(self.out), before)
        self.assertTrue(os.path.isfile(os.path.join(tmp, "algo")))

    def test_out_symlink(self):
        os.makedirs(os.path.join(self.repo, "site"))
        real = os.path.join(self.repo, "outro")
        os.makedirs(real)
        os.symlink(real, self.out)
        code, txt = self.run_gen()
        self.assertEqual(code, 2, txt[-3000:])
        self.assertEqual(os.listdir(real), [])

    def test_out_other_path(self):
        code, txt = self.run_gen("--out", os.path.join(self.repo, "outro"))
        self.assertEqual(code, 2, txt[-3000:])
        self.assertFalse(os.path.lexists(os.path.join(self.repo, "outro")))

    def test_trace_symlink_in_out(self):
        before = self.seed_out()
        alvo = os.path.join(self.repo, "alvo.json")
        with open(alvo, "w") as f:
            f.write("{}")
        os.symlink(alvo, os.path.join(self.out, "trace_200_no_failures_r001.json"))
        before["trace_200_no_failures_r001.json"] = b"{}"
        self.assertIntact(*self.run_gen(), 2, before)

    def test_unknown_trace_name_rejected(self):
        before = self.seed_out({"trace_x.json": "{}"})
        self.assertIntact(*self.run_gen(), 2, before)

    def test_dirty_trace_parser_without_allow_dirty(self):
        before = self.seed_out()
        p = os.path.join(self.repo, "tools", "trace_from_log.py")
        with open(p) as f:
            text = f.read()
        replace_file(p, text + "\n# alteração local\n")
        self.assertIntact(*self.run_gen(), 2, before)

    def test_max_trace_file(self):
        before = self.seed_out()
        self.assertIntact(*self.run_gen("--max-trace-file", "100"), 4, before)

    def test_max_trace_bytes(self):
        before = self.seed_out()
        self.assertIntact(*self.run_gen("--max-trace-bytes", "1000"), 4, before)

    def test_unknown_file_in_out_rejected(self):
        before = self.seed_out({"notas.txt": "meu arquivo"})
        self.assertIntact(*self.run_gen(), 2, before)

    def test_uncommitted_change_in_out_rejected(self):
        self.seed_out()
        replace_file(os.path.join(self.out, "index.json"), '{"editado":true}')
        before = snapshot(self.out)
        self.assertIntact(*self.run_gen("--allow-dirty"), 2, before)

    def test_publish_failure_restores_out(self):
        self.seed_out()
        before = snapshot(self.out)
        tmp_root = os.path.join(self.repo, ".site-build-tmp")
        tmp_data = os.path.join(tmp_root, "data")
        os.makedirs(tmp_data)
        with open(os.path.join(tmp_data, "index.json"), "w") as f:
            f.write("{}")
        real_rename = os.rename
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("falha simulada no 2º rename")
            return real_rename(src, dst)

        with mock.patch.object(bsd.os, "rename", side_effect=flaky):
            with self.assertRaises(bsd.BuildError) as cm:
                bsd.publish(tmp_root, tmp_data, self.out)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("git checkout -- site/data", str(cm.exception))
        self.assertEqual(snapshot(self.out), before)

    # ------------------------------------------------------------ código 3 e 4

    def test_readme_value_changed(self):
        before = self.seed_out()
        self.mutate_and_commit("README.md",
                               lambda t: t.replace("| 200 nós — sem falhas | 99,3% ± 0,3%",
                                                   "| 200 nós — sem falhas | 99,6% ± 0,3%"))
        self.assertIntact(*self.run_gen(), 3, before)

    def test_s1_cumulative_broken(self):
        before = self.seed_out()
        self.mutate_and_commit("results-synapt/200_with_failures/run_004/IntuitiveQValues.txt",
                               lambda t: t.replace("S1 count total: ", "S1 count total: 1"))
        self.assertIntact(*self.run_gen(), 3, before)

    def test_max_bytes(self):
        before = self.seed_out()
        self.assertIntact(*self.run_gen("--max-bytes", "1000"), 4, before)

    # ------------------------------------------------------------ unidades

    def test_prefix_match_requires_min_length(self):
        a = ["x"] * 999
        self.assertFalse(bsd.prefix_match(a, a))
        self.assertTrue(bsd.prefix_match(["x"] * 1000, ["x"] * 1200))


class RoundTripTests(unittest.TestCase):
    """Confere site/data/*.json publicado contra os CSV brutos de results-synapt/ (só leitura)."""

    def test_site_data_matches_raw_csv(self):
        import json
        import statistics
        out = os.path.join(REAL_ROOT, "site", "data")
        if not os.path.isfile(os.path.join(out, "index.json")):
            self.skipTest("site/data ainda não gerado")
        with open(os.path.join(out, "index.json")) as f:
            idx = json.load(f)
        self.assertEqual((idx["t0"], idx["dt"], idx["nc"]), (170, 5, 145))
        self.assertEqual(idx["cols"], bsd.COLS)
        checked = 0
        for meta in idx["grupos"]:
            gid = meta["id"]
            with open(os.path.join(out, f"{gid}.json")) as f:
                g = json.load(f)
            raw = [bsd.parse_stats(os.path.join(REAL_RESULTS, gid, f"run_{r:03d}", "IntuitiveStats.csv"))
                   for r in range(1, 36)]
            self.assertEqual(len(g["runs"]), 35)
            for r, run in enumerate(g["runs"]):
                for i, c in enumerate(bsd.COLS):
                    tol = 0 if c in bsd.INT_COLS else 0.5 * 10 ** -bsd.DEC[c] + 1e-12
                    for k in range(145):
                        self.assertLessEqual(abs(run["d"][i][k] - raw[r][i][k]), tol,
                                             f"{gid} r{r + 1} {c} ciclo {k}")
                        checked += 1
            for i, c in enumerate(bsd.COLS):
                tol = 0.5 * 10 ** -bsd.DEC[c] + 1e-9
                for k in range(145):
                    vals = [raw[r][i][k] for r in range(35)]
                    self.assertLessEqual(abs(g["agg"]["m"][i][k] - statistics.fmean(vals)), tol)
                    self.assertLessEqual(abs(g["agg"]["sd"][i][k] - statistics.stdev(vals)), tol)
        self.assertEqual(checked, 7 * 35 * 17 * 145)

    def test_traces_match_regeneration_and_csv(self):
        """Cada (grupo, rodada) está em traces ou em sem_trace (nunca nos dois); cada trace publicado
        é idêntico ao regerado do log real e suas contagens por ciclo batem com o CSV bruto."""
        import json
        import trace_from_log as tfl
        out = os.path.join(REAL_ROOT, "site", "data")
        if not os.path.isfile(os.path.join(out, "index.json")):
            self.skipTest("site/data ainda não gerado")
        with open(os.path.join(out, "index.json")) as f:
            idx = json.load(f)
        if "traces" not in idx or not idx["traces"]:
            self.skipTest("site/data sem traces (gerado antes da Fase B)")
        com = {(x["g"], x["r"]) for x in idx["traces"]}
        sem = {(x["g"], x["r"]) for x in idx.get("sem_trace", [])}
        todos = {(m["id"], r) for m in idx["grupos"] for r in range(1, 36)}
        self.assertFalse(com & sem)
        self.assertEqual(com | sem, todos)
        th = {m["id"]: m["thS1"] for m in idx["grupos"]}
        for x in idx["traces"]:
            g, r = x["g"], x["r"]
            d = os.path.join(REAL_RESULTS, g, f"run_{r:03d}")
            cols = bsd.parse_stats(os.path.join(d, "IntuitiveStats.csv"))
            with open(os.path.join(out, x["f"])) as f:
                pub = json.load(f)
            regen, motivo = tfl.build_trace(os.path.join(d, "simulation.log"), cols, int(g[:3]), g, r, th[g])
            self.assertIsNone(motivo, f"{g} r{r}")
            self.assertEqual(pub, json.loads(bsd.dumps(regen)), f"{g} r{r}")
            for k, c in enumerate(tfl.role_counts(pub)):
                self.assertEqual((c["orfao"], c["s1"], c["s2"]),
                                 (cols[tfl.C_ORF][k], cols[tfl.C_S1][k], cols[tfl.C_S2][k]), f"{g} r{r} k{k}")

    def test_s1_iff_pthreat_above_group_threshold(self):
        """Em todo ciclo com órfãos, todas as decisões são S1 sse pThreat > limiar S1 do grupo,
        tanto no CSV bruto quanto no JSON publicado (o rótulo da página depende disso)."""
        import json
        out = os.path.join(REAL_ROOT, "site", "data")
        if not os.path.isfile(os.path.join(out, "index.json")):
            self.skipTest("site/data ainda não gerado")
        with open(os.path.join(out, "index.json")) as f:
            idx = json.load(f)
        ip, io, i1 = (bsd.COLS.index(c) for c in ("pt", "orf", "s1"))
        for meta in idx["grupos"]:
            with open(os.path.join(out, meta["id"] + ".json")) as f:
                g = json.load(f)
            for r in range(1, 36):
                raw = bsd.parse_stats(os.path.join(REAL_RESULTS, meta["id"], f"run_{r:03d}",
                                                   "IntuitiveStats.csv"))
                pub = g["runs"][r - 1]["d"]
                for k in range(145):
                    if raw[io][k] == 0:
                        continue
                    all_s1 = raw[i1][k] == raw[io][k]
                    self.assertTrue(raw[i1][k] in (0, raw[io][k]), f"{meta['id']} r{r} ciclo {k}: misto")
                    self.assertEqual(all_s1, raw[ip][k] > meta["thS1"], f"{meta['id']} r{r} ciclo {k} (bruto)")
                    self.assertEqual(all_s1, pub[ip][k] > meta["thS1"], f"{meta['id']} r{r} ciclo {k} (JSON)")


if __name__ == "__main__":
    unittest.main()
