"""
build_clustering_recovery_table.py
Constrói o CSV equivalente à TABLE I (Clustering and Recovery Behavior Under Failures)
incluindo o cenário 5 (250 random) e a nova coluna Success%.

Success = (recluster + reallocate) / orphans * 100
       = % de nós órfãos que receberam alguma ação corretiva
"""
import os
import re
import glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(HERE, 'data')
SCEN_RANDOM = os.path.join(ROOT, 'results-synapt', '250_random_failures')
OUT  = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)

# ── Cenários existentes (per-(run, wave) já em failure_response.csv) ─────────
fr = pd.read_csv(os.path.join(DATA, 'failure_response.csv'))
sr = pd.read_csv(os.path.join(DATA, 'simulation_runs.csv'))

# ── Cenário 250 random: parse de simulation.log + IntuitiveStats.csv ─────────
WAVE_FIRE_RE = re.compile(
    r'FAILURE_WAVE:\s*===\s*Onda\s+(\d+)\s+de\s+\d+\s+no\s+tempo\s+([\d.]+)s'
)
WAVE_DONE_RE = re.compile(
    r'FAILURE_WAVE:\s*Onda\s+(\d+)\s+completa\s*—\s*(\d+)\s+l[íi]deres falharam,\s*(\d+)\s+órf[ãa]os reais'
)
CYCLE_BASE, CYCLE_STEP = 160, 5
ACTION_WINDOW_CYCLES = 3   # cycles after fire to count actions (15s window)


def cycle_ge(t):
    """Próximo ciclo do AP >= t."""
    if t < CYCLE_BASE:
        return CYCLE_BASE
    n = (t - CYCLE_BASE) / CYCLE_STEP
    if n.is_integer():
        return int(t)
    return CYCLE_BASE + (int(n) + 1) * CYCLE_STEP


random_rows = []
for run_dir in sorted(glob.glob(os.path.join(SCEN_RANDOM, 'run_*'))):
    run = int(os.path.basename(run_dir).split('_')[1])
    log_path = os.path.join(run_dir, 'simulation.log')
    stats_path = os.path.join(run_dir, 'IntuitiveStats.csv')
    if not (os.path.exists(log_path) and os.path.exists(stats_path)):
        continue

    # Parse fire times + per-wave (n_leaders, n_orphans) from log
    waves = {}
    with open(log_path) as f:
        for line in f:
            m = WAVE_FIRE_RE.search(line)
            if m:
                w = int(m.group(1))
                ft = float(m.group(2))
                waves.setdefault(w, {})['fire_time'] = ft
                continue
            m = WAVE_DONE_RE.search(line)
            if m:
                w = int(m.group(1))
                waves.setdefault(w, {})['n_leaders_failed'] = int(m.group(2))
                waves.setdefault(w, {})['n_orphans'] = int(m.group(3))

    # Pull per-wave actions from IntuitiveStats time series.
    # Janela é truncada no impacto da PRÓXIMA onda pra evitar contar 2x ações
    # que pertencem a outra onda quando elas caem próximas no random.
    stats = pd.read_csv(stats_path).set_index('timestamp')
    sorted_w = sorted(
        ((w, info) for w, info in waves.items() if 'fire_time' in info),
        key=lambda kv: kv[1]['fire_time'],
    )
    next_impact_by_w = {}
    for i, (w, _) in enumerate(sorted_w):
        if i + 1 < len(sorted_w):
            next_impact_by_w[w] = cycle_ge(sorted_w[i + 1][1]['fire_time'])
        else:
            next_impact_by_w[w] = float('inf')

    for w, info in waves.items():
        if 'fire_time' not in info or 'n_orphans' not in info:
            continue
        impact = cycle_ge(info['fire_time'])
        end_excl = min(impact + CYCLE_STEP * ACTION_WINDOW_CYCLES,
                       next_impact_by_w[w])
        cycles = [c for c in range(impact, int(end_excl), CYCLE_STEP)
                  if c in stats.index]
        if not cycles:
            continue
        realloc = int(stats.loc[cycles, 'actionsReallocate'].sum())
        reclust = int(stats.loc[cycles, 'actionsRecluster'].sum())
        random_rows.append({
            'group': '250_random_failures',
            'n_nodes': 250,
            'run': run,
            'failure_wave': w,
            'failure_time': impact,
            'n_leaders_failed': info['n_leaders_failed'],
            'n_orphans': info['n_orphans'],
            'actions_reallocate_at_impact': realloc,
            'actions_recluster_at_impact': reclust,
            'qi_pre_failure': np.nan, 'sr_pre_failure': np.nan,
            'qi_at_impact': np.nan, 'sr_at_impact': np.nan,
            'qi_3cycles_after': np.nan, 'sr_3cycles_after': np.nan,
            'qi_recovery_delta': np.nan,
        })

random_df = pd.DataFrame(random_rows)
print(f'250_random_failures: {len(random_df)} eventos (run, wave) extraídos')

fr_all = pd.concat([fr, random_df], ignore_index=True)

# ── Adiciona n_clusters_initial: para o random, lê de simulation_runs depois ─
# Para o random, n_clusters_initial não está em simulation_runs.csv. Vou extrair
# da primeira linha do IntuitiveStats.csv (cycleS1 + cycleS2 não dão isso) ou
# do log (FAILURE_WAVE_ELIGIBLE no primeiro fire = aproximação) — mais simples:
# usar o número de líderes elegíveis na onda 1 + leaders já falhados (=0 inicialmente)
# como proxy. Aqui vou usar a contagem de "FAILURE_WAVE_ELIGIBLE" na onda 1 + skipped
# (elegíveis + idle).
ELIGIBLE_RE = re.compile(r'FAILURE_WAVE_ELIGIBLE:')
SKIPPED_RE  = re.compile(r'FAILURE_WAVE_SKIPPED:')

def extract_clusters_from_logs(group):
    """Conta FAILURE_WAVE_ELIGIBLE (active) e FAILURE_WAVE_SKIPPED (idle) na onda 1
    de cada run do cenário. Retorna DataFrame com (run, Tot, Act, Idle)."""
    base = os.path.join(ROOT, 'results-synapt', group)
    rows = []
    for run_dir in sorted(glob.glob(os.path.join(base, 'run_*'))):
        run = int(os.path.basename(run_dir).split('_')[1])
        log_path = os.path.join(run_dir, 'simulation.log')
        if not os.path.exists(log_path):
            continue
        elig = 0
        skip = 0
        in_first_wave = False
        with open(log_path) as f:
            for line in f:
                if 'FAILURE_WAVE: === Onda 1' in line:
                    in_first_wave = True
                    continue
                if 'FAILURE_WAVE: === Onda 2' in line:
                    break
                if in_first_wave:
                    if ELIGIBLE_RE.search(line): elig += 1
                    elif SKIPPED_RE.search(line): skip += 1
        rows.append({
            'group': group,
            'run': run,
            'n_clusters_initial': elig + skip,
            'n_clusters_active':  elig,
            'n_clusters_idle':    skip,
        })
    return pd.DataFrame(rows)


cluster_dfs = []
for g in ['200_with_failures', '250_with_failures', '300_with_failures', '250_random_failures']:
    df_g = extract_clusters_from_logs(g)
    print(f'  {g}: Tot={df_g["n_clusters_initial"].mean():.1f}, '
          f'Act={df_g["n_clusters_active"].mean():.1f}, '
          f'Idle={df_g["n_clusters_idle"].mean():.1f}')
    cluster_dfs.append(df_g)

clusters_all = pd.concat(cluster_dfs, ignore_index=True)


# ── Agrega por cenário ──────────────────────────────────────────────────────
SCENARIO_ORDER = [
    ('200_with_failures',   '200'),
    ('250_with_failures',   '250'),
    ('300_with_failures',   '300'),
    ('250_random_failures', '250 random'),
]

def stats_pair(s):
    return s.mean(), s.std(ddof=1)

# Valores da tabela de referência do paper (cenários 200/250/300 com falhas) —
# Tot/Act/Idle e Fail/Orph já validados. Apenas o 250 random é calculado do zero.
REFERENCE_VALUES = {
    '200_with_failures': dict(tot=(20, 1), act=(16, 1), idle=(4, 1),
                               fail=(3.6, 0.1), orph=(30.5, 1.9)),
    '250_with_failures': dict(tot=(22, 1), act=(18, 1), idle=(4, 1),
                               fail=(3.6, 0.1), orph=(28.6, 1.7)),
    '300_with_failures': dict(tot=(30, 2), act=(24, 2), idle=(6, 1),
                               fail=(3.7, 0.1), orph=(29.0, 1.6)),
}

rows = []
for group, label in SCENARIO_ORDER:
    f = fr_all[fr_all['group'] == group].copy()

    # Agrega por RUN (soma 3 ondas) pra computar Realloc%/Reclust%/Success% sobre
    # # de órfãos. Usar per-run em vez de per-wave evita atribuição errada quando
    # ondas se sobrepõem (caso de stress no cenário random); pra ondas não-sobrepostas,
    # o resultado é o mesmo.
    per_run = f.groupby('run').agg({
        'n_orphans': 'sum',
        'actions_reallocate_at_impact': 'sum',
        'actions_recluster_at_impact': 'sum',
    })
    per_run['realloc_pct'] = np.where(per_run['n_orphans'] > 0,
                                       per_run['actions_reallocate_at_impact'] / per_run['n_orphans'] * 100,
                                       0)
    per_run['reclust_pct'] = np.where(per_run['n_orphans'] > 0,
                                       per_run['actions_recluster_at_impact'] / per_run['n_orphans'] * 100,
                                       0)
    per_run['success_pct'] = per_run['realloc_pct'] + per_run['reclust_pct']

    rl_pct_m, rl_pct_s = stats_pair(per_run['realloc_pct'])
    rc_pct_m, rc_pct_s = stats_pair(per_run['reclust_pct'])
    succ_m,  succ_s    = stats_pair(per_run['success_pct'])

    if group in REFERENCE_VALUES:
        # Usa valores da tabela do paper (já validados pelo usuário)
        ref = REFERENCE_VALUES[group]
        tot_m, tot_s   = ref['tot']
        act_m, act_s   = ref['act']
        idle_m, idle_s = ref['idle']
        fail_m, fail_s = ref['fail']
        orph_m, orph_s = ref['orph']
    else:
        # 250 random: extrai do log/stats
        c = clusters_all[clusters_all['group'] == group]
        tot_m, tot_s   = stats_pair(c['n_clusters_initial'])
        act_m, act_s   = stats_pair(c['n_clusters_active'])
        idle_m, idle_s = stats_pair(c['n_clusters_idle'])
        fail_m, fail_s = stats_pair(f['n_leaders_failed'])
        orph_m, orph_s = stats_pair(f['n_orphans'])

    rows.append({
        'cenario': label,
        'total_clusters_media':         round(tot_m, 1),
        'total_clusters_desvio':        round(tot_s, 1),
        'clusters_ativos_media':        round(act_m, 1),
        'clusters_ativos_desvio':       round(act_s, 1),
        'clusters_ociosos_media':       round(idle_m, 1),
        'clusters_ociosos_desvio':      round(idle_s, 1),
        'lideres_falhos_media':         round(fail_m, 2),
        'lideres_falhos_desvio':        round(fail_s, 2),
        'orfaos_gerados_media':         round(orph_m, 1),
        'orfaos_gerados_desvio':        round(orph_s, 1),
        'pct_orfaos_realocados_media':  round(rl_pct_m, 1),
        'pct_orfaos_realocados_desvio': round(rl_pct_s, 1),
        'pct_orfaos_reclusterizados_media':  round(rc_pct_m, 1),
        'pct_orfaos_reclusterizados_desvio': round(rc_pct_s, 1),
        'pct_orfaos_recuperados_media':  round(succ_m, 1),
        'pct_orfaos_recuperados_desvio': round(succ_s, 1),
    })

table = pd.DataFrame(rows)

out_path = os.path.join(OUT, 'clustering_recovery_table.csv')
table.to_csv(out_path, index=False)

print()
print('=== Tabela final ===')
print(table.to_string(index=False))
print(f'\nSalvo em: {out_path}')
