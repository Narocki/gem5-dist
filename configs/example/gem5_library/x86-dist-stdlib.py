#!/usr/bin/env python3
# Copyright (c) 2026
# All rights reserved.
#
# Distributed x86 FS configuration using gem5 stdlib components.

import argparse
import os
import sys
from pathlib import Path
from typing import (
    Optional,
    Tuple,
)

import m5
from m5.objects import (
    EtherDump,
    Root,
    Terminal,
)
from m5.util import warn

try:
    from gem5.components.boards.x86_dist_board import (
        X86DistBoard,
        build_dist_switch,
        build_hierarchical_dist_switch,
    )
except ModuleNotFoundError:
    # Fallback for development trees when the Python module list in the build
    # has not been refreshed yet. This allows running without rebuilding first.
    repo_root = Path(__file__).resolve().parents[3]
    src_python = repo_root / "src" / "python"
    if str(src_python) not in sys.path:
        sys.path.insert(0, str(src_python))
    from gem5.components.boards.x86_dist_board import (
        X86DistBoard,
        build_dist_switch,
        build_hierarchical_dist_switch,
    )

from gem5.components.cachehierarchies.classic.no_cache import NoCache
from gem5.components.cachehierarchies.classic.private_l1_cache_hierarchy import (
    PrivateL1CacheHierarchy,
)
from gem5.components.memory.single_channel import SingleChannelDDR3_1600
from gem5.components.processors.cpu_types import CPUTypes
from gem5.components.processors.simple_processor import SimpleProcessor
from gem5.components.processors.simple_switchable_processor import (
    SimpleSwitchableProcessor,
)
from gem5.isas import ISA
from gem5.resources.resource import (
    DiskImageResource,
    KernelResource,
)


def _cpu_type_from_str(cpu_type: str) -> CPUTypes:
    cpu_map = {
        "atomic": CPUTypes.ATOMIC,
        "timing": CPUTypes.TIMING,
        "o3": CPUTypes.O3,
        "kvm": CPUTypes.KVM,
    }
    return cpu_map[cpu_type]


def _add_dist_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dist", action="store_true", help="Run dist-gem5")
    parser.add_argument(
        "--is-switch",
        action="store_true",
        help="Select the network switch simulator process",
    )
    parser.add_argument("--dist-rank", type=int, default=0)
    parser.add_argument("--dist-size", type=int, default=0)
    parser.add_argument("--dist-server-name", type=str, default="127.0.0.1")
    parser.add_argument("--dist-server-port", type=int, default=2200)
    parser.add_argument("--dist-sync-repeat", type=str, default="0us")
    parser.add_argument(
        "--dist-sync-start", type=str, default="1000000000000t"
    )
    parser.add_argument("--ethernet-linkspeed", type=str, default="10Gbps")
    parser.add_argument("--ethernet-linkdelay", type=str, default="10us")
    parser.add_argument(
        "--etherdump",
        action="store",
        type=str,
        default="",
        help="pcap capture filename",
    )
    parser.add_argument(
        "--num-leaf-switches",
        type=int,
        default=1,
        help="Number of leaf switches below the master switch",
    )
    parser.add_argument(
        "--nodes-per-switch",
        type=str,
        default="",
        help="Comma-separated node count for each leaf switch",
    )
    parser.add_argument(
        "--trace-internal-links",
        action="store_true",
        help="Generate PCAP traces for internal hierarchical switch links",
    )
    parser.add_argument(
        "--internal-trace-prefix",
        type=str,
        default="hier_switch",
        help="Prefix for generated internal hierarchical switch PCAP files",
    )
    parser.add_argument(
        "--internal-trace-maxlen",
        type=int,
        default=256,
        help="Snap length (bytes) for internal hierarchical switch PCAPs",
    )


def _add_node_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kernel", type=str, help="Linux kernel binary path")
    parser.add_argument("--disk", type=str, help="Disk image path")
    parser.add_argument(
        "--disk-root-partition",
        type=str,
        default=None,
        help="Disk root partition suffix (e.g., 1, p2)",
    )
    parser.add_argument(
        "--root",
        type=str,
        default="/dev/sda2",
        help="Kernel root= argument",
    )
    parser.add_argument(
        "--kernel-cmd",
        type=str,
        default=None,
        help="Extra kernel command-line arguments",
    )
    parser.add_argument(
        "--readfile",
        type=str,
        default="",
        help="Boot script to load via m5 readfile",
    )
    parser.add_argument(
        "--checkpoint-dir",
        type=str,
        default=m5.options.outdir,
        help="Directory to save/read checkpoints",
    )
    parser.add_argument(
        "--restore-from",
        type=str,
        default=None,
        help="Restore from checkpoint path (absolute or relative to checkpoint-dir)",
    )
    parser.add_argument(
        "--cpu-type",
        type=str,
        choices=["atomic", "timing", "o3", "kvm"],
        default="timing",
        help="CPU model",
    )
    parser.add_argument(
        "--num-cores", type=int, default=2, help="Number of CPU cores"
    )
    parser.add_argument(
        "--clk-freq", type=str, default="3GHz", help="Board clock frequency"
    )
    parser.add_argument(
        "--mem-size", type=str, default="2GiB", help="Memory size (<=3GiB)"
    )
    parser.add_argument(
        "--continue-after-panic",
        action="store_true",
        help="Set on_panic/on_oops to dump dmesg and continue",
    )
    parser.add_argument(
        "--switch-on-workbegin",
        action="store_true",
        help=(
            "Use a switchable processor and switch CPU model on first "
            "workbegin exit event (e.g., before MPI ROI)."
        ),
    )
    parser.add_argument(
        "--switch-start-cpu-type",
        type=str,
        choices=["atomic", "timing", "o3", "kvm"],
        default=None,
        help=(
            "Starting CPU model when --switch-on-workbegin is enabled. "
            "Defaults to --cpu-type."
        ),
    )
    parser.add_argument(
        "--switch-next-cpu-type",
        type=str,
        choices=["atomic", "timing", "o3", "kvm"],
        default=None,
        help=(
            "CPU model to switch to on first workbegin when "
            "--switch-on-workbegin is enabled."
        ),
    )
    parser.add_argument(
        "--kvm-dist-allow-smp",
        action="store_true",
        help=(
            "Allow KVM + dist-gem5 with more than one core (experimental). "
            "Without this flag, KVM in distributed mode is forced to 1 core "
            "for stability."
        ),
    )
    parser.add_argument(
        "--kvm-dist-eventq-strategy",
        type=str,
        choices=["shared", "separate"],
        default="shared",
        help=(
            "Event queue strategy for KVM + dist-gem5. 'shared' keeps all "
            "CPU-side objects on eventq 0; 'separate' assigns one event queue "
            "per KVM core (stdlib default)."
        ),
    )


def _run_loop(
    checkpoint_dir: str,
    switchable_processor: Optional[SimpleSwitchableProcessor] = None,
    switch_start_cpu_type: Optional[str] = None,
    switch_next_cpu_type: Optional[str] = None,
) -> None:
    switched_to_atomic = False

    while True:
        event = m5.simulate()
        exit_msg = event.getCause()
        exit_hyper_id = event.getHypercallId()

        if exit_msg == "checkpoint":
            print("Dropping checkpoint at tick %d" % m5.curTick())
            cpt_dir = os.path.join(checkpoint_dir, "cpt.%d" % m5.curTick())
            m5.checkpoint(cpt_dir)
            print("Checkpoint done.")
            continue

        # Trigger the CPU switch at the ROI boundary marked by m5_work_begin.
        # This allows boot/network setup to run on KVM and detailed ROI (MPI)
        # to run on Atomic.
        if (
            switchable_processor is not None
            and not switched_to_atomic
            and "workbegin" in exit_msg.lower()
        ):
            print(
                "Switching CPU model: %d -> %d",
                switch_start_cpu_type,
                switch_next_cpu_type,
            )
            switchable_processor.switch()
            switched_to_atomic = True
            continue

        if exit_hyper_id == 5 or "workend" in exit_msg:
            print("Workload ended @", m5.curTick())
            continue

        print(exit_msg, "@", m5.curTick())
        sys.exit(event.getCode())


def _instantiate(restore_from: str, checkpoint_dir: str) -> None:
    if restore_from:
        if checkpoint_dir and not os.path.isabs(restore_from):
            checkpoint = os.path.join(checkpoint_dir, restore_from)
        else:
            checkpoint = restore_from
        m5.instantiate(checkpoint)
    else:
        m5.instantiate()


def _build_node(
    options: argparse.Namespace,
) -> Tuple[Root, Optional[SimpleSwitchableProcessor]]:
    if not options.kernel:
        raise ValueError("--kernel is required for node mode")
    if not options.disk:
        raise ValueError("--disk is required for node mode")
    if options.dist_size <= 0:
        raise ValueError("--dist-size must be > 0")

    start_cpu_type = (
        options.switch_start_cpu_type
        if options.switch_on_workbegin and options.switch_start_cpu_type
        else options.cpu_type
    )

    effective_cores = options.num_cores
    if (
        options.dist
        and start_cpu_type == "kvm"
        and options.num_cores > 1
        and not options.kvm_dist_allow_smp
    ):
        warn(
            "KVM + x86 dist may hang during SMP bring-up; forcing "
            "--num-cores=1 for stability. Use --kvm-dist-allow-smp "
            "to enable experimental multi-core KVM."
        )
        effective_cores = 1

    switchable_processor: Optional[SimpleSwitchableProcessor] = None

    if options.switch_on_workbegin:
        if options.switch_next_cpu_type is None:
            raise ValueError(
                "--switch-next-cpu-type is required when "
                "--switch-on-workbegin is enabled"
            )

        switchable_processor = SimpleSwitchableProcessor(
            starting_core_type=_cpu_type_from_str(start_cpu_type),
            switch_core_type=_cpu_type_from_str(options.switch_next_cpu_type),
            isa=ISA.X86,
            num_cores=effective_cores,
        )
        processor = switchable_processor
    else:
        processor = SimpleProcessor(
            cpu_type=_cpu_type_from_str(options.cpu_type),
            isa=ISA.X86,
            num_cores=effective_cores,
        )

    board = X86DistBoard(
        clk_freq=options.clk_freq,
        processor=processor,
        memory=SingleChannelDDR3_1600(size=options.mem_size),
        cache_hierarchy=PrivateL1CacheHierarchy(
            l1d_size="32kB", l1i_size="32kB"
        ),
        dist_rank=options.dist_rank,
        dist_size=options.dist_size,
        dist_server_name=options.dist_server_name,
        dist_server_port=options.dist_server_port,
        dist_sync_repeat=options.dist_sync_repeat,
        dist_sync_start=options.dist_sync_start,
        ethernet_linkspeed=options.ethernet_linkspeed,
        ethernet_linkdelay=options.ethernet_linkdelay,
        etherdump=(options.etherdump or None),
    )

    root_arg = options.root
    if root_arg.startswith("/dev/hda"):
        warn(
            f"Mapping legacy root device '{root_arg}' to '/dev/sda*' for "
            "this x86 PCI IDE setup."
        )
        root_arg = root_arg.replace("/dev/hda", "/dev/sda", 1)

    board.set_kernel_disk_workload(
        kernel=KernelResource(local_path=options.kernel),
        disk_image=DiskImageResource(
            local_path=options.disk,
            root_partition=options.disk_root_partition,
        ),
        readfile=(options.readfile or None),
        kernel_args=[
            "earlyprintk=ttyS0",
            "console=ttyS0",
            f"root={root_arg}",
            "rw",
        ],
    )

    # Force UART (COM1) output to a text file in m5out.
    board.terminal = Terminal(outfile="file")
    board.pc.com_1.device = board.terminal

    if options.kernel_cmd:
        board.append_kernel_arg(options.kernel_cmd)

    if start_cpu_type == "kvm":
        # With this Ubuntu kernel on gem5 x86, keeping APIC enabled may still
        # trip the early IO-APIC/PIT sanity check. This keeps APIC enabled but
        # skips the fatal check and uses a stable TSC clocksource.
        if (
            not options.kernel_cmd
            or "no_timer_check" not in options.kernel_cmd
        ):
            board.append_kernel_arg(
                "clocksource=tsc tsc=reliable no_timer_check"
            )

    if options.continue_after_panic:
        board.workload.on_panic = "DumpDmesgAndContinue"
        board.workload.on_oops = "DumpDmesgAndContinue"

    root = board._pre_instantiate(full_system=True)

    # Dist sync uses a global event object constructed before instantiate.
    # KVM + dist-gem5 is sensitive to event queue placement during SMP boot.
    # Keep a switchable policy for experimentation.
    if options.dist and start_cpu_type == "kvm":
        if options.kvm_dist_eventq_strategy == "shared":
            warn(
                "KVM + dist-gem5: using shared eventq strategy "
                "(all CPU-side objects on eventq 0)."
            )
            for core in board.get_processor().get_cores():
                simobj = core.get_simobject()
                for obj in simobj.descendants():
                    obj.eventq_index = 0
                simobj.eventq_index = 0
        else:
            warn(
                "KVM + dist-gem5: using separate eventq strategy "
                "(one queue per KVM core, stdlib default)."
            )

    return root, switchable_processor


def _build_switch(options: argparse.Namespace) -> Root:
    if options.dist_size <= 0:
        raise ValueError("--dist-size must be > 0")

    use_hierarchical_switch = (
        options.num_leaf_switches > 1
        or bool(options.nodes_per_switch)
        or options.trace_internal_links
    )

    if use_hierarchical_switch:
        switch = build_hierarchical_dist_switch(
            dist_rank=options.dist_rank,
            dist_size=options.dist_size,
            dist_server_name=options.dist_server_name,
            dist_server_port=options.dist_server_port,
            dist_sync_repeat=options.dist_sync_repeat,
            dist_sync_start=options.dist_sync_start,
            ethernet_linkspeed=options.ethernet_linkspeed,
            ethernet_linkdelay=options.ethernet_linkdelay,
            num_leaf_switches=options.num_leaf_switches,
            nodes_per_switch=(options.nodes_per_switch or None),
            trace_internal_links=options.trace_internal_links,
            internal_trace_prefix=options.internal_trace_prefix,
            internal_trace_maxlen=options.internal_trace_maxlen,
        )
    else:
        switch = build_dist_switch(
            dist_rank=options.dist_rank,
            dist_size=options.dist_size,
            dist_server_name=options.dist_server_name,
            dist_server_port=options.dist_server_port,
            dist_sync_repeat=options.dist_sync_repeat,
            dist_sync_start=options.dist_sync_start,
            ethernet_linkspeed=options.ethernet_linkspeed,
            ethernet_linkdelay=options.ethernet_linkdelay,
        )

    if options.etherdump:
        switch.dump = EtherDump(file=options.etherdump)

    return Root(full_system=True, system=switch)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Distributed x86 full-system configuration (stdlib board)"
    )
    _add_dist_args(parser)
    _add_node_args(parser)
    options = parser.parse_args()

    # Dist sync at tick 0 can trip global event scheduling assertions in some
    # configurations. Normalize zero to the next tick.
    if str(options.dist_sync_start).strip() in {
        "0",
        "0t",
        "0ps",
        "0ns",
        "0us",
        "0ms",
        "0s",
    }:
        warn("--dist-sync-start=0 is not supported; using 1t instead.")
        options.dist_sync_start = "1t"

    switchable_processor: Optional[SimpleSwitchableProcessor] = None

    if options.is_switch:
        print("Running in switch mode")
        _build_switch(options)
    else:
        print("Running in node mode")
        _, switchable_processor = _build_node(options)

    _instantiate(options.restore_from, options.checkpoint_dir)
    _run_loop(
        options.checkpoint_dir,
        switchable_processor=switchable_processor,
        switch_next_cpu_type=options.switch_next_cpu_type,
        switch_start_cpu_type=options.switch_start_cpu_type,
    )


if __name__ == "__m5_main__":
    main()
