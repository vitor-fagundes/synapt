# Plano — Simulador web do SYNAPT, Fase A (revisão 5, congelada)

Repositório: `/home/ubuntu/workspace/ns-3-dev/scratch/synapt` (Git próprio), branch `feat/simulador-web` criada de `synapt-v1`.
Referência só de estrutura e interação, sem copiar texto nem modelo: `/tmp/intuirt-ref` (INTUIRT). A referência busca `data/trace_*.json`, mas os traces estão na raiz; isso não é normativo aqui.

**Escopo desta aprovação: somente a Fase A.** A Fase B (trace a partir do log) terá plano próprio, com CLI, testes e o contrato de `index.json.traces`, revisado antes de implementar.

## Decisões fechadas (pelo usuário, autor do SYNAPT, em 2026-11-25; não reabrir em revisão)

1. **Precedência para o que foi executado: logs > código atual > README.** As contradições entre o README e os dados/site (tempos, override para N≥300, `--run=1` nos exemplos, linha "300 com falhas") são **exceção permanente aceita**. O README não é editado; o site mostra as diferenças na seção de método.
2. **Publicação de `site/data`:** troca de diretório por dois `os.rename`. **Não é atômica**; é **recuperável via git**, porque `site/data` é versionado: se falhar, `git checkout -- site/data`. Não há código 5, nem gancho `SYNAPT_BUILD_FAIL_AT`, nem tratamento da falha do rollback.
3. **Actions do workflow por tag** (como no exemplo oficial do GitHub Pages), não por SHA.
4. **Upload na Fase A aceita só `IntuitiveStats.csv`.**
5. **Teste e2e:**
   - `tools/package.json` com `playwright` em `devDependencies` (versão fixa 1.63.0, a do Chromium já em cache);
   - `tools/node_modules/` não é versionado;
   - `tools/test_site_e2e.mjs` é pulado com aviso claro (código 0 e a mensagem "PULADO: playwright não instalado em tools/") se o pacote não estiver instalado.
   - Não usa `node_modules` de outros projetos.
6. **`--allow-dirty`** só relaxa a checagem de alterações não commitadas no código (`tools/`) e em `site/`. A checagem `git status --porcelain results-synapt` vazio (antes e depois da geração) **não pode ser desligada por nenhuma opção**: se não estiver vazia, código 2.
7. **Portão:** esta revisão é a última de plano. Achados de NO-GO que sejam só lacunas de especificação ou ampliação de escopo ficam como pendências na seção "Pendências da revisão final" e a Fase A é implementada. A implementação só para diante de achado novo de corretude dos dados, perda de dado, escrita fora de `site/` ou `.site-build-tmp/`, ou segurança.

## Precedência das fontes

Para o que foi executado: **logs > código atual > README**. O código atual não descreve `300_no_failures`, que é pré-calibragem e não tem `DENSITY_OVERRIDE` em nenhum dos 35 logs. O README **não é editado**; o site mostra as diferenças na seção de método:
- tempos: o README diz 1º ciclo em 160 s e ondas em 300/450/600 s; os logs mostram 170 s e 310/460/610 s, porque o AP começa em t=10 s e os agendamentos são relativos;
- parâmetros: o README aplica o override a todo N≥300; os logs mostram o override em `300_with_failures` (35/35) e não em `300_no_failures` (0/35);
- a linha "300 nós — com falhas" da tabela do README corresponde a `300_with_failures_OLD_backup`; o site usa o `300_with_failures` atual;
- os exemplos de execução do README usam `--run=1`; as séries publicadas usaram `--run=0`.

## Restrições

- `results-synapt/` é **somente leitura**: os scripts só leem dali.
- Nenhum `.cc/.h` alterado; o NS-3 não é executado.
- Site: `site/index.html` único (HTML+CSS+JS inline, sem framework, sem build, sem CDN além do Google Fonts: Spectral e Inter) + `site/data/*.json`. O Pages publica só `site/`.
- Workflow único, `.github/workflows/pages.yml`:
  - gatilhos `push` em `synapt-v1` e `workflow_dispatch`;
  - `permissions: {contents: read, pages: write, id-token: write}`; `concurrency: {group: pages, cancel-in-progress: false}`;
  - job `deploy` com `environment: {name: github-pages, url: ${{ steps.deployment.outputs.page_url }}}`;
  - passos `actions/checkout@v4`, `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3` (`path: site`), `actions/deploy-pages@v4` (`id: deployment`);
  - sem Jekyll e sem sparse checkout. O deploy só é verificado depois do push feito pelo usuário.
- pt-BR. Nomes e parâmetros conforme o README, exceto onde a precedência acima manda o contrário (e aí isso é exibido).
- A página não mostra topologia sintética nem estima o que não está nos dados. Fetch com falha → erro visível, nunca dados inventados.
- Constantes em todo lugar: `t0=170`, `dt=5`, `nc=145`.
- Limiar S1 e ξ **por grupo**, lidos dos logs (override presente → 0,245/−0,033; ausente → 0,20/−0,05). Em N=300, aviso: sem falhas = pré-calibragem, com falhas = pós-calibragem.
- Grupos publicados (7 × 35 = 245 rodadas): `200_no_failures`, `200_with_failures`, `250_no_failures`, `250_with_failures`, `250_random_failures`, `300_no_failures`, `300_with_failures`. `300_with_failures_OLD_backup` entra só na validação.
- Seção de método, como escolha de desenho:
  - todas as séries usam `--run=0` de propósito, fixando a aleatoriedade do NS-3;
  - as 35 rodadas são episódios sucessivos de aprendizado encadeados pelo `knowledge.dat`, com carga (capacidades e tarefas por `std::random_device`) variando entre rodadas;
  - cenários = execuções independentes;
  - o agregado se chama "média ± DP ao longo das 35 rodadas", nunca intervalo de confiança;
  - linha de proveniência do `--run=0`, em `index.json` (campo `run_evid` por grupo) e no site:
    - 200/250 sem e com falhas e `300_no_failures`: "runner versionado (`run_simulations.sh`, sectional@c5e78e0)";
    - `250_random_failures` e `300_with_failures`: "--run=0 declarado pelo autor; consistente com a sequência idêntica de eventos do NS-3".
  - Fonte da declaração do autor: a decisão do usuário (autor do SYNAPT) em 2026-11-25. Ela é registrada em `site/README.md` (seção Proveniência), criado já no commit 2b, junto com os dados que a citam; o commit 5 completa o restante do README do site. A consistência vem da comparação do prefixo do `simulation.log` (algoritmo abaixo) com os grupos de sectional de mesmo N, feita em 2026-11-25 nas 35 rodadas: `250_random` × `250_no` = 35/35 e `300_with` × `300_no` = 35/35. Controles com `--run=0` versionado: `250_with` × `250_no` = 35/35 e `200_with` × `200_no` = 35/35. O gerador **re-verifica isso para todas as rodadas** e grava o resultado em `run_evid_check: {ref: "<grupo de referência>", iguais: <int>, total: 35}`. O campo só existe nos objetos de `250_random_failures` (ref `250_no_failures`) e de `300_with_failures` (ref `300_no_failures`). A página mostra "prefixo idêntico em iguais/35 rodadas".
  - Algoritmo, para cada k: lê o `simulation.log` da rodada k do grupo e da rodada k da referência.
    - Descarta as linhas que casam `^\d+\+`, `^CENARIO`, `^FAILURE_CONFIG`, `^INTUITIVE_PERSIST` e `^DENSITY_OVERRIDE`.
    - Mantém as linhas desde o início até, exclusive, a primeira que começa com `N: C `.
    - Normaliza `^(N: MS \(.*), \d+\)$` para `\1)` (remove o tamanho do pacote, que depende das capacidades sorteadas).
    - Conta "igual" se as duas listas coincidirem **até o comprimento da menor**. O ponto de corte (primeiro `N: C `) depende das capacidades sorteadas, não da semente; em igualdade estrita, até os controles dão só 8–18/35.
  - O contrato registra o que o teste mostra: o mesmo `--run` dos grupos de sectional. Não há controle negativo com outro `--run` nos dados.
- `.DS_Store` e `__pycache__` fora do versionamento.

## Commits planejados

0. `docs: plano do simulador web` — este arquivo em `docs/plano-simulador-web.md`.
1. `chore: gitignore` — `.gitignore` (`__pycache__/`, `*.pyc`, `.DS_Store`, `.site-build-tmp/`, `node_modules/`) + `git rm --cached plots/__pycache__/regen_csvs.cpython-310.pyc`.
2a. `feat(tools): gerador de dados da Fase A` — só `tools/build_site_data.py` e os testes do gerador (`tools/test_build_site_data.py`).
2c. `test(site): e2e` — `tools/package.json`, `tools/package-lock.json`, `tools/test_site_e2e.mjs` (depois da página).
2b. `data(site): dados da Fase A` — `site/data/index.json` + `site/data/<grupo>.json`, gerados com o gerador já commitado em 2a.
3. `feat(site): página de replay` — `site/index.html`.
4. `ci: GitHub Pages` — `.github/workflows/pages.yml`.
5. `docs(site): README do site` — `site/README.md`.

## `tools/build_site_data.py` — contrato

Python 3.10, só stdlib. Uso: `python3 tools/build_site_data.py [--results results-synapt] [--out site/data] [--check-only]`.

- **`--out` restrito:** `realpath(--out)` tem de ser exatamente `<raiz do repositório>/site/data`; qualquer outro caminho dá código 2. (`--out` existe só para os testes, que passam um repositório de teste montado no scratchpad com a mesma estrutura.)
- **Cardinalidade (código 2 se violada):**
  - exatamente os 7 grupos publicados mais `300_with_failures_OLD_backup` (só validação), cada um com exatamente `run_001`…`run_035`;
  - grupos com falhas fixas: exatamente 3 linhas `FAILURE_CONFIG: Onda`, 1 `CENARIO4`, 3 `FAILURE_WAVE: ===` e 3 `Onda k completa`, com k = 1, 2, 3 em ordem crescente de tempo;
  - aleatório: exatamente 3 `FAILURE_CONFIG_RANDOM`, 3 `FAILURE_CONFIG: Onda`, 0 `CENARIO4`, 3 `===` e 3 `completa`, com k = 1, 2, 3 e `t = cfg_t + 10` (tolerância 0,001; `cfg_t` vem de `FAILURE_CONFIG_RANDOM`);
  - (cardinalidades conferidas nos 280 logs em 2026-11-25: todas valem)
  - grupos sem falhas: zero linhas de qualquer um desses tipos;
  - nos grupos fixos, `t = cfg_t + 10`.
- **Leitura:** abre tudo em modo leitura. Aborta com código 2 se `realpath(--out)` ou o diretório temporário estiver dentro de `realpath(--results)`.
- **`--check-only`:** lê, valida e imprime o relatório; **não grava nada** em lugar nenhum.
- **Diretório temporário:** `.site-build-tmp/` na raiz do repositório (fora de `site/`, no `.gitignore`).
  - É criado com `os.mkdir`, que é exclusivo: se o caminho já existir (diretório, arquivo ou symlink), o gerador **aborta com código 2 sem apagar nada** e pede remoção manual.
  - `site/` e `--out` não podem ser symlinks (`os.lstat`); se forem, código 2.
  - `.site-build-tmp/` e o pai de `--out` têm de estar no mesmo sistema de arquivos (`st_dev` igual); senão, código 2 antes de gerar.
- **Troca do conjunto inteiro** (troca de diretório por dois renames; não é atômica, é recuperável via git, ver Decisão 2):
  1. gera tudo em `.site-build-tmp/data/`;
  2. valida tudo;
  3. copia para `.site-build-tmp/data/` os `trace_*.json` já existentes em `--out` (a Fase B vai criá-los; o gerador da Fase A não pode apagá-los). Cada um tem de ser arquivo regular (`os.lstat`, sem symlink); senão, código 2. **Só então** confere o tamanho: a soma de todos os arquivos em `.site-build-tmp/data/` contra `--max-bytes`, gerando código 4;
  4. `os.rename(--out, .site-build-tmp/old)`;
  5. `os.rename(.site-build-tmp/data, --out)`. Se o passo 5 falhar, tenta `os.rename(.site-build-tmp/old, --out)`, imprime a instrução `git checkout -- site/data` e sai com código 2;
  6. remove `.site-build-tmp/` inteiro.
  - Em qualquer saída com erro antes do passo 4, `--out` fica intocado e `.site-build-tmp/` é apagado (`try/finally`, só o que o próprio gerador criou).
  - Se `--out` ainda não existir, o passo 4 é pulado.
- **Códigos de saída:**
  - 0 = ok (inclusive com a divergência conhecida e aceita do item 300 com falhas e com avisos da conferência com `failure_response.csv`);
  - 2 = erro de entrada, formato, ambiente ou publicação (arquivo ausente, cabeçalho, contagem de linhas, timestamps, parser, temporário preexistente, symlink, sistema de arquivos diferente, falha na troca);
  - 3 = divergência de validação não aceita contra o README, ou soma S1/S2 não fecha;
  - 4 = tamanho total > `--max-bytes` (padrão 5 000 000).
- **Parâmetros de teste:** `--readme` (padrão `README.md`) e `--max-bytes`.
- **Proteção de `results-synapt/`:** no início e no fim, o gerador roda `git status --porcelain -- results-synapt`. Saída não vazia dá código 2, e nenhuma opção desliga isso (Decisão 6).
- **`commit_codigo`:** é o `git rev-parse HEAD` no momento da geração, ou seja, o commit que já contém o gerador (2a), nunca o commit que vai conter o JSON (2b). Se `tools/build_site_data.py` tiver alteração não commitada (`git status --porcelain tools/build_site_data.py` não vazio), o gerador aborta com código 2, salvo `--allow-dirty`, que grava `commit_codigo:"<hash>-dirty"`. `--allow-dirty` cobre só `tools/` e `site/`.

Entradas por rodada (`results-synapt/<g>/run_NNN/`):
- `IntuitiveStats.csv`: o cabeçalho tem de ser exatamente `timestamp,qi,sr,tii,arf,meanRho,netLearning,pThreat,totalOrphans,actionsReallocate,actionsRecluster,actionsDoNothing,cycleS1,cycleS2,anomalies,qDoNothing,qReallocate,qRecluster`, com 145 linhas e `timestamp == 170 + 5k` (k=0..144).
- `IntuitiveQValues.txt`: o banner diz "4x3", mas o arquivo tem 3 estados; o banner é ignorado. O parser valida pelo nome os 3 estados (`EXISTING_VIABLE`, `ORPHAN_ONLY`, `NO_MATCH`) × 3 ações (`DO_NOTHING`, `REALLOCATE_TO_EXISTING`, `RECLUSTER_ORPHANS`), mais `Epsilon final`, `S1 count total` e `S2 count total`.
  - Os totais são acumulados na série, porque os contadores persistem no `knowledge.dat`. O script verifica `S1tot(k) = S1tot(k−1) + Σ cycleS1(k)`, com `S1tot(0) = 0`, e o mesmo para S2; se não fechar, código 3.
- `simulation.log`, só por regex:
  - `FAILURE_WAVE: === Onda (\d+) de (\d+) no tempo ([\d.]+)s ===`;
  - `Onda (\d+) completa — (\d+) líderes falharam, (\d+) órfãos reais`;
  - `FAILURE_CONFIG_RANDOM: Onda (\d+) t=([\d.]+)s, ([\d.]+)%`;
  - `FAILURE_CONFIG: Onda (\d+) agendada para t=([\d.]+)s`;
  - `CENARIO4: (\d+) ondas de falha configuradas, ([\d.]+)% cada`;
  - a presença de `DENSITY_OVERRIDE`;
  - `INTUITIVE_PERSIST: … não encontrado` (rodada começou do zero).

**Mapa CSV → chave curta** (ordem canônica = ordem do CSV, sem `timestamp`):

| # | CSV | chave | tipo |
|---|---|---|---|
| 0 | qi | qi | float 4 casas |
| 1 | sr | sr | float |
| 2 | tii | tii | float |
| 3 | arf | arf | float (6 casas, pois os valores ficam em ~0,007) |
| 4 | meanRho | rho | float |
| 5 | netLearning | nl | float |
| 6 | pThreat | pt | float |
| 7 | totalOrphans | orf | int |
| 8 | actionsReallocate | rea | int |
| 9 | actionsRecluster | rec | int |
| 10 | actionsDoNothing | dn | int |
| 11 | cycleS1 | s1 | int |
| 12 | cycleS2 | s2 | int (inclui as decisões "S1+S2") |
| 13 | anomalies | an | int |
| 14 | qDoNothing | qd | float |
| 15 | qReallocate | qr | float |
| 16 | qRecluster | qc | float |

Saídas:
- `index.json`:
  ```
  {v:1, commit_codigo:"<HEAD do gerador>", nota_commit:"dados gerados de results-synapt/ nesse commit",
   t0:170, dt:5, nc:145, cols:[17 chaves],
   grupos:[{id, n, cen:"no"|"with"|"random", runs:35, thS1, xi, override, origem, run_evid, run_evid_check?, nota}],
   traces:[]}
  ```
  `traces` fica vazio na Fase A.
- `<grupo>.json`:
  ```
  {id, runs:[{r, zero:bool, d:[17][145], ondas:[{w, t, cfg_t, pct, lf, orf}],
             fim:{eps, s1tot, s2tot, Q:[3][3]}}],
   agg:{m:[17][145], sd:[17][145]}}
  ```
  - A orientação é sempre `[coluna][ciclo]`.
  - `agg` usa DP amostral (n−1) e é gerado para todos os grupos.
  - No `250_random_failures`, a página não marca ondas no modo média (variam por rodada) e mostra uma nota.
  - Campos de cada onda, todos obrigatórios e nunca `null`:
    - `t`: tempo real em que a onda disparou (`FAILURE_WAVE: === Onda … no tempo T s`);
    - `cfg_t`: tempo configurado. Nos grupos fixos vem de `FAILURE_CONFIG: Onda k agendada para t=…s` (300/450/600); no aleatório, de `FAILURE_CONFIG_RANDOM` (tempo sorteado);
    - `pct`: percentual da onda (30 nos fixos, por `CENARIO4: … 30% cada`; no aleatório, o sorteado);
    - `lf`, `orf`: líderes falhos e órfãos de `Onda k completa`.
    - Os grupos sem falhas têm `ondas: []`.
- **Precisão** (ao serializar):
  - floats de `d`, `agg.m` e `agg.sd` com 4 casas, exceto `arf` (6);
  - `Q` com 4 casas; `eps` com 6; `t`, `cfg_t` e `pct` com 3;
  - contagens como inteiros (em `agg`, a média e o DP das contagens com 4 casas);
  - `-0.0` vira `0`; NaN ou infinito → código 2.
  - JSON compacto (`separators=(",",":")`, `ensure_ascii=False`, UTF-8).
- **Conferência com `plots/data/failure_response.csv`:**
  - Cobertura esperada: as 315 chaves (grupo, rodada, onda) de `200/250/300_with_failures`. `250_random_failures` não está no CSV e não é conferido; isso é impresso.
  - Para cada chave, compara `n_leaders_failed` e `n_orphans` com o log; `failure_time` é comparado com `cfg_t` em 200/250 e com `int(t)` em 300 (semântica mista).
  - Chave ausente, chave extra ou valor divergente gera **aviso** com contagem e exemplos, sem mudar o código de saída. Nada é corrigido.
- **Validação impressa**, calculada dos CSV brutos e não do JSON arredondado:
  - QI e SR finais (t=890): média ± DP amostral por grupo, em %, 1 casa, contra a tabela "Resultados Resumidos" do README (lida por regex). "300 nós — com falhas" é a divergência aceita: o script imprime o grupo atual e o `OLD_backup` e confirma que o README bate com o `OLD_backup`. Se o `OLD_backup` também não bater, código 3.
  - S1/S2 por N: soma das decisões das 35 rodadas dos grupos com falhas fixas, contra a tabela "Distribuição S1/S2".
  - Qualquer outra divergência além do arredondamento → código 3. Nada é ajustado.
- **Tamanho:** soma dos JSON > 5 MB → código 4.

## Interfaces internas

Gerador (`tools/build_site_data.py`):
- `parse_stats(path) -> list[list[float]]` (17×145);
- `parse_qvalues(path) -> dict`;
- `parse_log(path, cen) -> dict` (ondas, override, zero, linhas de prefixo);
- `log_prefix(path) -> list[str]`;
- `build_group(results, gid) -> dict`;
- `validate_readme(readme_path, raw) -> list[str]` (divergências);
- `crosscheck_failure_response(csv_path, groups) -> list[str]` (avisos);
- `publish(tmp_data, out) -> None` (passos 3–6);
- `main(argv) -> int`.

Página (`site/index.html`, JS inline):
- `loadIndex()`, `loadGroup(id)`: fetch com erro visível;
- `selectRun(gid, r|'media')`;
- `render(k)`: KPIs, painel, etapas, cursor;
- `drawChart(metric)`;
- `buildCompareTable(n)`;
- `parseLocalCsv(text) -> {cols, t, d} | Error`;
- `showError(msg)`;
- `setTheme(mode)`.

## `site/index.html`

Seções (âncoras): `#sim`, `#res`, `#como`, `#metodo`, `#dados`, rodapé.

- **Controles:**
  - N (200/250/300);
  - cenário (sem falhas / com falhas / falhas aleatórias; este só existe em 250 e fica desabilitado nos outros N);
  - rodada 1–35;
  - modo "média ± DP ao longo das 35 rodadas".
- **Replay:**
  - índice interno k = 0..144, com t = 170 + 5k;
  - a interface **exibe a posição 1-based** "ciclo k+1 de 145". Exemplo: t = 310 s → k = 28 → "ciclo 29 de 145";
  - anterior/pausar/próximo; barra de tempo; velocidade 0,5×/1×/2×/4× (1× ≈ 700 ms por ciclo).
- **KPIs do ciclo:**
  - QI; SR; TII;
  - pThreat contra o limiar do grupo, com o rótulo "acima/abaixo do limiar S1";
  - órfãos; anomalias;
  - S1/S2 do ciclo, acumulado na rodada e acumulado na série (= `s1tot` da rodada anterior + soma na rodada);
  - ações REALLOCATE/RECLUSTER/DO_NOTHING; Q-médios.
  - No modo média, cada KPI mostra média ± DP.
- **Painel Dual-System:** contadores e a fração S1 do ciclo; sem "confiança". O ε aparece só como "ε ao fim da rodada".
- **Topologia:** "requer trace (Fase B)"; nenhuma topologia sintética.
- **Gráfico SVG inline:**
  - métrica selecionável;
  - linhas verticais nas ondas reais da rodada;
  - linha do limiar no gráfico de pThreat;
  - faixa ±DP no modo média;
  - cursor no ciclo atual.
- **Tabela comparativa** entre os cenários do N escolhido.
  - Primeiro calcula-se um resumo por rodada; depois, a média ± DP amostral desses 35 resumos ("média ± DP ao longo das 35 rodadas"):
    - QI final = `qi[144]`; SR final = `sr[144]`;
    - QI mínimo = `min(qi[0..144])`;
    - pThreat médio = média de `pt[0..144]`;
    - órfãos totais = `Σ orf`;
    - REALLOCATE/RECLUSTER/DO_NOTHING = `Σ rea`, `Σ rec`, `Σ dn`.
  - Sem DP:
    - % S1 das decisões = `Σs1 / (Σs1+Σs2)`, somando as 35 rodadas (mesma definição da tabela do README); grupos sem decisões mostram "sem decisões (nenhum órfão)";
    - "ε ao fim da série (rodada 35)";
    - thS1/ξ do grupo.
  - Os resumos são calculados no navegador, a partir de `d` (já arredondado a 4 casas). A validação contra o README usa os CSV brutos no gerador.
  - Nota da tabela: "Os cenários (grupos) são execuções independentes entre si: cargas sorteadas separadamente. Dentro de cada grupo, as 35 rodadas são episódios encadeados pelo knowledge.dat." Em N=300, o aviso pré/pós-calibragem.
- **Como funciona:** as 7 etapas.
  - Durante o replay, acende a sequência executada no ciclo: 1–4 e 7 sempre; 5 se `orf > 0`; 6 se `rec > 0`.
  - Fora disso, a etapa fica apagada com "sem órfãos neste ciclo".
- **Método e proveniência:** o texto da seção Restrições, as diferenças do README (Precedência), os parâmetros por grupo e o `run_evid`.
- **Seus dados:** comandos para rodar o NS-3 e o gerador, mais o botão "Carregar trace".
  - Na Fase A, o botão aceita **só** `IntuitiveStats.csv` avulso:
    - cabeçalho exato de 18 colunas; ≥ 1 linha; todos os valores numéricos finitos;
    - timestamps **estritamente crescentes** (logo, únicos);
    - limite de 20 MB.
    - Usa os timestamps do próprio arquivo e mostra só as métricas, rotulado "arquivo local".
  - Um `.json` recebe a mensagem visível "traces JSON chegam na Fase B; nesta versão o botão aceita apenas IntuitiveStats.csv", sem ser interpretado. O contrato do JSON fica para o plano da Fase B.
  - Arquivo inválido gera um erro visível com o motivo (linha e coluna, quando aplicável).
  - Todo texto vindo de arquivo entra via `textContent` ou atributos SVG, nunca via `innerHTML`.
- **Visual:**
  - fontes: `https://fonts.googleapis.com/css2?family=Spectral:wght@500;600&family=Inter:wght@400;500;600;700&display=swap`, com fallback Georgia e system-ui se o carregamento falhar;
  - `color-scheme: light dark` e tokens com `light-dark()`;
  - alternância claro/escuro via `data-theme` no `<html>`;
  - Spectral + Inter;
  - responsivo até 390 px sem rolagem horizontal;
  - respeita `prefers-reduced-motion`.
- **Fetch:** `data/index.json` e `data/<grupo>.json`, relativos a `site/index.html`. Em erro, faixa vermelha (`role="alert"`) com a URL e:
  - "HTTP <status>", se houve resposta;
  - "falha de rede (sem resposta HTTP)", se o `fetch` rejeitou;
  - "JSON inválido", se o parse falhou.
  - Os painéis ficam vazios.

## Teste (critérios objetivos; todos obrigatórios para aceitar a Fase A)

Testes do gerador (`tools/test_build_site_data.py`, unittest, stdlib). Cada teste monta, num diretório temporário (`tempfile.mkdtemp`, fora do repositório), um repositório de teste:
- `git init` e um commit contendo `tools/build_site_data.py`, para que "raiz do repositório" (= `git rev-parse --show-toplevel`) e `commit_codigo` fiquem definidos;
- `results-synapt/` montado com rodadas copiadas;
- `README.md` de teste;
- `site/data/` inicial.

O gerador é chamado como subprocesso dentro desse repositório. Os testes rodam sobre essas cópias: alguns diretórios de rodada **copiados** de `results-synapt/` e um README de teste. Nunca tocam em `results-synapt/` nem em `site/`. Cada caso de erro e o `--check-only` afirmam o código de saída e que `--out` ficou byte a byte intacto; o caminho feliz afirma o código 0, o conteúdo regerado e a preservação dos `trace_*.json`:
- cabeçalho de CSV alterado → 2; 144 linhas → 2; timestamp fora da grade → 2;
- `IntuitiveQValues.txt` sem um estado → 2;
- valor da tabela do README alterado → 3; total S1 acumulado quebrado → 3;
- `--max-bytes 1000` → 4;
- `.site-build-tmp/` preexistente → 2, e o caminho preexistente continua lá;
- `--out` symlink → 2;
- falha simulada no passo 5 (o teste troca `os.rename` dentro do processo, chamando `publish()` diretamente) → o rollback restaura `--out`;
- arquivo novo não rastreado em `results-synapt/` do repositório de teste → 2, mesmo com `--allow-dirty`;
- `--out` diferente de `site/data` → 2; `trace_*.json` symlink em `--out` → 2;
- rodada faltando (`run_035` ausente) → 2; uma `Onda k completa` a menos num grupo com falhas → 2;
- caminho feliz → 0, com `trace_*.json` preexistente preservado;
- `--check-only` → 0, sem nenhum arquivo criado.

Teste de ponta a ponta, versionado em `tools/test_site_e2e.mjs`, com comando único documentado em `site/README.md`: `cd tools && npm install && node test_site_e2e.mjs <dir-capturas>`. Sem playwright instalado, o teste é pulado com aviso (Decisão 5). O script sobe o próprio `http.server` e grava as capturas num diretório passado por argumento, fora de `site/`:

- O gerador termina com código 0 e total ≤ 5 MB. `--check-only` não altera `git status`.
- `git status --porcelain results-synapt` vazio antes e depois.
- O script serve `site/` com `python3 -m http.server` e usa Playwright 1.63.0 de `tools/node_modules` + o Chromium em cache. Critérios:
  - zero mensagens `console.error` ou `pageerror`;
  - a 390 px, `document.documentElement.scrollWidth <= innerWidth`;
  - capturas claro/escuro × 1366 px/390 px;
  - um teste com `data/index.json` inacessível (rota bloqueada) tem de mostrar a faixa de erro;
  - um upload de CSV válido funciona; CSV com timestamps decrescentes e um `.json` mostram as mensagens de erro e de "Fase B";
  - replay: clicar em "próximo" 28 vezes a partir do início mostra "ciclo 29 de 145" e t = 310 s.

## Pendências da revisão final (goldfish 2026-11-25, NO-GO)

Relatório: 14 achados. Classificação pela Decisão 7:

**Decisão fechada (não reabre):**
- README contradiz tempos, parâmetros de N=300, linha "300 com falhas" e `--run`: Decisão 1.
- README omite `250_random_failures` e descreve outra estrutura de pastas: coberto pela Decisão 1; o site lista também essas duas diferenças na seção de método.

**Lacuna de especificação ou escopo (grupos A/C; resolvida na implementação como indicado):**
- e2e "obrigatório" × pulável: o pulo existe para outros ambientes; **nesta entrega o e2e é executado**, e a aceitação da Fase A exige essa execução.
- `origem` e `nota` sem domínio:
  - `origem` ∈ {"sectional@c5e78e0 (results-intuitive)", "synapt (results-synapt)"}, derivado do caminho do knowledgePath no log;
  - `nota` é texto fixo por grupo, definido numa tabela no gerador.
- Primeira publicação (`--out` inexistente) com falha no passo 5: não há rollback; `.site-build-tmp/` é removido e o código é 2.
- JSON válido mas fora do schema: a página valida a presença e as dimensões de `cols`, `grupos`, `runs[].d` (17×145) e `agg`, e mostra "dados em formato inesperado: <campo>", sem `pageerror`.
- Teste da falha do passo 5: testa `publish()` diretamente e afirma a exceção e a restauração de `--out`; o código 2 do CLI é testado à parte.
- Ordem dos commits: o 2c (e2e) vem depois do 3 (página).
- `run_evid_check`: exige prefixo comum ≥ 1000 linhas; não há controle negativo com outro `--run` (registrado no site como limite da evidência).
- Proveniência de `run_simulations.sh`: citar o commit completo `c5e78e0` do repositório sectional (caminho local `scratch/sectional`).
- Referência INTUIRT: permalink `https://github.com/carlosapepe/INTUIRT/tree/d135685`.
- Upload: aceita qualquer nome com extensão `.csv`; o limite é 20 MiB (20 971 520 bytes).

**Corretude dos dados, perda de dado, escrita fora de `site/`/`.site-build-tmp/` ou segurança:** ver "Achados levados ao usuário" abaixo.

**Achados levados ao usuário (2026-11-25) e decisões:**
- *Perda de dado na troca de `site/data`:* **rejeitar desconhecidos.**
  - Se `site/data` tiver qualquer arquivo além de `index.json`, dos 7 `<grupo>.json` e de `trace_*.json`, o gerador aborta com código 2 sem apagar nada.
  - Arquivo conhecido em `site/data` com alteração não commitada também dá código 2, mesmo com `--allow-dirty`, que não cobre `site/data`.
  - Nada é descartado em silêncio.
- *Prova de somente leitura de `results-synapt/`:* `tools/with_results_guard.sh <comando…>` envolve **toda** execução do gerador e dos testes:
  1. aborta se estiver rodando como root (`id -u` = 0), porque o chmod não protege nesse caso;
  2. `chmod -R a-w results-synapt`;
  3. `git status --porcelain -- results-synapt` vazio;
  4. manifesto de metadados `find results-synapt -printf '%P\t%s\t%T@\n' | LC_ALL=C sort | sha256sum`;
  5. roda o comando;
  6. repete os passos 3 e 4; os hashes de antes e depois têm de ser iguais;
  7. `chmod -R u+w results-synapt` no fim, inclusive em erro (`trap`).
  - Sem hash do conteúdo. Se uma checagem falhar, sai com código ≠ 0 e mostra qual.
