"""
plot_fig3_learning_evolution.py
Figura 3 do paper — Net Learning Index e Epsilon ao longo das 35 rodadas.
Inclui o cenário 5 (250 random failures).
Saída em plots/figures/.
"""
import os
import re
import glob
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({
    'font.family':        'DejaVu Sans',
    'font.size':          18,
    'axes.titlesize':     18,
    'axes.titleweight':   'bold',
    'axes.labelsize':     18,
    'axes.labelweight':   'bold',
    'xtick.labelsize':    18,
    'ytick.labelsize':    18,
    'axes.spines.top':    True,
    'axes.spines.right':  True,
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
DATA = os.path.join(HERE, 'data')
OUT  = os.path.join(HERE, 'figures')
os.makedirs(OUT, exist_ok=True)

# ── Cenários existentes (com_failures, 200/250/300) ─────────────────────────
df = pd.read_csv(os.path.join(DATA, 'q_learning_evolution.csv'))

# ── Cenário 5: 250 random failures — extraído dos run_NNN ───────────────────
RANDOM_DIR = os.path.join(ROOT, 'results-synapt', '250_random_failures')
EPSILON_RE = re.compile(r'Epsilon final:\s*([\d.]+)')

random_rows = []
for run_dir in sorted(glob.glob(os.path.join(RANDOM_DIR, 'run_*'))):
    run = int(os.path.basename(run_dir).split('_')[1])

    stats_csv = os.path.join(run_dir, 'IntuitiveStats.csv')
    qvals_txt = os.path.join(run_dir, 'IntuitiveQValues.txt')
    if not (os.path.exists(stats_csv) and os.path.exists(qvals_txt)):
        continue

    s = pd.read_csv(stats_csv)
    net_learning = s['netLearning'].iloc[-1]

    eps = None
    with open(qvals_txt) as f:
        for line in f:
            m = EPSILON_RE.search(line)
            if m:
                eps = float(m.group(1)); break
    if eps is None:
        continue

    random_rows.append({
        'group': '250_random_failures',
        'n_nodes': 250,
        'run': run,
        'net_learning': net_learning,
        'epsilon': eps,
    })

random_df = pd.DataFrame(random_rows)
print(f'250_random_failures: {len(random_df)} rodadas extraídas')

df = pd.concat([df, random_df], ignore_index=True)

# ── Configuração de plot ────────────────────────────────────────────────────
GROUPS = [
    '200_with_failures',
    '250_with_failures',
    '250_random_failures',
    '300_with_failures',
]
GROUP_LABELS = {
    '200_with_failures':   '200 nodes',
    '250_with_failures':   '250 nodes',
    '250_random_failures': '250 nodes (random)',
    '300_with_failures':   '300 nodes',
}
GROUP_COLORS = {
    '200_with_failures':   '#1565C0',
    '250_with_failures':   '#2E7D32',
    '250_random_failures': '#F57C00',
    '300_with_failures':   '#B71C1C',
}
GROUP_LINESTYLES = {
    '200_with_failures':   '-',
    '250_with_failures':   '-',
    '250_random_failures': '-',
    '300_with_failures':   '-',
}


def plot_metric(metric, ylabel, title, ymax, fname):
    fig, ax = plt.subplots(figsize=(7, 6))
    for group in GROUPS:
        sub = df[df['group'] == group].sort_values('run')
        if sub.empty or sub[metric].isna().all():
            continue
        ax.plot(sub['run'], sub[metric],
                marker='o', markersize=3, linewidth=2,
                label=GROUP_LABELS[group],
                color=GROUP_COLORS[group],
                linestyle=GROUP_LINESTYLES[group],
                alpha=0.85)

    ax.set_title(title, fontweight='bold')
    ax.set_xlabel('Rounds')
    ax.set_ylabel(ylabel)
    ax.set_xlim(1, 35)
    ax.set_ylim(0, ymax)
    ax.legend(loc='best')

    plt.tight_layout()
    base = os.path.join(OUT, fname)
    plt.savefig(base + '.pdf', bbox_inches='tight')
    plt.savefig(base + '.png', bbox_inches='tight')
    plt.close()
    print(f'  saved {base}.pdf / .png')


print('\nGerando figuras...')
#plot_metric('net_learning', 'Network Learning Index',
plot_metric('net_learning', 'NLI',
            '(A)',
            ymax=1.05, fname='fig3_A_net_learning')

plot_metric('epsilon', 'Epsilon (exploration)',
            '(B)',
            ymax=df['epsilon'].max() * 1.1, fname='fig3_B_epsilon')

print(f'\nTodos os arquivos em: {OUT}')
