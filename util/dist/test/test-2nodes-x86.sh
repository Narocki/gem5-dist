#! /bin/bash

#
# Copyright (c) 2015, 2022 Arm Limited
# All rights reserved
#
# The license below extends only to copyright in the software and shall
# not be construed as granting a license to any other intellectual
# property including but not limited to intellectual property relating
# to a hardware implementation of the functionality of the software
# licensed hereunder.  You may use the software subject to the license
# terms below provided that you ensure that this notice is replicated
# unmodified and in its entirety in all distributions of the software,
# modified or unmodified, in source code or in binary form.
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
#
# This is an example script to start a dist gem5 simulations using
# two AArch64 systems. It is also uses the example
# dist gem5 bootscript util/dist/test/simple_bootscript.rcS that will
# run the linux ping command to check if we can see the peer system
# connected via the simulated Ethernet link.

GEM5_DIR=$(pwd)/$(dirname $0)/../../..

# IMG=$M5_PATH/disks/ubuntu-18.04-arm64-docker.img
IMG=$M5_PATH/disks/x86-ubuntu-22.04-img-20250731
# VMLINUX=$M5_PATH/binaries/vmlinux.arm64
VMLINUX=$M5_PATH/binaries/x86-linux-kernel-5.15.180

FS_CONFIG=$M5_PATH/configs/example/x86/dist_x86.py
SW_CONFIG=$M5_PATH/configs/dist/sw.py
GEM5_EXE=$M5_PATH/build/X86/gem5.opt

BOOT_SCRIPT=$M5_PATH/util/dist/test/simple_bootscript.rcS
GEM5_DIST_SH=$M5_PATH/util/dist/gem5-dist-x86.sh
# Disable kernel address space layout randomization (KASLR).
# gem5 installs kernel-function PC events (e.g., panic/oops handlers) and
# relies on stable symbol addresses; KASLR can cause false triggers and/or
# broken dmesg dumps.
#KERNEL_CMD="random.trust_cpu=on nokaslr acpi=off libata.dma=0"
KERNEL_CMD="random.trust_cpu=on nokaslr acpi=off"
# Keep switch and nodes consistent. Increasing link speed reduces the chances
# of DistEtherLink reporting "packet not sent, link busy" during bursts.
# ETH_LINK_DELAY="500ms"


#ETH_LINK_DELAY="1ms"
ETH_LINK_DELAY="20us"
#ETH_LINK_DELAY="50us"
#ETH_LINK_DELAY="26us"
#ETH_LINK_DELAY="10us"
# Higher speed reduces chances of "packet not sent, link busy" bursts.
ETH_LINK_SPEED="400Gbps"

#SYN_REPEAT="50us"
SYN_REPEAT="20us"
#SYN_REPEAT="25us"
#SYN_REPEAT="10us"
SYN_START="8000000000000t"
SYN_START="0"
DEBUG_FLAGS="--debug-flags=DistEthernet,Cache,CoherentXBar"
DEBUG_FLAGS="" #--debug-flags=DistEthernet,ExecAll" #Ethernet,DistEthernetCmd,DistEthernetPkt,WorkItems"
ENABLE_CHECKPOINT_RESTORE=false
RESTORE_FROM_ARG=""
SW_CHKPT_RESTORE_ARG="--checkpoint-restore=\"\""
if [ "$ENABLE_CHECKPOINT_RESTORE" = true ]; then
    RESTORE_FROM_ARG="--restore-from=cpt.12068050000000"
    SW_CHKPT_RESTORE_ARG="--checkpoint-restore=\"r 2\""
fi
CPU_TYPE="atomic"

# Don't immediately terminate the simulation if gem5 detects execution of the
# kernel panic/oops handlers. This helps distinguish a real Linux panic from a
# false trigger of the PC event hook and preserves the serial output.
# (Configured in configs/example/arm/dist_bigLITTLE.py so it only affects FS nodes.)

NNODES=4

LSB_MCPU_HOSTS="gem5-cluster_master 1 gem5-cluster_worker1 1 gem5-cluster_worker2 1 gem5-cluster_worker3 1"
export LSB_MCPU_HOSTS

$GEM5_DIST_SH -n $NNODES                                  \
        -x $GEM5_EXE                                \
        -s $SW_CONFIG                               \
        -p 2210                                     \
        -f $FS_CONFIG                               \
        --kernel-cmd "$KERNEL_CMD"                 \
        --sw-args                                   \
          --dist-sync-start=$SYN_START                    \
          --dist-sync-repeat=$SYN_REPEAT          \
          --ethernet-linkdelay=$ETH_LINK_DELAY    \
          --ethernet-linkspeed=$ETH_LINK_SPEED    \
        --m5-args                                   \
          --debug-file=debug.log                \
          --debug-start="1325168160008" \
          $DEBUG_FLAGS                           \
          --listener-mode=off                     \
        --fs-args                                   \
          --dist-sync-start=$SYN_START                    \
          --dist-sync-repeat=$SYN_REPEAT          \
          --ethernet-linkdelay=$ETH_LINK_DELAY    \
          --ethernet-linkspeed=$ETH_LINK_SPEED    \
          --cpu-type=$CPU_TYPE                       \
          --caches                                \
          --last-cache-level=1                    \
          --little-cpus=1                         \
          --big-cpus=1                            \
          --disk=$IMG                             \
          --kernel=$VMLINUX                       \
          --bootscript=$BOOT_SCRIPT               \
          $RESTORE_FROM_ARG
          #$RESTORE_FROM_ARG
        #--cf-args                                   \
          #$CHKPT_RESTORE

# $GEM5_DIST_SH -n $NNODES                                  \
#               -x $GEM5_EXE                                \
#               -s $SW_CONFIG                               \
#               -f $FS_CONFIG                               \
#         --kernel-cmd "$KERNEL_CMD"                 \
#             --sw-args                                   \
#                   --dist-sync-start=0                  \
#                   --dist-sync-repeat=10us              \
#                   --ethernet-linkdelay=$ETH_LINK_DELAY \
#                   --ethernet-linkspeed=$ETH_LINK_SPEED \
#               --m5-args                                   \
#                  $DEBUG_FLAGS                             \
#               --fs-args                                   \
#                 --dist-sync-start=0                    \
#                 --dist-sync-repeat=10us                \
#                 --ethernet-linkdelay=$ETH_LINK_DELAY   \
#                 --ethernet-linkspeed=$ETH_LINK_SPEED   \
#                   --cpu-type=atomic                       \
#                   --little-cpus=1                         \
#                   --big-cpus=1                            \
#                   --machine-type=VExpress_GEM5_Foundation \
#                   --disk=$IMG                             \
#                   --kernel=$VMLINUX                       \
#                   --bootscript=$BOOT_SCRIPT               \
#               --cf-args                                   \
#                   $CHKPT_RESTORE
