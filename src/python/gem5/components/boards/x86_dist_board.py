# Copyright (c) 2026
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are
# met: redistributions of source code must retain the above copyright
# notice, this list of conditions and the following disclaimer;
# redistributions in binary form must reproduce the above copyright
# notice, this list of conditions and the following disclaimer in the
# documentation and/or other materials provided with the distribution;
# neither the name of the copyright holders nor the names of its
# contributors may be used to endorse or promote products derived from
# this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
# THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from typing import (
    List,
    Optional,
)

from m5.objects import (
    AddrRange,
    DistEtherLink,
    EtherDump,
    EtherLink,
    EtherSwitch,
    IGbE_e1000,
    X86IntelMPIOIntAssignment,
)

from ...utils.override import overrides
from ..cachehierarchies.abstract_cache_hierarchy import AbstractCacheHierarchy
from ..memory.abstract_memory_system import AbstractMemorySystem
from ..processors.abstract_processor import AbstractProcessor
from .x86_board import X86Board


class X86DistBoard(X86Board):
    """
    X86 board with dist-gem5 networking support.

    This class extends `X86Board` with:
      * an E1000 NIC attached to the x86 PCI host,
      * a `DistEtherLink` connected to that NIC interface,
      * Intel MP table routing for NIC interrupts.
    """

    def __init__(
        self,
        clk_freq: str,
        processor: AbstractProcessor,
        memory: AbstractMemorySystem,
        cache_hierarchy: AbstractCacheHierarchy,
        dist_rank: int = 0,
        dist_size: int = 1,
        dist_server_name: str = "127.0.0.1",
        dist_server_port: int = 2200,
        dist_sync_repeat: str = "0us",
        dist_sync_start: str = "1000000000000t",
        ethernet_linkspeed: str = "10Gbps",
        ethernet_linkdelay: str = "10us",
        etherdump: Optional[str] = None,
    ) -> None:
        super().__init__(
            clk_freq=clk_freq,
            processor=processor,
            memory=memory,
            cache_hierarchy=cache_hierarchy,
        )

        if dist_size <= 0:
            raise ValueError("`dist_size` must be greater than 0.")
        if dist_rank < 0 or dist_rank >= dist_size:
            raise ValueError(
                "`dist_rank` must be in the range [0, dist_size)."
            )

        self._dist_rank = dist_rank
        self._dist_size = dist_size
        self._dist_server_name = dist_server_name
        self._dist_server_port = dist_server_port
        self._dist_sync_repeat = dist_sync_repeat
        self._dist_sync_start = dist_sync_start
        self._ethernet_linkspeed = ethernet_linkspeed
        self._ethernet_linkdelay = ethernet_linkdelay
        self._etherdump = etherdump

        # Keep this aligned with the distributed x86 example scripts.
        self._nic_pci_bus = 0
        # Keep NIC on a low PCI device number. In our Linux+MP-table setup,
        # high slot numbers (e.g., 10) have shown unreliable IRQ routing.
        self._nic_pci_dev = 5
        self._nic_pci_func = 0
        self._nic_interrupt_pin = 1
        # Keep aligned with the known-working non-stdlib dist x86 setup
        # (configs/example/x86/dist_x86.py + fs_x86_dist.py).
        # The MP-table entry there uses IOAPIC pin 17 for the E1000 NIC.
        # Keep that pin here as well.
        self._nic_interrupt_line = 17

    @overrides(X86Board)
    def _setup_board(self) -> None:
        super()._setup_board()

        # X86Board bridges CPU->IO only for 0xC0000000.. by default.
        # With 2GiB RAM, Linux may place PCI MMIO BARs at 0x80000000.
        # If that range is not bridged, MMIO reads from the NIC BAR return
        # zeros (seen as EEPROM checksum/MAC errors in e1000).
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
            self._setup_dist_ethernet()

    def _setup_dist_ethernet(self) -> None:
        # Use e1000 model (82547), which is PCI (not PCIe) and matches this
        # board's legacy PCI host topology.
        self.ethernet = IGbE_e1000()
        self.ethernet.host = self.pc.pci_host
        self.ethernet.pci_bus = self._nic_pci_bus
        self.ethernet.pci_dev = self._nic_pci_dev
        self.ethernet.pci_func = self._nic_pci_func
        self.ethernet.InterruptLine = self._nic_interrupt_line
        self.ethernet.InterruptPin = self._nic_interrupt_pin
        self.ethernet.pio = self.iobus.mem_side_ports
        self.ethernet.dma = self.iobus.cpu_side_ports

        self.etherlink = DistEtherLink(
            speed=self._ethernet_linkspeed,
            delay=self._ethernet_linkdelay,
            dist_rank=self._dist_rank,
            dist_size=self._dist_size,
            server_name=self._dist_server_name,
            server_port=self._dist_server_port,
            sync_start=self._dist_sync_start,
            sync_repeat=self._dist_sync_repeat,
        )
        self.etherlink.int0 = self.ethernet.interface

        if self._etherdump:
            self.etherdump = EtherDump(file=self._etherdump)
            self.etherlink.dump = self.etherdump

        # Route NIC PCI INTA# to the selected interrupt line.
        #
        # For this x86 + KVM path, `ConformTrigger` may make Linux/KVM touch
        # Local APIC trigger-mode registers, which gem5 does not implement.
        # Match the known-working legacy x86 dist config and advertise the NIC
        # interrupt as edge-triggered/high-active.
        nic_int_assignment = X86IntelMPIOIntAssignment(
            interrupt_type="INT",
            polarity="ActiveHigh",
            trigger="EdgeTrigger",
            source_bus_id=0,
            source_bus_irq=(self._nic_pci_dev << 2) | 0,
            dest_io_apic_id=self.get_processor().get_num_cores(),
            dest_io_apic_intin=self._nic_interrupt_line,
        )
        # Make sure this SimObject is not orphaned when referenced from the
        # MP table vector parameter.
        self.workload.intel_mp_table.add_child(
            f"pci_dev{self._nic_pci_dev}_inta", nic_int_assignment
        )
        base_entries = list(self.workload.intel_mp_table.base_entries)
        base_entries.append(nic_int_assignment)
        self.workload.intel_mp_table.base_entries = base_entries

        # Keep inherited MADT/MP entries from X86Board unchanged and only add
        # the NIC MP-table routing entry above, matching fs_x86_dist behavior.


def build_dist_switch(
    dist_rank: int,
    dist_size: int,
    dist_server_name: str = "127.0.0.1",
    dist_server_port: int = 2200,
    dist_sync_repeat: str = "0us",
    dist_sync_start: str = "1000000000000t",
    ethernet_linkspeed: str = "10Gbps",
    ethernet_linkdelay: str = "10us",
) -> EtherSwitch:
    """Create an `EtherSwitch` configured for dist-gem5 links."""

    if dist_size <= 0:
        raise ValueError("`dist_size` must be greater than 0.")

    switch = EtherSwitch()
    switch.portlink = [
        DistEtherLink(
            speed=ethernet_linkspeed,
            delay=ethernet_linkdelay,
            dist_rank=dist_rank,
            dist_size=dist_size,
            server_name=dist_server_name,
            server_port=dist_server_port,
            sync_start=dist_sync_start,
            sync_repeat=dist_sync_repeat,
            is_switch=True,
            num_nodes=dist_size,
        )
        for _ in range(dist_size)
    ]

    for i, link in enumerate(switch.portlink):
        link.int0 = switch.interface[i]

    return switch


def _parse_nodes_per_switch(
    spec: Optional[str], num_leaf: int, num_nodes: int
) -> List[int]:
    if not spec:
        base = num_nodes // num_leaf
        extra = num_nodes % num_leaf
        return [base + (1 if i < extra else 0) for i in range(num_leaf)]

    vals = [int(x.strip()) for x in spec.split(",") if x.strip()]
    if len(vals) != num_leaf:
        raise ValueError(
            "`nodes_per_switch` must contain exactly "
            f"{num_leaf} values; got {len(vals)}."
        )
    if any(v <= 0 for v in vals):
        raise ValueError("`nodes_per_switch` values must all be > 0.")
    if sum(vals) != num_nodes:
        raise ValueError(
            "sum(nodes_per_switch) must equal dist_size "
            f"({num_nodes}), got {sum(vals)}."
        )

    return vals


def build_hierarchical_dist_switch(
    dist_rank: int,
    dist_size: int,
    dist_server_name: str = "127.0.0.1",
    dist_server_port: int = 2200,
    dist_sync_repeat: str = "0us",
    dist_sync_start: str = "1000000000000t",
    ethernet_linkspeed: str = "10Gbps",
    ethernet_linkdelay: str = "10us",
    num_leaf_switches: int = 1,
    nodes_per_switch: Optional[str] = None,
    trace_internal_links: bool = False,
    internal_trace_prefix: str = "hier_switch",
    internal_trace_maxlen: int = 256,
) -> EtherSwitch:
    """Create a hierarchical `EtherSwitch` topology for dist-gem5."""

    if dist_size <= 0:
        raise ValueError("`dist_size` must be greater than 0.")
    if num_leaf_switches < 1:
        raise ValueError("`num_leaf_switches` must be >= 1.")
    if num_leaf_switches > dist_size:
        raise ValueError("`num_leaf_switches` cannot exceed `dist_size`.")

    nodes_per_leaf = _parse_nodes_per_switch(
        nodes_per_switch, num_leaf_switches, dist_size
    )

    master = EtherSwitch()
    leaves = []
    uplinks = []
    node_links = []

    rank_base = 0

    if trace_internal_links:
        master.dump = EtherDump(
            file=f"{internal_trace_prefix}.master_switch.pcap",
            maxlen=internal_trace_maxlen,
        )

    for leaf_idx, leaf_nodes in enumerate(nodes_per_leaf):
        leaf = EtherSwitch()
        leaves.append(leaf)

        if trace_internal_links:
            leaf.dump = EtherDump(
                file=f"{internal_trace_prefix}.leaf{leaf_idx}.switch.pcap",
                maxlen=internal_trace_maxlen,
            )

        rank_start = rank_base
        rank_base += leaf_nodes

        uplink = EtherLink(
            speed=ethernet_linkspeed,
            delay=ethernet_linkdelay,
        )

        if trace_internal_links:
            uplink.dump = EtherDump(
                file=f"{internal_trace_prefix}.leaf{leaf_idx}.uplink.pcap",
                maxlen=internal_trace_maxlen,
            )

        uplink.int0 = leaf.interface[leaf_nodes]
        uplink.int1 = master.interface[leaf_idx]
        uplinks.append(uplink)

        for port_idx in range(leaf_nodes):
            link = DistEtherLink(
                speed=ethernet_linkspeed,
                delay=ethernet_linkdelay,
                dist_rank=dist_rank,
                dist_size=dist_size,
                server_name=dist_server_name,
                server_port=dist_server_port,
                sync_start=dist_sync_start,
                sync_repeat=dist_sync_repeat,
                is_switch=True,
                num_nodes=dist_size,
            )

            if trace_internal_links:
                link.dump = EtherDump(
                    file=(
                        f"{internal_trace_prefix}.leaf{leaf_idx}."
                        f"port{port_idx}.to_rank{rank_start + port_idx}.pcap"
                    ),
                    maxlen=internal_trace_maxlen,
                )

            link.int0 = leaf.interface[port_idx]
            node_links.append(link)

    master.leaf_switches = leaves
    master.uplinks = uplinks
    master.portlink = node_links

    return master
