#!/usr/bin/env python3
"""EventBroadcaster monitor — GUI window (Tkinter).

A standalone window that shows, in time order, everything QGC broadcasts on the
EventBroadcaster Tx UDP port (45678): every guided-action button press, mission
edit/upload, UxAS search publish, etc. Same data as the terminal eb_monitor.py
but as a scrolling, colour-coded table you can filter / pause / save.

    python3 eb_monitor_gui.py
    python3 eb_monitor_gui.py --port 45678

Pairs with eb_scenario.py / eb_replay.py (which inject commands on 45679).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk, filedialog

CAT_COLORS = {
    "action":      "#00b4d8",   # button presses
    "command":     "#e9c46a",   # inbound commands (scenario/replay)
    "qgc_mission": "#c77dff",   # mission edits/uploads
    "uxas_search": "#74c476",   # UxAS search publishes
    "mavlink":     "#5b8def",   # vehicle command results
    "plan":        "#bb88ff",
    "bridge":      "#f4a261",
}


def _fmt_time(ts: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return "--:--:--.---"


class Listener(threading.Thread):
    """Background UDP receiver → thread-safe queue (GUI must not block on recv)."""
    def __init__(self, host: str, port: int, q: queue.Queue):
        super().__init__(daemon=True)
        self.q = q
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        self.sock.bind((host, port))
        self._run = True

    def run(self):
        while self._run:
            try:
                raw, _ = self.sock.recvfrom(65535)
            except OSError:
                break
            try:
                self.q.put(json.loads(raw.decode("utf-8", "replace")))
            except Exception:
                pass


class App:
    def __init__(self, root: tk.Tk, host: str, port: int):
        self.root = root
        self.port = port
        root.title(f"EventBroadcaster Monitor — :{port}")
        root.geometry("980x560")

        self.q: queue.Queue = queue.Queue()
        self.paused = tk.BooleanVar(value=False)
        self.autoscroll = tk.BooleanVar(value=True)
        self.filter_cat = tk.StringVar(value="all")
        self.count = 0
        self._last_seq = None
        self._rows: list[dict] = []   # kept for save

        # --- toolbar ---
        bar = ttk.Frame(root, padding=(6, 4))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="Filter:").pack(side=tk.LEFT)
        self.cat_box = ttk.Combobox(bar, width=14, state="readonly",
                                    textvariable=self.filter_cat,
                                    values=["all", "action", "command", "qgc_mission",
                                            "uxas_search", "mavlink", "plan", "bridge"])
        self.cat_box.pack(side=tk.LEFT, padx=(2, 10))
        self.cat_box.bind("<<ComboboxSelected>>", lambda e: self._refilter())
        ttk.Checkbutton(bar, text="Pause", variable=self.paused).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="Auto-scroll", variable=self.autoscroll).pack(side=tk.LEFT, padx=8)
        ttk.Button(bar, text="Clear", command=self._clear).pack(side=tk.LEFT)
        ttk.Button(bar, text="Save…", command=self._save).pack(side=tk.LEFT, padx=4)
        self.status = ttk.Label(bar, text="0 events")
        self.status.pack(side=tk.RIGHT)

        # --- table ---
        cols = ("time", "seq", "category", "event", "data")
        widths = (110, 50, 110, 200, 460)
        frame = ttk.Frame(root)
        frame.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, anchor=tk.W,
                             stretch=(c == "data"))
        for cat, col in CAT_COLORS.items():
            self.tree.tag_configure(cat, foreground=col)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.listener = Listener(host, port, self.q)
        self.listener.start()
        self.root.after(60, self._pump)

    def _passes(self, cat: str) -> bool:
        f = self.filter_cat.get()
        return f == "all" or f == cat

    def _add(self, msg: dict):
        seq = msg.get("seq")
        key = (seq, msg.get("category"), msg.get("event"))
        if key == self._last_seq and seq is not None:
            return
        self._last_seq = key
        self._rows.append(msg)
        self.count += 1
        cat = msg.get("category", "?")
        if not self._passes(cat):
            self.status.config(text=f"{self.count} events")
            return
        data = msg.get("data", {})
        data_str = json.dumps(data, ensure_ascii=False) if data else ""
        self.tree.insert("", tk.END, values=(
            _fmt_time(msg.get("timestamp", 0)), seq, cat,
            msg.get("event", ""), data_str), tags=(cat,))
        if self.autoscroll.get():
            self.tree.yview_moveto(1.0)
        self.status.config(text=f"{self.count} events")

    def _pump(self):
        if not self.paused.get():
            for _ in range(500):
                try:
                    self._add(self.q.get_nowait())
                except queue.Empty:
                    break
        self.root.after(60, self._pump)

    def _refilter(self):
        self.tree.delete(*self.tree.get_children())
        for msg in self._rows:
            cat = msg.get("category", "?")
            if not self._passes(cat):
                continue
            data = msg.get("data", {})
            self.tree.insert("", tk.END, values=(
                _fmt_time(msg.get("timestamp", 0)), msg.get("seq"), cat,
                msg.get("event", ""),
                json.dumps(data, ensure_ascii=False) if data else ""), tags=(cat,))
        if self.autoscroll.get():
            self.tree.yview_moveto(1.0)

    def _clear(self):
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        self.count = 0
        self.status.config(text="0 events")

    def _save(self):
        path = filedialog.asksaveasfilename(defaultextension=".jsonl",
                                            filetypes=[("JSON lines", "*.jsonl")])
        if not path:
            return
        with open(path, "w") as fh:
            for msg in self._rows:
                fh.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.status.config(text=f"saved {len(self._rows)} → {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=45678)
    args = ap.parse_args(argv)
    root = tk.Tk()
    App(root, args.host, args.port)
    root.mainloop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
