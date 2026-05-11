"""
plot_fig4a_recovery_success.py
Figura 4(A) do paper — barra de % de órfãos com ação corretiva (realocar ou recluster)
por cenário. Lê de figures/clustering_recovery_table.csv.
Saída: plots/figures/fig4_A_recovery_success.{pdf,png}
"""
import os
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
    'axes.axisbelow':     True,
    'grid.color':         '#DDDDDD',
    'grid.linestyle':     '--',
    'grid.linewidth':     0.8,
    'grid.alpha':         0.6,
    'figure.facecolor':   'white',
    'axes.facecolor':     'white',
    'figure.dpi':         150,
})

HERE = os.path.dirname(os.path.abspath(__file__))
OUT  = os.path.join(HERE, 'figures')
CSV  = os.path.join(OUT, 'clustering_recovery_table.csv')
os.makedirs(OUT, exist_ok=True)

df = pd.read_csv(CSV)
scenarios = ['250 (FR)' if s == '250 random' else s
             for s in df['cenario'].astype(str).tolist()]
means     = np.minimum(df['pct_orfaos_recuperados_media'].values, 100)
stds      = df['pct_orfaos_recuperados_desvio'].values

color_map = {
    '200':       '#66BB6A',
    '250':       '#42A5F5',
    '300':       '#AB47BC',
    '250 (FR)':  '#FFA726',
}
colors = [color_map.get(s, '#888888') for s in scenarios]

fig, ax = plt.subplots(figsize=(7, 5))
bars = ax.bar(scenarios, means, yerr=stds, color=colors,
              edgecolor='black', linewidth=1.2,
              width=0.65,
              capsize=8, error_kw={'linewidth': 1.5, 'ecolor': 'black'})

for bar, m, s in zip(bars, means, stds):
    ax.text(bar.get_x() + bar.get_width() / 2,
            min(m + s + 1.0, 103),
            f'{m:.1f}%',
            ha='center', va='bottom',
            fontsize=16, fontweight='bold')

ax.axhline(100, color='red', linestyle='--', linewidth=1.2, alpha=0.6)

ax.set_title('(A)', fontweight='bold')
ax.set_ylabel('Recovered (%)')
ax.set_xlabel('Scenario (number of nodes)')
ax.set_ylim(0, 110)
ax.set_yticks([0, 25, 50, 75, 100])

plt.tight_layout()
base = os.path.join(OUT, 'fig4_A_recovery_success')
plt.savefig(base + '.pdf', bbox_inches='tight')
plt.savefig(base + '.png', bbox_inches='tight')
plt.close()
print(f'saved {base}.pdf / .png')
