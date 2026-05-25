#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Dist-gem5 x86 stdlib scaling test that runs 4, 8, and 16 node cases.
# Each case is split evenly across two machines using LSB_MCPU_HOSTS and
# SSH_PORTS so the launchers distribute the simulation over both hosts.

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname "$0")/../../..

M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist-x86-stdlib.sh

RUN_TAG=${RUN_TAG:-two-hosts-scaling-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-two-hosts] Output root: ${RUN_ROOT}"

# Dist link/synchronization tuning.
ETH_LINK_DELAY=${ETH_LINK_DELAY:-500us}
ETH_LINK_SPEED=${ETH_LINK_SPEED:-1Gbps}
SYN_REPEAT=${SYN_REPEAT:-500us}
SYN_START=${SYN_START:-7400040006400t}

# CPU model for stdlib script: atomic|timing|o3|kvm
CPU_TYPE=${CPU_TYPE:-timing}
NUM_CORES=${NUM_CORES:-1}
ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

# Optional debug flags for gem5, e.g. DistEthernet,DistSync
DEBUG_FLAGS=${DEBUG_FLAGS:-}

KERNEL_CMD=${KERNEL_CMD:-"random.trust_cpu=on nokaslr"}

# Node counts to sweep. Keep them even so each case can be split 50/50.
NNODES_LIST=${NNODES_LIST:-"2"}

# Two hosts and their SSH ports. Override these before running the script.
DIST_HOST_A=${DIST_HOST_A:-127.0.0.1}
DIST_HOST_B=${DIST_HOST_B:-127.0.0.1}
SSH_PORT_A=${SSH_PORT_A:-22}
SSH_PORT_B=${SSH_PORT_B:-22}

# SSH user used by the launcher. Keep this non-root unless you explicitly
# need a different account on the remote machines.
SSH_USER=${SSH_USER:-$(id -un)}

DIST_HOST_A="192.168.1.75"
DIST_HOST_B="192.168.1.118"
SSH_PORT_A="2222"
SSH_PORT_B="2224"

ENABLE_SWITCH=1

SYN_START="0t"

export LSB_MCPU_HOSTS
export SSH_PORTS

M5_ARGS=(
  --listener-mode=off
)

if [[ -n "${DEBUG_FLAGS}" ]]; then
  M5_ARGS+=(--debug-flags=${DEBUG_FLAGS})
fi

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi

for NNODES in ${NNODES_LIST}; do
  if (( NNODES % 2 != 0 )); then
    echo "[dist-two-hosts] NNODES must be even to split evenly: ${NNODES}" >&2
    exit 1
  fi

  echo "[dist-two-hosts] Running with NNODES=${NNODES}"

  HALF_NODES=$((NNODES / 2))
  export LSB_MCPU_HOSTS="${DIST_HOST_A} ${HALF_NODES} ${DIST_HOST_B} ${HALF_NODES}"

  # Construir la lista de puertos repitiendo el puerto por cada nodo
  PORTS_A=""
  PORTS_B=""
  for (( i=0; i<HALF_NODES; i++ )); do
      PORTS_A+="${SSH_PORT_A} "
      PORTS_B+="${SSH_PORT_B} "
  done

  export SSH_PORTS="${PORTS_A}${PORTS_B}"
  echo "[dist-two-hosts] LSB_MCPU_HOSTS=${LSB_MCPU_HOSTS}"
  echo "[dist-two-hosts] SSH_PORTS=${SSH_PORTS}"

  CASE_DIR=${RUN_ROOT}/nodes-${NNODES}
  CORE_LOG=${CASE_DIR}/run.log
  RESOURCE_LOG=${CASE_DIR}/host_resources.txt
  mkdir -p "${CASE_DIR}"

  echo "[dist-two-hosts] Running ${NNODES} nodes as ${HALF_NODES}+${HALF_NODES} across ${DIST_HOST_A}:${SSH_PORT_A} and ${DIST_HOST_B}:${SSH_PORT_B}"
  echo "[dist-two-hosts] LSB_MCPU_HOSTS=${LSB_MCPU_HOSTS}"
  echo "[dist-two-hosts] SSH_PORTS=${SSH_PORTS}"
  echo "[dist-two-hosts] Core log: ${CORE_LOG}"
  echo "[dist-two-hosts] Resource log: ${RESOURCE_LOG}"

  {
    /usr/bin/time -v -o "${RESOURCE_LOG}" \
      "${GEM5_DIST_SH}" -n "${NNODES}"                               \
        -r "${CASE_DIR}"                                             \
        -c "${CASE_DIR}"                                             \
        -x "${GEM5_EXE}"                                             \
        -p 2210                                                       \
        --kernel-cmd "${KERNEL_CMD}"                                \
        --sw-args                                                     \
          --dist-sync-start=${SYN_START}                             \
          --dist-sync-repeat=${SYN_REPEAT}                           \
          --ethernet-linkdelay=${ETH_LINK_DELAY}                     \
          --ethernet-linkspeed=${ETH_LINK_SPEED}                     \
        --m5-args                                                     \
          "${M5_ARGS[@]}"                                            \
        --fs-args                                                     \
          --dist-sync-start=${SYN_START}                             \
          --dist-sync-repeat=${SYN_REPEAT}                           \
          --ethernet-linkdelay=${ETH_LINK_DELAY}                     \
          --ethernet-linkspeed=${ETH_LINK_SPEED}                     \
          --cpu-type=${CPU_TYPE}                                     \
          --num-cores=${NUM_CORES}                                   \
          "${SWITCH_FS_ARGS[@]}"                                     \
          --root=/dev/sda2                                           \
          --disk=${IMG}                                              \
          --kernel=${VMLINUX}                                        \
          --readfile=${BOOT_SCRIPT}                                  \
          --continue-after-panic
  } 2>&1 | tee -a "${CORE_LOG}"
done

echo "[dist-two-hosts] Done. Results in ${RUN_ROOT}"