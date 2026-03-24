# Copyright (c) 2025
# All rights reserved.
#
# Example configuration for a distributed full-system x86 run (dist-gem5).
# Mirrors configs/example/arm/fs_bigLITTLE.py but targets x86 and avoids
# make* helpers.

import os
import sys

import m5
import m5.util
from m5.objects import *

m5.util.addToPath("../../")

import devices_x86
from common import FSConfig
from common.Caches import IOCache

from gem5.simulate.exit_event import ExitEvent

default_mem_size = "2GiB"


def _to_ticks(value):
    return m5.ticks.fromSeconds(m5.util.convert.anyToLatency(value))


def _using_pdes(root):
    for obj in root.descendants():
        if (
            not m5.proxy.isproxy(obj.eventq_index)
            and obj.eventq_index != root.eventq_index
        ):
            return True
    return False


def addOptions(parser):
    parser.add_argument(
        "--kernel", type=str, required=True, help="Linux kernel"
    )
    parser.add_argument(
        "--disk",
        action="append",
        type=str,
        default=[],
        help="Disks to instantiate (CowDiskImage)",
    )
    parser.add_argument(
        "--bootscript", type=str, default="", help="Linux bootscript"
    )
    parser.add_argument(
        "--cpu-type",
        type=str,
        choices=list(cpu_types.keys()),
        default="timing",
        help="CPU simulation mode",
    )
    parser.add_argument(
        "--big-cpus",
        type=int,
        default=1,
        help="Number of big CPUs to instantiate",
    )
    parser.add_argument(
        "--little-cpus",
        type=int,
        default=1,
        help="Number of little CPUs to instantiate",
    )
    parser.add_argument(
        "--big-cpu-clock",
        type=str,
        default="2GHz",
        help="Big CPU clock frequency",
    )
    parser.add_argument(
        "--little-cpu-clock",
        type=str,
        default="1GHz",
        help="Little CPU clock frequency",
    )
    parser.add_argument(
        "--mem-size", type=str, default=default_mem_size, help="Memory size"
    )
    parser.add_argument(
        "--kernel-cmd", type=str, default=None, help="Extra kernel args"
    )
    parser.add_argument(
        "--root",
        type=str,
        default="/dev/sda2",
        help="Specify the kernel CLI root= argument",
    )
    parser.add_argument(
        "--sim-quantum",
        type=str,
        default="1ms",
        help="Simulation quantum for parallel simulation",
    )
    parser.add_argument(
        "--restore-from",
        type=str,
        default=None,
        help="Restore from checkpoint",
    )
    parser.add_argument(
        "--caches",
        action="store_true",
        default=False,
        help="Instantiate caches (not implemented here)",
    )
    parser.add_argument(
        "--last-cache-level",
        type=int,
        default=2,
        help="Last level of caches (ignored)",
    )
    return parser


def createSystem(
    caches,
    kernel,
    bootscript,
    disks=None,
    mem_size=default_mem_size,
    total_cpus=1,
):
    disks = disks or []

    sys = m5.objects.System()
    sys.m5ops_base = 0xFFFF0000
    sys.readfile = bootscript
    sys.mem_ranges = [AddrRange(mem_size)]
    sys.workload = X86FsLinux()
    sys.workload.object_file = kernel
    # Provide default voltage/clock domains so bridges inherit a valid clk_domain.
    sys.voltage_domain = VoltageDomain(voltage="1.0V")
    sys.clk_domain = SrcClockDomain(
        clock="1GHz", voltage_domain=sys.voltage_domain
    )

    # Populate e820 memory map (mirrors FSConfig.makeX86System logic for 64-bit).
    entries = [
        X86E820Entry(addr=0x00000000, size="639KiB", range_type=1),
        X86E820Entry(addr=0x0009FC00, size="385KiB", range_type=2),
        X86E820Entry(
            addr=0x00100000,
            size="%dB" % (sys.mem_ranges[0].size() - 0x100000),
            range_type=1,
        ),
    ]

    if len(sys.mem_ranges) == 1:
        entries.append(
            X86E820Entry(
                addr=sys.mem_ranges[0].size(),
                size="%dB" % (0xC0000000 - sys.mem_ranges[0].size()),
                range_type=2,
            )
        )

    entries.append(X86E820Entry(addr=0xFFFF0000, size="64KiB", range_type=2))

    if len(sys.mem_ranges) == 2:
        entries.append(
            X86E820Entry(
                addr=0x100000000,
                size="%dB" % (sys.mem_ranges[1].size()),
                range_type=1,
            )
        )

    sys.workload.e820_table.entries = entries

    # Use the shared MemBus helper defined in common/FSConfig (mirrors ARM flow)
    sys.membus = FSConfig.MemBus()
    sys.membus.snoop_filter = NULL  # No snoop filter on membus
    sys.iobus = IOXBar()
    sys.bridge = Bridge(delay="50ns")
    sys.bridge.mem_side_port = sys.iobus.cpu_side_ports
    sys.bridge.cpu_side_port = sys.membus.mem_side_ports
    # Allow bridge to pass through PCI/IO/APIC regions (mirrors FSConfig.connectX86ClassicSystem).
    io_base = 0x8000000000000000
    pci_cfg_base = 0xC000000000000000
    intr_base = 0xA000000000000000
    apic_size = 1 << 12
    sys.bridge.ranges = [
        AddrRange(0xC0000000, 0xFFFF0000),
        AddrRange(io_base, intr_base - 1),
        AddrRange(pci_cfg_base, Addr.max),
    ]

    # DMA path from IO bus to memory bus.
    # Match the classic FS pattern: IOCache for cached systems, iobridge for
    # non-cached systems.
    if caches:
        sys.iocache = IOCache(addr_ranges=sys.mem_ranges)
        sys.iocache.cpu_side = sys.iobus.mem_side_ports
        sys.iocache.mem_side = sys.membus.cpu_side_ports
    else:
        sys.iobridge = Bridge(delay="50ns", ranges=sys.mem_ranges)
        sys.iobridge.cpu_side_port = sys.iobus.mem_side_ports
        sys.iobridge.mem_side_port = sys.membus.cpu_side_ports

    # APIC bridge for local APIC ranges
    sys.apicbridge = Bridge(delay="50ns")
    sys.apicbridge.cpu_side_port = sys.iobus.mem_side_ports
    sys.apicbridge.mem_side_port = sys.membus.cpu_side_ports
    sys.apicbridge.ranges = [
        AddrRange(intr_base, intr_base + total_cpus * apic_size - 1)
    ]

    sys.pc = Pc()
    # Route IDE DMA directly to the memory bus and tell attachIO not to override.
    # sys.pc.south_bridge.ide.dma = sys.membus.cpu_side_ports
    # sys.pc.attachIO(sys.iobus, dma_ports=[sys.pc.south_bridge.ide.dma])
    # Single attach path: never reconnect ide.dma later in build().
    # if ide_dma_direct:
    #   sys.pc.south_bridge.ide.dma = sys.membus.cpu_side_ports
    #   sys.pc.attachIO(sys.iobus, dma_ports=[sys.pc.south_bridge.ide.dma])
    # else:
    #    sys.pc.attachIO(sys.iobus)
    # sys._dma_ports = [sys.pc.south_bridge.ide.dma]
    # sys.pc.attachIO(sys.iobus, dma_ports=sys._dma_ports)
    sys.pc.attachIO(sys.iobus)

    sys.system_port = sys.membus.cpu_side_ports

    # Populate MP table + ACPI MADT entries so Linux sees CPUs/APIC/ISA routing.
    base_entries = []
    ext_entries = []
    madt_records = []
    for i in range(total_cpus):
        base_entries.append(
            X86IntelMPProcessor(
                local_apic_id=i,
                local_apic_version=0x14,
                enable=True,
                bootstrap=(i == 0),
            )
        )
        madt_records.append(
            X86ACPIMadtLAPIC(acpi_processor_id=i, apic_id=i, flags=1)
        )

    io_apic = X86IntelMPIOAPIC(
        id=total_cpus, version=0x11, enable=True, address=0xFEC00000
    )
    sys.pc.south_bridge.io_apic.apic_id = io_apic.id
    base_entries.append(io_apic)
    madt_records.append(
        X86ACPIMadtIOAPIC(id=io_apic.id, address=io_apic.address, int_base=0)
    )

    pci_bus = X86IntelMPBus(bus_id=0, bus_type="PCI   ")
    isa_bus = X86IntelMPBus(bus_id=1, bus_type="ISA   ")
    base_entries += [pci_bus, isa_bus]
    ext_entries.append(
        X86IntelMPBusHierarchy(bus_id=1, subtractive_decode=True, parent_bus=0)
    )

    # --- INTERRUPCIÓN DISCO IDE (Slot 4) ---
    pci_dev4_inta = X86IntelMPIOIntAssignment(
        interrupt_type="INT",
        polarity="ConformPolarity",
        trigger="ConformTrigger",
        source_bus_id=0,
        source_bus_irq=(4 << 2) | 0,  # Slot 4, Pin A
        dest_io_apic_id=io_apic.id,
        dest_io_apic_intin=16,  # Pin 16 del IOAPIC
    )
    base_entries.append(pci_dev4_inta)
    madt_records.append(
        X86ACPIMadtIntSourceOverride(
            bus_source=pci_dev4_inta.source_bus_id,
            irq_source=pci_dev4_inta.source_bus_irq,
            sys_int=pci_dev4_inta.dest_io_apic_intin,
            flags=0,
        )
    )

    # --- INTERRUPCIÓN RED e1000 (Slot 10) ---
    # Nic real en 00:0a.0; mapeamos INT A del slot 10 al pin 17 del IOAPIC.
    pci_dev10_inta = X86IntelMPIOIntAssignment(
        interrupt_type="INT",
        polarity="ActiveHigh",
        trigger="EdgeTrigger",
        source_bus_id=0,
        source_bus_irq=(10 << 2) | 0,  # Slot 10 (0x0a), Pin A
        dest_io_apic_id=io_apic.id,
        dest_io_apic_intin=17,  # Pin 17 del IOAPIC
    )
    base_entries.append(pci_dev10_inta)

    # IMPORTANTE: No añadas records de 'IntSourceOverride' para estos dos.
    def assignISAInt(irq, apic_pin):
        base_entries.append(
            X86IntelMPIOIntAssignment(
                interrupt_type="ExtInt",
                polarity="ConformPolarity",
                trigger="ConformTrigger",
                source_bus_id=1,
                source_bus_irq=irq,
                dest_io_apic_id=io_apic.id,
                dest_io_apic_intin=0,
            )
        )
        base_entries.append(
            X86IntelMPIOIntAssignment(
                interrupt_type="INT",
                polarity="ConformPolarity",
                trigger="ConformTrigger",
                source_bus_id=1,
                source_bus_irq=irq,
                dest_io_apic_id=io_apic.id,
                dest_io_apic_intin=apic_pin,
            )
        )
        madt_records.append(
            X86ACPIMadtIntSourceOverride(
                bus_source=1, irq_source=irq, sys_int=apic_pin, flags=0
            )
        )

    assignISAInt(0, 2)
    assignISAInt(1, 1)
    for i in range(3, 15):
        assignISAInt(i, i)

    sys.workload.intel_mp_table.base_entries = base_entries
    sys.workload.intel_mp_table.ext_entries = ext_entries

    madt = X86ACPIMadt(
        local_apic_address=0, records=madt_records, oem_id="madt"
    )
    acpi = sys.workload.acpi_description_table_pointer
    acpi.rsdt.entries.append(madt)
    acpi.xsdt.entries.append(madt)
    acpi.oem_id = "gem5"
    acpi.rsdt.oem_id = "gem5"
    acpi.xsdt.oem_id = "gem5"

    # Force UART output into a file (system.terminal). Listener ports stay
    # disabled via --listener-mode=off in the launcher, avoiding socket use.
    sys.terminal = Terminal(outfile="file")
    sys.pc.com_1.device = sys.terminal

    # Disks
    if disks:
        sys.pc.south_bridge.ide.disks = FSConfig.makeCowDisks(
            [os.path.expanduser(p) for p in disks]
        )

    # Memory controllers
    # sys.mem_ctrls = [
    #    SimpleMemory(range=r, port=sys.membus.mem_side_ports)
    #    for r in sys.mem_ranges
    # ]

    # Memory controllers: selectable DDR3 or DDR4 via --dram-type
    dram_choice = "DDR3"
    if dram_choice == "DDR3":
        mem_intf_cls = DDR3_1600_8x8
    else:
        mem_intf_cls = DDR4_2400_8x8

    # Instantiate DRAM interface, create controller, set range and port.
    mem_ctrls = []
    for r in sys.mem_ranges:
        intf = mem_intf_cls()
        intf.range = AddrRange(r.start, size=r.size())
        mem_ctrl = intf.controller()
        mem_ctrl.port = sys.membus.mem_side_ports
        mem_ctrls.append(mem_ctrl)

    sys.mem_ctrls = mem_ctrls

    return sys


class X86CpuCluster:
    def __init__(
        self,
        system,
        num_cpus,
        cpu_clock,
        cluster_cls,
        use_caches=True,
        last_cache_level=1,
    ):
        # cluster_cls is a CpuCluster SimObject from devices_x86.py
        self.cluster = cluster_cls(system, num_cpus, cpu_clock)
        # Register as child of system with a unique name to satisfy SimObject parenting
        system.add_child(f"cluster_{id(self.cluster)}", self.cluster)

        # Connect memory side (only add private L1/L2 when requested)
        if use_caches:
            self.cluster.addL1()
            if last_cache_level == 2:
                self.cluster.addL2(self.cluster.clk_domain)
        self.cluster.connectMemSide(system.membus)

        # Hook up interrupt controller ports for each CPU
        for cpu in self.cluster.cpus:
            cpu.createInterruptController()
            cpu.interrupts[0].pio = system.membus.mem_side_ports
            cpu.interrupts[0].int_responder = system.membus.mem_side_ports
            cpu.interrupts[0].int_requestor = system.membus.cpu_side_ports

    @property
    def cpus(self):
        return list(self.cluster.cpus)

    def memory_mode(self):
        return self.cluster.memory_mode()

    def require_caches(self):
        return self.cluster.require_caches()

    def connect_mem_side(self, bus):
        self.cluster.connectMemSide(bus)


cpu_types = {
    "timing": (devices_x86.LittleCluster, devices_x86.LittleCluster),
    "o3": (devices_x86.BigCluster, devices_x86.LittleCluster),
    "atomic": (devices_x86.AtomicCluster, devices_x86.AtomicCluster),
}

if devices_x86.have_kvm:
    cpu_types["kvm"] = (devices_x86.KvmCluster, devices_x86.KvmCluster)


def build(options):
    m5.ticks.fixGlobalFrequency()

    kernel_cmd = [
        "earlyprintk=ttyS0",
        "console=ttyS0",
        f"root={options.root}",
        "rw",
    ]

    root = Root(full_system=True)

    disks = [] if len(options.disk) == 0 else options.disk
    total_cpus = options.big_cpus + options.little_cpus
    if total_cpus == 0:
        m5.util.panic("Empty CPU clusters")

    system = createSystem(
        options.caches,
        options.kernel,
        options.bootscript,
        disks=disks,
        mem_size=options.mem_size,
        total_cpus=total_cpus,
    )

    if options.cpu_type == "kvm":
        if not devices_x86.have_kvm:
            m5.util.panic("KVM support not available in this build of gem5")
        if not "BaseKvmCPU" in globals():
            m5.util.panic("KVM support not available on this host")
        system.kvm_vm = KvmVM()
        # kernel_cmd.append("random.trust_cpu=on noapic")
        kernel_cmd.append("clocksource=tsc tsc=reliable no_timer_check")
        kernel_cmd.append("lpj=3274752")

    root.system = system
    if options.kernel_cmd:
        system.workload.command_line = " ".join(
            kernel_cmd + [options.kernel_cmd]
        )
    else:
        system.workload.command_line = " ".join(kernel_cmd)

    big_model, little_model = cpu_types[options.cpu_type]

    all_cpus = []
    big_cluster = None
    little_cluster = None

    if options.big_cpus > 0:
        big_cluster = X86CpuCluster(
            system,
            options.big_cpus,
            options.big_cpu_clock,
            big_model,
            use_caches=options.caches,
            last_cache_level=options.last_cache_level,
        )
        system.mem_mode = big_cluster.memory_mode()
        all_cpus += big_cluster.cpus

    if options.little_cpus > 0:
        little_cluster = X86CpuCluster(
            system,
            options.little_cpus,
            options.little_cpu_clock,
            little_model,
            use_caches=options.caches,
            last_cache_level=options.last_cache_level,
        )
        system.mem_mode = little_cluster.memory_mode()
        all_cpus += little_cluster.cpus

    if (
        big_cluster
        and little_cluster
        and big_cluster.memory_mode() != little_cluster.memory_mode()
    ):
        m5.util.panic("Memory mode mismatch among CPU clusters")

    # Para KVM con múltiples vCPUs, pon cada CPU en su propia event queue
    # para que cada hilo KVM del host tenga su propio scheduler. Esto debe
    # hacerse después de crear caches/hijos para no heredar el eventq del CPU.
    if options.cpu_type == "kvm" and len(all_cpus) > 1:
        device_eq = 0
        first_cpu_eq = 1
        for idx, cpu in enumerate(all_cpus):
            for obj in cpu.descendants():
                obj.eventq_index = device_eq
            cpu.eventq_index = first_cpu_eq + idx

    # CPUs are children of their clusters; no need to reparent into system.cpu
    # system_port already wired by connectX86ClassicSystem

    if options.caches:
        m5.util.inform("x86: cache hierarchy enabled")
    else:
        m5.util.inform("x86: running without private L1/L2 caches")

    return root


def instantiate(options, checkpoint_dir=None):
    root = Root.getInstance()
    if root and _using_pdes(root):
        m5.util.inform(
            "Running in PDES mode with a %s simulation quantum.",
            options.sim_quantum,
        )
        root.sim_quantum = _to_ticks(options.sim_quantum)

    if options.restore_from:
        if checkpoint_dir and not os.path.isabs(options.restore_from):
            cpt = os.path.join(checkpoint_dir, options.restore_from)
        else:
            cpt = options.restore_from
        m5.util.inform("Restoring from checkpoint %s", cpt)
        m5.instantiate(cpt)
    else:
        m5.instantiate()


def run(checkpoint_dir=m5.options.outdir):
    while True:
        event = m5.simulate()
        exit_msg = event.getCause()
        exit_hyper_id = event.getHypercallId()
        if exit_msg == "checkpoint":
            print("Dropping checkpoint at tick %d" % m5.curTick())
            cpt_dir = os.path.join(checkpoint_dir, "cpt.%d" % m5.curTick())
            m5.checkpoint(cpt_dir)
            print("Checkpoint done.")
        if exit_hyper_id == 5 or "workend" in exit_msg:
            print("Workload ended @ ", m5.curTick())
            continue
        else:
            print(exit_msg, " @ ", m5.curTick())
            break
    sys.exit(event.getCode())
