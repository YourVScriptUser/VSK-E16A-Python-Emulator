"""
 
The Virtual System Komputer (VSK) family:
 
name/architecture/assembly name
 
VSK-8 / EFASM8BIT / xasm8
 
8-bit
microcontroller-like ISA
8 registers
up to 512B of ram
 
BIOS: eBIOS
 
VSK-16 / EFASM16BIT / xasm-16
 
16-bit
microcontroller-like ISA
12 registers (7 general purpose)
up to 64KWords (128KiB)
 
BIOS: eBIOS
 
VSK-16A / VSKa / Xasm16A
 
16-bit
CISC-like CPU ISA
16 general purpose registers (r0-r15)
up to 64Kwords (128KiB)
 
BIOS: VSK sysROM propiertery BIOS
 
(the A stands for abstraction)
 
VSK-E16A / ExVSKa / Xasm16A Extended
 
(copy of the VSK-16A - adds hardware interrupts and PMIO)
 
16-bit
CISC-like CPU ISA
16 general purpose registers (r0-r15)
up to 64Kwords (128KiB)
 
BIOS: VSK sysROM propiertery BIOS
 
(the E stands for extended)
 
"""
 
#==========# MODULES            #==========#
import datetime
import time
import sys
import subprocess
import msvcrt
import tkinter as tk
import psutil
import threading
import pymsgbox
import os
import random
import re
import keyboard
 
from Teknikality import colors, numbertools, clearscreen, give, hide_cursor
from types       import FunctionType as function
from inspect     import stack
from mhzmasheen  import khz_to_spi, spi_to_khz
from tkinter import ttk
 
#==========# INIT               #==========#
# Please note that there likely are references to how the VSK-16A worked in this file that no longer apply to the VSK-E16A
 
# Extended version of the VSK-16A
# Adds:
#  Hardware interrupts
#  Basic stack-based PMIO
 
clearscreen()
 
def is_ctrl_held(check_duration=0.3):
    # poll it ten billion times because keyboard is 6 years old and hates to run on 3.14.3
    # and pynput sucks
    # once this stops working its ctypes time (:
 
    end = time.time() + check_duration
    while time.time() < end:
        if keyboard.is_pressed('ctrl'):
            return True
        time.sleep(0.02)
    return False
 
 
 
 
 
 
 
def open_debug_gui(update_interval=50):
    """
    VSK-E16A Debugger — read-only, never mutates VM state except via
    explicit user actions in the Live Patch panel (same write paths as
    the F12 terminal).
 
    Perf strategy:
      - Only the visible memory viewport is re-formatted/diffed per tick.
      - Disassembly recomputes when IP changes OR when the tab becomes
        visible again (fixes: previously could stay blank if you switched
        to the tab while IP was stationary, or missed an update if IP
        changed while another tab was selected).
      - Sparkline redraws on its own slower cadence (250ms) since 50ms
        resolution on a 1s-windowed kHz stat is wasted draw calls.
      - Nonzero-byte memory usage stays on a 2s cadence scan.
      - Register flash uses direct widget config, no per-tick allocation
        beyond what actually changed.
      - Motherboard Visualizer tab is fully lazy: its geometry is built
        exactly once, on first visibility, and its per-tick update
        function returns immediately (no-op) whenever that tab is not
        the selected tab -- so it costs nothing while hidden.
    """
 
    BG        = "#0a0f1a"
    PANEL_BG  = "#131c2b"
    PANEL_BG2 = "#0c1420"
    BORDER    = "#22314a"
    FG        = "#e6ebf2"
    FG_DIM    = "#7d8ba0"
    ACCENT    = "#5b8def"
    GOOD      = "#5fbf7a"
    WARN      = "#e0a84c"
    PC_BG     = "#2a3a55"
    PC_WORD_BG = "#8a2a2a"
    PC_WORD_FG = "#ffe3e3"
    FLASH_BG  = "#26385a"   # register "just changed" flash color — not a heat reading
    DECOMP_FG = "#7fd08a"
 
    OPVAL_TO_MNEM = {
        0: ("EINVAL", 0), 1: ("HLT", 0), 4: ("MOV", 4), 8: ("VOUT", 0),
        12: ("ADD", 3), 16: ("SUB", 3), 20: ("MUL", 3), 24: ("DIV", 3),
        28: ("JMP", 1), 32: ("CALL", 1), 36: ("RET", 0), 40: ("PUSH", 1),
        44: ("POP", 1), 48: ("CMP", 2), 52: ("JE", 1), 56: ("JNE", 1),
        60: ("DHI", 0), 64: ("LOAD", None), 68: ("INC", 1), 72: ("DEC", 1),
        76: ("NUMP", 2), 78: ("EHI", 0), 80: ("IN", 1), 82: ("INT", 1),
        84: ("JH", 1), 86: ("JL", 1), 88: ("CLF", 0), 90: ("JPTR", 1),
        92: ("MOVFS", 2), 94: ("MOVSP", 1), 96: ("OUT", 0), 98: ("SHLT", 0),
    }
 
    def fmt_mov_operand(mode, val):
        if mode == 1:
            return f"r{val}"
        elif mode == 2:
            return f"{val}"
        elif mode == 3:
            return f"[r{val}]"
        elif mode == 4:
            return f"[{val}]"
        return f"?{val}"
 
    MOVFS_REV = {44: "rFLAGS", 99: "rIP", 22: "rSP"}
 
    def gui_thread():
        try:
            root = tk.Tk()
            root.iconify()
            root.title("VSK-E16A Debugger")
            root.geometry("1400x860")
            root.minsize(980, 620)
            root.configure(bg=BG)
 
            import tkinter.font as tkfont
            available = set(tkfont.families())
            mono_name = "Consolas" if "Consolas" in available else "Courier New"
 
            style = ttk.Style(root)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure("Dbg.TNotebook", background=BG, borderwidth=0)
            style.configure("Dbg.TNotebook.Tab", background=PANEL_BG, foreground=FG_DIM,
                             padding=(10, 4), font=(mono_name, 9))
            style.map("Dbg.TNotebook.Tab",
                      background=[("selected", PANEL_BG2)],
                      foreground=[("selected", FG)])
 
            follow_pc = {"v": True}
            last_ip_for_disasm = {"v": None}
            last_disasm_tab_visible = {"v": False}
 
            # ================= HEADER =================
            header = tk.Frame(root, bg=BG)
            header.pack(fill="x", padx=14, pady=(12, 6))
            tk.Label(header, text="VSK-E16A", bg=BG, fg=FG,
                     font=(mono_name, 14, "bold")).pack(side="left")
            tk.Label(header, text="  debugger", bg=BG, fg=FG_DIM,
                     font=(mono_name, 10)).pack(side="left")
 
            status_dot = tk.Label(header, text="●", bg=BG, fg=GOOD, font=(mono_name, 12))
            status_dot.pack(side="left", padx=(14, 2))
            status_txt = tk.Label(header, text="RUNNING", bg=BG, fg=FG_DIM, font=(mono_name, 9))
            status_txt.pack(side="left")
 
            def mk_btn(parent, text, cmd, w=10):
                b = tk.Button(parent, text=text, command=cmd, bg=PANEL_BG2, fg=FG,
                              activebackground=FLASH_BG, activeforeground=FG,
                              font=(mono_name, 9), relief="flat", width=w,
                              highlightbackground=BORDER, highlightthickness=1, bd=0)
                b.pack(side="left", padx=3)
                return b
 
            btn_row = tk.Frame(header, bg=BG)
            btn_row.pack(side="right")
 
            def do_dump():
                try:
                    make_mem_dump(noPrintStatusString=True)
                except Exception:
                    pass
            mk_btn(btn_row, "💾 DUMP", do_dump, 9)
 
            cycle_label = tk.Label(header, text="cycles: 0", bg=BG, fg=FG_DIM, font=(mono_name, 9))
            cycle_label.pack(side="right", padx=(0, 16))
 
            # ================= TOP: REGISTERS + SPARKLINE + STACK =================
            top_row = tk.Frame(root, bg=BG)
            top_row.pack(fill="x", padx=14, pady=(0, 8))
 
            reg_panel = tk.Frame(top_row, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
            reg_panel.pack(side="left", fill="both", expand=True)
 
            reg_header = tk.Frame(reg_panel, bg=PANEL_BG)
            reg_header.pack(fill="x", padx=12, pady=(10, 4))
            tk.Label(reg_header, text="REGISTERS", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 9)).pack(side="left")
            tk.Label(reg_header, text="purple = value changed this tick", bg=PANEL_BG, fg=FG_DIM,
                     font=(mono_name, 8)).pack(side="right")
 
            reg_grid = tk.Frame(reg_panel, bg=PANEL_BG)
            reg_grid.pack(fill="x", padx=12, pady=(0, 8))
 
            reg_value_labels, reg_prev_values, reg_cells = [], [None] * 16, []
            cols = 8
            for i in range(16):
                r, c = divmod(i, cols)
                cell = tk.Frame(reg_grid, bg=PANEL_BG2, highlightbackground=BORDER, highlightthickness=1)
                cell.grid(row=r, column=c, padx=3, pady=3, sticky="nsew")
                reg_grid.grid_columnconfigure(c, weight=1)
                tk.Label(cell, text=f"R{i}", bg=PANEL_BG2, fg=FG_DIM, font=(mono_name, 8), anchor="w").pack(anchor="w", padx=7, pady=(4, 0))
                vlab = tk.Label(cell, text="0x0000", bg=PANEL_BG2, fg=FG, font=(mono_name, 10), anchor="w")
                vlab.pack(anchor="w", padx=7, pady=(0, 5))
                reg_value_labels.append(vlab)
                reg_cells.append(cell)
 
            info_row = tk.Frame(reg_panel, bg=PANEL_BG)
            info_row.pack(fill="x", padx=12, pady=(0, 10))
 
            def make_stat(parent, name):
                box = tk.Frame(parent, bg=PANEL_BG2, highlightbackground=BORDER, highlightthickness=1)
                box.pack(side="left", fill="x", expand=True, padx=3)
                tk.Label(box, text=name, bg=PANEL_BG2, fg=FG_DIM, font=(mono_name, 8)).pack(anchor="w", padx=7, pady=(4, 0))
                val = tk.Label(box, text="--", bg=PANEL_BG2, fg=FG, font=(mono_name, 10), anchor="w")
                val.pack(anchor="w", padx=7, pady=(0, 5))
                return val
 
            ip_val = make_stat(info_row, "IP")
            sp_val = make_stat(info_row, "SP")
            flag_val = make_stat(info_row, "FLAGS")
            if_val = make_stat(info_row, "IF")
            mem_val = make_stat(info_row, "VM MEMORY")
            host_val = make_stat(info_row, "HOST CPU")
            speed_val = make_stat(info_row, "GUEST SPEED")
            depth_val = make_stat(info_row, "PMIO DEPTH")
            guest_val = make_stat(info_row, "GUEST CPU")
 
            side_panel = tk.Frame(top_row, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1, width=340)
            side_panel.pack(side="left", fill="y", padx=(8, 0))
            side_panel.pack_propagate(False)
 
            tk.Label(side_panel, text="CLOCK (kHz)", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 9)).pack(anchor="w", padx=12, pady=(10, 2))
            spark = tk.Canvas(side_panel, bg=PANEL_BG2, height=70, highlightthickness=1, highlightbackground=BORDER)
            spark.pack(fill="x", padx=12)
            spark_history = [0.0] * 60
 
            tk.Label(side_panel, text="PMIO STACK (top → bottom)", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 9)).pack(anchor="w", padx=12, pady=(10, 2))
            pmio_frame = tk.Frame(side_panel, bg=PANEL_BG2, highlightbackground=BORDER, highlightthickness=1)
            pmio_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
            pmio_text = tk.Text(pmio_frame, wrap="none", bg=PANEL_BG2, fg=FG, insertbackground=FG,
                                 font=(mono_name, 9), borderwidth=0, highlightthickness=0, height=6)
            pmio_text.pack(fill="both", expand=True, padx=6, pady=6)
            pmio_text.config(state="disabled")
            last_pmio_snapshot = [None]
 
            # ================= MIDDLE: TABBED NOTEBOOK =================
            nb = ttk.Notebook(root, style="Dbg.TNotebook")
            nb.pack(fill="both", expand=True, padx=14, pady=(0, 12))
 
            # ---- Tab: Memory ----
            mem_tab = tk.Frame(nb, bg=PANEL_BG)
            nb.add(mem_tab, text=" MEMORY ")
 
            mem_header = tk.Frame(mem_tab, bg=PANEL_BG)
            mem_header.pack(fill="x", padx=12, pady=(10, 4))
            tk.Label(mem_header, text="live decompile", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 8)).pack(side="right", padx=(6, 4))
            decompile_var = tk.BooleanVar(value=False)
            tk.Checkbutton(mem_header, variable=decompile_var, bg=PANEL_BG, selectcolor=PANEL_BG2,
                           activebackground=PANEL_BG, highlightthickness=0, bd=0).pack(side="right")
            tk.Label(mem_header, text="follow PC", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 8)).pack(side="right", padx=(10, 4))
            follow_var = tk.BooleanVar(value=True)
            def on_follow_toggle():
                follow_pc["v"] = follow_var.get()
            tk.Checkbutton(mem_header, variable=follow_var, command=on_follow_toggle, bg=PANEL_BG,
                           selectcolor=PANEL_BG2, activebackground=PANEL_BG, highlightthickness=0, bd=0).pack(side="right")
 
            goto_entry = tk.Entry(mem_header, bg=PANEL_BG2, fg=FG, insertbackground=FG, width=8,
                                   font=(mono_name, 9), relief="flat", highlightbackground=BORDER, highlightthickness=1)
            goto_entry.pack(side="left", padx=(0, 4))
            def do_goto(event=None):
                try:
                    addr = int(goto_entry.get(), 0) & 0xFFFF
                    line_no = (addr // 16) + 1
                    mem_text.see(f"{line_no}.0")
                    follow_var.set(False)
                    follow_pc["v"] = False
                except Exception:
                    pass
            goto_entry.bind("<Return>", do_goto)
            mk_btn(mem_header, "GO", do_goto, 4)
            tk.Label(mem_header, text="  goto addr (hex/dec)", bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 8)).pack(side="left")
 
            mem_inner = tk.Frame(mem_tab, bg=PANEL_BG)
            mem_inner.pack(fill="both", expand=True, padx=12, pady=(0, 12))
            mem_text = tk.Text(mem_inner, wrap="none", bg=PANEL_BG2, fg=FG, insertbackground=FG,
                                font=(mono_name, 9), borderwidth=0, highlightthickness=0, cursor="arrow")
            mem_text.pack(side="left", fill="both", expand=True)
            mem_scroll = tk.Scrollbar(mem_inner, command=mem_text.yview)
            mem_scroll.pack(side="right", fill="y")
            mem_text.config(yscrollcommand=mem_scroll.set, state="disabled")
            mem_text.tag_configure("pc", background=PC_BG)
            mem_text.tag_configure("pc_word", background=PC_WORD_BG, foreground=PC_WORD_FG)
            mem_text.tag_configure("decomp", foreground=DECOMP_FG)
            mem_text.tag_raise("pc_word", "pc")
 
            # ---- Tab: Disassembly (xasm16A-syntax, IP-window) ----
            disasm_tab = tk.Frame(nb, bg=PANEL_BG)
            nb.add(disasm_tab, text=" DISASSEMBLY ")
            disasm_text = tk.Text(disasm_tab, wrap="none", bg=PANEL_BG2, fg=FG, insertbackground=FG,
                                   font=(mono_name, 10), borderwidth=0, highlightthickness=0)
            disasm_text.pack(fill="both", expand=True, padx=12, pady=12)
            disasm_text.tag_configure("cur", background=PC_BG, foreground=PC_WORD_FG)
            disasm_text.tag_configure("addr", foreground=FG_DIM)
            disasm_text.tag_configure("mnem", foreground=DECOMP_FG)
            disasm_text.config(state="disabled")
 
            # ---- Tab: Motherboard Visualizer ----
            # Zero cost while not selected: geometry is built exactly once,
            # on first visibility, and per-tick updates are skipped entirely
            # unless mobo_tab_is_visible() is true (mirrors the disassembly
            # tab's own visibility gate above).
            PCB_GREEN       = "#0b3d1f"   # board substrate
            PCB_GREEN_LT    = "#0e4a26"   # slightly lighter panel green (unused headroom for future chip fills)
            PCB_EDGE        = "#062712"   # darker board edge / mounting rim
            SILK_WHITE      = "#e8f0e8"   # silkscreen text
            SILK_DIM        = "#7fa88f"   # dim silkscreen labels
            CHIP_BODY       = "#111418"   # IC package body (matte black plastic)
            CHIP_EDGE       = "#2a2f36"
            PIN_OFF         = "#6b5e1a"   # dull yellow
            PIN_ON          = "#f5d90a"   # solid yellow
            TRACE_OFF       = "#274d34"   # unlit copper trace (dark green-copper, not neon)
            TRACE_ON        = "#f5d90a"   # lit trace = solid yellow (matches pins)
            PAD_COPPER      = "#c98a2c"   # unlit copper pad ring (decorative package pins only)
 
            mobo_tab = tk.Frame(nb, bg=PCB_EDGE)
            nb.add(mobo_tab, text=" MOTHERBOARD ")
 
            mobo_canvas = tk.Canvas(mobo_tab, bg=PCB_EDGE, highlightthickness=0)
            mobo_canvas.pack(fill="both", expand=True, padx=10, pady=10)
 
            mobo_built = {"v": False}
            last_mobo_tab_visible = {"v": False}
 
            mobo_reg_led_ids = {}      # reg index -> list of 16 rect ids, MSB..LSB
            mobo_flag_led_ids = {}     # name -> list of rect ids
            mobo_ram_bit_ids = []      # 16 rect ids along RAM chip edge, MSB..LSB
            mobo_sio_bit_ids = []      # 8 rect ids on Super I/O showing PMIO stack depth
            mobo_clk_trace_ids = []    # segments of the CLK bus (touches all 4 chips)
            mobo_extra_trace_ids = {}  # name -> list of segment ids
            mobo_last_state = {
                "cycle": None, "regs": [None] * 16, "flag": None,
                "ipreg": None, "sp": None, "iF": None,
                "pmio_depth": None, "fault": None, "video_frame": None,
            }
 
            def _mobo_led_row(canvas, x, y, count, cellsz=10, gap=2):
                ids = []
                for i in range(count):
                    rx, ry = x + i * (cellsz + gap), y
                    rid = canvas.create_rectangle(
                        rx, ry, rx + cellsz, ry + cellsz,
                        fill=PIN_OFF, outline="#332c0a", width=1
                    )
                    ids.append(rid)
                return ids
 
            def build_motherboard():
                """Static PCB geometry — runs exactly once, ever."""
                if mobo_built["v"]:
                    return
                mobo_built["v"] = True
 
                W, H = 1620, 780
                mobo_canvas.configure(scrollregion=(0, 0, W, H))
 
                # ---- Board substrate ----
                mobo_canvas.create_rectangle(6, 6, W - 6, H - 6,
                                              fill=PCB_GREEN, outline="#083018", width=3)
                for cx, cy in [(30, 30), (W - 30, 30), (30, H - 30), (W - 30, H - 30)]:
                    mobo_canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9,
                                             fill=PCB_EDGE, outline="#0a2e17", width=2)
                    mobo_canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                             fill="#3a4a3f", outline="")
 
                # ---- Fixed chip footprints (no overlap by construction) ----
                cpu_x, cpu_y, cpu_w, cpu_h = 60, 90, 340, 600
                ram_x, ram_y, ram_w, ram_h = 640, 90, 260, 220
                vid_x, vid_y, vid_w, vid_h = 840, 400, 260, 160
                sio_x, sio_y, sio_w, sio_h = 980, 90, 260, 220
 
                def draw_chip(x, y, w, h, label1, label2=None):
                    mobo_canvas.create_rectangle(x, y, x + w, y + h,
                                                  fill=CHIP_BODY, outline=CHIP_EDGE, width=2)
                    mobo_canvas.create_arc(x + w / 2 - 14, y - 10, x + w / 2 + 14, y + 14,
                                            start=200, extent=140, style="arc",
                                            outline=CHIP_EDGE, width=2)
                    mobo_canvas.create_text(x + w / 2, y + 26, text=label1,
                                             fill=SILK_WHITE, font=(mono_name, 13, "bold"))
                    if label2:
                        mobo_canvas.create_text(x + w / 2, y + 46, text=label2,
                                                 fill=SILK_DIM, font=(mono_name, 9))
 
                draw_chip(cpu_x, cpu_y, cpu_w, cpu_h, "VSK-E16A 2026", "CPU / MICROCODE UNIT")
                draw_chip(ram_x, ram_y, ram_w, ram_h, "64K RAM", "SYSTEM MEMORY")
                draw_chip(vid_x, vid_y, vid_w, vid_h, "Video Output", "MMIO 0xF100")
                draw_chip(sio_x, sio_y, sio_w, sio_h, "Super I/O", "PMIO CONTROLLER")
 
                # ---- Decorative package pins along CPU's left edge (fixed count) ----
                n_pkg_pins = 14
                pin_span = cpu_h - 60
                for i in range(n_pkg_pins):
                    py = cpu_y + 30 + i * (pin_span / (n_pkg_pins - 1))
                    mobo_canvas.create_rectangle(cpu_x - 14, py - 3, cpu_x, py + 3,
                                                  fill=PAD_COPPER, outline="#7a5418")
 
                # ---- Register LED block ----
                # Sits fully inside the chip body, well below label2, with a
                # reserved left column wide enough that the "R15" / "FLAGS"
                # labels never crowd the LEDs, and enough top clearance from
                # label2 that nothing here can ever sit under a trace (all
                # traces are routed outside the chip's own footprint).
                label_col_w = 46
                led_x0 = cpu_x + label_col_w
                led_y0 = cpu_y + 74
                row_h = 16
                cellsz = 10
                gap = 2
                for r in range(16):
                    ry = led_y0 + r * row_h
                    mobo_canvas.create_text(led_x0 - 8, ry + cellsz / 2, text=f"R{r}",
                                             fill=SILK_WHITE, font=(mono_name, 9), anchor="e")
                    ids = _mobo_led_row(mobo_canvas, led_x0, ry, 16, cellsz=cellsz, gap=gap)
                    mobo_reg_led_ids[r] = ids
 
                flag_y = led_y0 + 16 * row_h + 12
                mobo_canvas.create_line(led_x0 - 8, flag_y - 6, cpu_x + cpu_w - 14, flag_y - 6,
                                         fill="#1c3a26", width=1)
                specials = [("IF", 1), ("FLAGS", 16), ("IP", 16), ("SP", 16)]
                sy = flag_y
                for name, width in specials:
                    mobo_canvas.create_text(led_x0 - 8, sy + cellsz / 2, text=name,
                                             fill=SILK_WHITE, font=(mono_name, 9), anchor="e")
                    ids = _mobo_led_row(mobo_canvas, led_x0, sy, width, cellsz=cellsz, gap=gap)
                    mobo_flag_led_ids[name] = ids
                    sy += row_h
 
                # ---- RAM address-bus pins along RAM chip's bottom edge (IP binary) ----
                ram_pin_y = ram_y + ram_h - 34
                ram_pin_x0 = ram_x + 18
                ram_pin_span = ram_w - 36
                mobo_canvas.create_text(ram_x + ram_w / 2, ram_pin_y - 14,
                                         text="ADDR/IP BUS (MSB \u2192 LSB)",
                                         fill=SILK_DIM, font=(mono_name, 7))
                bit_cell = (ram_pin_span - 15 * 2) / 16
                for i in range(16):
                    bx = ram_pin_x0 + i * (bit_cell + 2)
                    rid = mobo_canvas.create_rectangle(bx, ram_pin_y, bx + bit_cell, ram_pin_y + 12,
                                                        fill=PIN_OFF, outline="#332c0a", width=1)
                    mobo_ram_bit_ids.append(rid)
 
                # ---- CLK trace: single bus along the bottom touching all four chips ----
                clk_y = H - 60
                clk_segs = []
                clk_segs.append(mobo_canvas.create_line(
                    cpu_x + cpu_w / 2, cpu_y + cpu_h, cpu_x + cpu_w / 2, clk_y,
                    fill=TRACE_OFF, width=4))
                clk_segs.append(mobo_canvas.create_line(
                    ram_x + ram_w / 2, ram_y + ram_h, ram_x + ram_w / 2, clk_y,
                    fill=TRACE_OFF, width=4))
                clk_segs.append(mobo_canvas.create_line(
                    vid_x + vid_w / 2, vid_y + vid_h, vid_x + vid_w / 2, clk_y,
                    fill=TRACE_OFF, width=4))
                clk_segs.append(mobo_canvas.create_line(
                    sio_x + sio_w / 2, sio_y + sio_h, sio_x + sio_w / 2, clk_y,
                    fill=TRACE_OFF, width=4))
                clk_segs.append(mobo_canvas.create_line(
                    cpu_x + cpu_w / 2, clk_y, sio_x + sio_w / 2, clk_y,
                    fill=TRACE_OFF, width=4))
                mobo_canvas.create_text(cpu_x + cpu_w / 2 - 26, clk_y + 14, text="CLK BUS",
                                         fill=SILK_DIM, font=(mono_name, 8))
                mobo_clk_trace_ids.extend(clk_segs)
 
                # ---- FAULT trace: CPU -> RAM, upper channel above the RAM pin strip ----
                fault_y = cpu_y + 24
                f1 = mobo_canvas.create_line(cpu_x + cpu_w, fault_y, ram_x, ram_y + 30,
                                              fill=TRACE_OFF, width=3)
                mobo_canvas.create_text((cpu_x + cpu_w + ram_x) / 2, fault_y - 12, text="FAULT",
                                         fill=SILK_DIM, font=(mono_name, 8))
                mobo_extra_trace_ids["fault"] = [f1]
 
                # ---- VIDEO traces: three separate lines CPU -> Video Output, each its
                #      own fixed channel so multiple traces are visibly distinct and
                #      none crosses the CLK bus, FAULT line, or PMIO/SIO lines ----
                vid_y1 = cpu_y + 90
                vid_y2 = cpu_y + 130
                vid_y3 = cpu_y + 170
                v1 = mobo_canvas.create_line(cpu_x + cpu_w, vid_y1, vid_x, vid_y + 24,
                                              fill=TRACE_OFF, width=3)
                v2 = mobo_canvas.create_line(cpu_x + cpu_w, vid_y2, vid_x, vid_y + 54,
                                              fill=TRACE_OFF, width=3)
                v3 = mobo_canvas.create_line(cpu_x + cpu_w, vid_y3, vid_x, vid_y + 84,
                                              fill=TRACE_OFF, width=3)
                mobo_canvas.create_text((cpu_x + cpu_w + vid_x) / 2, vid_y1 - 12, text="VOUT DATA",
                                         fill=SILK_DIM, font=(mono_name, 8))
                mobo_canvas.create_text((cpu_x + cpu_w + vid_x) / 2, vid_y2 - 12, text="VOUT ADDR",
                                         fill=SILK_DIM, font=(mono_name, 8))
                mobo_canvas.create_text((cpu_x + cpu_w + vid_x) / 2, vid_y3 - 12, text="VOUT STROBE",
                                         fill=SILK_DIM, font=(mono_name, 8))
                mobo_extra_trace_ids["video"] = [v1, v2, v3]
 
                # ---- PMIO traces: CPU <-> Super I/O, and Super I/O -> RAM
                #      (disk reads/writes route through PMIO into RAM) ----
                pmio_y1 = cpu_y + 220
                p1 = mobo_canvas.create_line(cpu_x + cpu_w, pmio_y1, sio_x, sio_y + 30,
                                              fill=TRACE_OFF, width=3)
                mobo_canvas.create_text((cpu_x + cpu_w + sio_x) / 2, pmio_y1 - 12, text="PMIO",
                                         fill=SILK_DIM, font=(mono_name, 8))
                p2 = mobo_canvas.create_line(sio_x, sio_y + sio_h - 30, ram_x + ram_w, ram_y + ram_h - 30,
                                              fill=TRACE_OFF, width=3)
                mobo_canvas.create_text((sio_x + ram_x + ram_w) / 2, sio_y + sio_h - 42, text="SIO\u2194RAM",
                                         fill=SILK_DIM, font=(mono_name, 7))
                mobo_extra_trace_ids["pmio"] = [p1, p2]
 
                # ---- Super I/O activity LEDs (stack depth pins, MSB..LSB of depth) ----
                sio_pin_y = sio_y + 66
                sio_pin_x0 = sio_x + 18
                mobo_canvas.create_text(sio_x + sio_w / 2, sio_pin_y - 12,
                                         text="PMIO STACK DEPTH", fill=SILK_DIM, font=(mono_name, 7))
                sio_bit_span = sio_w - 36
                sio_bit_cell = (sio_bit_span - 7 * 2) / 8
                for i in range(8):
                    bx = sio_pin_x0 + i * (sio_bit_cell + 2)
                    rid = mobo_canvas.create_rectangle(bx, sio_pin_y, bx + sio_bit_cell, sio_pin_y + 12,
                                                        fill=PIN_OFF, outline="#332c0a", width=1)
                    mobo_sio_bit_ids.append(rid)
 
                # ---- Legend (static) ----
                lx, ly = 60, H - 40
                mobo_canvas.create_rectangle(lx, ly, lx + 12, ly + 12, fill=PIN_ON, outline="#332c0a")
                mobo_canvas.create_text(lx + 18, ly + 6, text="= ON", fill=SILK_DIM,
                                         font=(mono_name, 8), anchor="w")
                mobo_canvas.create_rectangle(lx + 70, ly, lx + 82, ly + 12, fill=PIN_OFF, outline="#332c0a")
                mobo_canvas.create_text(lx + 88, ly + 6, text="= OFF", fill=SILK_DIM,
                                         font=(mono_name, 8), anchor="w")
 
            def mobo_tab_is_visible():
                try:
                    return nb.select() == str(mobo_tab)
                except Exception:
                    return False
 
            def _bits16(v):
                return [(v >> b) & 1 for b in range(15, -1, -1)]  # MSB..LSB
 
            def update_motherboard_view(force=False):
                visible = mobo_tab_is_visible()
                just_became_visible = visible and not last_mobo_tab_visible[0]
                last_mobo_tab_visible[0] = visible
 
                if not visible:
                    return  # zero-cost path: nothing below this line runs
 
                if not mobo_built["v"]:
                    build_motherboard()
                force = force or just_became_visible
 
                for r in range(16):
                    newv = Registers.regs16[r]
                    if force or mobo_last_state["regs"][r] != newv:
                        bits = _bits16(newv)
                        ids = mobo_reg_led_ids[r]
                        for i, bit in enumerate(bits):
                            mobo_canvas.itemconfig(ids[i], fill=(PIN_ON if bit else PIN_OFF))
                        mobo_last_state["regs"][r] = newv
 
                if force or mobo_last_state["iF"] != Registers.iF:
                    mobo_canvas.itemconfig(mobo_flag_led_ids["IF"][0],
                                            fill=(PIN_ON if Registers.iF else PIN_OFF))
                    mobo_last_state["iF"] = Registers.iF
 
                if force or mobo_last_state["flag"] != Registers.flag:
                    bits = _bits16(Registers.flag)
                    for i, bit in enumerate(bits):
                        mobo_canvas.itemconfig(mobo_flag_led_ids["FLAGS"][i],
                                                fill=(PIN_ON if bit else PIN_OFF))
                    mobo_last_state["flag"] = Registers.flag
 
                if force or mobo_last_state["ipreg"] != Registers.ip:
                    bits = _bits16(Registers.ip)
                    for i, bit in enumerate(bits):
                        mobo_canvas.itemconfig(mobo_flag_led_ids["IP"][i],
                                                fill=(PIN_ON if bit else PIN_OFF))
                    for i, bit in enumerate(bits):
                        mobo_canvas.itemconfig(mobo_ram_bit_ids[i],
                                                fill=(PIN_ON if bit else PIN_OFF))
                    mobo_last_state["ipreg"] = Registers.ip
 
                if force or mobo_last_state["sp"] != Registers.stack:
                    bits = _bits16(Registers.stack)
                    for i, bit in enumerate(bits):
                        mobo_canvas.itemconfig(mobo_flag_led_ids["SP"][i],
                                                fill=(PIN_ON if bit else PIN_OFF))
                    mobo_last_state["sp"] = Registers.stack
 
                if force or mobo_last_state["cycle"] != Values.cycle:
                    lit = (Values.cycle % 2 == 0)
                    color = TRACE_ON if lit else TRACE_OFF
                    for seg in mobo_clk_trace_ids:
                        mobo_canvas.itemconfig(seg, fill=color)
                    mobo_last_state["cycle"] = Values.cycle
 
                fault_now = bool(Values.T_FAULT)
                if force or mobo_last_state["fault"] != fault_now:
                    color = TRACE_ON if fault_now else TRACE_OFF
                    for seg in mobo_extra_trace_ids["fault"]:
                        mobo_canvas.itemconfig(seg, fill=color)
                    mobo_last_state["fault"] = fault_now
 
                pmio_now = len(Memory.pmio_stack)
                if force or mobo_last_state["pmio_depth"] != pmio_now:
                    color = TRACE_ON if pmio_now > 0 else TRACE_OFF
                    for seg in mobo_extra_trace_ids["pmio"]:
                        mobo_canvas.itemconfig(seg, fill=color)
                    depth_bits = [(pmio_now >> b) & 1 for b in range(7, -1, -1)]
                    for i, bit in enumerate(depth_bits):
                        mobo_canvas.itemconfig(mobo_sio_bit_ids[i],
                                                fill=(PIN_ON if bit else PIN_OFF))
                    mobo_last_state["pmio_depth"] = pmio_now
 
                frame_now = Microcode._last_frame
                if force or mobo_last_state["video_frame"] != frame_now:
                    changed = mobo_last_state["video_frame"] is not None and frame_now != mobo_last_state["video_frame"]
                    color = TRACE_ON if changed else TRACE_OFF
                    for seg in mobo_extra_trace_ids["video"]:
                        mobo_canvas.itemconfig(seg, fill=color)
                    mobo_last_state["video_frame"] = frame_now
 
            # ---- Tab: Live Patch ----
            patch_tab = tk.Frame(nb, bg=PANEL_BG)
            nb.add(patch_tab, text=" LIVE PATCH ")
            patch_grid = tk.Frame(patch_tab, bg=PANEL_BG)
            patch_grid.pack(padx=16, pady=16, anchor="nw")
 
            def patch_row(label, width=10):
                row = tk.Frame(patch_grid, bg=PANEL_BG)
                row.pack(fill="x", pady=4)
                tk.Label(row, text=label, bg=PANEL_BG, fg=FG_DIM, font=(mono_name, 9), width=14, anchor="w").pack(side="left")
                e = tk.Entry(row, bg=PANEL_BG2, fg=FG, insertbackground=FG, width=width,
                             font=(mono_name, 9), relief="flat", highlightbackground=BORDER, highlightthickness=1)
                e.pack(side="left", padx=4)
                return e
 
            tk.Label(patch_grid, text="Writes apply immediately, live, while the VM runs.",
                     bg=PANEL_BG, fg=WARN, font=(mono_name, 8)).pack(anchor="w", pady=(0, 8))
 
            reg_num_e = patch_row("Register #")
            reg_val_e = patch_row("Value")
            def apply_reg_patch():
                try:
                    r = int(reg_num_e.get(), 0)
                    v = int(reg_val_e.get(), 0) & 0xFFFF
                    if 0 <= r <= 15:
                        Registers.regs16[r] = v
                except Exception:
                    pass
            mk_btn(patch_grid, "WRITE REGISTER", apply_reg_patch, 16)
 
            tk.Frame(patch_grid, bg=BORDER, height=1).pack(fill="x", pady=10)
 
            mem_addr_e = patch_row("Memory addr")
            mem_val_e = patch_row("Value")
            def apply_mem_patch():
                try:
                    a = int(mem_addr_e.get(), 0) & 0xFFFF
                    v = int(mem_val_e.get(), 0) & 0xFFFF
                    Memory.memory[a] = v
                except Exception:
                    pass
            mk_btn(patch_grid, "WRITE MEMORY", apply_mem_patch, 16)
 
            tk.Frame(patch_grid, bg=BORDER, height=1).pack(fill="x", pady=10)
 
            ip_patch_e = patch_row("Set IP")
            def apply_ip_patch():
                try:
                    Registers.ip = int(ip_patch_e.get(), 0) & 0xFFFF
                except Exception:
                    pass
            mk_btn(patch_grid, "SET IP", apply_ip_patch, 16)
 
            # ================= STATE for perf-critical loop =================
            num_lines = (Values.MEMORY_SIZE + 15) // 16
            last_line_strings = [None] * num_lines
            last_pc_line = [None]
            last_pc_word_line = [None]
 
            nonzero_count = [sum(1 for b in Memory.memory if b != 0)]
 
            ADDR_PREFIX_LEN = 6
            HEXWORD_LEN = 5
            LEFT_HEX_LEN = HEXWORD_LEN * 8
            GAP_LEN = 2
 
            def hex_col_start(word_index_in_line):
                if word_index_in_line < 8:
                    return ADDR_PREFIX_LEN + word_index_in_line * HEXWORD_LEN
                right_index = word_index_in_line - 8
                return ADDR_PREFIX_LEN + LEFT_HEX_LEN + GAP_LEN + right_index * HEXWORD_LEN
 
            def format_line(i, decompile):
                chunk = Memory.memory[i:i + 16]
                if len(chunk) < 16:
                    chunk = list(chunk) + [0] * (16 - len(chunk))
                hex_left = " ".join(f"{b:04X}" for b in chunk[:8])
                hex_right = " ".join(f"{b:04X}" for b in chunk[8:])
                hex_part = f"{hex_left:<23}  {hex_right:<23}"
                ascii_part = " ".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
                line = f"{i:04X}: {hex_part}  {ascii_part}"
                if decompile:
                    line += "   " + decompile_line_quick(i, chunk)
                return line
 
            def decompile_line_quick(base_addr, chunk):
                out, pos, n = [], 0, len(chunk)
                while pos < n:
                    op = chunk[pos]
                    entry = OPVAL_TO_MNEM.get(op)
                    if entry is None:
                        out.append(f".{op:04X}")
                        pos += 1
                        continue
                    mnem, argc = entry
                    pos += 1
                    if mnem == "MOV":
                        pos += 4
                        out.append("MOV")
                    elif mnem == "LOAD":
                        out.append("LOAD")
                        pos += 1
                    elif argc == 0:
                        out.append(mnem)
                    else:
                        out.append(mnem)
                        pos += argc
                return " ".join(out)
 
            def build_initial_memory_view():
                mem_text.config(state="normal")
                lines = []
                decompile = decompile_var.get()
                for idx, i in enumerate(range(0, Values.MEMORY_SIZE, 16)):
                    line = format_line(i, decompile)
                    last_line_strings[idx] = line
                    lines.append(line)
                mem_text.insert("1.0", "\n".join(lines))
                mem_text.config(state="disabled")
 
            build_initial_memory_view()
 
            def visible_line_range():
                top_frac, bottom_frac = mem_text.yview()
                total = num_lines
                first = max(0, int(top_frac * total) - 2)
                last = min(total - 1, int(bottom_frac * total) + 2)
                return first, last
 
            def update_memory_view():
                decompile = decompile_var.get()
                first, last = visible_line_range()
 
                mem_text.config(state="normal")
                for idx in range(first, last + 1):
                    i = idx * 16
                    line = format_line(i, decompile)
                    if line != last_line_strings[idx]:
                        last_line_strings[idx] = line
                        line_no = idx + 1
                        mem_text.delete(f"{line_no}.0", f"{line_no}.end")
                        mem_text.insert(f"{line_no}.0", line)
 
                pc_addr = Registers.ip
                pc_line_index = pc_addr // 16
                if last_pc_line[0] != pc_line_index:
                    if last_pc_line[0] is not None:
                        mem_text.tag_remove("pc", f"{last_pc_line[0] + 1}.0", f"{last_pc_line[0] + 1}.end")
                    mem_text.tag_add("pc", f"{pc_line_index + 1}.0", f"{pc_line_index + 1}.end")
                    last_pc_line[0] = pc_line_index
 
                word_in_line = pc_addr % 16
                col_start = hex_col_start(word_in_line)
                col_end = col_start + 4
                line_no = pc_line_index + 1
                pc_word_range = (line_no, col_start, col_end)
                if last_pc_word_line[0] != pc_word_range:
                    if last_pc_word_line[0] is not None:
                        pl, ps, pe = last_pc_word_line[0]
                        mem_text.tag_remove("pc_word", f"{pl}.{ps}", f"{pl}.{pe}")
                    mem_text.tag_add("pc_word", f"{line_no}.{col_start}", f"{line_no}.{col_end}")
                    last_pc_word_line[0] = pc_word_range
 
                if follow_pc["v"]:
                    mem_text.see(f"{pc_line_index + 1}.0")
 
                mem_text.config(state="disabled")
 
            def format_xasm16a(base_addr, chunk, pos_start):
                pos = pos_start
                n = len(chunk)
                line_addr = base_addr + pos
                op = chunk[pos]
                entry = OPVAL_TO_MNEM.get(op)
                if entry is None:
                    return line_addr, f"DW 0x{op:04X}", pos + 1
 
                mnem, argc = entry
                pos += 1
 
                if mnem == "MOV":
                    mode_a = chunk[pos] if pos < n else 0
                    mode_b = chunk[pos + 1] if pos + 1 < n else 0
                    dest = chunk[pos + 2] if pos + 2 < n else 0
                    src = chunk[pos + 3] if pos + 3 < n else 0
                    dest_s = fmt_mov_operand(mode_a, dest)
                    src_s = fmt_mov_operand(mode_b, src)
                    return line_addr, f"mov {dest_s}, {src_s}", pos + 4
 
                elif mnem in ("ADD", "SUB", "MUL", "DIV"):
                    a = chunk[pos] if pos < n else 0
                    b = chunk[pos + 1] if pos + 1 < n else 0
                    d = chunk[pos + 2] if pos + 2 < n else 0
                    return line_addr, f"{mnem.lower()} r{d}, r{a}, r{b}", pos + 3
 
                elif mnem in ("JMP", "CALL", "JE", "JNE", "JH", "JL"):
                    addr = chunk[pos] if pos < n else 0
                    return line_addr, f"{mnem.lower()} 0x{addr:04X}", pos + 1
 
                elif mnem in ("PUSH", "POP", "INC", "DEC", "IN", "JPTR", "MOVSP"):
                    r = chunk[pos] if pos < n else 0
                    return line_addr, f"{mnem.lower()} r{r}", pos + 1
 
                elif mnem == "CMP":
                    a = chunk[pos] if pos < n else 0
                    b = chunk[pos + 1] if pos + 1 < n else 0
                    return line_addr, f"cmp r{a}, r{b}", pos + 2
 
                elif mnem == "NUMP":
                    a = chunk[pos] if pos < n else 0
                    b = chunk[pos + 1] if pos + 1 < n else 0
                    return line_addr, f"nump r{a}, r{b}", pos + 2
 
                elif mnem == "INT":
                    addr = chunk[pos] if pos < n else 0
                    return line_addr, f"int 0x{addr:04X}", pos + 1
 
                elif mnem == "MOVFS":
                    dest = chunk[pos] if pos < n else 0
                    src = chunk[pos + 1] if pos + 1 < n else 0
                    src_s = MOVFS_REV.get(src, f"?{src}")
                    return line_addr, f"movfs r{dest}, {src_s}", pos + 2
 
                elif mnem == "LOAD":
                    reg = chunk[pos] if pos < n else 0
                    chars = []
                    p = base_addr + pos + 1
                    for _ in range(48):
                        if p >= Values.MEMORY_SIZE:
                            break
                        ch = Memory.memory[p]
                        if ch == 0:
                            break
                        chars.append(chr(ch) if 32 <= ch <= 126 else "?")
                        p += 1
                    text = "".join(chars)
                    return line_addr, f'load r{reg}, "{text}"', pos + 1 + len(chars) + 1
 
                elif argc == 0:
                    return line_addr, mnem.lower(), pos
 
                else:
                    args = [str(chunk[pos + k]) if pos + k < n else "0" for k in range(argc)]
                    return line_addr, f"{mnem.lower()} " + ", ".join(args), pos + argc
 
            def disasm_tab_is_visible():
                try:
                    return nb.select() == str(disasm_tab)
                except Exception:
                    return False
 
            def update_disasm_view(force=False):
                visible = disasm_tab_is_visible()
 
                just_became_visible = visible and not last_disasm_tab_visible[0]
                last_disasm_tab_visible[0] = visible
 
                if not visible:
                    return
 
                ip_changed = Registers.ip != last_ip_for_disasm["v"]
                if not (force or ip_changed or just_became_visible):
                    return
 
                last_ip_for_disasm["v"] = Registers.ip
 
                pc = Registers.ip
                start = max(0, pc - 24)
                window = Memory.memory[start:start + 96]
 
                disasm_text.config(state="normal")
                disasm_text.delete("1.0", "end")
                pos = 0
                cur_line_idx = None
                line_no = 0
                while pos < len(window):
                    line_addr, text, next_pos = format_xasm16a(start, window, pos)
                    is_cur = (line_addr == pc)
                    disasm_text.insert("end", f"{line_addr:04X}   {text}\n")
                    if is_cur:
                        cur_line_idx = line_no
                    line_no += 1
                    if next_pos <= pos:
                        pos += 1
                    else:
                        pos = next_pos
 
                if cur_line_idx is not None:
                    disasm_text.tag_add("cur", f"{cur_line_idx + 1}.0", f"{cur_line_idx + 1}.end")
                    disasm_text.see(f"{cur_line_idx + 1}.0")
                disasm_text.config(state="disabled")
 
            # Redraw immediately whenever the user clicks onto a tab,
            # instead of waiting for the next 50ms poll to notice.
            def on_tab_changed(event=None):
                update_disasm_view(force=disasm_tab_is_visible())
                update_motherboard_view(force=mobo_tab_is_visible())
            nb.bind("<<NotebookTabChanged>>", on_tab_changed)
 
            def update_pmio_view():
                stack = Memory.pmio_stack
                snap = tuple(stack[-40:])
                if snap == last_pmio_snapshot[0]:
                    return
                last_pmio_snapshot[0] = snap
                pmio_text.config(state="normal")
                pmio_text.delete("1.0", "end")
                if not stack:
                    pmio_text.insert("end", "(empty)")
                else:
                    for v in reversed(snap):
                        pmio_text.insert("end", f"{b16hex(v)}\n")
                pmio_text.config(state="disabled")
 
            def update_sparkline():
                spark_history.pop(0)
                spark_history.append(Values.SPEED)
                spark.delete("all")
                w = spark.winfo_width() or 300
                h = spark.winfo_height() or 70
                mx = max(spark_history) or 1
                step = w / (len(spark_history) - 1)
                pts = []
                for i, v in enumerate(spark_history):
                    x = i * step
                    y = h - (v / mx) * (h - 10) - 4
                    pts.extend([x, y])
                if len(pts) >= 4:
                    spark.create_line(*pts, fill=ACCENT, width=2, smooth=True)
                spark.create_text(6, 6, anchor="nw", fill=FG_DIM,
                                   font=(mono_name, 8), text=f"{Values.SPEED} kHz")
 
            sparkline_tick = [0]
 
            def return_guest_usage_percent():
                current_clock = Values.SPEED
                theory_max = 1250
                # Theoretical max clock was calculated by:
                """
                  l:
                    jmp l
                """
                # clock was measured while the loop ran.
 
                percent = round((current_clock / theory_max) * 100, 1)

                # still cap the clock to 0-100
                percent = numbertools.cap(percent, 0, 100) 

                return percent
 
            def update_gui():
                try:
                    mem_usage_count = nonzero_count[0]
                    mem_usage = round((mem_usage_count / len(Memory.memory)) * 100, 1)
                    host_usage = psutil.cpu_percent(interval=None)
 
                    mem_val.config(text=f"{mem_usage}% {mem_usage_count}/{Values.MEMORY_SIZE}B")
                    host_val.config(text=f"{host_usage}%")
                    speed_val.config(text=f"{Values.SPEED}kHz")
                    guest_val.config(text=f"{return_guest_usage_percent()}%")
                    depth_val.config(text=f"{len(Memory.pmio_stack)}")
                    if_val.config(text=f"{Registers.iF}")
 
                    for i, lab in enumerate(reg_value_labels):
                        newv = Registers.regs16[i]
                        if reg_prev_values[i] != newv:
                            lab.config(text=f"0x{newv:04X}")
                            reg_cells[i].config(bg=FLASH_BG)
                            lab.config(bg=FLASH_BG)
                            root.after(150, lambda c=reg_cells[i], l=lab: (c.config(bg=PANEL_BG2), l.config(bg=PANEL_BG2)))
                            reg_prev_values[i] = newv
 
                    ip_val.config(text=f"0x{Registers.ip:04X}")
                    sp_val.config(text=f"0x{Registers.stack:04X}")
                    flag_val.config(text=f"0x{Registers.flag:04X}")
                    cycle_label.config(text=f"cycles: {Values.cycle}")
 
                    yview = mem_text.yview()
                    update_memory_view()
                    if not follow_pc["v"]:
                        mem_text.yview_moveto(yview[0])
 
                    update_pmio_view()
 
                    sparkline_tick[0] += 1
                    if sparkline_tick[0] % 5 == 0:
                        update_sparkline()
 
                    # Called every tick now; internally no-ops unless the
                    # tab is visible AND (IP changed OR tab just became
                    # visible). This replaces the old outer IP-changed
                    # gate, which could "consume" an IP-change event while
                    # the tab was hidden and leave it permanently stale.
                    update_disasm_view()
 
                    # Same no-op-when-hidden contract as update_disasm_view():
                    # returns immediately unless the Motherboard tab is the
                    # one currently selected.
                    update_motherboard_view()
 
                    root.after(update_interval, update_gui)
                except Exception:
                    root.destroy()
 
            def refresh_nonzero_count():
                nonzero_count[0] = sum(1 for b in Memory.memory if b != 0)
                root.after(2000, refresh_nonzero_count)
 
            root.after(2000, refresh_nonzero_count)
            update_gui()
            root.mainloop()
 
        except Exception:
            sys.exit(1)
 
    threading.Thread(target=gui_thread, daemon=True).start()
 
 
 
def b16hex(i: int) -> str:
    i &= 0xFFFF
    return f"0x{i:04x}"
LOG_PATH = "C:/python/real16bit/log.txt" # not used anymore, wont bother changing.
 
def LoadImage(cpath: str, noPrintStatusString=False) -> list[int]:
    if not noPrintStatusString:
        print(f"{colors.BRIGHT_BLACK}Loading image: {cpath}{colors.RESET}")
 
    words = []
    with open(cpath, 'rb') as f:
       while True:
         data = f.read(2)
         if len(data) == 0:
            break    
 
         if len(data) != 2:
            CriticalError(12)
        
         words.append(int.from_bytes(data, "big"))
         # Would add a sleep here but that makes it very slow
         # cpu go brrrr
 
    return words
 
 
def CriticalError(ec):
    clearscreen()
    hide_cursor()
    
    # Expects ec to be valid, python error if not.
 
    error_code_map = {
        55: f"{colors.BG_RED + colors.BLACK}VMBIOS Not Present!{colors.RESET}\n\n{colors.BRIGHT_BLACK}Flash to: {BIOS_PATH}{colors.RESET}",
        12: f"{colors.BG_RED + colors.BLACK}LoadImage(): Image is corrupt.{colors.RESET}"
    }
 
 
    print(f"{colors.YELLOW}VM Error (Code {hex(ec)}){colors.RESET}\n\n\n\n")
   
    print((colors.YELLOW) + "=" * int(os.get_terminal_size().columns - 2))
    print(error_code_map[int(ec)])
    print((colors.YELLOW) + "=" * int(os.get_terminal_size().columns - 2))
   
    print(f"\n\n\n{colors.BRIGHT_PURPLE}CriticalError(): process ended with error code {hex(ec)}")
    input(f"Press any key to continue...{colors.RESET}")
    sys.exit(int(ec))
 
 
def Flopper():
    while True:
        time.sleep(0.04)
        address = random.randint(0, Values.MEMORY_SIZE - 1)
        bit_position = random.randint(0, 7)
        Memory.memory[address] ^= (1 << bit_position)
 
def Compile(apath: str, cpath: str, noPrintStatusString=False) -> None:
    if not noPrintStatusString:
        print(f"Compiling asm source from '{apath}' to '{cpath}'")

    mc = assemble_source(open(apath, "r").read())
    open(apath, "r").close()
    with open(cpath, 'wb') as f:
         for word in mc:
             f.write(word.to_bytes(2, 'big'))
 
 
USE_BIOS_COMPAT = False
def Terminal():
    global USE_BIOS_COMPAT
 
    # Terminal that can be accessed by holding ctrl at startup
    # If it is not being held, VM will boot from harddisk
    default_image = r"C:\python\VSK-E16A Workspace/VM Harddisk/VMDisk.vhd"
    time.sleep(0.7)
    if not is_ctrl_held():
        load_to_memory(0, LoadImage(default_image))
        if keyboard.is_pressed('p'):
          print("Normal VM boot has been paused. (you were holding P)")
          print("Press enter to boot.")
          while True:
            if keyboard.is_pressed('return'):
               break
          time.sleep(0.1)
        return
 
    print(f"{colors.RED}+" + ("=" * int(os.get_terminal_size().columns - 3)) + f"+\nVSK-E16A Virtual Machine pre-run shell\nType 'help' for a list of commands.\nType 'run -d' to boot vm from harddisk")
    print(colors.BRIGHT_BLACK)
 
    def track_com(w: str, args: list[str]) -> None:
        print(f"{w}...\nArgs:\n{', '.join(args)}")
    while True:
      try:
       command = input(f"{colors.BRIGHT_BLACK}/> ")
       if not command:
           continue
       operands = command.split()[1:]
       operands = ' '.join(operands)
       operands = operands.split(",")
       command = command.split()[0]
      
       if command == "help":
           print(f"""
Arguments:
(required)
[optional]                
 
Inbuilt commands:\n
image (asm path) (image path):    Build a disk image from assembly text file
load (image path):                Load a disk image into memory.
run [-d] [-fun]:                  Run the emulator, booting whatever is in memory.           
                                      [-d]: Boot from the emulator harddisk.
                                    [-fun]: Run the random bit flopper.
boot (image path):                Boot a .img file.
erase:                            Erase the emulator virtual disk.
packrun (asm path):               Compile and boot an assembly file without saving an image.
update:                           Re-compile BIOS from the source folder. 
enabcsm:                          Enable compatibility for older BIOSes.
launchdbg:                        Launch the debug GUI early.   
 
exit:                             Shutdown the VM.
help:                             Show this menu.
 
                
 
                 """)
       
       elif command == "image":
           if len(operands) < 2:
               print("Invalid amount of operands.")
               continue
           track_com('compiling', operands)
           asm_path = operands[0]
           compile_path = operands[1]
           Compile(asm_path.strip(), compile_path.strip())
           print(f"{colors.BRIGHT_BLACK}Success!")
 
 
       
       elif command == "load":
           if len(operands) == 0:
               print("Invalid amount of operands")
               continue
           track_com('loading', operands)
           path = operands[0]
           print("Loading the image")
           mem = LoadImage(path)
           print("Loading the words into VM memory")
           load_to_memory(0, mem)
           print("Done. Type 'run' to boot.")
 
       elif command == "run":
           for op in operands:
             if op.strip() == "-fun":
                 threading.Thread(target=Flopper, daemon=True).start()
                 print("the RBF machine is running!")
                 time.sleep(1)
             if op.strip() == "-d":
                 load_to_memory(0, LoadImage(default_image))
           break
      
       elif command == "boot":
           if len(operands) == 0:
               print("Invalid amount of operands")
               continue
           image = operands[0]
           load_to_memory(0, LoadImage(image))
           print("Booting image into memory...")
           break
       elif command == "erase":
           init_vm_disk()
       elif command == "packrun":
           asm_path = operands[0]
           with open(asm_path, "r") as f:
               code = f.read()
               b = assemble_source(code)
               load_to_memory(0, b)
               break
       elif command == "update":
           Compile("C:/python/VSK-E16A Workspace/Source/bios.xsm", BIOS_PATH)
           print(colors.BRIGHT_BLACK)
 
       elif command == "enabcsm":
           print("enabcsm: No longer implemented.")
          
       elif command == "exit":
           time.sleep(1)
           sys.exit(0)
 
       elif command == "launchdbg":
           open_debug_gui()
 
      except Exception as e:
          print(f"{colors.RED}|!|{colors.BRIGHT_BLACK} " + str(e))
          continue
 
 
print(colors.RESET)
BIOS_PATH = r"C:\python\VSK-E16A Workspace\VMBIOS\BIOS.rom"
#==========# STATIC CLASSES     #==========#
 
class Values:
 
     PMIO_STACK_SIZE         = 256       # Size of the PMIO stack
     MASK_16                 = 0xFFFF    # 16-bit mask value
     MEMORY_SIZE             = 2**16     # Size of VM memory (64K)
     VIDEO_MEMORY_3839_MODE  = 0xF100
     cycle                   = 0
     INTERVAL                = 100
     T_FAULT                 = False
     IVT                     = 0x0000
     IVT_END                 = 0x00FF
     SPEED                   = 0
     REFRESH_INTERVAL        = 0.03333   # 30hz (seconds)
     CAN_REFRESH             = False
 
 
class InterruptVectors:
     ERROR     = 0x00FF
     KEYBOARD  = 0x00EF
 
class Registers:
 
     ip       = 0               # Hardware reserved - Instruction Pointer
     regs16   = [0] * 16        # General purpose   - 16-bit GPRs
     flag     = 0               # Hardware reserved - Flags Register
     stack    = 0x0000          # Hardware reserved - Stack Pointer
     iF       = 0               # Hardware reserved - HWI Interrupt Flag
 
    
            
class Memory:
    
     memory = give.memoryarray(Values.MEMORY_SIZE)
     pmio_stack = [] # Empty because it uses a append and size check instead of being 256 0s.
 
#==========# LOGGING            #==========#    
 
 
 
cmd = (
    'cmd /c start /min cmd /k '
    '"title Virtual Machine Log & color 08 & '
    'powershell -NoExit -Command Get-Content -Path C:\\python\\real16bit\\log.txt -Wait"' # also not used anymore, wont bother changing.
)
 
#==========# PASS 1 (ASSEMBLY)  #==========#
 
class Mode():
    REGISTER = 1
    IMMEDIATE = 2
    MEMORY_REGISTER = 3
    MEMORY_ADDRESS = 4
 
 
opcode_sizes = {
    "HLT": 1,
    "MOV": 5,
    "VOUT": 1,
    "ADD": 4,
    "SUB": 4,
    "MUL": 4,
    "DIV": 4,
    "JMP": 2,
    "CALL": 2,
    "RET": 1,
    "PUSH": 2,
    "POP": 2,
    "CMP": 3,
    "JE": 2,
    "JNE": 2,
    "DHI": 1,
    "INC": 2,
    "DEC": 2,
    "NUMP": 3,
    "EHI": 1,
    "IN": 2,
    "INT": 2,
    "JH": 2,
    "JL": 2,
    "CLF": 1,
    "JPTR": 2,
    "MOVFS": 3,
    "MOVSP": 2,
    "OUT": 1,   
    "SHLT": 1,  
}
 
def assemble_source(source: str) -> list[int]:
     # %include no longer exists - that never worked with the vscode extensions, so goodbye.
    
     print(f"{colors.CYAN}Please wait whilst the code is compiled{colors.RESET}")
 
     def parse_dw_operands(ops: list[str | int]) -> list[int]:
         res = []
    
         # re-join in case multiple raw chunks were passed, then split on
         # commas that aren't inside quotes
         joined = ",".join(str(o) for o in ops)
         parts = re.findall(r'"[^"]*"|\'[^\']*\'|[^,]+', joined)
    
         for op in parts:
             op = op.strip()
             op = strip_quotes(op)
    
             # dec
             if numbertools.strict_is_int(op):
                 res.append(int(op, 0))
    
             # hex
             elif op.startswith("0x"):
                 res.append(int(op, 0))
    
             # str
             else:
                 res.extend(ord(c) for c in op)
    
         return res
 
     def get_mode(s: str) -> Mode:
          s = s.strip(",").lower()
          match s:
              case _ if s.startswith("[") and s.endswith("]"):
                  inner = s[1:-1].strip()
                  if inner.startswith("r") and inner[1:].isdigit():
                      return Mode.MEMORY_REGISTER
                  return Mode.MEMORY_ADDRESS
     
              case _ if s.startswith("r") and s[1:].isdigit():
                  return Mode.REGISTER
     
              case _:
                  try:
                      int(s, 0)  
                      return Mode.IMMEDIATE
                  except ValueError:
                      raise ValueError(f"Unknown operand: {s}")
 
     def strip_quotes(s: str) -> str:
          return s.lstrip('"').rstrip('"').lstrip("'").rstrip("'")
               
     _bytes = [0] * Values.MEMORY_SIZE
     lines = [line.strip() for line in source.splitlines() if line.strip()]
     _pointer = 0
 
     def resolve_labels():
      print("Finding labels...")
      labels = {}
      pointer = 0
      for line in lines:
        if line.startswith(";"):
            continue
        if line.endswith(":"):
            labels[line[:-1]] = pointer
            continue
        opcode = line.split()[0].upper()
        if opcode == "%REGION":
            loc = int(line.split()[1], 0)
            pointer = loc
            continue
        if opcode == "%DEFINEWORD":
             raw = line.split(maxsplit=1)[1]
             pointer += len(parse_dw_operands([raw]))
             continue
        if opcode.startswith("%"):
            continue
        if opcode == "LOAD":
            str_text = strip_quotes(line.split(maxsplit=2)[2])
            pointer += 2 + len(str_text) + 1
            continue
        pointer += opcode_sizes.get(opcode, 0)
      print(f"Found {len(labels.keys())} labels.")
 
      return labels
     labels = resolve_labels()
 
     def _next(value: int) -> None:
          nonlocal _pointer
          _bytes[_pointer] = int(value)
          _pointer += 1
 
     for line in lines:
          print(f"\rCompiling... {len([d for d in _bytes if d != 0])} words written", end="")
          time.sleep(0.0005) # Sleep for a bit to reduce CPU usage
          opcode = line.split()[0].upper()
          operands = [strip_quotes(op) for op in line.split()[1:]]
 
          if not line:
               continue
         
          elif line.startswith(";") or line.endswith(":"):
               continue
 
          elif opcode == "HLT":
               _next(1)
 
          elif opcode == "MOV":
               if operands[0] in labels:
                   operands[0] = str(labels[operands[0]])
               if operands[1] in labels:
                   operands[1] = str(labels[operands[1]])
               mode_a = get_mode(operands[0])
               mode_b = get_mode(operands[1])
               stripped_dest = operands[0].strip(",[]").lstrip("r")
               stripped_src = operands[1].strip(",[]").lstrip("r")
               dest = int(stripped_dest, 0)
               src = int(stripped_src, 0)
               _next(4)
               _next(mode_a)
               _next(mode_b)
               _next(dest)
               _next(src)
 
          elif opcode == "VOUT":
               _next(8)
         
          elif opcode == "%ASC":
               address = int(operands[0], 0)
               word = strip_quotes(operands[1])
               word = ord(word)
               _bytes[address] = word
 
          elif opcode in ("ADD", "SUB", "MUL", "DIV"):
               _next({
                   "ADD": 12,
                   "SUB": 16,
                   "MUL": 20,
                   "DIV": 24
               }[opcode])
               _next(int(operands[0].strip("r,"), 0))
               _next(int(operands[1].strip("r,"), 0))
               _next(int(operands[2].strip("r,"), 0))
         
          elif opcode == "JMP":   
               _next(28)
               target = operands[0]
               if target in labels:
                   _next(labels[target])
               else:
                   _next(int(target, 0))
         
          elif opcode == "%STR":
               address = int(operands[0], 0)
               word = strip_quotes(' '.join(operands[1:]))
               start = address 
               offset = 0
               for l in word:
                    _bytes[start + offset] = ord(l)
                    offset += 1
         
          elif opcode == "CALL":
              _next(32)
              _next(labels[operands[0]])
             
          elif opcode == "RET":
              _next(36)
         
          elif opcode == "PUSH":
              _next(40)
              _next(int(operands[0].strip("r"),0))
         
          elif opcode == "POP":
              _next(44)
              _next(int(operands[0].strip("r"),0))
          elif opcode == "CMP":
              reg_a = operands[0].strip(",").strip("r")
              reg_b = operands[1].strip("r")
              _next(48)
              _next(int(reg_a, 0))
              _next(int(reg_b, 0))
          elif opcode == "JE":
               _next(52)
               target = operands[0]
               if target in labels:
                   _next(labels[target])
               else:
                   _next(int(target, 0))
          elif opcode == "JNE":
               _next(56)
               target = operands[0]
               if target in labels:
                   _next(labels[target])
               else:
                   _next(int(target, 0))
 
          elif opcode == "DHI":
              _next(60)
        
          elif opcode == "LOAD":
               reg = int(operands[0].strip(",").lstrip("r"), 0)
               str_text = strip_quotes(line.split(maxsplit=2)[2])
               _next(64)
               _next(reg)
               for ch in str_text:
                   _next(ord(ch))
               _next(0)   # terminator
         
          elif opcode == "INC":
              _next(68)
              _next(int(operands[0].strip(",").lstrip("r"), 0))
          
          elif opcode == "DEC":
              _next(72)
              _next(int(operands[0].strip(",").lstrip("r"), 0))
 
          elif opcode == "%REGION":
              loc = int(operands[0], 0)
              _pointer = loc
          elif opcode == "NUMP":
               addr_reg = int(operands[0].strip(",").lstrip("rR"), 0)
               value_reg = int(operands[1].strip(",").lstrip("rR"), 0)
               _next(76)
               _next(addr_reg)
               _next(value_reg)
          elif opcode == "EHI":
              _next(78)
          elif opcode == "IN":
              _next(80)
              _next(int(operands[0].strip(",").lstrip("rR"), 0))
   
          elif opcode == "INT":
              addr = int(operands[0], 0)
              _next(82)
              _next(addr)
          elif opcode == "%DEFINEWORD":
              raw = line.split(maxsplit=1)[1]
              wds = parse_dw_operands([raw])
              for b in wds:
                  _next(b)
          elif opcode == "JH":
              _next(84)
              target = operands[0]
              if target in labels:
                  _next(labels[target])
              else:
                  _next(int(target, 0))
          elif opcode == "JL":
              _next(86)
              target = operands[0]
              if target in labels:
                 _next(labels[target])
              else:
                 _next(int(target, 0))
          elif opcode == "CLF":
              _next(88)
          elif opcode == "JPTR":
              _next(90)
              _next(int(operands[0].lstrip('r'), 0))
          elif opcode == "MOVFS":
              MAP = {
                  "rFLAGS": 44,
                  "rIP": 99,
                  "rSP": 22
              }
              _next(92)
              _next(int(operands[0].strip("r,"), 0))
              _next(MAP[operands[1]])
          elif opcode == "MOVSP":
              _next(94)
              _next(int(operands[0].strip("r"), 0))
          elif opcode == "OUT":
              _next(96)
          elif opcode == "SHLT":
              _next(98)
 
        
     print()     
     return [int(b) & Values.MASK_16 for b in _bytes]
 
def enable_compatibility_in_image(img):
    pointer = 0
    fillin = """
mov r1, 0xEFFF
movsp r1
jmp 0x0600
 
"""
    fillin = assemble_source(fillin)
    while True:
        img[pointer] = fillin[pointer]
        pointer += 1
        if pointer > 9:
            break
 
    return img
    
 
def load_bios(csm=False, noPrintStatusString=False):
    if not os.path.exists(BIOS_PATH):
        CriticalError(55)
 
    code = LoadImage(BIOS_PATH, noPrintStatusString)
    if csm:
        pass # legacy feature, removed.
    END = 0x10FE # BIOS has grown, legacy boot is GONE.
    start = 0x0000
    offset = 0
    for b in code:
        if (start + offset) > END:
            break
        Memory.memory[start + offset] = int(b) & Values.MASK_16
        offset += 1
   
 
 
def init_vm_disk():
    data = [0] * Values.MEMORY_SIZE
    with open("C:\python\VSK-E16A Workspace/VM Harddisk/VMdisk.vhd", "wb") as f:
        for i, b in enumerate(data):
            f.write(b.to_bytes(2, 'big'))
            print(f"\r{i}/{Values.MEMORY_SIZE}B Done.", end="", flush=True)
    print("\nVM disk erased.\nbig endian\n128KiB")
   
 
 
 
 
def load_to_memory(addr: int, values: bytearray, change_limit=Values.MEMORY_SIZE) -> None:
    start = addr
    offset = 0
    for _ in values:
        if (start + offset) >= Values.MEMORY_SIZE:
            break
        if (start + offset) >= change_limit:
            break
 
        Memory.memory[start + offset] = values[offset]
        offset += 1
 
 
Terminal()
open_debug_gui()
#==========# PASS 2 (EXECUTION) #==========#
 
class Microcode:
          # The reason overflow checks and other stuff are not done via a function is simply because its slightly faster.
          _last_frame = None # For extended_mode_refresh()
    
 
          def _halt():
               Values.T_FAULT = False
               Finally()
 
   
          def _ret():
              if Registers.stack < 0x0000:
                  raise StackOverflow
             
              if Registers.stack > 0xFFFF:
                  raise StackUnderflow
              Registers.ip = Memory.memory[Registers.stack]
              Registers.stack += 1
 
  
          def _call():
              address = cpu16._next()
              if address < 0x0000 or address > 0xFFFF:
                  raise RoutineAddressOutOfBounds
 
              Registers.stack -= 1
              if Registers.stack < 0x0000:
                  raise StackOverflow
             
              if Registers.stack > 0xFFFF:
                  raise StackUnderflow
              Memory.memory[Registers.stack] = Registers.ip
         
              Registers.ip = address
 
    
          def _pop():
              reg = cpu16._next()
              if reg < 0 or reg > 15:
                  raise RegisterOutOfBounds
 
              Registers.regs16[reg] = Memory.memory[Registers.stack]
              Registers.stack += 1
 
 
          def _push():
              reg = cpu16._next()
              if reg < 0 or reg > 15:
                  raise RegisterOutOfBounds
 
              Registers.stack -= 1
              if Registers.stack < 0x0000:
                  raise StackOverflow
             
              if Registers.stack > 0xFFFF:
                  raise StackUnderflow
              Memory.memory[Registers.stack] = Registers.regs16[reg]
   
          def _mov():
            try:
              mode_a = cpu16._next()
              mode_b = cpu16._next()
              dest = cpu16._next()
              src = cpu16._next()
             
            
              if mode_b == 1:  
                  src = Registers.regs16[src]
             
              elif mode_b == 2:   
                  pass
             
              elif mode_b == 3:   
                  src = Memory.memory[Registers.regs16[src]]
             
              elif mode_b == 4:
                  src = Memory.memory[src]
             
     
              if mode_a == 1:    
                  Registers.regs16[dest] = src & Values.MASK_16
             
              elif mode_a == 3:   
                  Memory.memory[Registers.regs16[dest]] = src & Values.MASK_16
             
              elif mode_a == 4:
                  Memory.memory[dest] = src & Values.MASK_16
 
            except:
                raise OpcodeTrap
         
      
          # _video_out (vout - opcode 0x8) has been replaced by a refresh thread.
          # may he live forever in our hearts.
 
   
          def _math(op):
            try:
              a = cpu16._next()
              b = cpu16._next()
              dest = cpu16._next()
         
              if op == "ADD":
                  result = Registers.regs16[a] + Registers.regs16[b]
         
              elif op == "SUB":
                  result = Registers.regs16[a] - Registers.regs16[b]
         
              elif op == "MUL":
                  result = Registers.regs16[a] * Registers.regs16[b]
         
              elif op == "DIV":
                  result = Registers.regs16[a] // Registers.regs16[b]
         
              Registers.regs16[dest] = result & Values.MASK_16
 
            except:
                raise OpcodeTrap
 
 
          def _jump():
               a = cpu16._next()
               if a < 0x0000 or a > 0xFFFF:
                   raise RoutineAddressOutOfBounds
               Registers.ip = a & Values.MASK_16
 
  
          def _compare():
              reg_a = cpu16._next()
              reg_b = cpu16._next()
              if (reg_a < 0 or reg_a > 15) or (reg_b < 0 or reg_b > 15):
                  raise RegisterOutOfBounds
 
              #EQ
              if Registers.regs16[reg_a] == Registers.regs16[reg_b]:
                  Registers.flag = 1
 
              #HI
              if Registers.regs16[reg_a] > Registers.regs16[reg_b]:
                  Registers.flag = 2
 
              #LO
              if Registers.regs16[reg_a] < Registers.regs16[reg_b]:
                  Registers.flag = 3
           
              
          
     
          def _je():
              addr = cpu16._next()
              if addr < 0 or addr > 0xFFFF:
                  raise RoutineAddressOutOfBounds
              if Registers.flag == 1:
                  Registers.ip = addr
 
     
          def _jne():
              addr = cpu16._next()
              if addr < 0 or addr > 0xFFFF:
                  raise RoutineAddressOutOfBounds
              if Registers.flag != 1 and Registers.flag != 0xFFFF:
                  Registers.ip = addr
 
          def _enable_hwi():
              Registers.iF = 1
         
          def _disable_hwi():
              Registers.iF = 0
 
          def _einval():
              pass

 
          def _load():
            try:
              reg = cpu16._next()
              if reg < 0 or reg > 15:
                  raise RegisterOutOfBounds
              base = Registers.regs16[reg]
              offset = 0
              while True:
                  ch = cpu16._next()
                  if ch == 0:
                      Memory.memory[base + offset] = 0
                      break
                  Memory.memory[base + offset] = ch
                  offset += 1
 
            except:
                raise OpcodeTrap
 
 
          def _inc():
              r = cpu16._next()
              if r < 0 or r > 15:
                  raise RegisterOutOfBounds
              Registers.regs16[r] += 1
              Registers.regs16[r] &= Values.MASK_16
 

          def _dec():
              r = cpu16._next()
              if r < 0 or r > 15:
                  raise RegisterOutOfBounds
              Registers.regs16[r] -= 1
              Registers.regs16[r] &= Values.MASK_16
 
   
          def _nump():
            try:
              addr_reg = cpu16._next()
              value_reg = cpu16._next()
              if addr_reg < 0 or addr_reg > 15:
                  raise RegisterOutOfBounds
              if value_reg < 0 or value_reg > 15:
                  raise RegisterOutOfBounds
              addr = Registers.regs16[addr_reg] & Values.MASK_16
              value = Registers.regs16[value_reg] & Values.MASK_16
     
              digits = str(value)
              for offset, ch in enumerate(digits):
                  if addr + offset >= Values.MEMORY_SIZE:
                      break
                  Memory.memory[addr + offset] = ord(ch) & Values.MASK_16
            except:
                raise OpcodeTrap
               
           
          
 
          
 
          def bits8_to_membits16word(a):
               words = []
               for i in range(0, len(a) - 1, 2):
                   words.append(int.from_bytes(a[i:i+2], "big"))
               return words
          
 
          def _interrupt():
              addr = cpu16._next()
              if addr > Values.IVT_END:
                  raise InvalidInterrupt
 
              Registers.stack -= 1
              if Registers.stack < 0x0000:
                  raise StackOverflow
             
              if Registers.stack > 0xFFFF:
                  raise StackUnderflow
              Memory.memory[Registers.stack] = Registers.ip
 
              Registers.ip = Memory.memory[addr]
 
          def _soft_halt():
              # Halts the cpu until a HWI is raised
              # If iF is 0, it will cause a infinite loop. This is intentional behaviour.
 
              while True:
                  time.sleep(0.01)
                  if Registers.iF:
                    if cpu16.Check_HWI_status():
                        break
 
 
          def _jumph():
              addr = cpu16._next()
              if addr < 0 or addr > 0xFFFF:
                  raise RoutineAddressOutOfBounds
              if Registers.flag == 2:
                  Registers.ip = addr
 
          def _jumpl():
              addr = cpu16._next()
              if addr < 0 or addr > 0xFFFF:
                  raise RoutineAddressOutOfBounds
              if Registers.flag == 3:
                  Registers.ip = addr
 
          def _mov_from_hardware_reserved():
              # rFLAGS  : 44
              # rIP     : 99
              # rSP     : 22
             
              dest = cpu16._next()
              src = cpu16._next()
              if src == 44:
                  Registers.regs16[dest] = Registers.flag
                 
              elif src == 99:
                  Registers.regs16[dest] = Registers.ip
 
              elif src == 22:
                  Registers.regs16[dest] = Registers.stack
 
          def _jump_pointer():
              rP = cpu16._next()
              if rP < 0 or rP > 15:
                  raise RegisterOutOfBounds
 
              # Wont check for memory bounds because registers are 16-bit
 
              Registers.ip = Registers.regs16[rP]
 
 
          def _clear_flags():
              Registers.flag = 0xFFFF
              # clear flags also resets the fault status
              Values.T_FAULT = False
 
          def _mov_sp():
              reg = cpu16._next()
              if reg < 0 or reg > 15:
                  raise RegisterOutOfBounds
             
              Registers.stack = Registers.regs16[reg]
 
          def _port_in():
              if len(Memory.pmio_stack) == 0:
                  raise PMIO_StackUnderFlow
                 
              if len(Memory.pmio_stack) > Values.PMIO_STACK_SIZE:
                  raise PMIO_StackOverFlow
                           
              
              reg = cpu16._next()
              if reg < 0 or reg > 15:
                  raise RegisterOutOfBounds
              Registers.regs16[reg] = Memory.pmio_stack.pop() & Values.MASK_16
 
              if len(Memory.pmio_stack) > Values.PMIO_STACK_SIZE:
                  raise PMIO_StackOverFlow
 
          def _port_out():
              if len(Memory.pmio_stack) > Values.PMIO_STACK_SIZE:
                  raise PMIO_StackOverFlow
              port   = Registers.regs16[1]
              value  = Registers.regs16[2]
              notify = Registers.regs16[3]
             
              if notify:
                  VirtualHardware.notify_virtual_device(port)
 
              # if it is not in notify mode, then just push value, if not, ignore value
              if not notify:
                  Memory.pmio_stack.append(value & Values.MASK_16)
 
          MICROCODE_TABLE = { 
 
              # *2 opcodes
              0: _einval,
              1: _halt,
              4: _mov,
           
              # +4 opcodes
              12: lambda: Microcode._math("ADD"),
              16: lambda: Microcode._math("SUB"),
              20: lambda: Microcode._math("MUL"),
              24: lambda: Microcode._math("DIV"),
              28: _jump,
              32: _call,
              36: _ret,
              40: _push,
              44: _pop,
              48: _compare,
              52: _je,
              56: _jne,
              60: _disable_hwi,
              64: _load,      
              68: _inc,
              72: _dec,
 
              # +2 opcodes
              76: _nump,      
              78: _enable_hwi,
              80: _port_in,
              82: _interrupt,
              84: _jumph,
              86: _jumpl,
              88: _clear_flags,
              90: _jump_pointer,
              92: _mov_from_hardware_reserved,
              94: _mov_sp,
              96: _port_out,
              98: _soft_halt
 
          }
 
 
def disk_read_word(addr: int) -> int:
    DISK_HANDLE.seek(addr * 2)
    data = DISK_HANDLE.read(2)
    if len(data) != 2:
        raise DiskIO
    return int.from_bytes(data, "big")
 
 
def disk_write_word(addr: int, value: int) -> None:
    DISK_HANDLE.seek(addr * 2)
    DISK_HANDLE.write((value & Values.MASK_16).to_bytes(2, "big"))
    DISK_HANDLE.flush()
 
class VirtualHardware:
 
    @staticmethod
    def Disk():
           # Expects PMIO stack to look like:
           #  [read_or_write] [address], [value: if write]
           #  mode is 1 for write, 0 for read
           # Exceptions should work here
 
           if len(Memory.pmio_stack) == 0:
               raise PMIO_StackUnderFlow
           mode_read_or_write = Memory.pmio_stack.pop()
 
 
           if len(Memory.pmio_stack) == 0:
               raise PMIO_StackUnderFlow
           addr = Memory.pmio_stack.pop()
 
           if mode_read_or_write == 1:
               if len(Memory.pmio_stack) == 0:
                   raise PMIO_StackUnderFlow
               value = Memory.pmio_stack.pop()
 
           # if mode is invalid (not 1 or 0), it will do nothing. This is intentional behaviour
           if mode_read_or_write == 1:
               disk_write_word(addr, value)
 
           if mode_read_or_write == 0:
               read_value = disk_read_word(addr)
 
               Memory.pmio_stack.append(read_value)
               if len(Memory.pmio_stack) > Values.PMIO_STACK_SIZE:
                   raise PMIO_StackOverFlow
 
    def PowerManagementUnit():
           # Expects PMIO stack to look like:
           #  [new mode]
           #  modes:
           #    1: restart
           # Exceptions should work here
 
           if len(Memory.pmio_stack) == 0:
               raise PMIO_StackUnderFlow
 
           mode = Memory.pmio_stack.pop()
 
           if mode == 1:
               Registers.ip = 0x0000
               Registers.stack = 0x0000
               Registers.regs16 = [0] * 16
               Memory.pmio_stack = []
  
               # reload the bios and reboot from existing OS
               load_bios(USE_BIOS_COMPAT, noPrintStatusString=True)
               clearscreen()
               return cpu16.Fetch_loop()
 
           else:
               # do nothing if else
               return
  
        
 
    
 
    @staticmethod
    def notify_virtual_device(port):
        if port not in PORT_MAP:
            # This is called inside Instruct() so it should fall through *should*
 
            raise PMIO_HardwareIOPortNotFound
 
        else: # if the device does exist, call its driver
            PORT_MAP[port]()
 
 
 
 
PORT_MAP = {
    1: VirtualHardware.Disk,
    2: VirtualHardware.PowerManagementUnit
       
}   
        
    
 
class DoHardwareInterrupt:
 
    @staticmethod
    def Keyboard(key):
        Memory.pmio_stack.append(key)
 
        if len(Memory.pmio_stack) > Values.PMIO_STACK_SIZE:
            Memory.pmio_stack = []
            raise PMIO_StackOverFlow
 
        Registers.stack -= 1
 
        # If the stack is in a bad state, simply just reset the stack to its original state and return early
        # GPFs are impossible here - Instruct() is the only place where they can actually be caught
 
        if Registers.stack < 0x0000:
           Registers.stack += 1
           return
 
        if Registers.stack > 0xFFFF:
           Registers.stack += 1
           return
 
        Memory.memory[Registers.stack] = Registers.ip
 
        Registers.ip = Memory.memory[InterruptVectors.KEYBOARD]
 
        return
 
 
  
class CPU16:
     def __init__(self):
          self.HALT = 1
    
 
     def _next(self) -> int:
          i = Memory.memory[Registers.ip]
          Registers.ip = (Registers.ip + 1) & Values.MASK_16
          return i
    
     
     def Instruct(self, i: int) -> None:
         try:
             i()
         except (Exception, KeyboardInterrupt) as e:
             error_codes = {
                 InvalidInterrupt: 178,
                 MMIO_OutOfBounds: 24,
                 RoutineAddressOutOfBounds: 79,
                 OpcodeTrap: 7,
                 RegisterOutOfBounds: 200,
                 StackOverflow: 10,
                 StackUnderflow: 11,
                 DiskIO: 300,
                 PMIO_StackOverFlow: 88,
                 PMIO_StackUnderFlow: 92,
                 PMIO_HardwareIOPortNotFound: 86
             }
             if Values.T_FAULT:
                 ec = error_codes.get(type(e), 800)
                 ea = Registers.ip
                 Finally(ec, ea)
             error_code = int(error_codes.get(type(e), 800))
             Registers.regs16[13] = error_code & Values.MASK_16
             Registers.regs16[14] = Registers.ip & Values.MASK_16
             for addr in range(Values.VIDEO_MEMORY_3839_MODE + 1, Values.MEMORY_SIZE):
                 Memory.memory[addr] = 0
             Memory.pmio_stack = []
             Registers.ip = Memory.memory[InterruptVectors.ERROR]
             Values.T_FAULT = True
             time_triple_fault_cancel()
             cpu16.Fetch_loop()
 
     def Check_HWI_status(self):
         key = is_key_down()
 
         if key:
             DoHardwareInterrupt.Keyboard(key)
             return True
 
         else:
             return False
         
     def Fetch_loop(self) -> None:
        Values.CAN_REFRESH = True

        try:
          while Registers.ip < Values.MEMORY_SIZE:
               if Registers.iF:
                   self.Check_HWI_status()
 
               Values.cycle += 1
               ins = self._next()
               if Registers.ip > Values.MASK_16 - 1:
                   Registers.ip = 0
               if ins > 0:
                 op = Microcode.MICROCODE_TABLE.get(ins, Microcode.MICROCODE_TABLE[0])
                 self.Instruct(op)
              
               else:
                   Registers.ip += 1 # Skip the blank instruction
        except KeyboardInterrupt:    # other errors will be caught in Instruct()
            Finally()
 
 
 
def is_key_down():
    # Using keyboard here is too slow
 
    if msvcrt.kbhit():
        char = msvcrt.getch()
       
        if char in (b'\x00', b'\xe0') and msvcrt.kbhit():
            return ord(msvcrt.getch())
           
        return ord(char)
    return False

def screen_refresh_thread():
  def refresh_thread():
  
    # Refreshes the screen at 30hz
    # May we have a moment of silence for whatever CPU thread this is running on.
    while True:
      if Values.CAN_REFRESH:
          color_map = {
                            130: colors.RED,
                            131: colors.GREEN,
                            132: colors.BLUE,
                            133: colors.BRIGHT_BLACK,
                            134: colors.CYAN,
                            135: colors.BG_RED,
                            136: colors.RESET
          }
  
          Fbuf = []
          _pointer = Values.VIDEO_MEMORY_3839_MODE
          while True:
              if _pointer > 0xFFFF:
                  _pointer = 0x0000
                  raise MMIO_OutOfBounds
  
              current_byte = Memory.memory[_pointer]
              if current_byte == 0:
                  break
  
              if 0 <= current_byte <= 127:
                  Fbuf.append(chr(current_byte))
              elif current_byte in color_map:
                  Fbuf.append(color_map[current_byte])
  
              _pointer += 1
  
          frame_str = "".join(Fbuf)
  
          # if the frame didn't change, don't do the refresh
          if frame_str == Microcode._last_frame:
              time.sleep(Values.REFRESH_INTERVAL) # ~30hz
              continue
          
          Microcode._last_frame = frame_str
  
          # reset cursor to 0,0 and append the new frame
          sys.stdout.write(colors.RESET + "\033[H\033[2J" + frame_str + "\033[0J")
          sys.stdout.flush()
  
          time.sleep(Values.REFRESH_INTERVAL) # ~30hz
      else:
          time.sleep(0.3) # so it doesn't bully the CPU when we are not refreshing
  threading.Thread(target=refresh_thread, daemon=True).start()
 
# interrupt shell removed
# everything possible through GUI
 
def time_triple_fault_cancel():
    # After 10 seconds, resets triple fault status.
    # If another fault occours before this resets, the emulator will hit Finaly() and the status of T_FAULT will not matter.
    def _thread_reset():
        time.sleep(10)
        Values.T_FAULT = False
 
    threading.Thread(target=_thread_reset, daemon=True).start()
       
 
            
def usdm():
    size = 0
    for i in Memory.memory:
        if i != 0:
            size += 1
 
    return size         
 
class InvalidInterrupt(Exception):
    pass
 
class MMIO_OutOfBounds(Exception):
    pass
 
class RoutineAddressOutOfBounds(Exception):
    pass
 
class OpcodeTrap(Exception):
    pass
 
class RegisterOutOfBounds(Exception):
    pass
 
class StackOverflow(Exception):
    pass
 
class StackUnderflow(Exception):
    pass
 
class DiskIO(Exception):
    pass
 
class PMIO_StackOverFlow(Exception):
    pass
 
class PMIO_StackUnderFlow(Exception):
    pass
 
class PMIO_HardwareIOPortNotFound(Exception):
    pass
 
def make_mem_dump(noPrintStatusString=False):
    DUMP_PATH = r"C:\python\VSK-E16A Workspace\Memory Dump\DUMP.img"
    with open(DUMP_PATH, "wb") as f:
        for o in Memory.memory:
            f.write(o.to_bytes(2, 'big'))
    if not noPrintStatusString:
        print(f"Memory dump created\nView it at: {DUMP_PATH}\n")
 
def Finally(ec=0, ea=0):
    Values.CAN_REFRESH = False

    print("\n")
    pymsgbox.alert(text="The CPU is halted.", title="VM Execution stopped")
    print(f"{colors.RED}|!| TRIPLE FAULT |!|\n\nError: {ec}\n\nAddress: {b16hex(ea)}{colors.BRIGHT_BLACK}" if Values.T_FAULT else "")
    print(colors.BRIGHT_BLACK)
    print("=" * 50)
    print("CPU Halted. VM Execution stopped.")
    print(f"Memory used: {usdm()}/{len(Memory.memory)}K")
    print(f"Registers: ")
    for r, v in enumerate(Registers.regs16):
        print(f"{r}: {b16hex(v)}")
    print("\n")
    print(f"FLAGS: {b16hex(Registers.flag)}")
    print(f"IP: {b16hex(Registers.ip)}")
    print(f"SP: {b16hex(Registers.stack)}")
    print("\n")
    print(colors.RESET)
    make_mem_dump()
    print("Enter exits, r restarts from disk  (returning to terminal from here is not supported)")
    print(colors.RESET)
    while msvcrt.kbhit():
        msvcrt.getch()
    time.sleep(0.5)
    while True:
        if keyboard.is_pressed('r'):
 
            Registers.ip = 0x0000
            Registers.stack = 0x0000
            Registers.regs16 = [0] * 16
            Memory.pmio_stack = []
 
            # Reload from disk
            load_to_memory(0, LoadImage(DISK_PATH))
            load_bios(USE_BIOS_COMPAT)
            clearscreen()
            return cpu16.Fetch_loop()
        if keyboard.is_pressed('enter'):
            sys.exit(ec)
            exit(ec)
        time.sleep(0.01)
 
def clock_monitor():
    last_cycles = Values.cycle
    last_time = time.perf_counter()
 
    while True:
        time.sleep(1)
 
        now = time.perf_counter()
        cycles = Values.cycle
 
        delta_cycles = cycles - last_cycles
        delta_time = now - last_time
 
        khz = delta_cycles / delta_time / 1_000
        Values.SPEED = round(khz, 1)
 
        last_cycles = cycles
        last_time = now
 
threading.Thread(target=clock_monitor, daemon=True).start()
 
 
load_bios(USE_BIOS_COMPAT)
DISK_PATH = "C:\python\VSK-E16A Workspace\VM Harddisk/VMdisk.vhd"
DISK_HANDLE = open(DISK_PATH, "rb+")
hide_cursor()
print(colors.RESET)
cpu16 = CPU16()
clearscreen()
screen_refresh_thread()
time.sleep(1)
cpu16.Fetch_loop()

 
print("The emulator mysteriously stopped.\nThis error should not happen.\n\n(cpu16.Fetch_loop() exited without reason)")
sys.exit(238)
 


