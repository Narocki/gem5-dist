#!/bin/bash

# Host counts compatible with both 2-leaf and 4-leaf runs.
#NODES_LIST=(4 8 12 16 20 24 28 32 36 40 44 48 52 56 60 64)
NODES_LIST=(2 4 6 8 16 32 64)
NLEAF_LIST=(0)
export ETH_LINK_DELAY="200us"
export ETH_LINK_SPEED="1Gbps"
export SYN_REPEAT="200us"
export SYN_START="7s"
export ENABLE_SWITCH=1
export CPU_TYPE="timing" # Fallback if switchable CPU mode is disabled; otherwise, the switch's next CPU type is used for hosts after the switch transition.

export NUM_CORES=1

# Prefer existing M5_PATH, fallback to workspace location
: ${M5_PATH:=/workspaces/gem5}
export M5_PATH

RUN_ROOT=${M5_PATH}/util/dist/test/runs-gem5-normal
mkdir -p "${RUN_ROOT}"

for NNODES in "${NODES_LIST[@]}"; do
    # Inside for only if NLEAF_LIST has more than one value, but it's simpler to just nest it here.
    for NLEAF in "${NLEAF_LIST[@]}"; do

        export NLEAF

        if [ "${NLEAF}" -lt 0 ]; then
            echo "[scaling][ERROR] NLEAF must be >= 0 (got ${NLEAF})"
            exit 1
        fi

        if [ "${NLEAF}" -eq 0 ]; then
            export ENABLE_HIERARCHICAL_SWITCH=0
            export NODES_PER_SWITCH=""
        else
            export ENABLE_HIERARCHICAL_SWITCH=1

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
        fi

        RUN_TAG="multiple-switches-${NNODES}-nodes-${NLEAF}-leaf-$(date +%Y%m%d-%H%M%S)"
        export RUN_TAG
        export NNODES

        WRAPPER_RESOURCE_LOG=${RUN_ROOT}/${RUN_TAG}-wrapper_resources.txt

        echo "[scaling] Starting run: NNODES=${NNODES} NLEAF=${NLEAF} RUN_TAG=${RUN_TAG}"

        /usr/bin/time -v -o "${WRAPPER_RESOURCE_LOG}" ./test-multiple-switches-x86-stdlib.sh

        RC=$?
        if [ $RC -ne 0 ]; then
            echo "[scaling][ERROR] run for NNODES=${NNODES} NLEAF=${NLEAF} failed (rc=$RC)"
            exit $RC
        fi

        echo "[scaling] Finished run: NNODES=${NNODES} NLEAF=${NLEAF}"
    done
done
