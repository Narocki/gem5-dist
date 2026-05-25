#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Dist-gem5 x86 stdlib sweep for host count with MPI np loop inside bootscript.
#
# Sweeps:
#   - Hosts in hostfile (dist-size): 2 and 4
#   - MPI processes (-np): passed as list and executed in-guest without reinicios
#
# Designed to compare behavior against a real 4-node cluster with 32 cores/node.

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname "$0")/../../..

M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb_mpi_np.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist-x86-stdlib.sh

RUN_TAG=${RUN_TAG:-hosts-np-scaling-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-hosts-np-scaling] Output root: ${RUN_ROOT}"

ENABLE_SWITCH=1
SWITCH_START_CPU_TYPE=kvm
SWITCH_NEXT_CPU_TYPE=o3
SYN_START="9s"
NUM_CORES=8
KVM_DIST_ALLOW_SMP=1
KVM_DIST_EVENTQ_STRATEGY=separate

# Dist link/sync tuning.
ETH_LINK_DELAY=${ETH_LINK_DELAY:-200us}
ETH_LINK_SPEED=${ETH_LINK_SPEED:-1Gbps}
SYN_REPEAT=${SYN_REPEAT:-200us}
SYN_START=${SYN_START:-6000040006400t}

# CPU model for stdlib script: atomic|timing|o3|kvm
CPU_TYPE=${CPU_TYPE:-timing}
NUM_CORES=${NUM_CORES:-32}

# Experimental KVM+dist SMP controls (only used when CPU_TYPE=kvm).
# Set KVM_DIST_ALLOW_SMP=1 to pass --kvm-dist-allow-smp.
# Strategy: shared|separate
KVM_DIST_ALLOW_SMP=${KVM_DIST_ALLOW_SMP:-0}
KVM_DIST_EVENTQ_STRATEGY=${KVM_DIST_EVENTQ_STRATEGY:-shared}

# Kernel args. mpi_np_list is appended por corrida de hosts.
KERNEL_CMD_BASE=${KERNEL_CMD_BASE:-"random.trust_cpu=on"}

# Sweeps.
HOST_COUNTS=${HOST_COUNTS:-"2"}
NP_LIST=${NP_LIST:-"32"}

HOST_COUNTS="4"
NP_LIST="2"
SYN_START="0t"
# Optional debug flags for gem5, e.g. DistEthernet,DistSync
DEBUG_FLAGS=${DEBUG_FLAGS:-}

# If provided, this mapping is reused for all cases.
USER_LSB_MCPU_HOSTS=${LSB_MCPU_HOSTS:-}

# Optional switch on first workbegin (e.g., before MPI).
ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi

KVM_DIST_FS_ARGS=()
if [[ "${CPU_TYPE}" == "kvm" || ( "${ENABLE_SWITCH}" == "1" && "${SWITCH_START_CPU_TYPE}" == "kvm" ) ]]; then
  KVM_DIST_FS_ARGS+=(
    --kvm-dist-eventq-strategy=${KVM_DIST_EVENTQ_STRATEGY}
  )
  if [[ "${KVM_DIST_ALLOW_SMP}" == "1" ]]; then
    KVM_DIST_FS_ARGS+=(--kvm-dist-allow-smp)
  fi
fi

M5_ARGS=(
  --listener-mode=off
)

if [[ -n "${DEBUG_FLAGS}" ]]; then
  M5_ARGS+=(--debug-flags=${DEBUG_FLAGS})
fi

for NNODES in ${HOST_COUNTS}; do
  # If user did not provide cluster allocation, adapt localhost mapping to NNODES.
  if [ -n "${USER_LSB_MCPU_HOSTS}" ]; then
    export LSB_MCPU_HOSTS="${USER_LSB_MCPU_HOSTS}"
  else
    export LSB_MCPU_HOSTS="127.0.0.1 ${NNODES}"
  fi
  export SSH_PORTS=${SSH_PORTS:-}

  CASE_DIR=${RUN_ROOT}/hosts-${NNODES}/multi-np
  CORE_LOG=${CASE_DIR}/run.log
  RESOURCE_LOG=${CASE_DIR}/host_resources.txt
  mkdir -p "${CASE_DIR}"

  NP_LIST_CMDLINE=$(echo "${NP_LIST}" | tr ' ' ',')
  KERNEL_CMD="${KERNEL_CMD_BASE} mpi_np_list=${NP_LIST_CMDLINE}"

  {
    echo "[dist-hosts-np-scaling] Running hosts=${NNODES} np_list=${NP_LIST}"
    echo "[dist-hosts-np-scaling] Core log: ${CORE_LOG}"
    echo "[dist-hosts-np-scaling] Resource log: ${RESOURCE_LOG}"

    /usr/bin/time -v -o "${RESOURCE_LOG}" \
      "${GEM5_DIST_SH}" -n "${NNODES}"                             \
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
          "${KVM_DIST_FS_ARGS[@]}"                                  \
          "${SWITCH_FS_ARGS[@]}"                                    \
          --root=/dev/sda2                                           \
          --disk=${IMG}                                              \
          --kernel=${VMLINUX}                                        \
          --readfile=${BOOT_SCRIPT}                                  \
          --continue-after-panic
  } 2>&1 | tee -a "${CORE_LOG}"
done

echo "[dist-hosts-np-scaling] Done. Results in ${RUN_ROOT}"
