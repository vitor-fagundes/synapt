"""
plot_fig2_qi_sr_panels.py
Figura 2 do paper — painéis A/B/C/D ao redor de falhas (250 nós).
  A - QI 250 random   (results-synapt/250_random_failures)
  B - SR 250 random   (results-synapt/250_random_failures)
  C - QI 250 fixed    (results-synapt/250_with_failures)
  D - SR 250 fixed    (results-synapt/250_with_failures)
Saída: plots/figures/fig2_{A,B,C,D}_*.
"""
import os
import re
import glob
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
warnings.filterwarnings('ignore')

plt.rcParams.update({
    'font.family':        'DejaVu Sans',
    'font.size':          20,
    'axes.titlesize':     20,
    'axes.titleweight':   'bold',
    'axes.labelsize':     20,
    'axes.labelweight':   'bold',
    'xtick.labelsize':    20,
    'ytick.labelsize':    20,
    'axes.spines.top':    True,
    'axes.spines.right':  True,
    'axes.edgecolor':     'black',
    'axes.linewidth':     1.0,
    'axes.grid':          True,
    'grid.color':         '#DDDDDD',
    'grid.linestyle':     '--',
    'grid.linewidth':     0.8,
    'grid.alpha':         0.6,
    'legend.frameon':     True,
    'legend.fontsize':    18,
    'legend.framealpha':  0.95,
    'figure.facecolor':   'white',
    'axes.facecolor':     'white',
    'figure.dpi':         150,
})

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT  = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)

WINDOW_PRE, WINDOW_POST, STEP = 30, 90, 5
REL_TIMES = np.arange(-WINDOW_PRE, WINDOW_POST + STEP, STEP)
CYCLE_BASE, CYCLE_STEP = 160, 5
FAILURE_COLORS = {1: '#42A5F5', 2: '#66BB6A', 3: '#FFA726'}

WAVE_RE = re.compile(
    r'FAILURE_CONFIG:\s*Onda\s+(\d+)\s+agendada\s+para\s+t=([\d.]+)s'
)


def round_down_to_cycle(t):
    if t < CYCLE_BASE:
        return None
    return CYCLE_BASE + int((t - CYCLE_BASE) // CYCLE_STEP) * CYCLE_STEP


def load_scenario(scen_dir):
    """Lê run_*/IntuitiveStats.csv + simulation.log e devolve rel_df alinhado por (failure_wave, rel_time)."""
    ts_rows, fr_rows = [], []
    run_dirs = sorted(glob.glob(os.path.join(scen_dir, 'run_*')))
    for run_dir in run_dirs:
        run = int(os.path.basename(run_dir).split('_')[1])

        stats_csv = os.path.join(run_dir, 'IntuitiveStats.csv')
        if os.path.exists(stats_csv):
            s = pd.read_csv(stats_csv)
            for _, r in s.iterrows():
                ts_rows.append({
                    'run': run,
                    'timestamp': int(r['timestamp']),
                    'qi': r['qi'],
                    'sr': r['sr'],
                })

        log = os.path.join(run_dir, 'simulation.log')
        if os.path.exists(log):
            with open(log) as f:
                for line in f:
                    m = WAVE_RE.search(line)
                    if m:
                        wave = int(m.group(1))
                        sched_t = float(m.group(2))
                        cyc = round_down_to_cycle(sched_t)
                        if cyc is None:
                            continue
                        fr_rows.append({
                            'run': run,
                            'failure_wave': wave,
                            'failure_time': cyc,
                        })

    ts_df = pd.DataFrame(ts_rows)
    fr_df = pd.DataFrame(fr_rows)

    records = []
    for _, row in fr_df.iterrows():
        r = int(row['run'])
        ft = int(row['failure_time'])
        sub = ts_df[ts_df['run'] == r].set_index('timestamp')
        for rel_t in REL_TIMES:
            abs_t = ft + rel_t
            if abs_t in sub.index:
                records.append({
                    'failure_wave': int(row['failure_wave']),
                    'rel_time': rel_t,
                    'qi': sub.loc[abs_t, 'qi'],
                    'sr': sub.loc[abs_t, 'sr'],
                })
    return pd.DataFrame(records), len(run_dirs)


def plot_metric(rel_df, metric, ylabel, title, fname):
    fig, ax = plt.subplots(figsize=(7, 5))

    for wave in [1, 2, 3]:
        agg = (rel_df[rel_df['failure_wave'] == wave]
               .groupby('rel_time')[metric].agg(['mean', 'std']))
        if agg.empty:
            continue
        ax.plot(agg.index, agg['mean'],
                color=FAILURE_COLORS[wave], linewidth=2,
                label=f'Failure {wave}')
        ax.fill_between(agg.index,
                        agg['mean'] - agg['std'],
                        agg['mean'] + agg['std'],
                        alpha=0.15, color=FAILURE_COLORS[wave])

    ax.axvline(0, color='red', linewidth=2, linestyle='--',
               alpha=0.7, label='Failure (t=0)')

    ax.set_title(title, fontweight='bold')
    ax.set_xlabel('Time relative to failures (s)')
    ax.set_ylabel(ylabel)
    ax.set_ylim(0.20, 1.05)
    ax.set_yticks([0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_xlim(-WINDOW_PRE, WINDOW_POST)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'{v*100:.0f}%'))
    ax.legend(loc='lower right')

    plt.tight_layout()
    base = os.path.join(OUT, fname)
    plt.savefig(base + '.pdf', bbox_inches='tight')
    plt.savefig(base + '.png', bbox_inches='tight')
    plt.close()
    print(f'  saved {base}.pdf / .png')


print('Carregando 250_random_failures...')
rnd, n_rnd = load_scenario(os.path.join(ROOT, 'results-synapt', '250_random_failures'))
print(f'  runs={n_rnd}  amostras alinhadas={len(rnd)}')

print('Carregando 250_with_failures...')
fix, n_fix = load_scenario(os.path.join(ROOT, 'results-synapt', '250_with_failures'))
print(f'  runs={n_fix}  amostras alinhadas={len(fix)}')

print('\nGerando figuras...')
plot_metric(rnd, 'qi', 'Quality Index',     '(A) - 250 nodes (FR)', 'fig2_A_qi_random')
plot_metric(rnd, 'sr', 'Service Reachability','(B) - 250 nodes (FR)', 'fig2_B_sr_random')
plot_metric(fix, 'qi', 'Quality Index',     '(C) - 250 nodes',      'fig2_C_qi_static')
plot_metric(fix, 'sr', 'Service Reachability','(D) - 250 nodes',      'fig2_D_sr_static')
print(f'\nTodos os arquivos em: {OUT}')
