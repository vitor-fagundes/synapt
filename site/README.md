# Simulador web do SYNAPT

Página estática (`site/index.html`) que faz o replay, ciclo a ciclo, das métricas registradas nas
245 rodadas de `results-synapt/` (7 grupos × 35 rodadas). Os dados ficam em `site/data/*.json`,
gerados por `tools/build_site_data.py`. O plano e as decisões estão em `docs/plano-simulador-web.md`.

## Proveniência

- **Precedência das fontes** para o que foi executado: logs > código atual > README (decisão do autor,
  2026-11-25). Onde o README diverge dos logs (tempos, override para N ≥ 300, `--run` dos exemplos,
  linha "300 nós — com falhas"), o site segue os logs e mostra a diferença.
- **`--run=0`:**
  - `200_no_failures`, `200_with_failures`, `250_no_failures`, `250_with_failures` e
    `300_no_failures`: o runner versionado `run_simulations.sh` (repositório sectional, commit
    `c5e78e07ac2d60c63cd376e40f5ea574a628dc1c`, em `scratch/sectional`) passa `--run=0`;
  - `250_random_failures` e `300_with_failures`: `--run=0` declarado pelo autor (decisão registrada
    em 2026-11-25), consistente com a sequência idêntica de eventos do NS-3.
  - O gerador verifica isso em todas as rodadas (`run_evid_check` em `index.json`): o prefixo do
    `simulation.log` até a disseminação de capacidades, sem o tamanho dos pacotes, coincide com o da
    rodada de mesmo índice de `250_no_failures` e de `300_no_failures`, respectivamente (35/35 em
    ambos).
  - Limite da evidência: não há nos dados uma execução com outro `--run` que sirva de controle negativo.
- **Parâmetros por grupo**, lidos dos logs: `300_with_failures` tem `DENSITY_OVERRIDE` (limiar S1
  0,245; ξ −0,033) nas 35 rodadas; os demais grupos, inclusive `300_no_failures` (gerado antes da
  calibragem `901ed81`), usam 0,20 e −0,05.

## Regerar os dados

```bash
# na raiz do repositório synapt; o guard deixa results-synapt/ sem escrita durante a execução
tools/with_results_guard.sh python3 tools/build_site_data.py --check-only   # só valida
tools/with_results_guard.sh python3 tools/build_site_data.py                # valida e publica em site/data
```

O mesmo comando gera os traces de topologia (Fase B), um por rodada (`data/trace_<grupo>_r<NNN>.json`), a
partir do `simulation.log`, com `tools/trace_from_log.py`. Uma rodada só ganha trace se passar nas verificações
(contagens por ciclo iguais às do CSV, `round(SR × vivos)` = vivos − órfãos − fora de cluster, membros e órfãos de
cada líder que cai, incluindo os seguidores registrados de novos líderes, coerência de reclusters e pendentes); as demais entram em `index.json.sem_trace` com o motivo.
Na geração atual: **245 de 245 rodadas com trace**.

O gerador recusa:
- `site/data` com arquivos desconhecidos ou não commitados;
- `.site-build-tmp/` já existente;
- `git status --porcelain -- results-synapt` não vazio.

Se a troca de `site/data` falhar, recupere com `git checkout -- site/data`.

## Testar

```bash
# gerador e parser de trace (num repositório de teste temporário; nada é gravado em results-synapt/ nem em site/)
tools/with_results_guard.sh python3 -m unittest tools/test_build_site_data.py tools/test_trace_from_log.py -v

# página: servidor local
cd site && python3 -m http.server 8000      # abra http://localhost:8000

# página: teste de ponta a ponta (Playwright + Chromium); pulado com aviso se o playwright não estiver instalado
(cd tools && npm install && npx playwright install chromium)   # uma vez, na raiz do repositório
tools/with_results_guard.sh bash -c 'cd tools && node test_site_e2e.mjs /tmp/capturas-synapt'
```

Abrir `index.html` com duplo clique não funciona: o navegador bloqueia o `fetch` de arquivos locais e a
página mostra a faixa de erro. Use o servidor local.

## Publicar

1. No GitHub: **Settings → Pages → Source: GitHub Actions**.
2. O workflow `.github/workflows/pages.yml` publica **somente `site/`** a cada push em `synapt-v1`. O
   disparo manual (**Actions → Pages → Run workflow**) só aparece depois que o `pages.yml` estiver na
   branch padrão do repositório. `results-synapt/` (~1,6 GB) não vai para o
   Pages, que tem limite de 1 GB.
3. Os caminhos do `fetch` são relativos (`data/index.json`, `data/<grupo>.json`) e batem com os
   arquivos publicados.

## Conteúdo

| Arquivo | O que é |
|---|---|
| `index.html` | Página única (HTML, CSS e JS inline; só as fontes Spectral e Inter vêm do Google Fonts) |
| `data/index.json` | Grupos, parâmetros por grupo (limiar S1, ξ, origem, evidência do `--run=0`), `t0=170`, `dt=5`, `nc=145` |
| `data/<grupo>.json` | 35 rodadas × 17 métricas × 145 ciclos, ondas reais por rodada, Q-table/ε/S1-S2 ao fim de cada rodada e média ± DP por ciclo |
| `data/trace_<grupo>_r<NNN>.json` | Topologia da rodada: eleição inicial (`lid0`), líderes, ondas e, por ciclo, só as mudanças (decisão de cada órfão, novos líderes, pendentes). As posições são calculadas na página a partir da grade de `contaski.cc` e de N |

O guard (`tools/with_results_guard.sh`) grava a permissão original de cada caminho de `results-synapt/`, aplica
`chmod -R a-w` durante o comando e restaura exatamente as permissões gravadas no fim.

## Referência

Interações e estrutura de seções inspiradas no INTUIRT
(<https://github.com/carlosapepe/INTUIRT/tree/d135685>), sem reutilizar texto nem modelo.
