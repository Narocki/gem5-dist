#! /bin/bash

# Wrapper para lanzar dist-gem5 x86 usando el board stdlib X86DistBoard.
# Reutiliza util/dist/gem5-dist-x86.sh y fija por defecto el config stdlib.
#
# Uso:
#   util/dist/gem5-dist-x86-stdlib.sh -x /ruta/a/gem5.opt --fs-args ...
#
# Cualquier argumento -f/-s pasado por el usuario sobrescribe estos defaults
# porque se añade al final (último valor gana en el parser del script base).

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

exec "${BASE_SCRIPT}" \
    -f "${DIST_STDLIB_CONFIG}" \
    -s "${DIST_STDLIB_CONFIG}" \
    "$@"
