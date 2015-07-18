#!/usr/bin/python
#
#  Copyright (C) 2015  Clifford Wolf <clifford@clifford.at>
#
#  Permission to use, copy, modify, and/or distribute this software for any
#  purpose with or without fee is hereby granted, provided that the above
#  copyright notice and this permission notice appear in all copies.
#
#  THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
#  WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
#  MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
#  ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
#  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
#  ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
#  OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
#

from __future__ import division
from __future__ import print_function

import icebox
import getopt, sys, re

strip_comments = False
strip_interconn = False
lookup_pins = False
check_ieren = False
check_driver = False
pcf_data = dict()
portnames = set()
unmatched_ports = set()
modname = "chip"

def usage():
    print("""
Usage: icebox_vlog [options] [bitmap.txt]

    -s
        strip comments from output

    -S
        strip comments about interconn wires from output

    -l
        convert io tile port names to chip pin numbers

    -n <module-name>
        name for the exported module (default: "chip")

    -p <pcf-file>
        use the set_io command from the specified pcf file

    -P <pcf-file>
        like -p, enable some hacks for pcf files created
        by the iCEcube2 placer.

    -R
        enable IeRen database checks

    -D
        enable exactly-one-driver checks
""")
    sys.exit(0)

try:
    opts, args = getopt.getopt(sys.argv[1:], "sSlap:P:n:RD")
except:
    usage()

for o, a in opts:
    if o == "-s":
        strip_comments = True
    elif o == "-S":
        strip_interconn = True
    elif o == "-l":
        lookup_pins = True
    elif o == "-n":
        modname = a
    elif o == "-a":
        pass # ignored for backward compatibility
    elif o in ("-p", "-P"):
        with open(a, "r") as f:
            for line in f:
                if o == "-P" and not re.search(" # ICE_(GB_)?IO", line):
                    continue
                line = re.sub(r"#.*", "", line.strip()).split()
                if len(line) and line[0] == "set_io":
                    p = line[1]
                    if o == "-P":
                        p = p.lower()
                        p = p.replace("_ibuf", "")
                        p = p.replace("_obuft", "")
                        p = p.replace("_obuf", "")
                        p = p.replace("_gb_io", "")
                    portnames.add(p)
                    if not re.match(r"[a-zA-Z_][a-zA-Z0-9_]*$", p):
                        p = "\\%s " % p
                    unmatched_ports.add(p)
                    pinloc = tuple([int(s) for s in line[2:]])
                    pcf_data[pinloc] = p
    elif o == "-R":
        check_ieren = True
    elif o == "-D":
        check_driver = True
    else:
        usage()

if len(args) == 0:
    args.append("/dev/stdin")

if len(args) != 1:
    usage()

if not strip_comments:
    print("// Reading file '%s'.." % args[0])
ic = icebox.iceconfig()
ic.read_file(args[0])
print()

text_wires = list()
text_ports = list()

luts_queue = set()
text_func = list()
failed_drivers_check = list()

netidx = [0]
nets = dict()
seg2net = dict()

iocells = set()
iocells_in = set()
iocells_out = set()
iocells_special = set()
iocells_type = dict()
iocells_negclk = set()
iocells_inbufs = set()

def is_interconn(netname):
    if netname.startswith("sp4_"): return True
    if netname.startswith("sp12_"): return True
    if netname.startswith("span4_"): return True
    if netname.startswith("span12_"): return True
    if netname.startswith("logic_op_"): return True
    if netname.startswith("neigh_op_"): return True
    if netname.startswith("local_"): return True
    return False

extra_connections = list()
extra_segments = list()

for bit in ic.extra_bits:
    entry = ic.lookup_extra_bit(bit)
    if entry[0] == "padin_glb_netwk":
        glb = int(entry[1])
        pin_entry = ic.padin_pio_db()[glb]
        iocells.add((pin_entry[0], pin_entry[1], pin_entry[2]))
        iocells_in.add((pin_entry[0], pin_entry[1], pin_entry[2]))
        s1 = (pin_entry[0], pin_entry[1], "io_%d/PAD" % pin_entry[2])
        s2 = (pin_entry[0], pin_entry[1], "wire_gbuf/padin_%d" % pin_entry[2])
        extra_connections.append((s1, s2))

for idx, tile in ic.io_tiles.items():
    tc = icebox.tileconfig(tile)
    iocells_type[(idx[0], idx[1], 0)] = ["0" for i in range(6)]
    iocells_type[(idx[0], idx[1], 1)] = ["0" for i in range(6)]
    for entry in ic.tile_db(idx[0], idx[1]):
        if check_ieren and entry[1] == "IoCtrl" and entry[2].startswith("IE_") and not tc.match(entry[0]):
            iren_idx = (idx[0], idx[1], 0 if entry[2] == "IE_0" else 1)
            for iren_entry in ic.ieren_db():
                if iren_idx[0] == iren_entry[3] and iren_idx[1] == iren_entry[4] and iren_idx[2] == iren_entry[5]:
                    iocells_inbufs.add((iren_entry[0], iren_entry[1], iren_entry[2]))
        if entry[1] == "NegClk" and tc.match(entry[0]):
            iocells_negclk.add((idx[0], idx[1], 0))
            iocells_negclk.add((idx[0], idx[1], 1))
        if entry[1].startswith("IOB_") and entry[2].startswith("PINTYPE_") and tc.match(entry[0]):
            match1 = re.match("IOB_(\d+)", entry[1])
            match2 = re.match("PINTYPE_(\d+)", entry[2])
            assert match1 and match2
            iocells_type[(idx[0], idx[1], int(match1.group(1)))][int(match2.group(1))] = "1"
    iocells_type[(idx[0], idx[1], 0)] = "".join(iocells_type[(idx[0], idx[1], 0)])
    iocells_type[(idx[0], idx[1], 1)] = "".join(iocells_type[(idx[0], idx[1], 1)])

for segs in sorted(ic.group_segments()):
    for seg in segs:
        if ic.tile_type(seg[0], seg[1]) == "IO":
            match = re.match("io_(\d+)/D_(IN|OUT)_(\d+)", seg[2])
            if match:
                cell = (seg[0], seg[1], int(match.group(1)))
                iocells.add(cell)
                if match.group(2) == "IN":
                    if check_ieren:
                        assert cell in iocells_inbufs
                    if iocells_type[cell] != "100000" or match.group(3) != "0":
                        iocells_special.add(cell)
                    iocells_in.add(cell)
                if match.group(2) == "OUT" and iocells_type[cell][2:6] != "0000":
                    if iocells_type[cell] != "100110" or match.group(3) != "0":
                        iocells_special.add(cell)
                    iocells_out.add(cell)
                extra_segments.append((seg[0], seg[1], "io_%d/PAD" % int(match.group(1))))

for cell in iocells:
    if iocells_type[cell] == "100110" and not cell in iocells_special:
        s1 = (cell[0], cell[1], "io_%d/PAD" % cell[2])
        s2 = (cell[0], cell[1], "io_%d/D_OUT_0" % cell[2])
        extra_connections.append((s1, s2))
        del iocells_type[cell]
    elif iocells_type[cell] == "100000" and not cell in iocells_special:
        s1 = (cell[0], cell[1], "io_%d/PAD" % cell[2])
        s2 = (cell[0], cell[1], "io_%d/D_IN_0" % cell[2])
        extra_connections.append((s1, s2))
        del iocells_type[cell]

def next_netname():
    while True:
        netidx[0] += 1
        n = "n%d" % netidx[0]
        if n not in portnames:
            return n

for segs in sorted(ic.group_segments(extra_connections=extra_connections, extra_segments=extra_segments)):
    n = next_netname()
    net_segs = set()
    renamed_net_to_port = False

    for s in segs:
        match =  re.match("io_(\d+)/PAD", s[2])
        if match:
            idx = (s[0], s[1], int(match.group(1)))
            p = "io_%d_%d_%d" % idx
            net_segs.add(p)
            if lookup_pins or pcf_data:
                for entry in icebox.pinloc_db:
                    if idx[0] == entry[1] and idx[1] == entry[2] and idx[2] == entry[3]:
                        if (entry[0],) in pcf_data:
                            p = pcf_data[(entry[0],)]
                            unmatched_ports.discard(p)
                        elif (entry[1], entry[2], entry[3]) in pcf_data:
                            p = pcf_data[(entry[1], entry[2], entry[3])]
                            unmatched_ports.discard(p)
                        elif lookup_pins:
                            p = "pin_%d" % entry[0]
            if not renamed_net_to_port:
                n = p
                if idx in iocells_in and idx not in iocells_out:
                    text_ports.append("input %s" % p)
                elif idx not in iocells_in and idx in iocells_out:
                    text_ports.append("output %s" % p)
                else:
                    text_ports.append("inout %s" % p)
                text_wires.append("wire %s;" % n)
                renamed_net_to_port = True
            elif idx in iocells_in and idx not in iocells_out:
                text_ports.append("input %s" % p)
                text_wires.append("assign %s = %s;" % (n, p))
            elif idx not in iocells_in and idx in iocells_out:
                text_ports.append("output %s" % p)
                text_wires.append("assign %s = %s;" % (p, n))
            else:
                text_ports.append("inout %s" % p)
                text_wires.append("assign %s = %s;" % (p, n))

        match =  re.match("lutff_(\d+)/", s[2])
        if match:
            luts_queue.add((s[0], s[1], int(match.group(1))))

    nets[n] = segs

    for s in segs:
        seg2net[s] = n

    if not renamed_net_to_port:
        text_wires.append("wire %s;" % n)

    for s in segs:
        if not strip_interconn or not is_interconn(s[2]):
            if s[2].startswith("glb_netwk_"):
                net_segs.add((0, 0, s[2]))
            else:
                net_segs.add(s)

    count_drivers = 0
    for s in segs:
        if re.match(r"ram/RDATA_", s[2]): count_drivers += 1
        if re.match(r"io_./D_IN_", s[2]): count_drivers += 1
        if re.match(r"lutff_./out", s[2]): count_drivers += 1

    if count_drivers != 1 and check_driver:
        failed_drivers_check.append(n)

    if not strip_comments:
        for s in sorted(net_segs):
                text_wires.append("// %s" % (s,))
        if count_drivers != 1 and check_driver:
            text_wires.append("// Number of drivers: %d" % count_drivers)
        text_wires.append("")

def seg_to_net(seg, default=None):
    if seg not in seg2net:
        if default is not None:
            return default
        n = next_netname()
        nets[n] = set([seg])
        seg2net[seg] = n
        text_wires.append("wire %s;" % n)
        if not strip_comments:
            if not strip_interconn or not is_interconn(seg[2]):
                text_wires.append("// %s" % (seg,))
            text_wires.append("")
    return seg2net[seg]

for cell in iocells:
    if cell in iocells_type:
        net_pad   = seg_to_net((cell[0], cell[1], "io_%d/PAD" % cell[2]))
        net_din0  = seg_to_net((cell[0], cell[1], "io_%d/D_IN_0" % cell[2]), "")
        net_din1  = seg_to_net((cell[0], cell[1], "io_%d/D_IN_1" % cell[2]), "")
        net_dout0 = seg_to_net((cell[0], cell[1], "io_%d/D_OUT_0" % cell[2]), "0")
        net_dout1 = seg_to_net((cell[0], cell[1], "io_%d/D_OUT_1" % cell[2]), "0")
        net_oen   = seg_to_net((cell[0], cell[1], "io_%d/OUT_ENB" % cell[2]), "1")
        net_cen   = seg_to_net((cell[0], cell[1], "io_global/cen"), "1")
        net_iclk  = seg_to_net((cell[0], cell[1], "io_global/inclk"), "0")
        net_oclk  = seg_to_net((cell[0], cell[1], "io_global/outclk"), "0")
        net_latch = seg_to_net((cell[0], cell[1], "io_global/latch"), "0")
        iotype = iocells_type[cell]

        if cell in iocells_negclk:
            posedge = "negedge"
            negedge = "posedge"
        else:
            posedge = "posedge"
            negedge = "negedge"

        text_func.append("// IO Cell %s" % (cell,))
        if not strip_comments:
            text_func.append("// PAD     = %s" % net_pad)
            text_func.append("// D_IN_0  = %s" % net_din0)
            text_func.append("// D_IN_1  = %s" % net_din1)
            text_func.append("// D_OUT_0 = %s" % net_dout0)
            text_func.append("// D_OUT_1 = %s" % net_dout1)
            text_func.append("// OUT_ENB = %s" % net_oen)
            text_func.append("// CLK_EN  = %s" % net_cen)
            text_func.append("// IN_CLK  = %s" % net_iclk)
            text_func.append("// OUT_CLK = %s" % net_oclk)
            text_func.append("// LATCH   = %s" % net_latch)
            text_func.append("// TYPE    = %s (LSB:MSB)" % iotype)
        
        if net_din0 != "" or net_din1 != "":
            if net_cen == "1":
                icen_cond = ""
            else:
                icen_cond = "if (%s) " % net_cen

            if net_din0 != "":
                if iotype[1] == "0" and iotype[0] == "0":
                    reg_din0 = next_netname()
                    text_func.append("reg %s;" % reg_din0)
                    text_func.append("always @(%s %s) %s%s <= %s;" % (posedge, net_iclk, icen_cond, reg_din0, net_pad))
                    text_func.append("assign %s = %s;" % (net_din0, reg_din0))

                if iotype[1] == "0" and iotype[0] == "1":
                    text_func.append("assign %s = %s;" % (net_din0, net_pad))

                if iotype[1] == "1" and iotype[0] == "0":
                    reg_din0 = next_netname()
                    reg_din0_latched = next_netname()
                    text_func.append("reg %s, %s;" % (reg_din0, reg_din0_latched))
                    text_func.append("always @(%s %s) %s%s <= %s;" % (posedge, net_iclk, icen_cond, reg_din0, net_pad))
                    text_func.append("always @* if (!%s) %s = %s;" % (net_latch, reg_din0_latched, reg_din0))
                    text_func.append("assign %s = %s;" % (net_din0, reg_din0_latched))

                if iotype[1] == "1" and iotype[0] == "1":
                    reg_din0 = next_netname()
                    text_func.append("reg %s;" % reg_din0)
                    text_func.append("always @* if (!%s) %s = %s;" % (net_latch, reg_din0, net_pad))
                    text_func.append("assign %s = %s;" % (net_din0, reg_din0))

            if net_din1 != "":
                reg_din1 = next_netname()
                text_func.append("reg %s;" % reg_din1)
                text_func.append("always @(%s %s) %s%s <= %s;" % (negedge, net_iclk, icen_cond, reg_din1, net_pad))
                text_func.append("assign %s = %s;" % (net_din1, reg_din1))

        if iotype[5] != "0" or iotype[4] != "0":
            if net_cen == "1":
                ocen_cond = ""
            else:
                ocen_cond = "if (%s) " % net_cen

            # effective OEN: iotype[4], iotype[5]

            if iotype[5] == "0" and iotype[4] == "1":
                eff_oen = "1"

            if iotype[5] == "1" and iotype[4] == "0":
                eff_oen = net_oen

            if iotype[5] == "1" and iotype[4] == "1":
                eff_oen = next_netname()
                text_func.append("reg %s;" % eff_oen)
                text_func.append("always @(%s %s) %s%s <= %s;" % (posedge, net_oclk, ocen_cond, eff_oen, net_oen))

            # effective DOUT: iotype[2], iotype[3]

            if iotype[2] == "0" and iotype[3] == "0":
                ddr_posedge = next_netname()
                ddr_negedge = next_netname()
                text_func.append("reg %s, %s;" % (ddr_posedge, ddr_negedge))
                text_func.append("always @(%s %s) %s%s <= %s;" % (posedge, net_oclk, ocen_cond, ddr_posedge, net_dout0))
                text_func.append("always @(%s %s) %s%s <= %s;" % (negedge, net_oclk, ocen_cond, ddr_negedge, net_dout1))
                eff_dout = next_netname()
                text_func.append("wire %s;" % (eff_dout))
                if cell in iocells_negclk:
                    text_func.append("assign %s = %s ? %s : %s;" % (eff_dout, net_oclk, ddr_negedge, ddr_posedge))
                else:
                    text_func.append("assign %s = %s ? %s : %s;" % (eff_dout, net_oclk, ddr_posedge, ddr_negedge))

            if iotype[2] == "0" and iotype[3] == "1":
                eff_dout = net_dout0

            if iotype[2] == "1" and iotype[3] == "0":
                eff_dout = next_netname()
                text_func.append("reg %s;" % eff_dout)
                text_func.append("always @(%s %s) %s%s <= %s;" % (posedge, net_oclk, ocen_cond, eff_dout, net_dout0))

            if iotype[2] == "1" and iotype[3] == "1":
                eff_dout = next_netname()
                text_func.append("reg %s;" % eff_dout)
                text_func.append("always @(%s %s) %s%s <= !%s;" % (posedge, net_oclk, ocen_cond, eff_dout, net_dout0))

            if eff_oen == "1":
                text_func.append("assign %s = %s;" % (net_pad, eff_dout))
            else:
                text_func.append("assign %s = %s ? %s : 1'bz;" % (net_pad, eff_oen, eff_dout))

        text_func.append("")

for p in unmatched_ports:
    text_ports.append("input %s" % p)

wire_to_reg = set()
lut_assigns = list()
const_assigns = list()
carry_assigns = list()
always_stmts = list()
max_net_len = 0

for lut in luts_queue:
    seq_bits = icebox.get_lutff_seq_bits(ic.logic_tiles[(lut[0], lut[1])], lut[2])
    if seq_bits[0] == "1":
        seg_to_net((lut[0], lut[1], "lutff_%d/cout" % lut[2]))

for lut in luts_queue:
    tile = ic.logic_tiles[(lut[0], lut[1])]
    lut_bits = icebox.get_lutff_lut_bits(tile, lut[2])
    seq_bits = icebox.get_lutff_seq_bits(tile, lut[2])
    net_in0 = seg_to_net((lut[0], lut[1], "lutff_%d/in_0" % lut[2]), "0")
    net_in1 = seg_to_net((lut[0], lut[1], "lutff_%d/in_1" % lut[2]), "0")
    net_in2 = seg_to_net((lut[0], lut[1], "lutff_%d/in_2" % lut[2]), "0")
    net_in3 = seg_to_net((lut[0], lut[1], "lutff_%d/in_3" % lut[2]), "0")
    net_out = seg_to_net((lut[0], lut[1], "lutff_%d/out" % lut[2]))
    if seq_bits[0] == "1":
        net_cout = seg_to_net((lut[0], lut[1], "lutff_%d/cout" % lut[2]))
        net_in1 = seg_to_net((lut[0], lut[1], "lutff_%d/in_1" % lut[2]), "0")
        net_in2 = seg_to_net((lut[0], lut[1], "lutff_%d/in_2" % lut[2]), "0")
        if lut[2] == 0:
            net_cin = seg_to_net((lut[0], lut[1], "carry_in_mux"))
            if icebox.get_carry_cascade_bit(tile) == "0":
                if not strip_comments:
                    text_wires.append("// Carry-In for (%d %d)" % (lut[0], lut[1]))
                text_wires.append("assign %s = %s;" % (net_cin, icebox.get_carry_bit(tile)))
                if not strip_comments:
                    text_wires.append("")
        else:
            net_cin = seg_to_net((lut[0], lut[1], "lutff_%d/cout" % (lut[2]-1)), "0")
        carry_assigns.append([net_cout, "/* CARRY %2d %2d %2d */ (%s & %s) | ((%s | %s) & %s)" %
                (lut[0], lut[1], lut[2], net_in1, net_in2, net_in1, net_in2, net_cin)])
    if seq_bits[1] == "1":
        n = next_netname()
        text_wires.append("wire %s;" % n)
        if not strip_comments:
            text_wires.append("// FF %s" % (lut,))
            text_wires.append("")
        net_cen = seg_to_net((lut[0], lut[1], "lutff_global/cen"), "1")
        net_clk = seg_to_net((lut[0], lut[1], "lutff_global/clk"), "0")
        net_sr  = seg_to_net((lut[0], lut[1], "lutff_global/s_r"), "0")
        if seq_bits[3] == "0":
            always_stmts.append("/* FF %2d %2d %2d */ always @(%sedge %s) if (%s) %s <= %s ? %s : %s;" %
                    (lut[0], lut[1], lut[2], "neg" if icebox.get_negclk_bit(tile) == "1" else "pos",
                    net_clk, net_cen, net_out, net_sr, seq_bits[2], n))
        else:
            always_stmts.append("/* FF %2d %2d %2d */ always @(%sedge %s, posedge %s) if (%s) %s <= %s; else if (%s) %s <= %s;" %
                    (lut[0], lut[1], lut[2], "neg" if icebox.get_negclk_bit(tile) == "1" else "pos",
                    net_clk, net_sr, net_sr, net_out, seq_bits[2], net_cen, net_out, n))
        wire_to_reg.add(net_out)
        net_out = n
    if not "1" in lut_bits:
        const_assigns.append([net_out, "1'b0"])
    elif not "0" in lut_bits:
        const_assigns.append([net_out, "1'b1"])
    else:
        def make_lut_expr(bits, sigs):
            if not sigs:
                return "%s" % bits[0]
            l_expr = make_lut_expr(bits[0:len(bits)//2], sigs[1:])
            h_expr = make_lut_expr(bits[len(bits)//2:len(bits)], sigs[1:])
            if h_expr == l_expr: return h_expr
            if sigs[0] == "0": return l_expr
            if sigs[0] == "1": return h_expr
            if h_expr == "1" and l_expr == "0": return sigs[0]
            if h_expr == "0" and l_expr == "1": return "!" + sigs[0]
            return "%s ? %s : %s" % (sigs[0], h_expr, l_expr)
        lut_expr = make_lut_expr(lut_bits, [net_in3, net_in2, net_in1, net_in0])
        lut_assigns.append([net_out, "/* LUT   %2d %2d %2d */ %s" % (lut[0], lut[1], lut[2], lut_expr)])
    max_net_len = max(max_net_len, len(net_out))

for a in const_assigns + lut_assigns + carry_assigns:
    text_func.append("assign %-*s = %s;" % (max_net_len, a[0], a[1]))

print("module %s (%s);\n" % (modname, ", ".join(text_ports)))

new_text_wires = list()
for line in text_wires:
    match = re.match(r"wire ([^ ;]+)(.*)", line)
    if match and match.group(1) in wire_to_reg:
        line = "reg " + match.group(1) + match.group(2)
    if strip_comments:
        if new_text_wires and new_text_wires[-1].split()[0] == line.split()[0] and new_text_wires[-1][-1] == ";":
            new_text_wires[-1] = new_text_wires[-1][0:-1] + "," + line[len(line.split()[0]):]
        else:
            new_text_wires.append(line)
    else:
        print(line)
for line in new_text_wires:
    print(line)
if strip_comments:
    print()

for line in text_func:
    print(line)
for line in always_stmts:
    print(line)
print()

for p in unmatched_ports:
    print("// Warning: unmatched port '%s'" %p)
if unmatched_ports:
    print()

print("endmodule")
print()

if failed_drivers_check:
    print("// Single-driver-check failed for %d nets:" % len(failed_drivers_check))
    print("// %s" % " ".join(failed_drivers_check))
    assert False

