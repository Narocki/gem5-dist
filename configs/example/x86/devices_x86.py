# Copyright (c) 2025
# All rights reserved.
#
# System components used by the x86 dist big.LITTLE-like configuration

import m5
from m5.objects import *

m5.util.addToPath("../../")
from common import ObjectList
from common.Caches import *

have_kvm = "X86KvmCPU" in ObjectList.cpu_list.get_names()


class L1I(L1_ICache):
    tag_latency = 1
    data_latency = 1
    response_latency = 1
    mshrs = 4
    tgts_per_mshr = 8
    size = "48KiB"
    assoc = 3


class L1D(L1_DCache):
    tag_latency = 2
    data_latency = 2
    response_latency = 1
    mshrs = 16
    tgts_per_mshr = 16
    size = "32KiB"
    assoc = 2
    write_buffers = 16


class L2(L2Cache):
    tag_latency = 12
    data_latency = 12
    response_latency = 5
    mshrs = 32
    tgts_per_mshr = 8
    size = "1MiB"
    assoc = 16
    write_buffers = 8
    clusivity = "mostly_excl"


class L3(Cache):
    size = "8MiB"
    assoc = 16
    tag_latency = 20
    data_latency = 20
    response_latency = 20
    mshrs = 20
    tgts_per_mshr = 12
    clusivity = "mostly_excl"


class X86CpuCluster(CpuCluster):
    def __init__(
        self,
        system,
        num_cpus,
        cpu_clock,
        cpu_voltage,
        cpu_type,
        l1i_type,
        l1d_type,
        l2_type,
    ):
        super().__init__()
        self._cpu_type = cpu_type
        self._l1i_type = l1i_type
        self._l1d_type = l1d_type
        self._l2_type = l2_type

        assert num_cpus > 0

        self.voltage_domain = VoltageDomain(voltage=cpu_voltage)
        self.clk_domain = SrcClockDomain(
            clock=cpu_clock, voltage_domain=self.voltage_domain
        )

        self.generate_cpus(cpu_type, num_cpus)

    def addL1(self):
        for cpu in self.cpus:
            l1i = None if self._l1i_type is None else self._l1i_type()
            l1d = None if self._l1d_type is None else self._l1d_type()
            if l1i is None and l1d is None:
                continue

            try:
                cpu.addPrivateSplitL1Caches(l1i, l1d)
            except AttributeError:
                m5.util.warn(
                    "Skipping private L1 cache hookup for CPU model %s; "
                    "it does not expose split L1 cache parameters.",
                    cpu.__class__.__name__,
                )

    def addL2(self, clk_domain):
        if self._l2_type is None:
            return
        self.toL2Bus = L2XBar(width=64, clk_domain=clk_domain)
        self.toL2Bus.snoop_filter = NULL
        self.l2 = self._l2_type()
        for cpu in self.cpus:
            cpu.connectCachedPorts(self.toL2Bus.cpu_side_ports)
        self.toL2Bus.mem_side_ports = self.l2.cpu_side

    def connectMemSide(self, bus):
        try:
            self.l2.mem_side = bus.cpu_side_ports
        except AttributeError:
            for cpu in self.cpus:
                cpu.connectCachedPorts(bus.cpu_side_ports)


class BigCluster(X86CpuCluster):
    def __init__(self, system, num_cpus, cpu_clock, cpu_voltage="1.0V"):
        super().__init__(
            system,
            num_cpus,
            cpu_clock,
            cpu_voltage,
            cpu_type=ObjectList.cpu_list.get("DerivO3CPU"),
            l1i_type=L1I,
            l1d_type=L1D,
            l2_type=L2,
        )


class LittleCluster(X86CpuCluster):
    def __init__(self, system, num_cpus, cpu_clock, cpu_voltage="1.0V"):
        super().__init__(
            system,
            num_cpus,
            cpu_clock,
            cpu_voltage,
            cpu_type=ObjectList.cpu_list.get("TimingSimpleCPU"),
            l1i_type=L1I,
            l1d_type=L1D,
            l2_type=L2,
        )


class AtomicCluster(X86CpuCluster):
    def __init__(self, system, num_cpus, cpu_clock, cpu_voltage="1.0V"):
        super().__init__(
            system,
            num_cpus,
            cpu_clock,
            cpu_voltage,
            cpu_type=ObjectList.cpu_list.get("AtomicSimpleCPU"),
            l1i_type=None,
            l1d_type=None,
            l2_type=None,
        )

    def addL1(self):
        pass


class KvmCluster(X86CpuCluster):
    def __init__(
        self,
        system,
        num_cpus,
        cpu_clock,
        cpu_voltage="1.0V",
    ):
        super().__init__(
            system,
            num_cpus,
            cpu_clock,
            cpu_voltage,
            cpu_type=ObjectList.cpu_list.get("X86KvmCPU"),
            l1i_type=None,
            l1d_type=None,
            l2_type=None,
        )

    def addL1(self):
        pass
