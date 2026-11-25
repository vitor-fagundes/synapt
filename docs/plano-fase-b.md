# Plano — Fase B: topologia a partir do `simulation.log` (revisão 1, congelada)

Repositório `synapt`, branch `feat/simulador-web`. Continua `docs/plano-simulador-web.md`: valem as restrições,
as decisões fechadas 1–7 e a precedência **logs > código atual > README**.

## Escopo e restrições

- Sem NS-3 e sem alterar `.cc/.h`. A flag `--traceOut` continua adiada.
- `results-synapt/` é só leitura. Toda execução do gerador e dos testes roda dentro de `tools/with_results_guard.sh`.
- Escrita apenas em `site/` (via `.site-build-tmp/` + troca de diretório) e em `tools/`, `docs/`. Sem push e sem PR:
  A e B são publicadas juntas.
- A página não desenha nada que não esteja no log ou que não seja determinado pelo código (as posições). Onde o log
  não registra, o canvas não infere.

## Fatos já conferidos nos 245 logs (2026-11-25, protótipo descartável)

- `N: <ip> CLSize k elects <ip>`: exatamente N linhas por rodada.
- O conjunto `N: LS` é igual ao conjunto `LR:` em todas as rodadas.
- O AP é `fe80::ff:fe00:<hex(N+1)>`, e o nó de índice i é `fe80::ff:fe00:<hex(i+1)>`.
- Posições da grade (`contaski.cc:133–143`, `RowFirst`): `sq = ceil(sqrt(40000/N))`, `per = ceil(200/sq)`,
  `x = (i mod per)·sq` e `y = floor(i/per)·sq`; o AP fica em (100, 100). Elas batem com a distância ao AP de
  `N: IP <ip> D <d>` para todos os nós das 245 rodadas.
- 203 rodadas têm, ao todo, 731 nós que elegem um nó que **não** se tornou líder. O `myLeader` desses nós aponta
  para um nó fora do `clusterInfoMap`: não são membros nem órfãos para o AP.
- Contando, por ciclo, as linhas `INTUITIVE_ORPHAN` (ciclo = última linha `INTUITIVE: t=` anterior), o total, as
  ações e S1/S2 (com S1+S2 contado como S2) **batem com o CSV nas 245 rodadas** (0 divergentes).
- Os 145 tempos de `INTUITIVE: t=` coincidem com os do CSV.
- O número de `N: BecomeLeader` é igual ao de `INTUITIVE_RECLUSTER: Novo cluster` em todas as rodadas.

## 1. Parser → trace por rodada

Módulo novo `tools/trace_from_log.py`, só stdlib e com funções puras:
- `parse_run(log_path, n) -> dict | SemTrace(motivo)`;
- `verify(trace, stats_cols) -> list[str]`, que devolve as divergências.

O gerador `tools/build_site_data.py` passa a gerar também os traces, no mesmo `.site-build-tmp/data/` e na mesma
troca de diretório, com um único comando. Isso muda o contrato da Fase A em três pontos, sujeitos ao OK:
- os `trace_*.json` passam a ser **regerados**, não mais preservados; deixa de existir a cópia de traces antigos;
- os nomes conhecidos em `site/data` passam a ser `index.json`, os 7 `<grupo>.json` e
  `trace_<grupo>_r<NNN>.json` (NNN = 001…035). Qualquer outro arquivo dá código 2, como antes;
- limites de tamanho:
  - `--max-bytes` (5 MB) vale só para `index.json` + grupos;
  - `--max-trace-bytes` (padrão 4 000 000) vale para a soma dos traces;
  - `--max-trace-file` (padrão 32 768) vale por arquivo;
  - qualquer excesso dá código 4.

**Arquivo** `site/data/trace_<grupo>_r<NNN>.json` (JSON compacto), um por rodada, carregado sob demanda:

```
{ "model":"synapt", "fonte":"log", "v":1, "g":"200_with_failures", "r":2, "n":200,
  "t0":170, "dt":5, "nc":145,
  "ap":[100,100], "pos":[[x,y], …n],            // derivadas da grade e conferidas com o log
  "thS1":0.2,
  "lid0":[j, …n],                                // índice do nó eleito por cada nó (pode não ser líder)
  "lideres0":[i, …],                             // nós com LS/LR
  "ondas":[{"w":1,"t":310,"k":28,"lf":[i,…]}],    // tempo real, ciclo de efeito e líderes que caíram
  "ev":[ {"k":28,                                // só ciclos com eventos
          "o":[[id, estado, acao, sis, destino]], // ordem do log
          "p":[[novoLider, nSeguidores]],
          "pend":[id, …]} ] }
```

- `estado`: 0 EXISTING_VIABLE, 1 ORPHAN_ONLY, 2 NO_MATCH.
- `acao`: 0 DO_NOTHING, 1 REALLOCATE, 2 RECLUSTER.
- `sis`: 0 S1, 1 S1+S2, 2 S2.
- `destino`: índice do líder de destino (REALLOCATE) ou −1.
- `pend`: órfãos de `INTUITIVE_RECLUSTER_FALLBACK` (marcados para RECLUSTER mas não agrupados).
- `k` do ciclo: índice do `INTUITIVE: t=` (t = 170 + 5k).
  - As linhas de órfão, recluster, fallback e BecomeLeader pertencem ao ciclo da última linha `INTUITIVE: t=`
    anterior.
  - As linhas `FAILURE_WAVE` pertencem ao **próximo** `INTUITIVE: t=` (a onda das 310 s é processada pelo ciclo de 310 s).

Regras léxicas: IP fora de `fe80::ff:fe00:<hex>` ou índice fora de [0, N] dá `SemTrace` com o motivo; o mesmo
vale para uma linha de órfão que não casa a regex. Nunca se descarta linha em silêncio.

**Tamanho:** estimativa de ~10 KB por arquivo (no máximo 401 entradas de órfão numa rodada) e ~2,5 MB no total.
Metas: ≤ 32 KB por arquivo e ≤ 4 MB no total, com os limites acima.

## 2. Verificação obrigatória (as 245 rodadas)

Uma rodada só ganha trace se passar em tudo abaixo; senão fica em `index.json.sem_trace` com o motivo. Nada é
corrigido à mão. O gerador imprime a contagem por grupo e por motivo.

1. **Eleição:** N linhas `elects`, um por nó; `lideres0` = conjunto LS = conjunto LR.
2. **Posições:** para os N nós, |d_log − d_grade| ≤ 10⁻⁵·d + 10⁻⁴.
3. **Ciclos:** 145 linhas `INTUITIVE: t=` com t = 170 + 5k, em ordem.
4. **Por ciclo, contra o `IntuitiveStats.csv`:**
   - `len(o)` = `totalOrphans`;
   - contagens por ação = `actionsReallocate` / `actionsRecluster` / `actionsDoNothing`;
   - nº de `sis = 0` = `cycleS1`;
   - nº de `sis ∈ {1, 2}` = `cycleS2`.
5. **Ondas:**
   - para cada onda, o número de `FAILURE_WAVE: Líder … falhou` = líderes falhos de `Onda w completa`;
   - cada líder que cai é líder vivo naquele momento (pelo estado reconstruído).
6. **Recluster:**
   - `BecomeLeader` = `Novo cluster`;
   - todo novo líder é um órfão RECLUSTER do mesmo ciclo;
   - Σ seguidores ≤ RECLUSTER − `pend` − novos líderes.
7. **Consistência dos órfãos**, por ciclo, com o estado reconstruído a partir dos eventos anteriores:
   - todo nó vivo cujo líder conhecido está morto aparece como órfão no ciclo, ou está em `pend`;
   - todo órfão registrado ou tem líder conhecido morto, ou tem pertencimento desconhecido (reclusterizado
     sem cluster registrado), ou estava pendente.

A contagem de traces e de `sem_trace` sai na saída do gerador e no relatório da entrega.

`index.json` ganha:
- `traces: [{g, r, f, bytes}]`;
- `sem_trace: [{g, r, motivo}]`.

A página só faz `fetch` de traces listados.

## 3. Lacuna: membros de clusters novos

- O log registra o novo líder (`Novo cluster - Líder X com k seguidores`), mas não quais órfãos o seguem.
- O órfão RECLUSTER que não virou líder nem ficou pendente é desenhado com a cor "reclusterizado", **sem linha**
  até um líder. O novo líder recebe destaque próprio.
- Quando um desses nós volta a ser órfão (porque seu novo líder caiu), o log o registra de novo e ele volta a ser
  rastreado.
- Legenda: **"pertencimento a clusters novos não registrado no log"**.
- Nenhuma ligação é inferida, nem no caso em que só um cluster novo se forma no ciclo.

## 4. Canvas 2D

- Um `<canvas>` na área "Topologia" do painel Dual-System (que hoje mostra "requer trace").
  - É quadrado, ocupa a largura do cartão e respeita `devicePixelRatio`.
  - Escala: campo de 0 a 210 m com margem; funciona a 390 px.
- **Estado no ciclo k**, reconstruído aplicando os eventos dos ciclos 0..k em ordem; não há lógica do modelo:
  - aplica `ondas` (morto) → `o` (decisões) → `p` (novos líderes) → `pend`;
  - REALLOCATE: o líder conhecido passa a ser `destino`;
  - RECLUSTER, sem ser novo líder nem pendente: pertencimento desconhecido;
  - DO_NOTHING e `pend`: continua órfão.
- **Papéis e marcas** (legenda em HTML com forma, cor e texto, nunca só cor):
  - AP: quadrado;
  - líder inicial: ponto grande de tinta;
  - membro: ponto pequeno cinza, com linha fina até o líder;
  - fora de cluster: anel cinza sem linha. Elegeu um nó que não virou líder (731 casos);
  - morto: × vermelho (status crítico);
  - órfão: anel âmbar (status de atenção) enquanto pendente;
  - realocado: ponto verde-água, com linha até o novo líder;
  - reclusterizado: ponto violeta, sem linha;
  - novo líder: ponto violeta grande com anel.
- **Ciclo da onda:**
  - os líderes que caíram recebem anel vermelho e o rótulo "onda w";
  - os órfãos daquele ciclo recebem anel âmbar sobreposto à cor da decisão tomada no mesmo ciclo, porque o órfão
    surge e é resolvido no mesmo ciclo.
  - A legenda explica: "anel âmbar = órfão neste ciclo".
- **Sincronia:** redesenha em todo `render(k)` (anterior/próximo/barra/reprodução). Trocar de rodada carrega o
  trace sob demanda.
  - No modo média e no arquivo local, aparece "topologia disponível por rodada".
  - Em rodada `sem_trace`, aparece "Rodada sem trace: <motivo>".
  - Se o `fetch` do trace falhar, aparece a faixa de erro e o canvas fica vazio.
- **Interação:**
  - tooltip (`pointermove`/toque) com nó, papel e última decisão (estado, ação, sistema, ciclo), com alvo de
    toque de 24 px;
  - abaixo do canvas, um resumo textual do ciclo para acessibilidade: líderes vivos, mortos, órfãos, realocados,
    reclusterizados e novos líderes.
- **Cores:** as cores categóricas novas (verde-água, violeta) passam pelo `validate_palette.js` nos dois modos,
  junto com S1/S2. Âmbar e vermelho vêm da paleta de status, sempre com forma e texto.
- **Movimento:** sem animação contínua; com `prefers-reduced-motion`, nenhuma transição.

## 5. Testes (todos dentro do guard)

- **`tools/test_trace_from_log.py`** (unittest, stdlib):
  - log sintético mínimo (N = 4) cobrindo eleição, eleitor de não líder, onda, órfãos das três ações, recluster
    com fallback e novo líder, conferindo o JSON esperado;
  - casos que viram `SemTrace` com motivo: contagem ≠ CSV, ciclo faltando, IP inválido, linha de órfão que não
    casa, BecomeLeader ≠ Novo cluster.
- **Ida e volta** (nos dados publicados):
  - para cada trace, as contagens por ciclo reconstruídas do JSON batem com o CSV bruto;
  - todo `(g, r)` está em `traces` ou em `sem_trace`, e nunca nos dois.
- **Gerador:** os testes da Fase A são ajustados ao novo contrato (nomes de trace conhecidos; limites de trace com
  código 4; trace preexistente desconhecido dá código 2).
- **e2e:**
  - o canvas renderiza, com pixels fora do fundo acima de um limiar;
  - ao avançar para o ciclo 29 (t = 310 s) de `200_with_failures`/rodada 2, o resumo mostra os líderes da onda 1
    como mortos e "onda 1";
  - uma rodada em `sem_trace` mostra o aviso. Se não houver nenhuma nos dados reais, o teste altera a resposta de
    `index.json` via `page.route` para simular;
  - o modo média mostra "topologia disponível por rodada";
  - 390 px sem rolagem horizontal; zero erros de console;
  - capturas durante a onda de falha: desktop claro, desktop escuro e 390 px.

## Commits planejados

1. `docs: plano da Fase B` (este arquivo).
2. `feat(tools): parser de trace a partir do simulation.log` + `tools/test_trace_from_log.py`.
3. `feat(tools): gerador publica traces e index.traces/sem_trace` + ajustes dos testes da Fase A.
4. `data(site): traces das 245 rodadas` (com a contagem traces / sem_trace na mensagem).
5. `feat(site): canvas de topologia sincronizado com o replay` + e2e.
6. `docs(site): README cobre a Fase B`.

## Pontos que dependem do OK do usuário

- (a) Unificar a geração de traces no `build_site_data.py`: regerar em vez de preservar traces, com os limites
  novos de tamanho.
- (b) O papel "fora de cluster" para os 731 nós que elegeram um não líder.
- (c) Representar o ciclo da onda com o anel de órfão sobreposto à decisão do mesmo ciclo.
- (d) A verificação 7 (consistência dos órfãos com o estado reconstruído) também leva a `sem_trace`, além das
  verificações que você exigiu.

## Pendências da revisão (preenchida após advisor + goldfish)

(vazia)
