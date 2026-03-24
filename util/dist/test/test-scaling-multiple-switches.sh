#!/bin/bash

# NODES_LIST=(2 4 6 8 10 12 14 16)
NODES_LIST=(18 20 22 24)

ENABLE_HIERARCHICAL_SWITCH=1
NLEAF=2
NUM_CORES=1

export ENABLE_HIERARCHICAL_SWITCH
export NLEAF
export NUM_CORES

# Prefer existing M5_PATH, fallback to workspace location
: ${M5_PATH:=/workspaces/gem5}
export M5_PATH

RUN_ROOT=${M5_PATH}/util/dist/test/runs-gem5-normal
mkdir -p "${RUN_ROOT}"

for NNODES in "${NODES_LIST[@]}"; do
    if [ $((NNODES % NLEAF)) -ne 0 ]; then
        echo "[scaling][ERROR] NNODES=${NNODES} must be divisible by NLEAF=${NLEAF}"
        exit 1
    fi

    NODES_PER_SWITCH_VALUE=$((NNODES / NLEAF))
    NODES_PER_SWITCH=""
    for ((i=0; i<NLEAF; i++)); do
        if [ -n "$NODES_PER_SWITCH" ]; then
            NODES_PER_SWITCH+=","
        fi
        NODES_PER_SWITCH+="${NODES_PER_SWITCH_VALUE}"
    done
    export NODES_PER_SWITCH

    RUN_TAG="multiple-switches-${NNODES}-nodes-$(date +%Y%m%d-%H%M%S)"
    export RUN_TAG
    export NNODES

    WRAPPER_RESOURCE_LOG=${RUN_ROOT}/${RUN_TAG}-wrapper_resources.txt

    echo "[scaling] Starting run: NNODES=${NNODES} RUN_TAG=${RUN_TAG}"

    /usr/bin/time -v -o "${WRAPPER_RESOURCE_LOG}" ./test-multiple-switches-x86-stdlib.sh

    RC=$?
    if [ $RC -ne 0 ]; then
        echo "[scaling][ERROR] run for NNODES=${NNODES} failed (rc=$RC)"
        exit $RC
    fi

    echo "[scaling] Finished run: NNODES=${NNODES}"
done
