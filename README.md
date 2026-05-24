# SYNAPT v2

**Extensão do SYNAPT para falha em nó-membro com decisão por cluster degradado.**

> Projeto acadêmico — Vítor Fagundes
> Orientadores: Prof. Aldri Santos (UFMG) e Prof. Carlos Pedroso (UFPR)
>
> Para o sistema base (v1 — falha de líder), ver [branch `synapt-v1`](https://github.com/vitor-fagundes/synapt/tree/synapt-v1).

---

## O que mudou em relação à v1

| Dimensão | v1 (`main`) | v2 (esta branch) |
|---|---|---|
| Gatilho da decisão | Líder cai → membros viram órfãos | Membros caem → cluster fica degradado |
| Unidade de decisão | Por órfão individual | Por cluster degradado |
| Estados | EXISTING_VIABLE / ORPHAN_ONLY / NO_MATCH | HEALTHY / DEGRADED / CRITICAL |
| Ações | REALLOCATE / RECLUSTER / DO_NOTHING | REINFORCE / DISBAND / DO_NOTHING |
| Q-table & R_int | Compartilhados | **Separados** (paralelos aos da v1) |
| Métrica de saúde | Similaridade entre nó e cluster | Redundância de capabilities + perda de massa |
| Inferência subproduto | — | Ranking de risco por Li ascendente |

A v1 permanece **intacta** e ativa nesta branch. Quando o cenário envolve falhas de líder ou quando DISBAND despeja membros como órfãos, o pipeline da v1 continua processando-os normalmente. As duas camadas compõem.

---

## Motivação

A v1 só age quando um líder cai — o evento que gera órfãos. Falhas em sensores comuns (não-líderes), bem mais frequentes na prática em redes IIoT, não tinham resposta no sistema. A v2 fecha essa lacuna adicionando uma camada paralela de decisão que monitora **o estado de saúde de cada cluster** e age quando a degradação se acumula, sem depender de uma falha catastrófica.

---

## Cenário de Falha v2

| Parâmetro | Valor |
|---|---|
| Modo (`--failureMode`) | `member` (v1 usa `leader`) |
| Estratégia de sorteio | **A — Pool global cross-cluster** (uniforme entre todos os membros vivos) |
| Ondas | 2 ondas em t = {300, 600}s |
| % por onda | 25% dos membros vivos no pool |
| Líderes | **Permanecem intactos** (sem falha de líder neste cenário) |

A escolha de 2 ondas × 25% (em vez de 3 × 30% como na v1) evita perda cumulativa excessiva (~44% ao fim vs. ~90% com 3×30%). A estratégia A — pool global — distribui o dano de forma esparsa entre clusters, refletindo falhas naturais de sensores em IIoT.

---

## Classificação do Cluster (estados)

Cada cluster vivo é avaliado a cada ciclo do orquestrador via **dois sinais ortogonais**:

### Sinais

1. **`capabilityCoverage` — redundância média por capability**

   ```
   coverage = média_sobre_caps_originais( portadores_atuais(cap) / portadores_originais(cap) )
   ```

   Mede perda *gradual* de redundância. Ex.: capability X tinha 10 portadores no cluster e perdeu 3 → contribui 0,7 para a média. O snapshot do "original" é feito no primeiro ciclo em que o cluster é observado com membros.

2. **`memberLossRate` — perda de massa**

   ```
   memberLossRate = 1 − min(1, membros_vivos / membros_iniciais)
   ```

   Mede perda de massa direta do cluster.

### Tabela de classificação

| Estado | `capabilityCoverage` | `memberLossRate` |
|---|---|---|
| **HEALTHY** | ≥ 0,85 | ≤ 0,30 |
| **DEGRADED** | < 0,85 **ou** > 0,30 (um sinal) | |
| **CRITICAL** | < 0,85 **e** > 0,30 (ambos) | |

> **Nota de calibração (importante).** A primeira implementação usava cobertura *baseada em presença* (`|caps_atuais ∩ caps_originais| / |caps_originais|`), mas a alta redundância intra-cluster fazia esse sinal ficar sempre em 1,0 — o sinal era estruturalmente morto. CRITICAL nunca aparecia. A migração para **redundância média por capability** tornou o sinal observável e fez os 3 estados serem alcançáveis com a mesma intensidade de falha. Detalhes em [IntuitiveLearning.cc](IntuitiveLearning.cc) (função `processClusterDegradationIntuitively`).

---

## Ações de Resiliência

### DO_NOTHING
Não intervém no cluster no ciclo atual. Sem custo, sem risco. Reavalia no próximo ciclo. Ação preferida em HEALTHY (que sequer entra na decisão) ou quando o custo de intervir excede o benefício esperado.

### REINFORCE
Repõe redundância puxando 1 membro de um cluster vizinho HEALTHY:

1. Busca cluster doador HEALTHY com `sim(líder_doador, líder_alvo) ≥ 0,85` e `tamanho ≥ 2`
2. Escolhe o membro do doador mais similar ao cluster alvo
3. Transfere via `setMyLeader(alvo)` + `addClusterMember(membro)` no alvo, removendo da lista do doador

Sem doador viável: fallback para **DO_NOTHING** (se estado DEGRADED) ou **DISBAND** (se estado CRITICAL). Ação preferida em DEGRADED.

### DISBAND
Dissolve o cluster: o líder é desativado (`StopApplication()`) e os membros sobreviventes são adicionados ao pool de órfãos. No próximo ciclo, o **pipeline da v1** os processa via `OrphanState` + Dual-System (REALLOCATE / RECLUSTER). Ação preferida em CRITICAL — composição limpa com a v1.

---

## Dual-System S1/S2 Paralelo

A v2 usa uma classe `ClusterDualSystemResponse` **separada** da `DualSystemResponse` da v1. Mesma estrutura, mesmos hiperparâmetros, mas Q-table e R_int distintos.

| Parâmetro | Valor | Origem |
|---|---|---|
| `ALPHA_Q` | 0,20 | Compartilhado com v1 |
| `GAMMA_Q` | 0,90 | Compartilhado com v1 |
| `ε` inicial | 0,15 | Compartilhado com v1 |
| `ε_decay` | 0,97 | Compartilhado com v1 |
| `ε_min` | 0,02 | Compartilhado com v1 |
| `THRESHOLD_S1` | 0,20 | Compartilhado com v1 |
| `THRESHOLD_S2` | 0,60 | Compartilhado com v1 |
| `CLUSTER_COVERAGE_THRESHOLD` | 0,85 | **Novo** (v2) |
| `CLUSTER_MEMBER_LOSS_THRESHOLD` | 0,30 | **Novo** (v2) |
| `REALLOCATION_THRESHOLD` (REINFORCE) | 0,85 | Compartilhado com v1 |

### Gatilho de sistema (idêntico ao da v1)

- `pThreat > THRESHOLD_S1` → **S1** (consulta R_int no bucket `CL_<estado>`)
- `qi < 0,60` ou `sr < 0,60` → **S1+S2** (S2 prevalece)
- Caso contrário → **S2 puro** (ε-greedy sobre Q-table)

### Heurísticas estáticas do S1 (bootstrap sem experiência)

| Estado | Ação |
|---|---|
| HEALTHY | DO_NOTHING |
| DEGRADED | REINFORCE |
| CRITICAL | DISBAND |

Após acumular R_int, S1 consulta `bestIntuitiveAction(bucket)` e retorna a ação com maior recompensa média na janela deslizante.

### Recompensas

| Situação | Reward |
|---|---|
| REINFORCE bem-sucedido | `10,0 × sim_doador` |
| REINFORCE inviável (sem doador) | reescreve action; reward conforme fallback |
| DISBAND | `10,0 × 0,5 = 5,0` (ação destrutiva, ganho contido) |
| DO_NOTHING em estado problemático | `−2,0` |

---

## Ciclo Intuitivo Integrado

A v2 adiciona um passo **[5b]** entre o processamento de órfãos da v1 e o recálculo de métricas:

```
[1]  Atualizar contagem de membros por cluster
[2]  Atualizar features do AnomalyDetector
[3]  Detectar anomalias (Z-score global + temporal)
[3.5] Atualizar aprendizado distribuído (Li)
[4]  Calcular métricas globais (QI, SR, pThreat, TII, ARF)
[5]  Processar órfãos individualmente (Dual-System da v1)
[5b] Processar clusters degradados (Dual-System da v2)         ← novo
       └─ Persistir snapshot do ciclo
[5c] Dump do ranking de risco (Li ascendente)                  ← novo
[6]  Recalcular métricas pós-ação
[7]  Agendar próximo ciclo
```

DISBAND em [5b] adiciona os membros sobreviventes a `pendingOrphans`, consumido por [5] do próximo ciclo. O acoplamento é assíncrono — DISBAND não tenta processar órfãos no mesmo ciclo.

---

## Inferência Subproduto: Ranking de Risco

A cada ciclo, todos os nós vivos são ordenados por `Li` ascendente e dumpados em `RiskRanking.csv`:

```
timestamp,rank,address,Li,isLeader,isAnomalous
160,1,fe80::ff:fe00:1f,0.412,0,0
160,2,fe80::ff:fe00:8a,0.428,1,0
...
```

Nós no topo do ranking (Li baixo) são candidatos a falhar. Validar a correlação entre Li baixo no ciclo *t* e ocorrência em CRITICAL / falha no ciclo *t+1* é uma capacidade preditiva direta do sistema — alinhada com a proposta de "inferência" do nome do projeto.

---

## Persistência (knowledge.dat)

O arquivo `knowledge.dat` agora carrega **8 seções** (5 da v1 + 3 da v2):

```
[HISTORICAL]            ─┐
[INTUITIVE]              │ v1 (preservado)
[QTABLE]                 │
[EPSILON]                │
[COUNTERS]              ─┘
[QTABLE_CLUSTER]        ─┐
[EPSILON_CLUSTER]        │ v2 (novo)
[COUNTERS_CLUSTER]      ─┘
```

Aprendizado cumulativo entre rodadas funciona para ambas as camadas independentemente. O parser é tolerante: se o arquivo da v1 (sem as 3 seções novas) for carregado, o cluster dual-system inicia do zero.

---

## Como Executar

### Script automatizado (recomendado)

```bash
# Build uma vez
./ns3 build

# 35 rodadas cumulativas, N=250 (default da v2)
./scratch/synapt/run_v2.sh 35 250

# Smoke test (10 rodadas, N=200)
./scratch/synapt/run_v2.sh 10 200
```

O [run_v2.sh](run_v2.sh) faz:
- Apaga `capacitiesFile-N-contaski.txt`, `tasksFile-N.txt`, `APStats.txt` antes de cada run → força geração fresca de capabilities via `std::random_device`
- Mantém `--run=1` **fixo** (seed NS-3 estável → clusterização consistente)
- Persiste `knowledge.dat` entre runs (aprendizado cumulativo)
- Usa `--no-build` (precisa do build manual antes)
- Salva cada rodada em `run_NNN/` com snapshot completo

### Comando manual

```bash
./ns3 run "scratch/synapt/contaski \
    --nNodes=250 \
    --run=1 \
    --multipleFailures=true \
    --multiFailurePercent=25 \
    --failureTimes=300,600 \
    --failureMode=member \
    --knowledgePath=knowledge.dat"
```

A flag `--failureMode` aceita `leader` (v1, default) ou `member` (v2). A flag `--knowledgePath` ativa persistência entre rodadas.

---

## Estrutura de Saída

```
results-synapt-v2/<N>_member/
├── knowledge.dat                      (estado final cumulativo após N rodadas)
└── run_NNN/                           (1 subpasta por rodada, 3 dígitos)
    ├── IntuitiveStats.csv             (30 colunas: v1 + 11 v2 por ciclo)
    ├── IntuitiveQValues.txt           (Q-tables finais da v1 e v2)
    ├── APStats.txt                    (tarefas despachadas e aceites)
    ├── RiskRanking.csv                (ranking de Li por ciclo — inferência)
    ├── capacitiesFile-N-contaski.txt  (snapshot — caps daquela rodada)
    ├── tasksFile-N.txt                (snapshot — tarefas daquela rodada)
    ├── knowledge_snapshot.dat         (conhecimento ao fim da rodada)
    └── simulation.log                 (stdout/stderr completo)
```

Estrutura espelha a v1 (`results-synapt/<scenario>/run_NNN/`).

### Colunas v2 em `IntuitiveStats.csv`

Adicionadas 11 colunas após as colunas originais da v1:

```
clustersHealthy, clustersDegraded, clustersCritical,
actionsReinforce, actionsDisband, actionsClusterDoNothing,
cycleS1Cluster, cycleS2Cluster,
qClusterDoNothing, qReinforce, qDisband
```

---

## Resultados Experimentais (35 rodadas cumulativas, N=250)

### Métricas globais

| Métrica | Média ± σ | Faixa |
|---|---|---|
| QI | 0,9016 ± 0,0033 | muito estável |
| SR | 0,7220 ± 0,0082 | estável |
| pThreat | 0,1986 ± 0,0211 | |
| Clusters por rodada | 22,3 ± 3,9 | [16, 32] |
| CRITICAL/rodada | 13,6 ± 1,4 | [11, 17] |
| DISBAND/rodada | 13,6 ± 1,4 | [11, 17] |
| REINFORCE/rodada | 47,3 ± 57,3 | [0, 212] |

### Q-table convergida (Estado DEGRADED — ação ideal: REINFORCE)

| Run | DO_NOTHING | **REINFORCE** | DISBAND |
|---|---|---|---|
| 1 | 22,3 | **55,4** | 0,0 |
| 5 | 80,7 | **91,9** | 0,0 |
| 10 | 81,0 | **92,2** | 0,0 |
| 35 | 81,0 | **92,2** | 0,0 |

### Q-table convergida (Estado CRITICAL — ação ideal: DISBAND)

| Run | DO_NOTHING | REINFORCE | **DISBAND** |
|---|---|---|---|
| 1 | 0,0 | 0,0 | **10,0** |
| 5 | 0,0 | 0,0 | **37,3** |
| 10 | 0,0 | 0,0 | **46,7** |
| 35 | 0,0 | 0,0 | **50,0** (saturado) |

### Transição S2 → S1 (efeito Kahneman cumulativo)

| Run | S1 cluster | S2 cluster | razão |
|---|---|---|---|
| 1 | 79 | 47 | 1,7 : 1 |
| 5 | 360 | 246 | 1,5 : 1 |
| 35 | 1.656 | 1.028 | 1,6 : 1 |

S1 (intuição) sustenta predominância 1,5–1,7 sobre S2 (deliberação) durante todas as 35 rodadas. Aprendizado consolidado migra de S2 (analítico) para S1 (intuitivo) — comportamento esperado pelo design dual-system.

---

## Observações sobre a Q-table esparsa

Três células ficam em zero após as 35 rodadas:

| Célula zerada | Causa |
|---|---|
| `Q[CRITICAL][REINFORCE]` | Fallback reescreve action para DISBAND antes do Q-update quando `findDonor()` falha em cluster CRITICAL (cluster degradado raramente acha doador similar). A célula nunca é atualizada. |
| `Q[CRITICAL][DO_NOTHING]` | Apenas 147 chamadas S2 em CRITICAL (a maioria foi S1 → DISBAND via heurística). Com `ε_min = 0,02`, a exploração direta foi insuficiente para amostrar essa célula. |
| `Q[DEGRADED][DISBAND]` | A política aprendida prefere REINFORCE de forma consistente; nenhuma exploração direta de DISBAND ocorreu em DEGRADED nas 881 chamadas S2 sob o seed fixo da NS-3. |

A esparsidade reflete uma **política decisiva**: REINFORCE para DEGRADED, DISBAND para CRITICAL. Os zeros indicam alternativas **não exploradas**, não alternativas **rejeitadas**. Para cobertura completa da Q-table, aumentar `ε_min` (ex.: 0,02 → 0,10) ou adotar exploração informada (UCB, Boltzmann).

---

## Arquivos modificados em relação à v1

```
IntuitiveLearning.h/cc      — Adiciona ClusterDegradedState, ClusterAction,
                              ClusterDualSystemResponse + persistência
NodeAPApplication.h/cc      — Adiciona triggerMemberFailureWave,
                              processClusterDegradationIntuitively,
                              dumpRiskRanking, originalClusterCapsCount,
                              flag failureMode
contaski.cc                 — Adiciona CLI flag --failureMode
run_v2.sh                   — Script de execução automatizada
```

Demais arquivos (`NodeApplication.cc/h`, `AnomalyDetector.cc/h`, `capabilities.cc/h`, `task.cc/h`, `MyTag.cc/h`, `constants.h`) permanecem inalterados.
