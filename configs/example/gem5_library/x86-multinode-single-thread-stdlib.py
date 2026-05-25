#!/usr/bin/env python3
# Copyright (c) 2026
# All rights reserved.
#
# Multi-node x86 FS configuration in a single gem5 process/thread.
# Nodes are connected via EtherSwitch + EtherLink (no dist-gem5).

import argparse
import os
import sys
from typing import List, Optional, Tuple

import m5
from m5.objects import (
    AddrRange,
    EtherDump,
    EtherLink,
    EtherSwitch,
    KvmVM,
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


try:
    from m5.objects import (
        IGbE_e1000,
        X86IntelMPIOIntAssignment,
    )
except ImportError:
    # Keep import failure explicit and actionable for custom builds.
    raise


class X86EthernetBoard(X86Board):
    """X86 board extended with an E1000 NIC for in-process multi-node runs."""

    def __init__(
        self,
        clk_freq,
        processor,
        memory,
        cache_hierarchy,
        nic_pci_dev: int = 5,
        nic_interrupt_line: int = 11,
    ):
        super().__init__(
            clk_freq=clk_freq,
            processor=processor,
            memory=memory,
            cache_hierarchy=cache_hierarchy,
        )
        self._nic_pci_bus = 0
        self._nic_pci_dev = nic_pci_dev
        self._nic_pci_func = 0
        self._nic_interrupt_pin = 1
        self._nic_interrupt_line = nic_interrupt_line

    def _setup_board(self) -> None:
        super()._setup_board()

        # Mirror the lower MMIO bridge extension used by x86 dist board.
        if self.is_fullsystem() and hasattr(self, "bridge"):
            low_pci_mmio_base = int(self.mem_ranges[0].size())
            low_pci_mmio_limit = 0xC0000000
            if low_pci_mmio_base < low_pci_mmio_limit:
                bridge_ranges = list(self.bridge.ranges)
                bridge_ranges.append(
                    AddrRange(low_pci_mmio_base, low_pci_mmio_limit - 1)
                )
                self.bridge.ranges = bridge_ranges

        if self.is_fullsystem():
            self._setup_ethernet()

    def _setup_ethernet(self) -> None:
        self.ethernet = IGbE_e1000()
        self.ethernet.host = self.pc.pci_host
        self.ethernet.pci_bus = self._nic_pci_bus
        self.ethernet.pci_dev = self._nic_pci_dev
        self.ethernet.pci_func = self._nic_pci_func
        self.ethernet.InterruptLine = self._nic_interrupt_line
        self.ethernet.InterruptPin = self._nic_interrupt_pin
        self.ethernet.pio = self.iobus.mem_side_ports
        self.ethernet.dma = self.iobus.cpu_side_ports

        nic_int_assignment = X86IntelMPIOIntAssignment(
            interrupt_type="INT",
            polarity="ActiveLow",
            trigger="LevelTrigger",
            source_bus_id=1,
            source_bus_irq=(self._nic_pci_dev << 2) | 0,
            dest_io_apic_id=self.get_processor().get_num_cores(),
            dest_io_apic_intin=self._nic_interrupt_line,
        )
        self.workload.intel_mp_table.add_child(
            f"pci_dev{self._nic_pci_dev}_inta", nic_int_assignment
        )
        base_entries = list(self.workload.intel_mp_table.base_entries)
        base_entries.append(nic_int_assignment)
        self.workload.intel_mp_table.base_entries = base_entries


def _cpu_type_from_str(cpu_type: str) -> CPUTypes:
    cpu_map = {
        "atomic": CPUTypes.ATOMIC,
        "timing": CPUTypes.TIMING,
        "o3": CPUTypes.O3,
        "kvm": CPUTypes.KVM,
    }
    return cpu_map[cpu_type]


def _add_args(parser: argparse.ArgumentParser) -> None:
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
        "--num-cores", type=int, default=2, help="Number of CPU cores per node"
    )
    parser.add_argument(
        "--num-nodes", type=int, default=2, help="Number of x86 nodes"
    )
    parser.add_argument(
        "--clk-freq", type=str, default="3GHz", help="Board clock frequency"
    )
    parser.add_argument(
        "--mem-size", type=str, default="2GiB", help="Memory size per node (<=3GiB)"
    )
    parser.add_argument(
        "--ethernet-linkspeed", type=str, default="10Gbps", help="Link speed"
    )
    parser.add_argument(
        "--ethernet-linkdelay", type=str, default="10us", help="Link delay"
    )
    parser.add_argument(
        "--etherdump-prefix",
        type=str,
        default="",
        help="If set, emit per-link PCAPs using this prefix",
    )
    parser.add_argument(
        "--continue-after-panic",
        action="store_true",
        help="Set on_panic/on_oops to dump dmesg and continue",
    )
    parser.add_argument(
        "--allow-kvm",
        action="store_true",
        help=(
            "Allow KVM CPU model. Disabled by default to preserve strict "
            "single-threaded simulation behavior."
        ),
    )
    parser.add_argument(
        "--kvm-eventq-strategy",
        type=str,
        choices=["shared", "separate"],
        default="separate",
        help=(
            "Event queue strategy for KVM start mode. "
            "'separate' assigns one queue per KVM core and enables "
            "sim_quantum; 'shared' keeps all CPU-side objects on eventq 0."
        ),
    )
    parser.add_argument(
        "--sim-quantum",
        type=str,
        default="1ms",
        help=(
            "Simulation quantum used when KVM starts on separate event queues."
        ),
    )
    parser.add_argument(
        "--switch-on-workbegin",
        action="store_true",
        help=(
            "Use a switchable processor and switch CPU model on first "
            "workbegin exit event."
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


def _validate_args(options: argparse.Namespace) -> None:
    if not options.kernel:
        raise ValueError("--kernel is required")
    if not options.disk:
        raise ValueError("--disk is required")
    if options.num_nodes < 2:
        raise ValueError("--num-nodes must be >= 2 for multi-node runs")

    start_cpu_type = (
        options.switch_start_cpu_type
        if options.switch_on_workbegin and options.switch_start_cpu_type
        else options.cpu_type
    )

    if options.switch_on_workbegin and options.switch_next_cpu_type is None:
        raise ValueError(
            "--switch-next-cpu-type is required when "
            "--switch-on-workbegin is enabled"
        )

    if (
        not options.allow_kvm
        and (
            options.cpu_type == "kvm"
            or start_cpu_type == "kvm"
            or options.switch_next_cpu_type == "kvm"
        )
    ):
        raise ValueError(
            "KVM is disabled in this single-threaded config. "
            "Use atomic/timing/o3 or pass --allow-kvm."
        )


def _node_readfile(options: argparse.Namespace, node_id: int) -> Optional[str]:
    if not options.readfile:
        return None

    with open(options.readfile, "r", encoding="utf-8") as src:
        payload = src.read()

    readfile_dir = os.path.join(m5.options.outdir, "node_readfiles")
    os.makedirs(readfile_dir, exist_ok=True)
    node_readfile = os.path.join(readfile_dir, f"node{node_id}.rcS")

    with open(node_readfile, "w", encoding="utf-8") as dst:
        dst.write("#!/bin/bash\n")
        dst.write(
            f"export GEM5_DIST_RANK_OVERRIDE={node_id}\n"
        )
        dst.write(
            f"export GEM5_DIST_SIZE_OVERRIDE={options.num_nodes}\n"
        )
        dst.write(payload)

    return node_readfile


def _build_board(
    options: argparse.Namespace, node_id: int
) -> Tuple[X86EthernetBoard, Optional[SimpleSwitchableProcessor], str]:
    start_cpu_type = (
        options.switch_start_cpu_type
        if options.switch_on_workbegin and options.switch_start_cpu_type
        else options.cpu_type
    )

    switchable_processor: Optional[SimpleSwitchableProcessor] = None

    if options.switch_on_workbegin:
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

    board = X86EthernetBoard(
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
        readfile=_node_readfile(options, node_id),
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
        if not options.kernel_cmd or "lpj=" not in options.kernel_cmd:
            # Avoid long/failed delay-loop calibration paths on some
            # KVM+gem5 x86 boots with minimal timer references.
            board.append_kernel_arg("lpj=3274752")

    if options.continue_after_panic:
        board.workload.on_panic = "DumpDmesgAndContinue"
        board.workload.on_oops = "DumpDmesgAndContinue"

    return board, switchable_processor, start_cpu_type


def _build_multinode_root(
    options: argparse.Namespace,
) -> Tuple[Root, List[SimpleSwitchableProcessor], Optional[str], Optional[str]]:
    boards: List[X86EthernetBoard] = []
    switchable_processors: List[SimpleSwitchableProcessor] = []
    switch_start_cpu_type: Optional[str] = None
    switch_next_cpu_type: Optional[str] = options.switch_next_cpu_type
    start_cpu_is_kvm = (
        (
            options.switch_on_workbegin
            and (
                options.switch_start_cpu_type == "kvm"
                or (
                    options.switch_start_cpu_type is None
                    and options.cpu_type == "kvm"
                )
            )
        )
        or (not options.switch_on_workbegin and options.cpu_type == "kvm")
    )
    shared_kvm_vm = KvmVM() if start_cpu_is_kvm else None

    all_kvm_cores = []

    for node_id in range(options.num_nodes):
        board, maybe_switchable, start_cpu_type = _build_board(options, node_id)

        if shared_kvm_vm is not None:
            board.kvm_vm = shared_kvm_vm

        # Equivalent to AbstractBoard._pre_instantiate() stage 1.
        board._connect_things()

        if start_cpu_is_kvm:
            all_kvm_cores.extend(board.get_processor().get_cores())

        boards.append(board)
        if maybe_switchable is not None:
            switchable_processors.append(maybe_switchable)
        if switch_start_cpu_type is None:
            switch_start_cpu_type = start_cpu_type

    root = Root(full_system=True)
    switch = EtherSwitch()
    root.switch = switch

    if start_cpu_is_kvm:
        if options.kvm_eventq_strategy == "shared":
            warn(
                "KVM start mode: using shared eventq strategy "
                "(all CPU-side objects on eventq 0)."
            )
            for core in all_kvm_cores:
                simobj = core.get_simobject()
                for obj in simobj.descendants():
                    obj.eventq_index = 0
                simobj.eventq_index = 0
        else:
            warn(
                "KVM start mode: using separate eventq strategy "
                "(one queue per KVM core) with sim_quantum=%s.",
                options.sim_quantum,
            )
            device_eq = 0
            first_cpu_eq = 1
            for idx, core in enumerate(all_kvm_cores):
                simobj = core.get_simobject()
                for obj in simobj.descendants():
                    obj.eventq_index = device_eq
                simobj.eventq_index = first_cpu_eq + idx

            # Enable PDES quantum coordination across event queues.
            root.sim_quantum = m5.ticks.fromSeconds(
                m5.util.convert.anyToLatency(options.sim_quantum)
            )

    for node_id, board in enumerate(boards):
        setattr(root, f"node{node_id}", board)

        link = EtherLink(
            speed=options.ethernet_linkspeed,
            delay=options.ethernet_linkdelay,
        )
        link.int0 = board.ethernet.interface
        link.int1 = switch.interface[node_id]

        if options.etherdump_prefix:
            dump = EtherDump(file=f"{options.etherdump_prefix}.node{node_id}.pcap")
            setattr(root, f"node{node_id}_dump", dump)
            link.dump = dump

        setattr(root, f"node{node_id}_link", link)

        # Equivalent to AbstractBoard._pre_instantiate() stage 3.
        board.get_processor()._pre_instantiate(root)
        board.get_memory()._pre_instantiate(root)
        if board.get_cache_hierarchy():
            board.get_cache_hierarchy()._pre_instantiate(root)

    return (
        root,
        switchable_processors,
        switch_start_cpu_type,
        switch_next_cpu_type,
    )


def _run_loop(
    checkpoint_dir: str,
    switchable_processors: Optional[List[SimpleSwitchableProcessor]] = None,
    switch_start_cpu_type: Optional[str] = None,
    switch_next_cpu_type: Optional[str] = None,
) -> None:
    switched_core_model = False
    workbegin_events = 0

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
            switchable_processors
            and not switched_core_model
            and "workbegin" in exit_msg.lower()
        ):
            workbegin_events += 1
            print(
                "Switching CPU model across",
                len(switchable_processors),
                "nodes:",
                switch_start_cpu_type,
                "->",
                switch_next_cpu_type,
            )
            for proc in switchable_processors:
                proc.switch()
            switched_core_model = True
            continue

        if "workbegin" in exit_msg.lower():
            workbegin_events += 1
            print(
                "Ignoring additional workbegin event",
                workbegin_events,
                "@",
                m5.curTick(),
            )
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "x86 multi-node full-system configuration in one gem5 process "
            "(single-threaded, non-dist)"
        )
    )
    _add_args(parser)
    options = parser.parse_args()
    _validate_args(options)

    # Required before any m5.ticks.fromSeconds(...) conversion.
    m5.ticks.fixGlobalFrequency()

    (
        _root,
        switchable_processors,
        switch_start_cpu_type,
        switch_next_cpu_type,
    ) = _build_multinode_root(options)
    _instantiate(options.restore_from, options.checkpoint_dir)
    _run_loop(
        options.checkpoint_dir,
        switchable_processors=switchable_processors,
        switch_start_cpu_type=switch_start_cpu_type,
        switch_next_cpu_type=switch_next_cpu_type,
    )


if __name__ == "__m5_main__":
    main()
