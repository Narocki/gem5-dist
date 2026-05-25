#! /bin/bash

#
# Copyright (c) 2026
# All rights reserved.
#
# Multi-node single-thread launcher (non-dist) for:
#   configs/example/gem5_library/x86-multinode-single-thread-stdlib.py
#
# This script only runs the single-thread multi-node mode.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
GEM5_DIR=$(cd "${SCRIPT_DIR}/../../.." && pwd)

M5_PATH=${M5_PATH:-/workspaces/gem5}
export M5_PATH

IMG=${M5_PATH}/disks/x86-ubuntu-22.04-img-20250731
VMLINUX=${M5_PATH}/binaries/x86-linux-kernel-5.15.180
BOOT_SCRIPT=${GEM5_DIR}/util/dist/test/simple_bootscript_igb_single_thread.rcS

GEM5_EXE=${GEM5_DIR}/build/X86/gem5.opt
FS_CONFIG=${GEM5_DIR}/configs/example/gem5_library/x86-multinode-single-thread-stdlib.py

RUN_TAG="memory"
RUN_TAG=${RUN_TAG:-$(date +%Y%m%d-%H%M%S)}
RUN_ROOT=${GEM5_DIR}/util/dist/test/runs-gem5-single-thread-multinode/${RUN_TAG}
mkdir -p "${RUN_ROOT}"

echo "[gem5-single-thread-multinode] Output root: ${RUN_ROOT}"

DEFAULT_NODES_LIST="64"
NODES_LIST_STR=${NODES_LIST:-$DEFAULT_NODES_LIST}
NODES_LIST_STR=${NODES_LIST_STR//,/ }
read -r -a NODES_LIST <<< "${NODES_LIST_STR}"

NUM_CORES=${NUM_CORES:-1}
CPU_TYPE=${CPU_TYPE:-atomic}
CLK_FREQ=${CLK_FREQ:-3GHz}
MEM_SIZE=${MEM_SIZE:-2GiB}
ROOT_DEV=${ROOT_DEV:-/dev/sda2}

ETH_SPEED=${ETH_SPEED:-1Gbps}
ETH_DELAY=${ETH_DELAY:-200us}

KERNEL_CMD=${KERNEL_CMD:-"random.trust_cpu=on nokaslr acpi=off"}

# Optional switchable-CPU mode: ENABLE_SWITCH=1 for KVM->Atomic flow.
ENABLE_SWITCH=1
ENABLE_SWITCH=${ENABLE_SWITCH:-0}
SWITCH_START_CPU_TYPE=${SWITCH_START_CPU_TYPE:-kvm}
SWITCH_NEXT_CPU_TYPE=${SWITCH_NEXT_CPU_TYPE:-atomic}

echo "[gem5-single-thread-multinode] Effective NODES_LIST: ${NODES_LIST}"
echo "[gem5-single-thread-multinode] Effective NUM_CORES: ${NUM_CORES}"
echo "[gem5-single-thread-multinode] Effective CPU_TYPE: ${CPU_TYPE}"
echo "[gem5-single-thread-multinode] Effective ENABLE_SWITCH: ${ENABLE_SWITCH}"

clk_to_hz() {
  local clk="$1"
  local num unit
  num=$(echo "$clk" | sed -E 's/^([0-9]+(\.[0-9]+)?).*/\1/')
  unit=$(echo "$clk" | sed -E 's/^[0-9]+(\.[0-9]+)?([[:alpha:]]+).*/\2/' | tr '[:upper:]' '[:lower:]')

  case "$unit" in
    ghz) awk -v n="$num" 'BEGIN{printf "%.0f", n*1e9}' ;;
    mhz) awk -v n="$num" 'BEGIN{printf "%.0f", n*1e6}' ;;
    khz) awk -v n="$num" 'BEGIN{printf "%.0f", n*1e3}' ;;
    hz|"") awk -v n="$num" 'BEGIN{printf "%.0f", n}' ;;
    *) echo "" ;;
  esac
}

emit_per_node_metrics() {
  local stats_file="$1"
  local output_file="$2"
  local num_nodes="$3"
  local clk_freq="$4"

  local clk_hz
  clk_hz=$(clk_to_hz "$clk_freq")

  awk \
    -v num_nodes="$num_nodes" \
    -v clk_hz="$clk_hz" \
    '
    BEGIN {
      global_sim = -1
      global_host = -1
    }

    /^simSeconds[[:space:]]+/ {
      global_sim = $2
      next
    }

    /^hostSeconds[[:space:]]+/ {
      global_host = $2
      next
    }

    $1 ~ /^node[0-9]+\.processor\..*\.core\.numCycles$/ {
      split($1, parts, ".")
      node = parts[1]
      val = $2 + 0
      total[node] += val
      if (val > maxc[node]) {
        maxc[node] = val
      }
      sum_total_cycles += val
      seen[node] = 1
      next
    }

    END {
      print "# Per-node summary (single-process gem5)"
      print "# hostSeconds is global for the whole run; per-node hostSeconds below is a proportional estimate by total cycles."
      printf "global.simSeconds %.9f\n", global_sim
      printf "global.hostSeconds %.9f\n", global_host
      printf "clock.hz %.0f\n", clk_hz + 0
      print ""

      printf "%-8s %-18s %-18s %-18s %-22s\n", "node", "maxCoreCycles", "totalCoreCycles", "simSeconds(est)", "hostSeconds(est,share)"

      for (i = 0; i < num_nodes; i++) {
        node = "node" i
        mc = maxc[node] + 0
        tc = total[node] + 0

        if (clk_hz + 0 > 0) {
          sim_est = mc / (clk_hz + 0)
        } else {
          sim_est = 0
        }

        if (sum_total_cycles > 0 && global_host >= 0) {
          host_est = global_host * (tc / sum_total_cycles)
        } else {
          host_est = 0
        }

        printf "%-8s %-18.0f %-18.0f %-18.9f %-22.9f\n", node, mc, tc, sim_est, host_est
      }
    }
  ' "$stats_file" > "$output_file"
}

for NUM_NODES in "${NODES_LIST[@]}"; do
  OUTDIR=${RUN_ROOT}/nodes-${NUM_NODES}_cores-${NUM_CORES}
  LOG_FILE=${OUTDIR}/run.log
  RESOURCE_LOG=${OUTDIR}/host_resources.txt
  mkdir -p "${OUTDIR}"

  echo "[gem5-single-thread-multinode] Running nodes=${NUM_NODES} cores/node=${NUM_CORES}"
  echo "[gem5-single-thread-multinode] Log: ${LOG_FILE}"
  echo "[gem5-single-thread-multinode] Resource log: ${RESOURCE_LOG}"

  SWITCH_ARGS=()
  if [[ "${ENABLE_SWITCH}" == "1" ]]; then
    SWITCH_ARGS+=(
      --allow-kvm
      --switch-on-workbegin
      --switch-start-cpu-type=${SWITCH_START_CPU_TYPE}
      --switch-next-cpu-type=${SWITCH_NEXT_CPU_TYPE}
    )
  fi

  cat > "${OUTDIR}/command.txt" <<EOF
${GEM5_EXE} -d ${OUTDIR} ${FS_CONFIG} \\
  --kernel=${VMLINUX} \\
  --disk=${IMG} \\
  --readfile=${BOOT_SCRIPT} \\
  --cpu-type=${CPU_TYPE} \\
  --num-cores=${NUM_CORES} \\
  --num-nodes=${NUM_NODES} \\
  --clk-freq=${CLK_FREQ} \\
  --mem-size=${MEM_SIZE} \\
  --root=${ROOT_DEV} \\
  --ethernet-linkspeed=${ETH_SPEED} \\
  --ethernet-linkdelay=${ETH_DELAY} \\
  --kernel-cmd '${KERNEL_CMD}' \\
  --continue-after-panic ${SWITCH_ARGS[*]}
EOF
  echo "[gem5-single-thread-multinode] Command file: ${OUTDIR}/command.txt"

  /usr/bin/time -v -o "${RESOURCE_LOG}" "${GEM5_EXE}" -d "${OUTDIR}" "${FS_CONFIG}" \
    --kernel="${VMLINUX}" \
    --disk="${IMG}" \
    --readfile="${BOOT_SCRIPT}" \
    --cpu-type="${CPU_TYPE}" \
    --num-cores="${NUM_CORES}" \
    --num-nodes="${NUM_NODES}" \
    --clk-freq="${CLK_FREQ}" \
    --mem-size="${MEM_SIZE}" \
    --root="${ROOT_DEV}" \
    --ethernet-linkspeed="${ETH_SPEED}" \
    --ethernet-linkdelay="${ETH_DELAY}" \
    --kernel-cmd "${KERNEL_CMD}" \
    --continue-after-panic \
    "${SWITCH_ARGS[@]}" 2>&1 | tee -a "${LOG_FILE}"

  if [[ -f "${OUTDIR}/stats.txt" ]]; then
    METRICS_FILE="${OUTDIR}/per_node_metrics.txt"
    emit_per_node_metrics "${OUTDIR}/stats.txt" "${METRICS_FILE}" "${NUM_NODES}" "${CLK_FREQ}"
    echo "[gem5-single-thread-multinode] Per-node metrics: ${METRICS_FILE}"
  fi

done

ln -sfn "${RUN_ROOT}" "${GEM5_DIR}/util/dist/test/runs-gem5-single-thread-multinode/latest"
echo "[gem5-single-thread-multinode] Latest run symlink: ${GEM5_DIR}/util/dist/test/runs-gem5-single-thread-multinode/latest"

echo "[gem5-single-thread-multinode] Done. Results in ${RUN_ROOT}"
