#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Example script to launch a 2-node dist-gem5 x86 simulation using
# the stdlib board-based distributed config:
#   configs/example/gem5_library/x86-dist-stdlib.py
#
# It mirrors util/dist/test/test-2nodes-x86.sh usage style.

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname $0)/../../..

# Default M5_PATH for this workspace if it is not exported by the user.
M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist-x86-stdlib.sh

# Fresh output root per execution (so m5out.* are always new and easy to find).
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/taskset/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-test] Output root: ${RUN_ROOT}"

# Kernel args for stable symbols.
KERNEL_CMD="random.trust_cpu=on nokaslr"

# Dist link/synchronization tuning.
ETH_LINK_DELAY="500us"
ETH_LINK_SPEED="1Gbps"
SYN_REPEAT="500us"
SYN_START="1t"

# CPU model for stdlib script: atomic|timing|o3|kvm
CPU_TYPE=${CPU_TYPE:-"timing"}
NUM_CORES=${NUM_CORES:-1}

# Sweep taskset sizes in powers of 2 up to a maximum.
TASKSET_MAX_CORES=${TASKSET_MAX_CORES:-$(nproc 2>/dev/null || echo 1)}
if ((TASKSET_MAX_CORES < 1)); then
  TASKSET_MAX_CORES=2
fi

TASKSET_CORES_VALUES=()
power=4
while ((power <= $(nproc 2>/dev/null || echo 1))); do
  TASKSET_CORES_VALUES+=("${power}")
  power=$((power * 2))
done

# Optional debug flags for gem5, e.g.:
#   DEBUG_FLAGS="DistEthernet,DistSync"
#DEBUG_FLAGS="Interrupt,I8259,LocalApic"
DEBUG_FLAGS=${DEBUG_FLAGS:-}


# Optional switch on first workbegin (e.g., just before MPI).
# Set ENABLE_SWITCH=1 to activate switchable processor mode.
ENABLE_SWITCH=1
TASKSET_ENABLED=1


ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

NNODES=${NNODES:-2}
NNODES=16

# If no cluster allocation is present, everything runs on localhost.
if [ -z "${LSB_MCPU_HOSTS:-}" ]; then
  LSB_MCPU_HOSTS="127.0.0.1 ${NNODES}"
fi

export LSB_MCPU_HOSTS
export SSH_PORTS=${SSH_PORTS:-}

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi

M5_ARGS=(
  --listener-mode=off
)

if [[ -n "${DEBUG_FLAGS}" ]]; then
  M5_ARGS+=(--debug-flags=${DEBUG_FLAGS})
fi

for TASKSET_CORES in "${TASKSET_CORES_VALUES[@]}"; do
  TASKSET_RANGE="0-$((TASKSET_CORES - 1))"
  TASKSET_RUN_ROOT="${RUN_ROOT}/taskset_${TASKSET_CORES}"
  mkdir -p "${TASKSET_RUN_ROOT}"

  echo "[dist-test] START taskset_cores=${TASKSET_CORES} taskset_range=${TASKSET_RANGE} run_root=${TASKSET_RUN_ROOT}"

  taskset -c "${TASKSET_RANGE}" \
    "${GEM5_DIST_SH}" -n ${NNODES}                                \
      -r ${TASKSET_RUN_ROOT}                                      \
      -c ${TASKSET_RUN_ROOT}                                      \
        -x ${GEM5_EXE}                                            \
        -p 2210                                                   \
        --kernel-cmd "${KERNEL_CMD}"                             \
        --sw-args                                                 \
          --dist-sync-start=${SYN_START}                         \
          --dist-sync-repeat=${SYN_REPEAT}                       \
          --ethernet-linkdelay=${ETH_LINK_DELAY}                 \
          --ethernet-linkspeed=${ETH_LINK_SPEED}                 \
        --m5-args                                                 \
          "${M5_ARGS[@]}"                                        \
        --fs-args                                                 \
          --dist-sync-start=${SYN_START}                         \
          --dist-sync-repeat=${SYN_REPEAT}                       \
          --ethernet-linkdelay=${ETH_LINK_DELAY}                 \
          --ethernet-linkspeed=${ETH_LINK_SPEED}                 \
          --cpu-type=${CPU_TYPE}                                 \
          --num-cores=${NUM_CORES}                               \
          "${SWITCH_FS_ARGS[@]}"                                \
          --root=/dev/sda2                                       \
          --disk=${IMG}                                           \
          --kernel=${VMLINUX}                                     \
          --readfile=${BOOT_SCRIPT}                               \
          --continue-after-panic
done
