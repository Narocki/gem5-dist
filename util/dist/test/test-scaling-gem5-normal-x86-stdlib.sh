#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Benchmark scaling launcher for non-distributed gem5 using:
#   configs/example/gem5_library/x86-stdlib.py
#
# It runs the same FS workload while varying the number of simulated cores
# (default: 2,4,6,...,16) to compare against dist-gem5 scaling experiments.

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname "$0")/../../..

M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
FS_CONFIG=${GEM5_DIR}/configs/example/gem5_library/x86-stdlib.py

RUN_TAG="$(date +%Y%m%d-%H%M%S)_63_cores"

RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs-gem5-normal/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[gem5-normal-scaling] Output root: ${RUN_ROOT}"

KERNEL_CMD=${KERNEL_CMD:-"random.trust_cpu=on nokaslr nmi_watchdog=0 acpi=off lpj=50000000"}
CPU_TYPE=${CPU_TYPE:-timing}
CLK_FREQ=${CLK_FREQ:-3GHz}
MEM_SIZE=${MEM_SIZE:-2GiB}
ROOT_DEV=${ROOT_DEV:-/dev/sda2}

# Default sweep for scaling study.
# CORES_LIST=${CORES_LIST:-"2 4 6 8 10 12 14 16"}
# CORES_LIST=(4 8 12 16 20 24 28 32 36 40 44 48 52 56 60 64)
CORES_LIST=${CORES_LIST:-"2"}

ENABLE_SWITCH=1
SWITCH_START_CPU_TYPE="atomic"
SWITCH_NEXT_CPU_TYPE="o3"
CORES_LIST="63"
RUN_TAG="memory"

# Optional switchable-CPU mode: ENABLE_SWITCH=1 for KVM->Atomic/Timing flow.
ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

for NCORES in ${CORES_LIST}; do
  OUTDIR=${RUN_ROOT}/cores-${NCORES}
  CORE_LOG=${OUTDIR}/run.log
  RESOURCE_LOG=${OUTDIR}/host_resources.txt
  mkdir -p "${OUTDIR}"

  SWITCH_ARGS=()
  if [[ "${ENABLE_SWITCH}" == "1" ]]; then
    SWITCH_ARGS+=(
      --switch-on-workbegin
      --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
      --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
    )
  fi

  {
    echo "[gem5-normal-scaling] Running N=${NCORES} (outdir=${OUTDIR})"
    echo "[gem5-normal-scaling] Core log: ${CORE_LOG}"
    echo "[gem5-normal-scaling] Resource log: ${RESOURCE_LOG}"

    /usr/bin/time -v -o "${RESOURCE_LOG}" "${GEM5_EXE}" -d "${OUTDIR}" "${FS_CONFIG}" \
      --kernel="${VMLINUX}" \
      --disk="${IMG}" \
      --readfile="${BOOT_SCRIPT}" \
      --cpu-type="${CPU_TYPE}" \
      --num-cores="${NCORES}" \
      --clk-freq="${CLK_FREQ}" \
      --mem-size="${MEM_SIZE}" \
      --root="${ROOT_DEV}" \
      --kernel-cmd "${KERNEL_CMD}" \
      --continue-after-panic \
      "${SWITCH_ARGS[@]:-}"
  } 2>&1 | tee -a "${CORE_LOG}"

  # Esperar si hay 2 procesos ejecutándose
  # if (( $(jobs -r -p | wc -l) >= 1 )); then
  #   wait -n
  # fi
done

wait  # Esperar a que terminen todos los procesos restantes
echo "[gem5-normal-scaling] Done. Results in ${RUN_ROOT}"
