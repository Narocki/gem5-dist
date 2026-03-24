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

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist-x86-stdlib.sh

# Fresh output root per execution (so m5out.* are always new and easy to find).
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-test] Output root: ${RUN_ROOT}"

# Kernel args for stable symbols.
KERNEL_CMD="random.trust_cpu=on nokaslr"

# Dist link/synchronization tuning.
ETH_LINK_DELAY="500us"
ETH_LINK_SPEED="600Gbps"
SYN_REPEAT="500us"
SYN_START="100000000000t"

# CPU model for stdlib script: atomic|timing|o3|kvm
CPU_TYPE="timing"
NUM_CORES=2

# Optional switch on first workbegin (e.g., just before MPI).
# Set ENABLE_SWITCH=1 to activate switchable processor mode.
ENABLE_SWITCH=1
SWITCH_START_CPU_TYPE="kvm"
SWITCH_NEXT_CPU_TYPE="atomic"

NNODES=2

# If no cluster allocation is present, everything runs on localhost.
NNODES=4

LSB_MCPU_HOSTS="192.168.1.75 1 192.168.1.75 1 192.168.1.118 1 192.168.1.118 1"
SSH_PORTS="2222 2223 2224 2225"
export LSB_MCPU_HOSTS
export SSH_PORTS

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi

${GEM5_DIST_SH} -n ${NNODES}                                \
  -r ${RUN_ROOT}                                          \
  -c ${RUN_ROOT}                                          \
    -x ${GEM5_EXE}                                          \
    -p 2210                                                 \
    --kernel-cmd "${KERNEL_CMD}"                           \
    --sw-args                                               \
      --dist-sync-start=${SYN_START}                       \
      --dist-sync-repeat=${SYN_REPEAT}                     \
      --ethernet-linkdelay=${ETH_LINK_DELAY}               \
      --ethernet-linkspeed=${ETH_LINK_SPEED}               \
    --m5-args                                               \
      --listener-mode=off                                   \
    --fs-args                                               \
      --dist-sync-start=${SYN_START}                       \
      --dist-sync-repeat=${SYN_REPEAT}                     \
      --ethernet-linkdelay=${ETH_LINK_DELAY}               \
      --ethernet-linkspeed=${ETH_LINK_SPEED}               \
      --cpu-type=${CPU_TYPE}                               \
      --num-cores=${NUM_CORES}                             \
      ${SWITCH_FS_ARGS[@]}                                 \
      --root=/dev/sda2                                     \
      --disk=${IMG}                                         \
      --kernel=${VMLINUX}                                   \
      --readfile=${BOOT_SCRIPT}                             \
      --continue-after-panic
