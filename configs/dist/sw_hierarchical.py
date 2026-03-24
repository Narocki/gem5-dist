# Copyright (c) 2015 The University of Illinois Urbana Champaign
# Copyright (c) 2026
# All rights reserved
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

"""Hierarchical dist-gem5 switch topology.

Creates one master switch and N leaf switches. Each leaf switch connects:
- one uplink to the master switch
- several DistEtherLink ports to distributed gem5 nodes
"""

import argparse

from m5.objects import DistEtherLink, EtherDump, EtherLink, EtherSwitch, Root
from m5.util import addToPath, fatal

addToPath("../")

from common import Options
from common import Simulation


def parse_nodes_per_switch(spec, num_leaf, num_nodes):
    if not spec:
        base = num_nodes // num_leaf
        extra = num_nodes % num_leaf
        return [base + (1 if i < extra else 0) for i in range(num_leaf)]

    vals = [int(x.strip()) for x in spec.split(",") if x.strip()]
    if len(vals) != num_leaf:
        fatal(
            f"--nodes-per-switch must contain exactly {num_leaf} values; got {len(vals)}"
        )
    if any(v <= 0 for v in vals):
        fatal("--nodes-per-switch values must all be > 0")
    if sum(vals) != num_nodes:
        fatal(
            f"sum(nodes-per-switch) must equal dist-size ({num_nodes}), got {sum(vals)}"
        )
    return vals


def build_switch(args):
    num_leaf = args.num_leaf_switches
    if num_leaf < 1:
        fatal("--num-leaf-switches must be >= 1")
    if num_leaf > args.dist_size:
        fatal("--num-leaf-switches cannot exceed --dist-size")

    nodes_per_leaf = parse_nodes_per_switch(
        args.nodes_per_switch, num_leaf, args.dist_size
    )

    master = EtherSwitch()
    leaves = []
    uplinks = []
    node_links = []

    rank_base = 0

    # Configure master switch and its links to leaf switches
    if args.trace_internal_links:
        master.dump = EtherDump(
            file=f"{args.internal_trace_prefix}.master_switch.pcap",
            maxlen=args.internal_trace_maxlen,
        )

    # Configure leaf switches, their uplinks to the master, and their downlinks to dist gem5 nodes
    for leaf_idx, leaf_nodes in enumerate(nodes_per_leaf):
        leaf = EtherSwitch()
        leaves.append(leaf)

        # Optional internal trace for leaf switch
        if args.trace_internal_links:
            leaf.dump = EtherDump(
                file=f"{args.internal_trace_prefix}.leaf{leaf_idx}.switch.pcap",
                maxlen=args.internal_trace_maxlen,
            )

        # Calculate rank range for this leaf switch's downlinks.
        # Rank is used for naming internal link traces
        rank_start = rank_base
        rank_end = rank_base + leaf_nodes - 1
        print(
            f"[sw_hierarchical] leaf {leaf_idx}: ranks {rank_start}..{rank_end} "
            f"(ports 0..{leaf_nodes - 1}), uplink_port={leaf_nodes}"
        )
        rank_base += leaf_nodes

        # Uplink: leaf <-> master
        # An EtherLink is sufficient for the uplink between leaf and master
        # since it doesn't need to carry dist traffic directly,
        # but we can still optionally trace it.
        up = EtherLink(
            speed=args.ethernet_linkspeed,
            delay=args.ethernet_linkdelay,
        )
        # Optional internal trace for uplink between leaf and master
        if args.trace_internal_links:
            up.dump = EtherDump(
                file=f"{args.internal_trace_prefix}.leaf{leaf_idx}.uplink.pcap",
                maxlen=args.internal_trace_maxlen,
            )

        # Connect leaf switch port 0..(leaf_nodes-1) to downlinks, and port leaf_nodes to uplink
        up.int0 = leaf.interface[leaf_nodes]
        up.int1 = master.interface[leaf_idx]
        uplinks.append(up)

        # Downlinks to distributed gem5 nodes
        # For each leaf node connected to this leaf switch, create a
        # DistEtherLink and connect it to the appropriate leaf switch port
        for p in range(leaf_nodes):
            link = DistEtherLink(
                speed=args.ethernet_linkspeed,
                delay=args.ethernet_linkdelay,
                dist_rank=args.dist_rank,
                dist_size=args.dist_size,
                server_name=args.dist_server_name,
                server_port=args.dist_server_port,
                sync_start=args.dist_sync_start,
                sync_repeat=args.dist_sync_repeat,
                is_switch=True,
                num_nodes=args.dist_size,
            )
            # Optional internal trace for this downlink to a dist gem5 node
            if args.trace_internal_links:
                link.dump = EtherDump(
                    file=(
                        f"{args.internal_trace_prefix}.leaf{leaf_idx}."
                        f"port{p}.to_rank{rank_start + p}.pcap"
                    ),
                    maxlen=args.internal_trace_maxlen,
                )
            # Connect this link to the leaf switch port
            link.int0 = leaf.interface[p]
            node_links.append(link)

    # Keep references alive
    # The master switch needs to keep references to the
    # leaf switches, uplinks, and node links to prevent them
    # from being garbage collected.
    master.leaf_switches = leaves
    master.uplinks = uplinks
    master.portlink = node_links

    return master


def main():
    parser = argparse.ArgumentParser()
    Options.addCommonOptions(parser)
    Options.addFSOptions(parser)
    parser.add_argument(
        "--num-leaf-switches",
        type=int,
        default=1,
        help="Number of leaf switches under the master switch",
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
        help="Generate PCAP traces for internal switch links and ports",
    )
    parser.add_argument(
        "--internal-trace-prefix",
        type=str,
        default="hier_switch",
        help="Prefix for generated internal PCAP files",
    )
    parser.add_argument(
        "--internal-trace-maxlen",
        type=int,
        default=256,
        help="Snap length (bytes) for internal PCAP traces",
    )
    args = parser.parse_args()

    system = build_switch(args)
    root = Root(full_system=True, system=system)
    Simulation.run(args, root, None, None)


if __name__ == "__m5_main__":
    main()
