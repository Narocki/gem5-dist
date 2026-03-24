#!/usr/bin/env python3
# Distributed x86 FS configuration modeled after arm/dist_bigLITTLE.py.

import argparse
import os

import fs_x86_dist as bL

import m5
from m5.objects import (
    DistEtherLink,
    EtherDump,
    IGbE_e1000,
    Root,
)

m5.util.addToPath("../../dist")
m5.util.addToPath("./")

import sw  # switch builder


def add_dist_options(parser):
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
        "--checkpoint-dir",
        type=str,
        default=m5.options.outdir,
        help="Directory to save/read checkpoints",
    )


def addEthernet(system, options):
    dev = IGbE_e1000()
    # Wire NIC onto IO bus; host must be set to the PCI host bridge
    dev.host = system.pc.pci_host
    dev.pci_bus = 0
    dev.pci_dev = 10
    dev.pci_func = 0
    # Align PCI INTx line with the MP table entry (slot 10 -> IOAPIC INTIN17).
    dev.InterruptLine = 17
    dev.InterruptPin = 1  # INTA#
    dev.pio = system.iobus.mem_side_ports
    if hasattr(dev, "dma"):
        # DMA debe ir al bus de memoria para que el dispositivo pueda acceder
        # a la RAM (el iobus solo expone MMIO/PIO y no enruta DMAs hacia la
        # memoria).
        dev.dma = system.iobus.cpu_side_ports
    system.ethernet = dev

    system.etherlink = DistEtherLink(
        speed=options.ethernet_linkspeed,
        delay=options.ethernet_linkdelay,
        dist_rank=options.dist_rank,
        dist_size=options.dist_size,
        server_name=options.dist_server_name,
        server_port=options.dist_server_port,
        sync_start=options.dist_sync_start,
        sync_repeat=options.dist_sync_repeat,
    )
    system.etherlink.int0 = m5.objects.Parent.system.ethernet.interface
    if options.etherdump:
        system.etherdump = EtherDump(file=options.etherdump)
        system.etherlink.dump = system.etherdump


def main():
    parser = argparse.ArgumentParser(
        description="Distributed x86 full-system configuration"
    )
    bL.addOptions(parser)
    add_dist_options(parser)
    options = parser.parse_args()

    if options.is_switch:
        root = Root(full_system=True, system=sw.build_switch(options))
    else:
        root = bL.build(options)
        addEthernet(root.system, options)

        # In distributed runs we often want to keep going long enough to
        # capture additional context when a kernel panic/oops is detected.
        root.system.workload.on_panic = "DumpDmesgAndContinue"
        root.system.workload.on_oops = "DumpDmesgAndContinue"

    bL.instantiate(options, checkpoint_dir=options.checkpoint_dir)
    bL.run(options.checkpoint_dir)


if __name__ == "__m5_main__":
    main()
