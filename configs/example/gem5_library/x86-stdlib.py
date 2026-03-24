#!/usr/bin/env python3
# Copyright (c) 2026
# All rights reserved.
#
# Non-distributed x86 FS configuration using gem5 stdlib components.

import argparse
import os
import sys
from typing import (
    Optional,
    Tuple,
)

import m5
from m5.objects import (
    Root,
    Terminal,
)
from m5.util import warn

from gem5.components.boards.x86_board import X86Board
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


def _run_loop(
    checkpoint_dir: str,
    switchable_processor: Optional[SimpleSwitchableProcessor] = None,
    switch_start_cpu_type: Optional[str] = None,
    switch_next_cpu_type: Optional[str] = None,
) -> None:
    switched_core_model = False

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

        if (
            switchable_processor is not None
            and not switched_core_model
            and "workbegin" in exit_msg.lower()
        ):
            print(
                "Switching CPU model:",
                switch_start_cpu_type,
                "->",
                switch_next_cpu_type,
            )
            switchable_processor.switch()
            switched_core_model = True
            continue

        if exit_hyper_id == 5 or "workend" in exit_msg.lower():
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
        raise ValueError("--kernel is required")
    if not options.disk:
        raise ValueError("--disk is required")

    start_cpu_type = (
        options.switch_start_cpu_type
        if options.switch_on_workbegin and options.switch_start_cpu_type
        else options.cpu_type
    )

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
            num_cores=options.num_cores,
        )
        processor = switchable_processor
    else:
        processor = SimpleProcessor(
            cpu_type=_cpu_type_from_str(options.cpu_type),
            isa=ISA.X86,
            num_cores=options.num_cores,
        )

    board = X86Board(
        clk_freq=options.clk_freq,
        processor=processor,
        memory=SingleChannelDDR3_1600(size=options.mem_size),
        cache_hierarchy=PrivateL1CacheHierarchy(
            l1d_size="32kB", l1i_size="32kB"
        ),
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

    board.terminal = Terminal(outfile="file")
    board.pc.com_1.device = board.terminal

    if options.kernel_cmd:
        board.append_kernel_arg(options.kernel_cmd)

    if start_cpu_type == "kvm":
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
    return root, switchable_processor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="x86 full-system configuration (stdlib board, non-distributed)"
    )
    _add_node_args(parser)
    options = parser.parse_args()

    _, switchable_processor = _build_node(options)

    _instantiate(options.restore_from, options.checkpoint_dir)
    _run_loop(
        options.checkpoint_dir,
        switchable_processor=switchable_processor,
        switch_start_cpu_type=options.switch_start_cpu_type,
        switch_next_cpu_type=options.switch_next_cpu_type,
    )


if __name__ == "__m5_main__":
    main()
