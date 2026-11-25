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

## Pendências da revisão (advisor + goldfish, 2026-11-25; goldfish NO-GO)

Relatório completo da goldfish: `/home/ubuntu/synapt-simulador-web/goldfish-fase-b.txt` (11 achados). Lacunas de
especificação (grupos A/C), a resolver na implementação como indicado:

1. **Decisões (a)–(d) pendentes**, embora o plano diga "congelado": dependem do OK do usuário (seção abaixo).
2. **O guard muda os modos de `results-synapt/`.** Verificado em 2026-11-25: todos os 1 978 arquivos são 644, os
   289 diretórios são 755 e nenhum estava sem `u+w`. Logo, `a-w` seguido de `u+w` restaura exatamente os modos.
   Opcional: o guard passar a salvar e restaurar os modos.
3. **`validate_palette.js`** é o script da skill de visualização, usado no desenvolvimento; não é arquivo do
   repositório. O resultado vai na mensagem de commit.
4. **Assinaturas:**
   - `build_trace(log_path, stats_cols, n, g, r, thS1) -> (trace | None, motivo | None)`;
   - o gerador injeta `g`, `r` e `thS1` e serializa;
   - `verify()` é chamado dentro de `build_trace`.
5. **Tabela de erros:**
   - o que a Fase A já trata como código 2 continua código 2 (CSV, Q-values, log ausente ou ilegível);
   - todo problema específico do trace vira `SemTrace` com motivo: linha malformada ou duplicada de eleição, onda,
     órfão ou recluster; IP fora do domínio; divergência de contagem ou de consistência.
6. **Precedência de papéis no desenho:** morto > órfão no ciclo > novo líder > líder > realocado >
   reclusterizado > membro > fora de cluster. As linhas são redesenhadas do zero a cada ciclo, a partir do
   estado reconstruído.
7. **Anel âmbar = nó presente em `o[k]`.** Um órfão não resolvido reentra pelo `pendingOrphans`
   (`NodeAPApplication.cc:948–963`). Verificação nova: não resolvido em k ⇒ presente em `o[k+1]`.
8. **Validação do trace no cliente** antes de desenhar:
   - `n` ∈ {200, 250, 300};
   - `pos` e `lid0` com comprimento n;
   - índices em [0, n);
   - enums no domínio;
   - `k` em [0, 145).
   - Se falhar, aparece a faixa de erro "trace em formato inesperado: <campo>" e o canvas fica vazio.
9. **Cobertura esperada:** 245/245, porque o protótipo deu 0 divergências. Um `sem_trace` vindo das verificações
   5–7 ou das novas deve ser tratado como suspeita de defeito do parser: investigar a causa antes de publicar e
   reportar, sem nunca tratar uma rodada como caso especial.
10. **Invariantes do código usados**, todos em synapt@901ed81:
    - pertencimento por `getMyLeader() == líder` (`NodeAPApplication.cc:741–752`);
    - órfãos = líder morto ∪ `pendingOrphans` (`:924–963`);
    - recluster e eleição do novo líder (`:1128–1222`);
    - `becomeLeader` (`NodeApplication.cc`).
11. **Fixture dos testes do gerador:** o filtro atual descarta as linhas de que o parser precisa. O caminho feliz
    passa a manter `INTUITIVE*`, `FAILURE_*`, `N: LS`, `elects`, `N: IP`, `LR:` e `N: BecomeLeader`, e afirma o
    número de traces publicados e o conteúdo de `index.traces`.
12. **Menor:** as posições vêm do código (`contaski.cc`); o log só confirma a distância de cada nó ao AP.

## Achados levados ao usuário (corretude dos dados; aguardam OK)

**A. O pertencimento nó→líder não era verificado.** As linhas do canvas são a principal afirmação nova da Fase B,
e o §2 só conferia órfãos. Verificações obrigatórias propostas:
- **A1 — identidade do SR por ciclo:**
  `round(SR[k] · vivos(k)) = vivos(k) − |o[k]| − fora_de_cluster(k)`, com `vivos(k)` = N − líderes mortos até o
  ciclo k. Num protótipo com o papel derivado a cada ciclo (ver B), ela vale nas **245/245 rodadas, em todos os
  145 ciclos**. Cobre também os grupos sem falhas.
- **A2 — por onda:** o número `Líder X falhou … n nós órfãos` de cada líder que cai deve ser igual ao número de
  membros reconstruídos de X.
  - Para líderes iniciais, a contagem deve ser exata.
  - Para novos líderes, o excedente sobre os membros conhecidos (eleitores e realocados) tem de ser coberto pelos
    nós de pertencimento desconhecido.
  - Na onda 1, `FAILURE_WAVE_ELIGIBLE … n real members` também é conferido.

**B. "Fora de cluster" não é fixo.** Em **51 rodadas**, um nó eleito que não virou líder é depois promovido a novo
líder por recluster. Pelo código (`getMyLeader() == líder`), quem o elegeu passa a ser membro desse cluster.
- O pertencimento desses eleitores é conhecido exatamente pela eleição registrada; não é inferido.
- O canvas desenha a linha até o novo líder a partir do ciclo seguinte à promoção.
- Se esse líder cair, os eleitores aparecem como órfãos no log.
- Com o papel fixo, a identidade do SR falha justamente nessas 51 rodadas; com o papel derivado por ciclo,
  vale em todas.

**C. O que o Python verifica tem de ser o que o JS desenha.** O e2e compara as contagens de papéis por ciclo
mostradas pela página (resumo textual) com as da reconstrução em Python, nos 145 ciclos, em pelo menos uma rodada
de cada grupo com falhas.

**D. Upload de JSON.** A página da Fase A diz que "traces JSON chegam na Fase B", e o pedido original (A.4) previa
isso. Proposta: aceitar no botão o `trace_*.json` da Fase B, com a mesma validação de schema do item 8 (entrada
não confiável, limite de 20 MiB, só `textContent`). Alternativa: trocar o texto da página.

**E. Momento do canvas.** No ciclo k, o canvas mostra o estado **depois** das ações do ciclo (com o anel âmbar
nos órfãos de `o[k]`), enquanto QI, SR e pThreat do ciclo são de **antes** das ações. Isso entra no ponto (c) e na
legenda: "canvas: após as ações do ciclo; métricas: antes".

## Decisões do usuário sobre a revisão (2026-11-25) — valem sobre o corpo acima

1. **A1 e A2 são obrigatórias.** A1 compara `round(SR × vivos)` com um inteiro (o SR tem 6 casas no CSV), nunca
   por igualdade de float. Falha → `sem_trace`.
2. **"Fora de cluster"** é recalculado a cada ciclo; a ligação é desenhada quando o nó eleito vira líder.
3. **Conferência Python × página** nos 145 ciclos, em pelo menos 1 rodada por grupo com falhas.
4. **Sem upload de JSON.** O texto da página diz que o botão aceita só `IntuitiveStats.csv` e que os traces são
   gerados pelo `build_site_data.py`.
5. **Momento do canvas invertido:** no ciclo k, o canvas mostra o estado **antes** das ações (o mesmo instante de
   QI, SR e pThreat), com a decisão de cada órfão sobreposta (ação + S1/S2). O resultado aparece no ciclo k+1.
   - Legenda: "estado no início do ciclo; marcas = decisões deste ciclo".
   - A conferência do item 3 usa esse instante.
6. **(a)–(d) aprovados.** Em (a), as posições são calculadas na página a partir dos parâmetros da grade e de N;
   o trace **não** grava `pos`.
7. **Guard:** grava a permissão original de cada caminho (stat) e restaura a partir dela.
8. **Seção Método:** nós que elegem um nó que nunca vira líder ficam fora de cluster desde o início; não são
   órfãos e não passam pelo mecanismo de recuperação; isso explica SR < 100% mesmo sem falhas.

## Desvios na implementação (2026-11-25)

- **Anel de órfão:** a cor indica o sistema que decidiu (azul S1, laranja S2), não âmbar. Todo órfão no início
  do ciclo tem decisão no mesmo ciclo.
- **Reclusterizado e novo líder** são losangos (vazado e cheio), não violeta: o violeta falhou no validador
  all-pairs contra o azul do S1 nos dois modos. Só três matizes passam (azul, laranja, verde-água); o resto é
  codificado por forma.
- **Topologia** num cartão próprio de largura total abaixo do replay, não dentro do painel Dual-System.
- **Verificação 6** usa igualdade (seguidores = RECLUSTER − pendentes − novos líderes), que é exata pelo código,
  em vez de ≤.
- **A2** roda em todas as ondas e também confere que elegíveis + ignorados = líderes vivos reconstruídos. Para
  novos líderes, a regra é estrita: log = membros conhecidos + seguidores registrados em `Novo cluster`. Ela vale
  em 1 188 conferências nas 245 rodadas.
- **Tooltip** também responde a toque (`pointerdown`).

Pendências:
- A conferência Python × página compara contagens por papel, não o líder de cada nó.

## Seguidores do recluster: o que o log registra (2026-11-25)

- A2 dos novos líderes usa a linha `INTUITIVE_RECLUSTER: Novo cluster - Líder X com m seguidores`
  (`NodeAPApplication.cc:1220`). Ela registra **só a quantidade** m, não quais nós seguem X.
- Consequência: a legenda mantém "pertencimento a clusters novos não registrado no log", e os reclusterizados
  continuam sem ligação (pendência 2 mantida).
- Nota, sem implementação: nos 61 de 430 ciclos com recluster em que se forma um único cluster novo (2 338
  seguidores), a identidade seria dedutível (candidatos RECLUSTER − pendentes − líder). Nos outros 369 ciclos
  (24 536 seguidores), não. Por decisão do usuário, nenhuma ligação é inferida.
- Pendência 1 resolvida: a conferência Python × página compara, em cada ciclo, o vetor completo papel:líder de cada
  nó (5 rodadas, 173 000 pares nó×ciclo idênticos).
