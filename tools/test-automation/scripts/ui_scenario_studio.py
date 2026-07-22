#!/usr/bin/env python3
"""UI Scenario Studio — author & run full QGC scenarios over EventBroadcaster.

QGC's global UI hook (UIScript) makes every base control broadcast on the
EventBroadcaster "ui" channel and replay from an inbound {"action":"ui","id":..}
command. QGC also broadcasts each vehicle's telemetry on the "telemetry" channel.
This tool lets you drive the ENTIRE UI from a scenario, with timing AND telemetry
conditions — e.g. "when the drone's alt_rel reaches 500 m, go to waypoint 3".

Two ways to use it:

  GUI (author + run + live palette + record):
      python3 ui_scenario_studio.py

  Headless (run a saved scenario):
      python3 ui_scenario_studio.py --run scenario.json
      python3 ui_scenario_studio.py --run scenario.json --dry-run

Scenario format (JSON):
{
  "name": "SAR run",
  "steps": [
    {"wait": 2,  "cmd": {"action": "ui", "id": "button:Arm"}},
    {"at":  10,  "cmd": {"action": "ui", "id": "textfield:150", "value": "200"}},
    {"when": {"vehicle": 1, "field": "alt_rel", "op": ">=", "value": 500},
     "timeout": 120,
     "cmd": {"action": "set_current_wp", "index": 3}},
    {"wait": 0,  "cmd": {"action": "ui", "id": "combobox:Wide (45°)", "value": 2}}
  ]
}

Trigger per step (pick one):
  "wait": <s>   pause this many seconds after the previous step
  "at":   <s>   fire at this many seconds from scenario start
  "when": {vehicle, field, op(>=,<=,>,<,==,!=), value}   wait until telemetry matches
                (optional "timeout": seconds; default 300)
"cmd" is sent verbatim to the EventBroadcaster Rx port (45679), so it can be any
UI action ({"action":"ui","id":...,"value":...}), a high-level command
({"action":"set_current_wp","index":N} / {"action":"select_vehicle","vehicleId":N}),
or any guided/ux_ command QGC already understands.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time

TX_PORT = 45678   # QGC -> world (ui, ui_registry, telemetry, ...)
RX_PORT = 45679   # world -> QGC (commands)


# ---------------------------------------------------------------------------
# Shared bus: receive telemetry / ui events, send commands
# ---------------------------------------------------------------------------
class Bus:
    def __init__(self, host="127.0.0.1"):
        self.host = host
        self.telemetry = {}     # vehicleId -> latest telemetry dict
        self.ui_ids = {}        # id -> type (discovered controls)
        self._subs = []         # callbacks(msg)
        self._tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self._rx.bind(("0.0.0.0", TX_PORT))
        self._run = True
        threading.Thread(target=self._loop, daemon=True).start()

    def subscribe(self, cb):
        self._subs.append(cb)

    def _loop(self):
        while self._run:
            try:
                raw, _ = self._rx.recvfrom(65535)
                msg = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                continue
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
            for cb in list(self._subs):
                try:
                    cb(msg)
                except Exception:
                    pass

    def send(self, cmd: dict):
        payload = json.dumps(cmd, ensure_ascii=False).encode("utf-8")
        self._tx.sendto(payload, (self.host, RX_PORT))

    def request_registry(self):
        """Ask QGC to dump every currently-registered control id."""
        self.send({"action": "ui", "id": "__dump__"})


# ---------------------------------------------------------------------------
# Scenario engine
# ---------------------------------------------------------------------------
_OPS = {
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a > b,  "<":  lambda a, b: a < b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def eval_condition(bus: Bus, when: dict) -> bool:
    vid = int(when.get("vehicle", 1))
    tel = bus.telemetry.get(vid)
    if not tel:
        return False
    field = when.get("field")
    if field not in tel:
        return False
    try:
        cur = float(tel[field])
        target = float(when.get("value"))
    except (TypeError, ValueError):
        cur, target = tel[field], when.get("value")
    return _OPS.get(when.get("op", "=="), _OPS["=="])(cur, target)


def run_scenario(bus: Bus, scenario: dict, dry_run=False, log=print, stop=lambda: False):
    steps = scenario.get("steps", [])
    log(f"[scenario] '{scenario.get('name', 'unnamed')}' — {len(steps)} step(s)"
        + ("  [DRY-RUN]" if dry_run else ""))
    t0 = time.time()
    for i, step in enumerate(steps):
        if stop():
            log("[scenario] stopped"); return
        # --- trigger ---
        if "at" in step:
            target = t0 + float(step["at"])
            while time.time() < target and not stop():
                time.sleep(0.05)
        elif "when" in step:
            when = step["when"]
            timeout = float(step.get("timeout", 300))
            deadline = time.time() + timeout
            log(f"[{i}] waiting for {when.get('field')} {when.get('op')} {when.get('value')} "
                f"(veh {when.get('vehicle', 1)}, timeout {timeout:.0f}s)")
            timed_out = False
            while not eval_condition(bus, when):
                if stop():
                    log("[scenario] stopped"); return
                if time.time() > deadline:
                    log(f"[{i}] ! condition timed out — skipping this step's command")
                    timed_out = True
                    break
                time.sleep(0.1)
            if timed_out:
                continue
        else:
            w = float(step.get("wait", 0))
            end = time.time() + w
            while time.time() < end and not stop():
                time.sleep(0.05)
        # --- command ---
        cmd = step.get("cmd")
        if not cmd:
            continue
        log(f"[{i}] -> {json.dumps(cmd, ensure_ascii=False)}")
        if not dry_run:
            bus.send(cmd)
    log("[scenario] done")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    bus = Bus()
    root = tk.Tk()
    root.title("UI Scenario Studio — QGC")
    root.geometry("1120x680")

    steps = []          # list of step dicts (the scenario being authored)
    stop_flag = {"v": False}

    # ---- layout: left = palette + telemetry, right = scenario steps ----
    left = ttk.Frame(root, padding=6); left.pack(side=tk.LEFT, fill=tk.BOTH)
    right = ttk.Frame(root, padding=6); right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # --- palette (discovered UI controls) ---
    ttk.Label(left, text="UI controls (click QGC or Refresh)", font=("", 10, "bold")).pack(anchor=tk.W)
    pf = ttk.Frame(left); pf.pack(fill=tk.X)
    search_var = tk.StringVar()
    ttk.Entry(pf, textvariable=search_var, width=26).pack(side=tk.LEFT)
    ttk.Button(pf, text="Refresh", width=8,
               command=lambda: bus.request_registry()).pack(side=tk.LEFT, padx=2)
    palette = tk.Listbox(left, width=40, height=18)
    palette.pack(fill=tk.BOTH, expand=True, pady=(2, 6))

    def refresh_palette(*_):
        q = search_var.get().lower()
        palette.delete(0, tk.END)
        for cid in sorted(bus.ui_ids):
            if q in cid.lower():
                palette.insert(tk.END, f"{cid}   [{bus.ui_ids[cid]}]")
    search_var.trace_add("write", refresh_palette)

    # --- telemetry readout ---
    ttk.Label(left, text="Telemetry", font=("", 10, "bold")).pack(anchor=tk.W)
    tel_box = tk.Text(left, width=40, height=7, font=("monospace", 8))
    tel_box.pack(fill=tk.X)

    # --- scenario step list ---
    ttk.Label(right, text="Scenario steps", font=("", 10, "bold")).pack(anchor=tk.W)
    cols = ("trigger", "command")
    tree = ttk.Treeview(right, columns=cols, show="headings", height=16)
    tree.heading("trigger", text="Trigger"); tree.column("trigger", width=260)
    tree.heading("command", text="Command"); tree.column("command", width=520)
    tree.pack(fill=tk.BOTH, expand=True)

    def redraw_steps():
        tree.delete(*tree.get_children())
        for s in steps:
            if "at" in s:      trig = f"at {s['at']}s"
            elif "when" in s:  w = s["when"]; trig = f"when {w.get('field')} {w.get('op')} {w.get('value')} (v{w.get('vehicle',1)})"
            else:              trig = f"wait {s.get('wait',0)}s"
            tree.insert("", tk.END, values=(trig, json.dumps(s.get("cmd", {}), ensure_ascii=False)))

    # --- step editor ---
    ed = ttk.LabelFrame(right, text="Add / edit step", padding=6); ed.pack(fill=tk.X, pady=6)
    # trigger row
    trow = ttk.Frame(ed); trow.pack(fill=tk.X)
    trig_kind = tk.StringVar(value="wait")
    for txt, val in (("wait", "wait"), ("at", "at"), ("when", "when")):
        ttk.Radiobutton(trow, text=txt, variable=trig_kind, value=val).pack(side=tk.LEFT)
    trig_val = tk.StringVar(value="1")
    ttk.Entry(trow, textvariable=trig_val, width=6).pack(side=tk.LEFT, padx=4)
    ttk.Label(trow, text="  when:").pack(side=tk.LEFT)
    w_veh = tk.StringVar(value="1"); w_field = tk.StringVar(value="alt_rel")
    w_op = tk.StringVar(value=">="); w_val = tk.StringVar(value="500")
    ttk.Entry(trow, textvariable=w_veh, width=3).pack(side=tk.LEFT)
    ttk.Combobox(trow, textvariable=w_field, width=11, values=[
        "alt_rel", "alt_amsl", "groundspeed", "heading", "current_wp", "lat", "lon"]).pack(side=tk.LEFT)
    ttk.Combobox(trow, textvariable=w_op, width=3, values=list(_OPS)).pack(side=tk.LEFT)
    ttk.Entry(trow, textvariable=w_val, width=7).pack(side=tk.LEFT)
    # command row
    crow = ttk.Frame(ed); crow.pack(fill=tk.X, pady=4)
    ttk.Label(crow, text="cmd id/action:").pack(side=tk.LEFT)
    cmd_id = tk.StringVar()
    ttk.Entry(crow, textvariable=cmd_id, width=34).pack(side=tk.LEFT, padx=2)
    ttk.Label(crow, text="value:").pack(side=tk.LEFT)
    cmd_val = tk.StringVar()
    ttk.Entry(crow, textvariable=cmd_val, width=10).pack(side=tk.LEFT, padx=2)
    raw_mode = tk.BooleanVar(value=False)
    ttk.Checkbutton(crow, text="raw JSON", variable=raw_mode).pack(side=tk.LEFT)

    def use_selected(_=None):
        sel = palette.curselection()
        if sel:
            cid = palette.get(sel[0]).split("   [")[0]
            cmd_id.set(cid)
    palette.bind("<<ListboxSelect>>", use_selected)

    def build_step():
        # trigger
        step = {}
        k = trig_kind.get()
        if k == "when":
            step["when"] = {"vehicle": int(w_veh.get() or 1), "field": w_field.get(),
                            "op": w_op.get(), "value": _num(w_val.get())}
        else:
            step[k] = _num(trig_val.get())
        # command
        idv = cmd_id.get().strip()
        if raw_mode.get():
            try:
                step["cmd"] = json.loads(idv)
            except Exception as e:
                messagebox.showerror("Bad JSON", str(e)); return None
        elif idv:
            if ":" in idv:                       # a UI control id
                cmd = {"action": "ui", "id": idv}
                if cmd_val.get().strip() != "":
                    cmd["value"] = _num(cmd_val.get())
                step["cmd"] = cmd
            else:                                # a bare action name
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
        sel = tree.selection()
        for iid in sel:
            idx = tree.index(iid)
            if 0 <= idx < len(steps):
                steps.pop(idx)
        redraw_steps()

    def move(delta):
        sel = tree.selection()
        if not sel:
            return
        i = tree.index(sel[0]); j = i + delta
        if 0 <= j < len(steps):
            steps[i], steps[j] = steps[j], steps[i]; redraw_steps()
            tree.selection_set(tree.get_children()[j])

    brow = ttk.Frame(ed); brow.pack(fill=tk.X)
    ttk.Button(brow, text="Add step", command=add_step).pack(side=tk.LEFT)
    ttk.Button(brow, text="Delete", command=del_step).pack(side=tk.LEFT, padx=2)
    ttk.Button(brow, text="↑", width=3, command=lambda: move(-1)).pack(side=tk.LEFT)
    ttk.Button(brow, text="↓", width=3, command=lambda: move(1)).pack(side=tk.LEFT)
    ttk.Button(brow, text="Send now", command=lambda: bus.send(build_step()["cmd"])
               if build_step() and build_step().get("cmd") else None).pack(side=tk.LEFT, padx=8)

    # --- run / save / load ---
    log_box = tk.Text(right, height=8, font=("monospace", 8)); log_box.pack(fill=tk.BOTH, expand=True, pady=4)
    def log(m):
        log_box.insert(tk.END, m + "\n"); log_box.see(tk.END)

    def do_run():
        stop_flag["v"] = False
        scen = {"name": "studio", "steps": list(steps)}
        threading.Thread(target=run_scenario,
                         args=(bus, scen), kwargs={"log": lambda m: root.after(0, log, m),
                                                   "stop": lambda: stop_flag["v"]},
                         daemon=True).start()

    def do_save():
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if p:
            json.dump({"name": "studio", "steps": steps}, open(p, "w"), ensure_ascii=False, indent=2)
            log(f"saved {len(steps)} steps -> {p}")

    def do_load():
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if p:
            steps.clear(); steps.extend(json.load(open(p)).get("steps", []))
            redraw_steps(); log(f"loaded {len(steps)} steps <- {p}")

    rrow = ttk.Frame(right); rrow.pack(fill=tk.X)
    ttk.Button(rrow, text="▶ Run", command=do_run).pack(side=tk.LEFT)
    ttk.Button(rrow, text="■ Stop", command=lambda: stop_flag.__setitem__("v", True)).pack(side=tk.LEFT, padx=2)
    ttk.Button(rrow, text="Save…", command=do_save).pack(side=tk.LEFT, padx=8)
    ttk.Button(rrow, text="Load…", command=do_load).pack(side=tk.LEFT)

    # periodic telemetry refresh + palette refresh
    def tick():
        tel_box.delete("1.0", tk.END)
        for vid in sorted(bus.telemetry):
            t = bus.telemetry[vid]
            tel_box.insert(tk.END, f"v{vid} {t.get('mode','?'):10} alt {float(t.get('alt_rel',0)):6.1f} "
                                   f"gs {float(t.get('groundspeed',0)):4.1f} wp {t.get('current_wp','?')}\n")
        refresh_palette()
        root.after(700, tick)

    bus.request_registry()
    root.after(500, tick)
    root.mainloop()


def _num(s):
    try:
        f = float(s)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", help="run a saved scenario JSON headless")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)

    if args.run:
        bus = Bus(host=args.host)
        time.sleep(0.5)
        scenario = json.load(open(args.run))
        run_scenario(bus, scenario, dry_run=args.dry_run)
        return 0

    launch_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
