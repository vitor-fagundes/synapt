"""
Regera (ou atualiza) os 3 CSVs em plots/data/ a partir das pastas
results-synapt/<scenario>/run_NNN/.

Lê: IntuitiveStats.csv, IntuitiveQValues.txt, simulation.log
Escreve: q_learning_evolution.csv, simulation_runs.csv, failure_response.csv

Uso:
  python3 regen_csvs.py --scenario 300_with_failures --source-dir results-synapt/300_with_failures_NEW_combined
  (substitui as linhas onde group=<scenario> nos 3 CSVs)
"""
import argparse
import os
import re
import glob
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(HERE, 'data')

EPSILON_RE      = re.compile(r'Epsilon final:\s*([\d.]+)')
S1_COUNT_RE     = re.compile(r'S1 count total:\s*(\d+)')
S2_COUNT_RE     = re.compile(r'S2 count total:\s*(\d+)')
WAVE_FIRE_RE    = re.compile(r'FAILURE_WAVE: === Onda (\d+) de \d+ no tempo ([\d.]+)s')
WAVE_DONE_RE    = re.compile(r'Onda (\d+) completa\s*—\s*(\d+) l[íi]deres falharam,\s*(\d+) órf[ãa]os')
ELIGIBLE_RE     = re.compile(r'FAILURE_WAVE_ELIGIBLE:')
SKIPPED_RE      = re.compile(r'FAILURE_WAVE_SKIPPED:')

CYCLE_BASE = 160
CYCLE_STEP = 5


def cycle_ge(t):
    if t < CYCLE_BASE: return CYCLE_BASE
    n = (t - CYCLE_BASE) / CYCLE_STEP
    return CYCLE_BASE + (int(n) + (0 if n.is_integer() else 1)) * CYCLE_STEP


def parse_run(run_dir, group, n_nodes):
    """Extrai 1 linha de q_learning_evolution, 1 de simulation_runs, e N linhas de failure_response."""
    run = int(os.path.basename(run_dir).split('_')[1])
    stats_csv = os.path.join(run_dir, 'IntuitiveStats.csv')
    qvals_txt = os.path.join(run_dir, 'IntuitiveQValues.txt')
    sim_log   = os.path.join(run_dir, 'simulation.log')

    if not (os.path.exists(stats_csv) and os.path.exists(qvals_txt) and os.path.exists(sim_log)):
        return None, None, []

    df = pd.read_csv(stats_csv)

    # IntuitiveQValues.txt → epsilon, s1, s2
    eps = s1 = s2 = None
    with open(qvals_txt) as f:
        txt = f.read()
    if m := EPSILON_RE.search(txt):  eps = float(m.group(1))
    if m := S1_COUNT_RE.search(txt): s1  = int(m.group(1))
    if m := S2_COUNT_RE.search(txt): s2  = int(m.group(1))

    # simulation.log → eventos por onda
    waves = {}    # wave -> dict(t, leaders_failed, orphans, n_elig, n_skip)
    n_elig_total = 0
    n_skip_total = 0
    current_wave = None
    with open(sim_log) as f:
        for line in f:
            if m := WAVE_FIRE_RE.search(line):
                current_wave = int(m.group(1))
                waves.setdefault(current_wave, {})['t'] = float(m.group(2))
                continue
            if m := WAVE_DONE_RE.search(line):
                w = int(m.group(1))
                waves.setdefault(w, {})['leaders_failed'] = int(m.group(2))
                waves[w]['orphans'] = int(m.group(3))
                continue
            if current_wave == 1:
                if ELIGIBLE_RE.search(line): n_elig_total += 1
                elif SKIPPED_RE.search(line): n_skip_total += 1

    # ===== q_learning_evolution =====
    last = df.iloc[-1]
    q_dn, q_ra, q_rc = float(last['qDoNothing']), float(last['qReallocate']), float(last['qRecluster'])
    dominant = ['donothing', 'reallocate', 'recluster'][[q_dn, q_ra, q_rc].index(max(q_dn, q_ra, q_rc))]
    qle_row = {
        'group': group, 'n_nodes': n_nodes, 'run': run,
        'q_donothing': q_dn, 'q_reallocate': q_ra, 'q_recluster': q_rc,
        'dominant_action': dominant,
        'net_learning': float(last['netLearning']),
        'epsilon': eps,
        's1_count': s1, 's2_count': s2,
    }

    # ===== simulation_runs =====
    first = df.iloc[0]
    n_leaders_failed_total = sum(w.get('leaders_failed', 0) for w in waves.values())
    n_orphans_total        = sum(w.get('orphans', 0)        for w in waves.values())
    sr_row = {
        'group': group, 'n_nodes': n_nodes, 'has_failures': 1, 'run': run,
        'n_clusters_initial': n_elig_total + n_skip_total,
        'tasks_dispatched': '',   # não consigo extrair direto, deixar vazio
        'tasks_remaining':  '',
        'tasks_total':      '',
        'qi_initial': float(first['qi']),
        'sr_initial': float(first['sr']),
        'qi_final':   float(last['qi']),
        'sr_final':   float(last['sr']),
        'tii_final':  float(last['tii']),
        'arf_final':  float(last['arf']),
        'p_threat_final':    float(last['pThreat']),
        'net_learning_final': float(last['netLearning']),
        'n_leaders_failed_total': n_leaders_failed_total,
        'n_orphans_total':        n_orphans_total,
    }

    # ===== failure_response (uma linha por onda) =====
    fr_rows = []
    df_idx = df.set_index('timestamp')
    for wnum in sorted(waves.keys()):
        w = waves[wnum]
        t  = w.get('t', None)
        if t is None: continue
        cyc_at  = cycle_ge(t)
        cyc_pre = cyc_at - CYCLE_STEP
        cyc_p3  = cyc_at + 3 * CYCLE_STEP

        def get(ts, col):
            return float(df_idx.loc[ts, col]) if ts in df_idx.index else float('nan')

        qi_pre  = get(cyc_pre, 'qi'); sr_pre  = get(cyc_pre, 'sr')
        qi_at   = get(cyc_at,  'qi'); sr_at   = get(cyc_at,  'sr')
        qi_p3   = get(cyc_p3,  'qi'); sr_p3   = get(cyc_p3,  'sr')
        # actions at impact: diff between cyc_at e cyc_pre
        if cyc_at in df_idx.index and cyc_pre in df_idx.index:
            arc = int(df_idx.loc[cyc_at, 'actionsRecluster']  - df_idx.loc[cyc_pre, 'actionsRecluster'])
            ara = int(df_idx.loc[cyc_at, 'actionsReallocate'] - df_idx.loc[cyc_pre, 'actionsReallocate'])
        else:
            arc = ara = 0
        fr_rows.append({
            'group': group, 'n_nodes': n_nodes, 'run': run,
            'failure_wave': wnum,
            'failure_time': int(t),
            'n_leaders_failed': w.get('leaders_failed', 0),
            'n_orphans':        w.get('orphans', 0),
            'qi_pre_failure': qi_pre, 'sr_pre_failure': sr_pre,
            'qi_at_impact':   qi_at,  'sr_at_impact':   sr_at,
            'qi_3cycles_after': qi_p3, 'sr_3cycles_after': sr_p3,
            'actions_recluster_at_impact': arc,
            'actions_reallocate_at_impact': ara,
            'qi_recovery_delta': qi_p3 - qi_at if not (qi_p3 != qi_p3 or qi_at != qi_at) else 0,
        })

    return qle_row, sr_row, fr_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scenario', required=True, help='nome a usar na coluna group (ex: 300_with_failures)')
    ap.add_argument('--source-dir', required=True, help='pasta com run_*/ (relativa ao raiz do projeto)')
    ap.add_argument('--n-nodes', type=int, default=300)
    args = ap.parse_args()

    src = os.path.join(ROOT, args.source_dir)
    run_dirs = sorted(glob.glob(os.path.join(src, 'run_*')))
    print(f'Encontrei {len(run_dirs)} runs em {src}')

    qle_rows, sr_rows, fr_rows = [], [], []
    for rd in run_dirs:
        q, s, fs = parse_run(rd, args.scenario, args.n_nodes)
        if q: qle_rows.append(q)
        if s: sr_rows.append(s)
        fr_rows.extend(fs)

    for fname, rows in [
        ('q_learning_evolution.csv', qle_rows),
        ('simulation_runs.csv',      sr_rows),
        ('failure_response.csv',     fr_rows),
    ]:
        path = os.path.join(DATA_DIR, fname)
        df = pd.read_csv(path)
        df = df[df['group'] != args.scenario].copy()
        new = pd.DataFrame(rows)
        merged = pd.concat([df, new], ignore_index=True)
        merged.to_csv(path, index=False)
        print(f'  {fname}: removidas {df.shape[0]-(merged.shape[0]-len(rows))} linhas antigas; '
              f'adicionadas {len(rows)} novas. Total {merged.shape[0]}.')

if __name__ == '__main__':
    main()
