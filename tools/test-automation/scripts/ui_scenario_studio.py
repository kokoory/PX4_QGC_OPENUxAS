#!/usr/bin/env python3
"""UI Automation Console — all-in-one QGC scenario record / replay / author / run.

One GUI that consolidates the EventBroadcaster tooling:
  • Monitor / Record — watch the full 45678 event stream, record it to .jsonl
  • Scenario         — author steps with timing + telemetry conditions, run them
  • Replay           — play a recorded .jsonl back with original timing
  • Stack            — start/stop the sim stack (UxAS, PX4 fleet, bridges, listener, QGC)

QGC's global UI hook (UIScript) makes every base control broadcast on the "ui"
channel and replay from {"action":"ui","id":..,"value":..}. QGC also broadcasts
per-vehicle "telemetry". This console rides that bus (Tx 45678 / Rx 45679).

    python3 ui_scenario_studio.py                 # GUI
    python3 ui_scenario_studio.py --run scen.json  # headless scenario
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque

TX_PORT = 45678   # QGC -> world (ui / action / uxas_ui / telemetry / ui_registry)
RX_PORT = 45679   # world -> QGC (commands)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
REPO = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "..", ".."))
CONFIGS = os.path.abspath(os.path.join(SCRIPT_DIR, "..", "configs"))
UXAS_BIN = os.path.join(HOME, "OpenUxAS/infrastructure/sbx/x86_64-linux/uxas-release/install/bin/uxas")
QGC_BIN = os.path.join(REPO, "build/Release/QGroundControl")
PX4_DIR = os.path.join(HOME, "PX4-Autopilot")
VWORLD_KEY = "4AB82F93-D134-3AFB-AEA8-59CB23854556"


# ---------------------------------------------------------------------------
# Bus: receive the event stream, send commands, optionally record to file
# ---------------------------------------------------------------------------
class Bus:
    def __init__(self, host="127.0.0.1"):
        self.host = host
        self.telemetry = {}
        self.ui_ids = {}
        self._subs = []
        self._rec_fh = None
        self._rec_lock = threading.Lock()
        self.rec_count = 0
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._rx.bind(("0.0.0.0", TX_PORT))
        self._last_key = None
        threading.Thread(target=self._loop, daemon=True).start()

    def subscribe(self, cb):
        self._subs.append(cb)

    def _loop(self):
        while True:
            try:
                raw, _ = self._rx.recvfrom(65535)
                line = raw.decode("utf-8", "replace")
                msg = json.loads(line)
            except Exception:
                continue
            # de-dup the localhost double-send
            key = (msg.get("seq"), msg.get("category"), msg.get("event"))
            dup = (key == self._last_key and key[0] is not None)
            self._last_key = key
            cat = msg.get("category")
            if cat == "telemetry":
                d = msg.get("data", {})
                if "vehicleId" in d:
                    self.telemetry[int(d["vehicleId"])] = d
            elif cat == "ui_registry":
                self.ui_ids[msg.get("event", "")] = (msg.get("data") or {}).get("type", "")
            elif cat == "ui":
                d = msg.get("data") or {}
                if d.get("id"):
                    self.ui_ids[d["id"]] = d.get("type", "")
            if dup:
                continue
            with self._rec_lock:
                if self._rec_fh:
                    self._rec_fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
                    self._rec_fh.flush()
                    self.rec_count += 1
            for cb in list(self._subs):
                try:
                    cb(msg)
                except Exception:
                    pass

    def send(self, cmd: dict):
        self._tx.sendto(json.dumps(cmd, ensure_ascii=False).encode("utf-8"), (self.host, RX_PORT))

    def request_registry(self):
        self.send({"action": "ui", "id": "__dump__"})

    def start_record(self, path):
        with self._rec_lock:
            self._rec_fh = open(path, "w")
            self.rec_count = 0

    def stop_record(self):
        with self._rec_lock:
            if self._rec_fh:
                self._rec_fh.close()
                self._rec_fh = None

    @property
    def recording(self):
        return self._rec_fh is not None


# ---------------------------------------------------------------------------
# Scenario engine (timing + telemetry conditions)
# ---------------------------------------------------------------------------
_OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b, "<": lambda a, b: a < b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def eval_condition(bus, when):
    vid = int(when.get("vehicle", 1))
    tel = bus.telemetry.get(vid)
    if not tel:
        return False
    field = when.get("field")
    if field not in tel:
        return False
    try:
        cur, target = float(tel[field]), float(when.get("value"))
    except (TypeError, ValueError):
        cur, target = tel[field], when.get("value")
    return _OPS.get(when.get("op", "=="), _OPS["=="])(cur, target)


def run_scenario(bus, scenario, dry_run=False, log=print, stop=lambda: False):
    steps = scenario.get("steps", [])
    log(f"[scenario] '{scenario.get('name', 'unnamed')}' — {len(steps)} step(s)"
        + ("  [DRY-RUN]" if dry_run else ""))
    t0 = time.time()
    for i, step in enumerate(steps):
        if stop():
            log("[scenario] stopped"); return
        if "at" in step:
            target = t0 + float(step["at"])
            while time.time() < target and not stop():
                time.sleep(0.05)
        elif "when" in step:
            when = step["when"]; timeout = float(step.get("timeout", 300))
            deadline = time.time() + timeout
            log(f"[{i}] wait {when.get('field')} {when.get('op')} {when.get('value')} "
                f"(v{when.get('vehicle', 1)}, ≤{timeout:.0f}s)")
            to = False
            while not eval_condition(bus, when):
                if stop(): log("[scenario] stopped"); return
                if time.time() > deadline:
                    log(f"[{i}] ! condition timed out — skip"); to = True; break
                time.sleep(0.1)
            if to:
                continue
        else:
            end = time.time() + float(step.get("wait", 0))
            while time.time() < end and not stop():
                time.sleep(0.05)
        cmd = step.get("cmd")
        if not cmd:
            continue
        log(f"[{i}] {human_cmd(cmd)}")
        if not dry_run:
            bus.send(cmd)
    log("[scenario] done")


# ---------------------------------------------------------------------------
# Replay: recorded broadcast events -> commands, resent with original timing
# ---------------------------------------------------------------------------
def to_command(evt):
    cat = evt.get("category"); data = evt.get("data") or {}
    if cat == "command":
        cmd = dict(data); cmd.setdefault("action", evt.get("event", ""))
        return cmd if cmd.get("action") else None
    if cat == "ui":
        cid = data.get("id")
        if not cid or cid == "__dump__":
            return None
        cmd = {"action": "ui", "id": cid}
        if "value" in data:
            cmd["value"] = data["value"]
        return cmd
    if cat == "action":
        cmd = {"action": evt.get("event", "")}
        for k in ("actionCode", "sliderValue", "optionChecked", "altitude"):
            if k in data:
                cmd[k] = data[k]
        return cmd if (cmd.get("action") or "actionCode" in cmd) else None
    if cat == "uxas_ui":
        ev = evt.get("event", "")
        if not ev:
            return None
        cmd = {"action": "ux_" + ev}
        for k in ("value", "kind", "shape", "mode", "name", "selected"):
            if k in data:
                cmd[k] = data[k]
        return cmd
    return None


def load_jsonl(path):
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def replay_recording(bus, events, rate=1.0, only=None, dry=False, log=print, stop=lambda: False):
    only = only or {"ui", "action", "command", "uxas_ui"}
    evs = [e for e in events if e.get("category") in only]
    log(f"[replay] {len(evs)} event(s), rate {rate}x" + ("  [DRY]" if dry else ""))
    prev = None
    for evt in evs:
        if stop():
            log("[replay] stopped"); return
        ts = float(evt.get("timestamp", 0) or 0)
        if prev is not None and ts >= prev:
            gap = (ts - prev) / rate
            end = time.time() + min(gap, 3600)
            while time.time() < end and not stop():
                time.sleep(0.02)
        prev = ts
        cmd = to_command(evt)
        if not cmd:
            continue
        log(human_cmd(cmd))
        if not dry:
            bus.send(cmd)
    log("[replay] done")


# ---------------------------------------------------------------------------
# Subprocess stack management (Stack tab)
# ---------------------------------------------------------------------------
class Stack:
    def __init__(self, log):
        self.log = log
        self.procs = {}   # name -> Popen

    def _env(self):
        e = dict(os.environ)
        e.setdefault("DISPLAY", ":1")
        return e

    def start(self, name, argv, cwd=None, env_extra=None):
        if name in self.procs and self.procs[name].poll() is None:
            self.log(f"[stack] {name} already running"); return
        env = self._env()
        if env_extra:
            env.update(env_extra)
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                                 stdout=open(f"/tmp/studio_{name}.log", "w"),
                                 stderr=subprocess.STDOUT)
            self.procs[name] = p
            self.log(f"[stack] started {name} (pid {p.pid}) -> /tmp/studio_{name}.log")
        except Exception as e:
            self.log(f"[stack] {name} failed: {e}")

    def stop_all(self, pkills):
        for name, p in list(self.procs.items()):
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except Exception:
                pass
        self.procs.clear()
        for pat in pkills:
            subprocess.call(["pkill", "-9", "-f", pat])
        self.log("[stack] stopped all")

    def status(self):
        return {n: (p.poll() is None) for n, p in self.procs.items()}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def _num(s):
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return s


_GUIDED = {"arm": "Arm", "disarm": "Disarm", "rtl": "RTL", "land": "Land",
           "start_mission": "Start Mission", "pause": "Pause"}


def human_cmd(cmd):
    """Render a command dict as a short, human-readable label (not raw JSON)."""
    if not isinstance(cmd, dict):
        return str(cmd)
    a = cmd.get("action", "")
    if a == "ui":
        cid = cmd.get("id", "?")
        return f"{cid}  =  {cmd['value']}" if "value" in cmd else f"click  {cid}"
    if a == "set_current_wp":
        return f"→ waypoint {cmd.get('index')}"
    if a == "select_vehicle":
        return f"select vehicle {cmd.get('vehicleId')}"
    if a == "takeoff":
        return "takeoff " + (f"{cmd['altitude']} m" if "altitude" in cmd else "")
    if a == "set_mode":
        return f"mode → {cmd.get('mode','')}"
    if a in _GUIDED:
        return _GUIDED[a]
    rest = " ".join(f"{k}={v}" for k, v in cmd.items() if k != "action")
    name = a[3:] if a.startswith("ux_") else a
    return f"{name}  {rest}".strip()


def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    bus = Bus()
    root = tk.Tk()
    root.title("UI Automation Console — QGC")
    root.geometry("1240x760")

    steps = []
    stop_flags = {"scenario": False, "replay": False}
    live_pending = deque()
    mon_pending = deque()

    # ===== LEFT column: palette + live events + telemetry =====
    left = ttk.Frame(root, padding=6); left.pack(side=tk.LEFT, fill=tk.BOTH)
    ttk.Label(left, text="UI controls (click QGC / Refresh)", font=("", 10, "bold")).pack(anchor=tk.W)
    pf = ttk.Frame(left); pf.pack(fill=tk.X)
    search_var = tk.StringVar()
    ttk.Entry(pf, textvariable=search_var, width=24).pack(side=tk.LEFT)
    ttk.Button(pf, text="Refresh", width=8, command=bus.request_registry).pack(side=tk.LEFT, padx=2)
    palette = tk.Listbox(left, width=38, height=10); palette.pack(fill=tk.BOTH, expand=True, pady=(2, 5))

    def refresh_palette(*_):
        q = search_var.get().lower()
        palette.delete(0, tk.END)
        for cid in sorted(bus.ui_ids):
            if q in cid.lower():
                palette.insert(tk.END, f"{cid}   [{bus.ui_ids[cid]}]")
    search_var.trace_add("write", refresh_palette)

    ttk.Label(left, text="Live events  (double-click → add step)", font=("", 10, "bold")).pack(anchor=tk.W)
    live_box = tk.Listbox(left, width=38, height=9); live_box.pack(fill=tk.BOTH, expand=True, pady=(2, 5))
    live_raw = []

    def on_live(msg):
        cat = msg.get("category")
        if cat not in ("ui", "action"):
            return
        d = msg.get("data") or {}
        if cat == "ui":
            cid = d.get("id")
            if not cid or cid == "__dump__":
                return
            cmd = {"action": "ui", "id": cid}
            if "value" in d:
                cmd["value"] = d["value"]
            label = f"ui  {cid}" + (f" = {d['value']}" if "value" in d else "")
        else:
            cmd = {"action": msg.get("event", "")}
            for k in ("altitude", "actionCode", "sliderValue", "optionChecked"):
                if k in d:
                    cmd[k] = d[k]
            label = f"act {msg.get('event','')}"
        live_pending.append((label, cmd))
    bus.subscribe(on_live)

    def add_live_step(_=None):
        sel = live_box.curselection()
        if sel and 0 <= sel[0] < len(live_raw):
            steps.append({"wait": 0, "cmd": dict(live_raw[sel[0]])}); redraw_steps()
            nb.select(tab_scen)
    live_box.bind("<Double-Button-1>", add_live_step)

    ttk.Label(left, text="Telemetry", font=("", 10, "bold")).pack(anchor=tk.W)
    tel_box = tk.Text(left, width=38, height=5, font=("monospace", 8)); tel_box.pack(fill=tk.X)

    # ===== RIGHT: notebook =====
    nb = ttk.Notebook(root); nb.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=6, pady=6)
    tab_scen = ttk.Frame(nb, padding=6); nb.add(tab_scen, text="Scenario")
    tab_mon = ttk.Frame(nb, padding=6); nb.add(tab_mon, text="Monitor / Record")
    tab_rep = ttk.Frame(nb, padding=6); nb.add(tab_rep, text="Replay")
    tab_stk = ttk.Frame(nb, padding=6); nb.add(tab_stk, text="Stack")

    # ---------- TAB: Scenario ----------
    schead = ttk.Frame(tab_scen); schead.pack(fill=tk.X)
    ttk.Label(schead, text="Scenario steps", font=("", 10, "bold")).pack(side=tk.LEFT)
    raw_view = tk.BooleanVar(value=False)
    ttk.Checkbutton(schead, text="raw JSON view", variable=raw_view,
                    command=lambda: redraw_steps()).pack(side=tk.RIGHT)
    cols = ("num", "trigger", "command")
    tree = ttk.Treeview(tab_scen, columns=cols, show="headings", height=13)
    tree.heading("num", text="#"); tree.column("num", width=32, anchor=tk.CENTER)
    tree.heading("trigger", text="When"); tree.column("trigger", width=230)
    tree.heading("command", text="Do"); tree.column("command", width=500)
    tree.pack(fill=tk.BOTH, expand=True)

    def redraw_steps():
        tree.delete(*tree.get_children())
        for i, s in enumerate(steps, 1):
            if "at" in s: trig = f"at {s['at']}s"
            elif "when" in s:
                w = s["when"]; trig = f"when {w.get('field')} {w.get('op')} {w.get('value')} (v{w.get('vehicle',1)})"
            else: trig = f"wait {s.get('wait',0)}s"
            cmd = s.get("cmd", {})
            desc = json.dumps(cmd, ensure_ascii=False) if raw_view.get() else human_cmd(cmd)
            tree.insert("", tk.END, values=(i, trig, desc))

    ed = ttk.LabelFrame(tab_scen, text="Add / edit step", padding=6); ed.pack(fill=tk.X, pady=6)
    trow = ttk.Frame(ed); trow.pack(fill=tk.X)
    trig_kind = tk.StringVar(value="wait")
    for t in ("wait", "at", "when"):
        ttk.Radiobutton(trow, text=t, variable=trig_kind, value=t).pack(side=tk.LEFT)
    trig_val = tk.StringVar(value="1"); ttk.Entry(trow, textvariable=trig_val, width=6).pack(side=tk.LEFT, padx=4)
    ttk.Label(trow, text=" when:").pack(side=tk.LEFT)
    w_veh = tk.StringVar(value="1"); w_field = tk.StringVar(value="alt_rel")
    w_op = tk.StringVar(value=">="); w_val = tk.StringVar(value="500")
    ttk.Entry(trow, textvariable=w_veh, width=3).pack(side=tk.LEFT)
    ttk.Combobox(trow, textvariable=w_field, width=11,
                 values=["alt_rel", "alt_amsl", "groundspeed", "heading", "current_wp", "lat", "lon"]).pack(side=tk.LEFT)
    ttk.Combobox(trow, textvariable=w_op, width=3, values=list(_OPS)).pack(side=tk.LEFT)
    ttk.Entry(trow, textvariable=w_val, width=7).pack(side=tk.LEFT)
    crow = ttk.Frame(ed); crow.pack(fill=tk.X, pady=4)
    ttk.Label(crow, text="cmd id/action:").pack(side=tk.LEFT)
    cmd_id = tk.StringVar(); ttk.Entry(crow, textvariable=cmd_id, width=32).pack(side=tk.LEFT, padx=2)
    ttk.Label(crow, text="value:").pack(side=tk.LEFT)
    cmd_val = tk.StringVar(); ttk.Entry(crow, textvariable=cmd_val, width=9).pack(side=tk.LEFT, padx=2)
    raw_mode = tk.BooleanVar(value=False)
    ttk.Checkbutton(crow, text="raw JSON", variable=raw_mode).pack(side=tk.LEFT)

    def use_palette(_=None):
        sel = palette.curselection()
        if sel:
            cmd_id.set(palette.get(sel[0]).split("   [")[0])
    palette.bind("<<ListboxSelect>>", use_palette)

    def build_step():
        step = {}; k = trig_kind.get()
        if k == "when":
            step["when"] = {"vehicle": int(w_veh.get() or 1), "field": w_field.get(),
                            "op": w_op.get(), "value": _num(w_val.get())}
        else:
            step[k] = _num(trig_val.get())
        idv = cmd_id.get().strip()
        if raw_mode.get():
            try:
                step["cmd"] = json.loads(idv)
            except Exception as e:
                messagebox.showerror("Bad JSON", str(e)); return None
        elif idv:
            cmd = {"action": "ui", "id": idv} if ":" in idv or idv.startswith("ux_") and False else None
            if ":" in idv:
                cmd = {"action": "ui", "id": idv}
            else:
                cmd = {"action": idv}
            if cmd_val.get().strip() != "":
                cmd["value"] = _num(cmd_val.get())
            step["cmd"] = cmd
        return step

    def add_step():
        s = build_step()
        if s is not None:
            steps.append(s); redraw_steps()

    def del_step():
        for iid in tree.selection():
            idx = tree.index(iid)
            if 0 <= idx < len(steps):
                steps.pop(idx)
        redraw_steps()

    def move(d):
        sel = tree.selection()
        if not sel:
            return
        i = tree.index(sel[0]); j = i + d
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]; redraw_steps()
            tree.selection_set(tree.get_children()[j])

    brow = ttk.Frame(ed); brow.pack(fill=tk.X)
    ttk.Button(brow, text="Add step", command=add_step).pack(side=tk.LEFT)
    ttk.Button(brow, text="Delete", command=del_step).pack(side=tk.LEFT, padx=2)
    ttk.Button(brow, text="↑", width=3, command=lambda: move(-1)).pack(side=tk.LEFT)
    ttk.Button(brow, text="↓", width=3, command=lambda: move(1)).pack(side=tk.LEFT)
    ttk.Button(brow, text="Send now",
               command=lambda: (build_step() or {}).get("cmd") and bus.send(build_step()["cmd"])).pack(side=tk.LEFT, padx=8)

    slog = tk.Text(tab_scen, height=7, font=("monospace", 8)); slog.pack(fill=tk.BOTH, expand=True, pady=4)
    def s_log(m): slog.insert(tk.END, m + "\n"); slog.see(tk.END)

    def run_scen():
        stop_flags["scenario"] = False
        threading.Thread(target=run_scenario,
                         args=(bus, {"name": "studio", "steps": list(steps)}),
                         kwargs={"log": lambda m: root.after(0, s_log, m),
                                 "stop": lambda: stop_flags["scenario"]}, daemon=True).start()

    def import_rec_as_steps():
        p = filedialog.askopenfilename(filetypes=[("JSON lines", "*.jsonl")])
        if not p:
            return
        n = 0
        for e in load_jsonl(p):
            c = to_command(e)
            if c:
                steps.append({"wait": 0, "cmd": c}); n += 1
        redraw_steps(); s_log(f"imported {n} step(s) from recording")

    rrow = ttk.Frame(tab_scen); rrow.pack(fill=tk.X)
    ttk.Button(rrow, text="▶ Run", command=run_scen).pack(side=tk.LEFT)
    ttk.Button(rrow, text="■ Stop", command=lambda: stop_flags.__setitem__("scenario", True)).pack(side=tk.LEFT, padx=2)
    ttk.Button(rrow, text="Save…", command=lambda: _save_steps(steps, s_log)).pack(side=tk.LEFT, padx=8)
    ttk.Button(rrow, text="Load…", command=lambda: _load_steps(steps, redraw_steps, s_log)).pack(side=tk.LEFT)
    ttk.Button(rrow, text="Import recording", command=import_rec_as_steps).pack(side=tk.LEFT, padx=8)

    def _save_steps(steps, log):
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            json.dump({"name": "studio", "steps": steps}, open(p, "w"), ensure_ascii=False, indent=2)
            log(f"saved {len(steps)} steps -> {p}")

    def _load_steps(steps, redraw, log):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            steps.clear(); steps.extend(json.load(open(p)).get("steps", [])); redraw()
            log(f"loaded {len(steps)} steps <- {p}")

    # ---------- TAB: Monitor / Record ----------
    mrow = ttk.Frame(tab_mon); mrow.pack(fill=tk.X)
    mon_filter = tk.StringVar(value="all")
    ttk.Label(mrow, text="Filter:").pack(side=tk.LEFT)
    ttk.Combobox(mrow, textvariable=mon_filter, width=12, state="readonly",
                 values=["all", "ui", "action", "uxas_ui", "uxas_search", "telemetry", "qgc_mission"]).pack(side=tk.LEFT, padx=3)
    rec_state = {"path": None}
    rec_lbl = ttk.Label(mrow, text="not recording", foreground="#888")
    def toggle_record():
        if bus.recording:
            bus.stop_record(); rec_lbl.config(text=f"saved {bus.rec_count} → {rec_state['path']}", foreground="#063")
            rec_btn.config(text="● Record")
        else:
            p = filedialog.asksaveasfilename(defaultextension=".jsonl", filetypes=[("JSON lines", "*.jsonl")])
            if not p:
                return
            rec_state["path"] = p; bus.start_record(p)
            rec_lbl.config(text=f"recording → {p}", foreground="#c00"); rec_btn.config(text="■ Stop rec")
    rec_btn = ttk.Button(mrow, text="● Record", command=toggle_record); rec_btn.pack(side=tk.LEFT, padx=8)
    ttk.Button(mrow, text="Clear", command=lambda: mon_list.delete(0, tk.END)).pack(side=tk.LEFT)
    rec_lbl.pack(side=tk.LEFT, padx=8)
    mon_list = tk.Listbox(tab_mon, font=("monospace", 8)); mon_list.pack(fill=tk.BOTH, expand=True, pady=4)

    def on_mon(msg):
        cat = msg.get("category", "?")
        if cat == "ui_registry":
            return
        f = mon_filter.get()
        if f != "all" and f != cat:
            return
        d = msg.get("data", {})
        ds = json.dumps(d, ensure_ascii=False) if d else ""
        mon_pending.append(f"{time.strftime('%H:%M:%S')} {cat:11} {msg.get('event',''):20} {ds}")
    bus.subscribe(on_mon)

    # ---------- TAB: Replay ----------
    rp = ttk.Frame(tab_rep); rp.pack(fill=tk.X)
    rep_path = tk.StringVar(value="(no file)")
    rep_events = {"list": []}
    def rep_load():
        p = filedialog.askopenfilename(filetypes=[("JSON lines", "*.jsonl")])
        if p:
            rep_events["list"] = load_jsonl(p); rep_path.set(f"{os.path.basename(p)}  ({len(rep_events['list'])} ev)")
    ttk.Button(rp, text="Load .jsonl…", command=rep_load).pack(side=tk.LEFT)
    ttk.Label(rp, textvariable=rep_path).pack(side=tk.LEFT, padx=8)
    rp2 = ttk.Frame(tab_rep); rp2.pack(fill=tk.X, pady=4)
    ttk.Label(rp2, text="rate:").pack(side=tk.LEFT)
    rep_rate = tk.StringVar(value="1.0"); ttk.Entry(rp2, textvariable=rep_rate, width=5).pack(side=tk.LEFT, padx=2)
    ttk.Label(rp2, text="only:").pack(side=tk.LEFT)
    rep_only = tk.StringVar(value="ui,action,command,uxas_ui")
    ttk.Entry(rp2, textvariable=rep_only, width=28).pack(side=tk.LEFT, padx=2)
    rep_dry = tk.BooleanVar(value=False); ttk.Checkbutton(rp2, text="dry-run", variable=rep_dry).pack(side=tk.LEFT)
    replog = tk.Text(tab_rep, height=14, font=("monospace", 8)); replog.pack(fill=tk.BOTH, expand=True, pady=4)
    def rp_log(m): replog.insert(tk.END, m + "\n"); replog.see(tk.END)
    def rep_run():
        if not rep_events["list"]:
            rp_log("load a .jsonl first"); return
        stop_flags["replay"] = False
        only = {x.strip() for x in rep_only.get().split(",") if x.strip()}
        threading.Thread(target=replay_recording,
                         args=(bus, rep_events["list"]),
                         kwargs={"rate": float(rep_rate.get() or 1), "only": only, "dry": rep_dry.get(),
                                 "log": lambda m: root.after(0, rp_log, m),
                                 "stop": lambda: stop_flags["replay"]}, daemon=True).start()
    rp3 = ttk.Frame(tab_rep); rp3.pack(fill=tk.X)
    ttk.Button(rp3, text="▶ Replay", command=rep_run).pack(side=tk.LEFT)
    ttk.Button(rp3, text="■ Stop", command=lambda: stop_flags.__setitem__("replay", True)).pack(side=tk.LEFT, padx=2)

    # ---------- TAB: Stack ----------
    stack = Stack(lambda m: root.after(0, st_log, m))
    ttk.Label(tab_stk, text="Sim stack — start / stop", font=("", 10, "bold")).pack(anchor=tk.W)
    sids = ttk.Frame(tab_stk); sids.pack(fill=tk.X, pady=3)
    ttk.Label(sids, text="vehicle ids:").pack(side=tk.LEFT)
    ids_var = tk.StringVar(value="1,2,4,11"); ttk.Entry(sids, textvariable=ids_var, width=16).pack(side=tk.LEFT, padx=4)
    stlog = tk.Text(tab_stk, height=16, font=("monospace", 8));
    def st_log(m): stlog.insert(tk.END, m + "\n"); stlog.see(tk.END)

    def s_uxas():
        stack.start("uxas", [UXAS_BIN, "-cfgPath", "./uxas_multi.xml"], cwd=CONFIGS)
    def s_px4():
        stack.start("px4", ["bash", os.path.join(SCRIPT_DIR, "launch_all.sh"), "--ids", ids_var.get()],
                    cwd=os.path.dirname(SCRIPT_DIR),
                    env_extra={"GZ_GUI": "1", "RECORDER": "0", "PX4_DIR": PX4_DIR})
    def s_bridges():
        stack.start("bridges", ["bash", os.path.join(SCRIPT_DIR, "launch_bridges.sh"),
                                "--ids", ids_var.get(), "--monitor-port", "45680"],
                    cwd=os.path.dirname(SCRIPT_DIR))
    def s_listener():
        stack.start("listener", [sys.executable, os.path.join(SCRIPT_DIR, "uxas_search_listener.py"),
                                 "--vworld-key", VWORLD_KEY, "--vehicles", "4",
                                 "--uxas-pub", "tcp://127.0.0.1:5560", "--uxas-pull", "tcp://127.0.0.1:5561",
                                 "--amase", "auto"])
    def s_qgc():
        stack.start("qgc", [QGC_BIN])
    def s_stopall():
        stack.stop_all(["launch_all.sh", "launch_bridges.sh", "bin/px4 -i", "gz sim",
                        "qgc_uxas_bridge.py", "uxas_search_listener.py", "uxas -cfgPath"])

    g = ttk.Frame(tab_stk); g.pack(fill=tk.X, pady=4)
    for txt, fn in (("Start UxAS", s_uxas), ("Start PX4 fleet", s_px4), ("Start bridges", s_bridges),
                    ("Start listener", s_listener), ("Start QGC", s_qgc)):
        ttk.Button(g, text=txt, command=fn).pack(side=tk.LEFT, padx=2)
    ttk.Button(g, text="■ Stop all", command=s_stopall).pack(side=tk.LEFT, padx=10)
    stlog.pack(fill=tk.BOTH, expand=True, pady=4)
    st_log(f"paths: UXAS={UXAS_BIN}\n       QGC={QGC_BIN}\n       PX4_DIR={PX4_DIR}")

    # ===== periodic UI refresh =====
    def tick():
        while live_pending:
            label, cmd = live_pending.popleft()
            live_raw.append(cmd); live_box.insert(tk.END, label); live_box.see(tk.END)
            if live_box.size() > 500:
                live_box.delete(0); live_raw.pop(0)
        n = 0
        while mon_pending and n < 200:
            mon_list.insert(tk.END, mon_pending.popleft()); n += 1
        if mon_list.size() > 2000:
            mon_list.delete(0, mon_list.size() - 2000)
        if n:
            mon_list.see(tk.END)
        tel_box.delete("1.0", tk.END)
        for vid in sorted(bus.telemetry):
            t = bus.telemetry[vid]
            tel_box.insert(tk.END, f"v{vid} {str(t.get('mode','?')):9} alt {float(t.get('alt_rel',0)):6.1f} "
                                   f"gs {float(t.get('groundspeed',0)):4.1f} wp {t.get('current_wp','?')}\n")
        refresh_palette()
        root.after(400, tick)

    bus.request_registry()
    root.after(500, tick)
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", help="run a saved scenario JSON headless")
    ap.add_argument("--replay", help="replay a recorded .jsonl headless")
    ap.add_argument("--rate", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    if args.run:
        bus = Bus(args.host); time.sleep(0.5)
        run_scenario(bus, json.load(open(args.run)), dry_run=args.dry_run); return 0
    if args.replay:
        bus = Bus(args.host); time.sleep(0.5)
        replay_recording(bus, load_jsonl(args.replay), rate=args.rate, dry=args.dry_run); return 0
    launch_gui(); return 0


if __name__ == "__main__":
    sys.exit(main())
