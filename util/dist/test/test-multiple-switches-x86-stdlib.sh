#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Example script to launch a dist-gem5 x86 stdlib simulation with a
# hierarchical switch topology.
#
# It combines:
#   - the stdlib x86 distributed board flow, and
#   - the hierarchical switch launcher options.

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname $0)/../../..

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist_multiple_switches_x86-stdlib.sh

RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-test] Output root: ${RUN_ROOT}"
CORE_LOG=${RUN_ROOT}/run.log
RESOURCE_LOG=${RUN_ROOT}/host_resources.txt

KERNEL_CMD="random.trust_cpu=on nokaslr acpi=off"

ETH_LINK_DELAY="1ms"
ETH_LINK_SPEED="1Gbps"
SYN_REPEAT="1ms"
SYN_START="1t"

CPU_TYPE="timing"
# NUM_CORES=1

ENABLE_SWITCH=1
SWITCH_START_CPU_TYPE="kvm"
SWITCH_NEXT_CPU_TYPE="atomic"

# NNODES=2
# ENABLE_HIERARCHICAL_SWITCH=1
# LSB_MCPU_HOSTS="192.168.1.75 1 192.168.1.118 1"
# SSH_PORTS="2222 2224"
# LSB_MCPU_HOSTS="192.168.1.75 2"
# SSH_PORTS="2222 2222"

# Optional switchable-CPU mode. Enable with ENABLE_SWITCH=1.
ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

# Single knob to enable/disable the hierarchical switch topology.
ENABLE_HIERARCHICAL_SWITCH=${ENABLE_HIERARCHICAL_SWITCH:-1}
NLEAF=${NLEAF:-2}
NNODES=${NNODES:-2}
NODES_PER_SWITCH=${NODES_PER_SWITCH:-1,1}

# If no allocation is provided, run everything on localhost.
if [ -z "${LSB_MCPU_HOSTS:-}" ]; then
  LSB_MCPU_HOSTS="127.0.0.1 ${NNODES}"
fi
export LSB_MCPU_HOSTS

# Optionally export SSH_PORTS before running this script when using
# remote hosts behind non-default SSH ports.
export SSH_PORTS=${SSH_PORTS:-}

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi

TOPOLOGY_ARGS=()
SW_ARGS=(
  --dist-sync-start=${SYN_START}
  --dist-sync-repeat=${SYN_REPEAT}
  --ethernet-linkdelay=${ETH_LINK_DELAY}
  --ethernet-linkspeed=${ETH_LINK_SPEED}
)

if [[ "${ENABLE_HIERARCHICAL_SWITCH}" == "1" ]]; then
  TOPOLOGY_ARGS+=(
    --num-leaf-switches "${NLEAF}"
    --nodes-per-switch "${NODES_PER_SWITCH}"
  )
  SW_ARGS+=(
    --trace-internal-links
    --internal-trace-prefix=${RUN_ROOT}/internal_sw
    --internal-trace-maxlen=512
  )
fi

{
  echo "[dist-test] Core log: ${CORE_LOG}"
  echo "[dist-test] Resource log: ${RESOURCE_LOG}"

  /usr/bin/time -v -o "${RESOURCE_LOG}" \
    "${GEM5_DIST_SH}" -n ${NNODES}                          \
      -r ${RUN_ROOT}                                         \
      -c ${RUN_ROOT}                                         \
      -x ${GEM5_EXE}                                         \
      -p 2210                                                \
      "${TOPOLOGY_ARGS[@]}"                                  \
      --kernel-cmd "${KERNEL_CMD}"                           \
      --sw-args                                              \
        "${SW_ARGS[@]}"                                      \
      --m5-args                                              \
        --listener-mode=off                                  \
      --fs-args                                              \
        --dist-sync-start=${SYN_START}                       \
        --dist-sync-repeat=${SYN_REPEAT}                     \
        --ethernet-linkdelay=${ETH_LINK_DELAY}               \
        --ethernet-linkspeed=${ETH_LINK_SPEED}               \
        --cpu-type=${CPU_TYPE}                               \
        --num-cores=${NUM_CORES}                             \
        "${SWITCH_FS_ARGS[@]}"                               \
        --root=/dev/sda2                                     \
        --disk=${IMG}                                        \
        --kernel=${VMLINUX}                                  \
        --readfile=${BOOT_SCRIPT}                            \
        --continue-after-panic
} 2>&1 | tee -a "${CORE_LOG}"
