#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Sweep script for dist-gem5 x86 stdlib runs over link delay and sync repeat.
#
# For each link delay in: 5, 10, 20, 50, 100, 200 us
# it runs sync repeat values of: equal, +50%, +100%, +150%, +200%, +400%.
#
# Each combination is executed twice:
# 1) flat topology (all nodes connected to one switch), and
# 2) hierarchical topology (one master switch + two leaf switches).

set -euo pipefail

GEM5_DIR=$(pwd)/$(dirname "$0")/../../..

M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
GEM5_DIST_SH=${GEM5_DIR}/util/dist/gem5-dist_multiple_switches_x86-stdlib.sh

KERNEL_CMD=${KERNEL_CMD:-"random.trust_cpu=on nokaslr"}
ETH_LINK_SPEED=${ETH_LINK_SPEED:-"1Gbps"}
SYN_START=${SYN_START:-"1t"}

CPU_TYPE=${CPU_TYPE:-"timing"}
NUM_CORES=${NUM_CORES:-1}
NNODES=${NNODES:-2}

# Optional lightweight debug trace to check liveness without huge files.
# Enable with: ENABLE_DEBUG_STATS=1
ENABLE_DEBUG_STATS=${ENABLE_DEBUG_STATS:-0}
# DEBUG_FLAGS=${DEBUG_FLAGS:-"TimeSync,WorkItems,DistEthernetCmd,Stats"}
DEBUG_START=${DEBUG_START:-}
DEBUG_FILE_NAME=${DEBUG_FILE_NAME:-"debug_liveness.log"}
# Keep only rank-0 debug output (m5out.0) for each case.
DEBUG_ONLY_M5OUT0=${DEBUG_ONLY_M5OUT0:-1}


SYN_START="0s"
ETH_LINK_SPEED="200Gbps"
NNODES=2
HIER_NODES_PER_SWITCH="1,1" 
LINK_DELAY_US_VALUES=(1000 500 250 125 50 20 2 1 0.5 0.2 0.1)
#LINK_DELAY_US_VALUES=(1000)
SYN_REPEAT_BONUS_PCT_VALUES=(-10)
ENABLE_SWITCH=1
NUM_CORES=1

ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

# Hierarchical topology defaults: one master switch and two leaf switches.
HIER_NLEAF=${HIER_NLEAF:-2}
HIER_NODES_PER_SWITCH=${HIER_NODES_PER_SWITCH:-1,1}

# Continue all experiments even if one fails (set STOP_ON_ERROR=1 to stop).
STOP_ON_ERROR=${STOP_ON_ERROR:-0}

# Parallel sweep controls.
# Each case typically runs 3 processes (2 hosts + 1 switch).
TOTAL_CPUS=$(nproc 2>/dev/null || echo 1)
MAX_CPUS_FOR_PARALLEL=${MAX_CPUS_FOR_PARALLEL:-$((TOTAL_CPUS ))}
if ((MAX_CPUS_FOR_PARALLEL < 1)); then
  MAX_CPUS_FOR_PARALLEL=1
fi
PROCS_PER_CASE=${PROCS_PER_CASE:-NNODES + 1}
MAX_PARALLEL_RUNS=${MAX_PARALLEL_RUNS:-$((MAX_CPUS_FOR_PARALLEL / PROCS_PER_CASE))}
if ((MAX_PARALLEL_RUNS < 1)); then
  MAX_PARALLEL_RUNS=1
fi

sanitize_tag_component() {
  local raw="$1"
  local safe
  safe=$(echo "${raw}" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g; s/-\{2,\}/-/g; s/^-//; s/-$//')
  if [[ -z "${safe}" ]]; then
    safe="na"
  fi
  echo "${safe}"
}

if [[ -z "${RUN_TAG:-}" ]]; then
  TS=$(date +%Y%m%d-%H%M%S)

  LD_MIN=$(printf '%s\n' "${LINK_DELAY_US_VALUES[@]}" | sort -g | head -n1)
  LD_MAX=$(printf '%s\n' "${LINK_DELAY_US_VALUES[@]}" | sort -g | tail -n1)
  LD_COUNT=${#LINK_DELAY_US_VALUES[@]}

  SR_MIN=$(printf '%s\n' "${SYN_REPEAT_BONUS_PCT_VALUES[@]}" | sort -g | head -n1)
  SR_MAX=$(printf '%s\n' "${SYN_REPEAT_BONUS_PCT_VALUES[@]}" | sort -g | tail -n1)
  SR_COUNT=${#SYN_REPEAT_BONUS_PCT_VALUES[@]}

  SPEED_TAG=$(sanitize_tag_component "${ETH_LINK_SPEED}")
  CPU_TAG=$(sanitize_tag_component "${CPU_TYPE}")

  RUN_TAG="sweep-linkdelay-synrepeat-${TS}-${NNODES}nodes-${CPU_TAG}-${NUM_CORES}cores-${SPEED_TAG}-ld${LD_MIN}to${LD_MAX}us-x${LD_COUNT}-sr${SR_MIN}to${SR_MAX}pct-x${SR_COUNT}-sw${ENABLE_SWITCH}"
fi

RUN_ROOT=${GEM5_DIR}/util/dist/test/sweep_runs/${RUN_TAG}
mkdir -p "${RUN_ROOT}"
echo "[dist-sweep] Output root: ${RUN_ROOT}"

MASTER_LOG=${RUN_ROOT}/run.log
SUMMARY_CSV=${RUN_ROOT}/summary.csv



echo "[dist-sweep] Detected ${TOTAL_CPUS} host CPUs, allowing up to ${MAX_CPUS_FOR_PARALLEL} for parallel runs, which means up to ${MAX_PARALLEL_RUNS} parallel cases (with ${PROCS_PER_CASE} processes each)." | tee -a "${MASTER_LOG}"

# Switch server base port; each parallel case gets BASE_PORT + case_id.
BASE_PORT=${BASE_PORT:-2210}

# If no allocation is provided, run everything on localhost.
if [ -z "${LSB_MCPU_HOSTS:-}" ]; then
  LSB_MCPU_HOSTS="127.0.0.1 ${NNODES}"
fi
export LSB_MCPU_HOSTS

# Optionally export SSH_PORTS before running when using remote hosts.
export SSH_PORTS=${SSH_PORTS:-}

SWITCH_FS_ARGS=()
if [[ "${ENABLE_SWITCH}" == "1" ]]; then
  SWITCH_FS_ARGS+=(
    --switch-on-workbegin
    --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
    --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
  )
fi


echo "topology,link_delay,sync_repeat,delay_us,syn_repeat_bonus_pct,exit_code,output_dir" > "${SUMMARY_CSV}"

CASE_STATUS_DIR="${RUN_ROOT}/.case_status"
mkdir -p "${CASE_STATUS_DIR}"

echo "[dist-sweep] Master log: ${MASTER_LOG}" | tee -a "${MASTER_LOG}"
echo "[dist-sweep] Summary CSV: ${SUMMARY_CSV}" | tee -a "${MASTER_LOG}"
echo "[dist-sweep] Parallelism: total_cpus=${TOTAL_CPUS} max_cpus_for_parallel=${MAX_CPUS_FOR_PARALLEL} procs_per_case=${PROCS_PER_CASE} max_parallel_runs=${MAX_PARALLEL_RUNS}" | tee -a "${MASTER_LOG}"

run_case() {
  local topology="$1"
  local delay_us="$2"
  local bonus_pct="$3"
  local case_id="$4"

  local link_delay="${delay_us}us"
  local syn_repeat_ns
  syn_repeat_ns=$(awk -v d="$delay_us" -v b="$bonus_pct" 'BEGIN { printf "%.0f", d * 10 * (100 + b) }')
  local syn_repeat="${syn_repeat_ns}ns"

  local bonus_label
  if [[ "${bonus_pct}" == "0" ]]; then
    bonus_label="equal"
  else
    bonus_label="plus${bonus_pct}pct"
  fi

  local case_dir="${RUN_ROOT}/${topology}/ld_${delay_us}us/sr_${bonus_label}"
  local core_log="${case_dir}/run.log"
  local resource_log="${case_dir}/host_resources.txt"
  local case_status_file="${CASE_STATUS_DIR}/case_${case_id}.csv"
  local case_port=$((BASE_PORT + case_id))
  local debug_file_rank0="${case_dir}/m5out.0/${DEBUG_FILE_NAME}"
  local debug_file_export="${case_dir}/debug_m5out0.log"

  mkdir -p "${case_dir}"

  local -a topology_args=()
  local -a sw_args=(
    --dist-sync-start=${SYN_START}
    --dist-sync-repeat=${syn_repeat}
    --ethernet-linkdelay=${link_delay}
    --ethernet-linkspeed=${ETH_LINK_SPEED}
  )
  local -a m5_args=(
    --listener-mode=off
  )

  if [[ "${ENABLE_DEBUG_STATS}" == "1" ]]; then
    m5_args+=(--debug-file="${DEBUG_FILE_NAME}")
    if [[ -n "${DEBUG_FLAGS}" ]]; then
      m5_args+=(--debug-flags="${DEBUG_FLAGS}")
    fi
    if [[ -n "${DEBUG_START}" ]]; then
      m5_args+=(--debug-start="${DEBUG_START}")
    fi
  fi

  if [[ "${topology}" == "hierarchical" ]]; then
    topology_args+=(
      --num-leaf-switches "${HIER_NLEAF}"
      --nodes-per-switch "${HIER_NODES_PER_SWITCH}"
    )
    sw_args+=(
      --trace-internal-links
      --internal-trace-prefix=${case_dir}/internal_sw
      --internal-trace-maxlen=512
    )
  fi

  echo "[dist-sweep] START case_id=${case_id} topology=${topology} link_delay=${link_delay} syn_repeat=${syn_repeat} port=${case_port} case_dir=${case_dir}" | tee -a "${MASTER_LOG}"

  set +e
  /usr/bin/time -v -o "${resource_log}" \
    "${GEM5_DIST_SH}" -n "${NNODES}"                              \
      -r "${case_dir}"                                              \
      -c "${case_dir}"                                              \
      -x "${GEM5_EXE}"                                              \
      -p "${case_port}"                                             \
      "${topology_args[@]}"                                         \
      --kernel-cmd "${KERNEL_CMD}"                                 \
      --sw-args                                                      \
        "${sw_args[@]}"                                             \
      --m5-args                                                      \
        "${m5_args[@]}"                                             \
      --fs-args                                                      \
        --dist-sync-start=${SYN_START}                              \
        --dist-sync-repeat=${syn_repeat}                             \
        --ethernet-linkdelay=${link_delay}                           \
        --ethernet-linkspeed=${ETH_LINK_SPEED}                       \
        --cpu-type=${CPU_TYPE}                                       \
        --num-cores=${NUM_CORES}                                     \
        "${SWITCH_FS_ARGS[@]}"                                      \
        --root=/dev/sda2                                             \
        --disk=${IMG}                                                \
        --kernel=${VMLINUX}                                          \
        --readfile=${BOOT_SCRIPT}                                    \
        --continue-after-panic \
    2>&1 | tee -a "${core_log}"
  local rc=${PIPESTATUS[0]}
  set -e

  if [[ "${ENABLE_DEBUG_STATS}" == "1" && "${DEBUG_ONLY_M5OUT0}" == "1" ]]; then
    # Remove debug traces from non-zero ranks/switch to keep only m5out.0.
    find "${case_dir}" -mindepth 2 -maxdepth 2 -type f -name "${DEBUG_FILE_NAME}" \
      ! -path "${case_dir}/m5out.0/*" -delete
    if [[ -f "${debug_file_rank0}" ]]; then
      cp -f "${debug_file_rank0}" "${debug_file_export}"
    fi
  fi

  echo "${topology},${link_delay},${syn_repeat},${delay_us},${bonus_pct},${rc},${case_dir}" > "${case_status_file}"

  if [[ "${rc}" -eq 0 ]]; then
    echo "[dist-sweep] DONE topology=${topology} link_delay=${link_delay} syn_repeat=${syn_repeat}" | tee -a "${MASTER_LOG}"
  else
    echo "[dist-sweep] FAIL(rc=${rc}) topology=${topology} link_delay=${link_delay} syn_repeat=${syn_repeat}" | tee -a "${MASTER_LOG}"
    if [[ "${STOP_ON_ERROR}" == "1" ]]; then
      echo "[dist-sweep] STOP_ON_ERROR=1, aborting." | tee -a "${MASTER_LOG}"
      exit "${rc}"
    fi
  fi
}

active_jobs=0
case_id=0

wait_for_one() {
  set +e
  wait -n
  local wait_rc=$?
  set -e
  active_jobs=$((active_jobs - 1))

  if [[ "${STOP_ON_ERROR}" == "1" && "${wait_rc}" -ne 0 ]]; then
    echo "[dist-sweep] STOP_ON_ERROR=1 and one parallel case failed, stopping remaining jobs." | tee -a "${MASTER_LOG}"
    jobs -pr | xargs -r kill
    wait || true
    exit "${wait_rc}"
  fi
}

for delay_us in "${LINK_DELAY_US_VALUES[@]}"; do
  for bonus_pct in "${SYN_REPEAT_BONUS_PCT_VALUES[@]}"; do
    run_case flat "${delay_us}" "${bonus_pct}" "${case_id}" &
    ((active_jobs+=1))
    ((case_id+=1))
    if ((active_jobs >= MAX_PARALLEL_RUNS)); then
      wait_for_one
    fi

    run_case hierarchical "${delay_us}" "${bonus_pct}" "${case_id}" &
    ((active_jobs+=1))
    ((case_id+=1))
    if ((active_jobs >= MAX_PARALLEL_RUNS)); then
      wait_for_one
    fi
  done
done

while ((active_jobs > 0)); do
  wait_for_one
done

mapfile -t case_status_files < <(find "${CASE_STATUS_DIR}" -type f -name "case_*.csv" | sort -V)
for case_status_file in "${case_status_files[@]}"; do
  cat "${case_status_file}" >> "${SUMMARY_CSV}"
done

echo "[dist-sweep] Finished all runs." | tee -a "${MASTER_LOG}"
echo "[dist-sweep] See summary: ${SUMMARY_CSV}" | tee -a "${MASTER_LOG}"
