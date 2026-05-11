# SYNAPT

**SYstem for Network-Adaptive Proactive Topology management**

> Resiliência adaptativa para redes IIoT baseada em aprendizado intuitivo dual (System 1 / System 2).
>
> Projeto acadêmico — Vítor Fagundes
> Orientadores: Prof. Aldri Santos (UFMG) e Prof. Carlos Pedroso (UFPR)

---

## Visão Geral

O SYNAPT é a evolução do sistema [SECTIONAL](https://github.com/vitor-fagundes/sectional). Em vez de um agente Q-Learning reativo que age apenas após uma falha, o SYNAPT implementa um **ciclo de decisão contínuo** inspirado no modelo de cognição dual de Kahneman:

- **Sistema 1 (S1) — Resposta Intuitiva:** rápida, baseada em experiência acumulada. Ativado quando a ameaça à rede (`pThreat`) ultrapassa um limiar, consulta o repositório de intuições (R_int) para agir imediatamente.
- **Sistema 2 (S2) — Deliberação Q-Learning:** lenta, analítica, epsilon-greedy sobre uma Q-table. Ativado em condições estáveis para aprender e refinar a política ao longo das rodadas.

O sistema opera a cada 5 segundos sobre toda a rede, detecta anomalias, atualiza o aprendizado distribuído dos nós e reintegra órfãos de forma autônoma — sem depender de uma falha explícita para agir.

---

## Arquitetura

```
contaski.cc              — Entrada principal: configura rede, falhas e ciclo de decisão
NodeApplication.cc/h     — Aplicação de cada nó sensor (beaconing, clustering, execução)
NodeAPApplication.cc/h   — Orquestrador AP: ciclo intuitivo, anomalias, órfãos
IntuitiveLearning.cc/h   — Motor de aprendizado: DistributedLearning, DualSystem, KnowledgeNetworks
AnomalyDetector.cc/h     — Detecção de anomalias por ensemble Z-score global + temporal
capabilities.cc/h        — Vetores de capacidades e similaridade (Eq. 1 do artigo)
task.cc/h                — Modelo de tarefas com capacidades requeridas e quorum
constants.h              — Enum de tipos de mensagem
MyTag.cc/h               — Tag NS-3 para identificação de mensagens UDP
```

---

## Fluxo de Simulação

| Instante | Evento |
|---|---|
| `t = 0–60 s` | Beaconing: nós descobrem vizinhos |
| `t = 60–90 s` | Disseminação de capacidades |
| `t = 90,5 s` | Clusterização + eleição de líder |
| `t = 150 s` | AP inicia despacho de tarefas |
| `t = 160 s` | **Primeiro ciclo intuitivo** (aguarda estabilização pós-clustering) |
| `t = 165, 170, ...` | Ciclos a cada 5 s até fim da simulação |
| `t = 300 s` | 1ª falha: 30% dos líderes ativos são removidos |
| `t = 450 s` | 2ª falha: 30% dos líderes ativos remanescentes |
| `t = 600 s` | 3ª falha: 30% dos líderes ativos remanescentes |

As falhas ocorrem apenas nos cenários `*_with_failures`. Nos cenários `*_no_failures`, o ciclo intuitivo ainda opera, detectando anomalias e otimizando continuamente.

---

## Ciclo de Decisão Intuitivo (a cada 5 s)

O orquestrador no AP executa 7 etapas em sequência:

### [1] Atualização dos Clusters
Recontabiliza os membros reais de cada cluster consultando o campo `myLeader` de cada nó vivo. O `initialMemberCount` é atualizado para cima quando a recuperação adiciona membros.

### [2] Detecção de Anomalias
Para cada líder vivo, computa três features:
- `taskAcceptRate` — aceites / despachos
- `timeSinceContact` — tempo desde o último heartbeat
- `clusterHealthRatio` — membros_atuais / membros_iniciais

O `AnomalyDetector` aplica um ensemble de dois critérios independentes (voto duplo):
- **Z-score global:** compara o nó contra a distribuição de todos os líderes ativos
- **Z-score temporal:** compara o nó contra seu próprio histórico

Um nó só é marcado como anômalo se **ambos** os critérios concordarem. Nós anômalos recebem penalidade `−0,05` no `Li`.

### [3] Aprendizado Distribuído
Executa um passo da equação de difusão de capacidade de aprendizado:

```
ΔLi = Σj β · (Lj − Li) + ξi
Li(t+1) = clip(Li(t) + α · ΔLi, 0, 1)
```

A vizinhança é definida pela topologia de clusters. O estímulo ambiental `ξi` penaliza nós anômalos (`−0,05`) e bonifica nós com vizinhos saudáveis (`+0,01 × fração_viva`).

### [4] Métricas Globais
- `pThreat = 1 − mean(Li dos líderes vivos)`
- `QI = 0,5 × conectividade + 0,3 × propagação + 0,2 × entrega`
- `SR` = nós cobertos / nós vivos
- `TII` (Topology Intuition Index) = Σ wᵢ · antScore(i) / |V_alive|

### [5] Processamento de Órfãos (por nó)
Para cada nó órfão (cujo líder morreu):

1. Calcula `bestSimExisting` (similaridade com melhor cluster ativo) e `simOrphans` (com outros órfãos)
2. Classifica o estado:
   - `EXISTING_VIABLE`: `bestSimExisting ≥ 0,85`
   - `ORPHAN_ONLY`: `simOrphans ≥ 0,85`
   - `NO_MATCH`: nenhum dos anteriores
3. Dual-System escolhe a ação:
   - `pThreat > THRESHOLD_S1` (0,20 default; 0,245 em N≥300) → **S1**: consulta R_int; fallback para heurística se sem experiência
   - `QI < 0,60` ou `SR < 0,60` → **S2** prevalece (ε-greedy sobre Q-table)
   - Caso contrário → **S2 puro** com ajuste por `networkLearningMean`
4. Executa a ação com fallback se inviável:
   - `REALLOCATE` → orphan adicionado ao melhor cluster via `addClusterMember` + `setMyLeader`
   - `RECLUSTER` → orphan marcado como candidato a novo cluster
   - `DO_NOTHING` → nó permanece órfão para reavaliação no próximo ciclo
5. Calcula recompensa e atualiza Q-table e R_int

### [6] Fase Coletiva de Recluster
Candidatos são agrupados por similaridade mútua (≥ 0,85). Para cada grupo, o nó com maior `Li` é promovido a novo líder via `becomeLeader()` e inserido no `clusterInfoMap`.

### [7] Persistência e Decaimento
- QI/SR recalculados com estado pós-ação
- `ε ← max(ε_min, ε × 0,97)` — somente se houve órfãos no ciclo
- `knowledge.dat` salvo ao fim da rodada; carregado no início da próxima (transferência de conhecimento entre rodadas)

---

## Parâmetros

### Aprendizado Distribuído

| Parâmetro | Valor | Descrição |
|---|---|---|
| `ALPHA_LEARN` (α) | 0.20 | Taxa de atualização do Li |
| `BETA_INFLUENCE` (β) | 0.10 | Peso da influência de vizinhos em ΔLi |
| `ξ_anomalous` | −0.05 (N≤250) / **−0.033 (N≥300)** | Penalidade do estímulo ambiental aplicada a nós anômalos (ver *Calibragem por Densidade* abaixo) |

### Q-Learning (Sistema 2)

| Parâmetro | Valor | Descrição |
|---|---|---|
| `ALPHA_Q` | 0.20 | Taxa de aprendizado da Q-table |
| `GAMMA_Q` | 0.90 | Fator de desconto |
| `ε` inicial | 0.15 | Exploração na rodada 1 |
| `ε_decay` | 0.97 | Fator de decaimento por ciclo com órfãos |
| `ε_min` | 0.02 | Piso de exploração garantido |

### Dual-System (Limiares)

| Parâmetro | Valor | Descrição |
|---|---|---|
| `THRESHOLD_S1` | 0.20 (N≤250) / **0.245 (N≥300)** | pThreat acima → ativa Sistema 1 (ver *Calibragem por Densidade* abaixo) |
| `THRESHOLD_S2` | 0.60 | QI ou SR abaixo → S2 prevalece |
| `REALLOCATION_THRESHOLD` | 0.85 | Similaridade mínima para REALLOCATE |
| `CLUSTERING_THRESHOLD` | 0.85 | Similaridade mínima para RECLUSTER |

### Recompensas

| Situação | Valor |
|---|---|
| Ação viável | `10.0 × similaridade` |
| Ação inviável | `−10.0` |
| DO_NOTHING com órfão | `−5.0` |

### Simulação

| Parâmetro | Valor |
|---|---|
| `decisionInterval` | 5.0 s |
| Primeiro ciclo | t = 160 s |
| Falhas (with_failures) | t = {300, 450, 600} s — 30% dos líderes cada |
| `MAX_INTUITIVE_HISTORY` | 100 (janela deslizante do R_int) |

---

## Cenários Experimentais

| Grupo | Falhas | Rodadas |
|---|---|---|
| `200_no_failures` | Nenhuma | 35 |
| `200_with_failures` | 3 falhas × 30% | 35 |
| `250_no_failures` | Nenhuma | 35 |
| `250_with_failures` | 3 falhas × 30% | 35 |
| `300_no_failures` | Nenhuma | 35 |
| `300_with_failures` | 3 falhas × 30% | 35 |

---

## Resultados Resumidos (35 rodadas)

### Quality Index (QI) e Survival Rate (SR) finais

| Cenário | QI médio | SR médio |
|---|---|---|
| 200 nós — sem falhas | 99,3% ± 0,3% | 99,3% ± 0,9% |
| 200 nós — com falhas | 99,1% ± 0,2% | 99,4% ± 0,6% |
| 250 nós — sem falhas | 99,2% ± 0,2% | 98,8% ± 0,8% |
| 250 nós — com falhas | 99,1% ± 0,2% | 99,2% ± 0,8% |
| 300 nós — sem falhas | 99,2% ± 0,3% | 98,5% ± 1,0% |
| 300 nós — com falhas | 98,9% ± 0,6% | 98,6% ± 1,8% |

### Recuperação pós-falha

Cada falha elimina em média 3–4 líderes e gera ~70 nós órfãos. O QI cai ~19 pp e retorna a ~99% em **3 ciclos (15 segundos)**.

### Distribuição S1/S2

| Escala | S1 | S2 |
|---|---|---|
| 200 nós | 51% | 49% |
| 250 nós | 65% | 35% |
| 300 nós | 78% | 22% |

A proporção de S2 decresce com o tamanho da rede (49% → 35% → 22%), seguindo a tendência natural de `pThreat` médio crescer com `N`. Para o cenário de 300 nós isso requer recalibragem dos gatilhos — ver seção abaixo.

---

## Calibragem por Densidade (N ≥ 300)

Durante a validação experimental, observamos que com `N = 300` o balanço S1/S2 colapsa: ~99% das decisões caem em S1 e o Sistema 2 quase nunca ativa (~1%). A causa é estrutural:

- **`pThreat = 1 − mean(L_i dos líderes vivos)`** se desloca para cima conforme `N` cresce. Em 35 rodadas observadas:
  - `N=200`: mediana(`pThreat`) ≈ 0,185
  - `N=250`: mediana(`pThreat`) ≈ 0,198
  - `N=300`: mediana(`pThreat`) ≈ **0,245**
- Com `THRESHOLD_S1 = 0,20` fixo, em 300 nós `pThreat > 0,20` em >90% dos ciclos, e o gatilho de S1 fica permanentemente acionado.

A causa do deslocamento é o número absoluto de líderes anômalos: a fração relativa é similar (~30% de falhas em qualquer `N`), mas o drag agregado sobre `mean(L_i)` cresce com `N` porque o estímulo `ξ_i = −0,05` se soma sobre mais líderes anômalos por ciclo.

**Solução adotada (válida apenas para N ≥ 300):**

| Ajuste | Default | Override N≥300 | Justificativa |
|---|---|---|---|
| `ξ_anomalous` | −0,050 | **−0,033** | Reduz o drag por anomalia em fator `200/N` (≈ 2/3 para N=300), aproximando a distribuição de `pThreat` da faixa de 200/250 |
| `THRESHOLD_S1` | 0,20 | **0,245** | Recalibra o gatilho S1/S2 para a mediana empírica observada em `N=300`, restaurando o balanço S1/S2 |

O override é aplicado em [NodeAPApplication.cc:79-87](NodeAPApplication.cc#L79-L87), no `StartApplication()`, condicional a `totalNetworkNodes >= 300`. Os parâmetros default (citados no artigo) permanecem intactos para `N ≤ 250`.

**Efeito empírico (média sobre 35 rodadas em N=300):**

| Métrica | Antes | Depois |
|---|---:|---:|
| S2 ratio | 1,2% | **22,0%** |
| Rodadas com S2 > 0 | 1 / 35 | **14 / 35** |
| `pT_mean` | 0,243 | 0,245 |
| `qi_final` | 0,989 | 0,990 |
| `sr_final` | 0,986 | 0,988 |
| Recuperação de órfãos | 99,9% | 99,7% |

Tanto a recuperação quanto a qualidade da rede ficam praticamente inalteradas — o ajuste corrige o balanço interno do mecanismo de decisão sem afetar a métrica principal de resiliência.

---

## Como Compilar e Executar

```bash
# Na raiz do ns-3-dev
./ns3 build

# Sem falhas, 200 nós, rodada 1
./ns3 run "contaski --nNodes=200 --run=1"

# Com 3 falhas sequenciais (t=300, 450, 600s, 30% cada), 200 nós, rodada 1
./ns3 run "contaski --nNodes=200 --run=1 \
  --multipleFailures=true \
  --multiFailurePercent=30 \
  --failureTimes=300,450,600"

# Com persistência de aprendizado entre rodadas
./ns3 run "contaski --nNodes=200 --run=1 \
  --multipleFailures=true \
  --knowledgePath=results-synapt/200_with_failures/knowledge.dat"

# Falha única (modo legado): 30% dos líderes em t=310s
./ns3 run "contaski --nNodes=200 --run=1 \
  --failurePercentage=30 \
  --failureTimeMin=310 --failureTimeMax=310"
```

O arquivo `knowledge.dat` é salvo ao fim de cada rodada e carregado automaticamente no início da próxima via `--knowledgePath`, preservando Q-table, ε, R_int e contadores S1/S2.

---

## Saída

| Arquivo/Pasta | Conteúdo |
|---|---|
| `results-synapt/<N>_<cenário>/IntuitiveStats/` | Métricas por ciclo (QI, SR, pThreat, TII, S1/S2) |
| `results-synapt/<N>_<cenário>/IntuitiveQValues/` | Q-values ao fim de cada rodada |
| `results-synapt/<N>_<cenário>/APStats/` | Tarefas despachadas e aceites |
| `results-synapt/<N>_<cenário>/knowledge/` | Snapshots do knowledge.dat por rodada |
| `knowledge.dat` | Estado persistido entre rodadas (Q-table, ε, R_int) |

---

## Relação com o Projeto SECTIONAL

O SYNAPT é a versão final do projeto, evoluindo o SECTIONAL em três dimensões:

| Dimensão | SECTIONAL (rl3.1) | SYNAPT |
|---|---|---|
| Quando age | Apenas após falha detectada | Continuamente a cada 5 s |
| Decisão | Q-Learning reativo | Dual-System S1/S2 |
| Estado | Similaridade (2 dimensões) | OrphanState por nó + pThreat global |
| Detecção de anomalias | Não há | Z-score global + temporal (ensemble) |
| Aprendizado entre rodadas | Q-table (qtable.csv) | Q-table + R_int + ε (knowledge.dat) |
| Granularidade de ação | Por grupo de órfãos | Por nó órfão individual |
