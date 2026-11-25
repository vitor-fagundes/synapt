#!/usr/bin/env bash
# Executa um comando garantindo que results-synapt/ fique intacto.
#
# Uso: tools/with_results_guard.sh <comando> [argumentos...]
#
# Antes e depois do comando: git status --porcelain -- results-synapt tem de
# estar vazio e o manifesto de metadados (caminho, tamanho, mtime) tem de ser
# o mesmo. Durante o comando, results-synapt/ fica sem permissão de escrita
# (chmod -R a-w); a permissão de escrita do dono é restaurada no fim, mesmo
# em caso de erro. Não roda como root, porque o chmod não protegeria.
set -u

if [ "$#" -eq 0 ]; then
    echo "uso: $0 <comando> [argumentos...]" >&2
    exit 2
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "GUARD: rodando como root; chmod não protege results-synapt/. Abortando." >&2
    exit 2
fi

ROOT=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel) || exit 2
cd "$ROOT" || exit 2
if [ ! -d results-synapt ]; then
    echo "GUARD: $ROOT/results-synapt não encontrado." >&2
    exit 2
fi

manifest() {
    find results-synapt -printf '%P\t%s\t%T@\n' | LC_ALL=C sort | sha256sum | cut -d' ' -f1
}
git_clean() {
    [ -z "$(git status --porcelain -- results-synapt)" ]
}

chmod -R a-w results-synapt || exit 2
trap 'chmod -R u+w "$ROOT/results-synapt"' EXIT

if ! git_clean; then
    echo "GUARD: git status --porcelain -- results-synapt não está vazio ANTES do comando." >&2
    git status --porcelain -- results-synapt | head >&2
    exit 2
fi
BEFORE=$(manifest)
echo "GUARD: results-synapt protegido (a-w); manifesto antes = $BEFORE" >&2

"$@"
RC=$?

FAIL=0
if ! git_clean; then
    echo "GUARD: FALHA — git status --porcelain -- results-synapt não está vazio DEPOIS do comando." >&2
    git status --porcelain -- results-synapt | head >&2
    FAIL=1
fi
AFTER=$(manifest)
if [ "$BEFORE" != "$AFTER" ]; then
    echo "GUARD: FALHA — manifesto de metadados mudou ($BEFORE -> $AFTER)." >&2
    FAIL=1
else
    echo "GUARD: manifesto depois = $AFTER (igual)" >&2
fi
if [ "$FAIL" -ne 0 ]; then
    exit 9
fi
exit "$RC"
