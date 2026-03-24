#! /bin/bash

# Wrapper para lanzar dist-gem5 x86 stdlib con soporte directo para
# topología jerárquica de switches.
#
# Reutiliza util/dist/gem5-dist-x86.sh y fija por defecto el config stdlib
# tanto para nodos como para switch. Además, acepta opciones jerárquicas en
# primer nivel y las reenvía automáticamente como argumentos del switch.
#
# Uso típico:
#   util/dist/gem5-dist_multiple_switches_x86-stdlib.sh \
#     -x build/X86/gem5.opt -n 4 \
#     --num-leaf-switches 2 --nodes-per-switch 2,2 \
#     --sw-args --trace-internal-links ... \
#     --fs-args ...

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_SCRIPT="${SCRIPT_DIR}/gem5-dist-x86.sh"

[ -x "${BASE_SCRIPT}" ] || {
    echo "Base script no encontrado o no ejecutable: ${BASE_SCRIPT}" >&2
    exit 1
}

if [ -z "${M5_PATH:-}" ]; then
    echo "M5_PATH no está definido. Exporta M5_PATH antes de ejecutar." >&2
    exit 1
fi

DIST_STDLIB_CONFIG="${M5_PATH}/configs/example/gem5_library/x86-dist-stdlib.py"

PASS_ARGS=()
EXTRA_SW_ARGS=()

while (($# > 0))
do
    case "$1" in
        --num-leaf-switches|--nodes-per-switch|--internal-trace-prefix|--internal-trace-maxlen)
            (($# >= 2)) || {
                echo "Falta valor para $1" >&2
                exit 1
            }
            EXTRA_SW_ARGS+=("$1" "$2")
            shift 2
            ;;
        --trace-internal-links)
            EXTRA_SW_ARGS+=("$1")
            shift 1
            ;;
        *)
            PASS_ARGS+=("$1")
            shift 1
            ;;
    esac
done

CMD=(
    "${BASE_SCRIPT}"
    -f "${DIST_STDLIB_CONFIG}"
    -s "${DIST_STDLIB_CONFIG}"
    "${PASS_ARGS[@]}"
)

if ((${#EXTRA_SW_ARGS[@]} > 0)); then
    CMD+=(--sw-args "${EXTRA_SW_ARGS[@]}")
fi

exec "${CMD[@]}"
