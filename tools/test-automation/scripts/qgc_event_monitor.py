#!/usr/bin/env python3
"""QGC Event Monitor.

Listens on UDP for QGC EventBroadcaster events and displays them in
real-time with ANSI color coding by category:

    action  = red
    view    = green
    map     = blue
    setting = yellow

Optionally saves all events to a JSON log file.

Usage:
    python3 qgc_event_monitor.py [--port 45678] [--log events.json]
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"

# Category-to-color mapping
CATEGORY_COLORS: dict[str, str] = {
    "action":  _C.RED,
    "view":    _C.GREEN,
    "map":     _C.BLUE,
    "setting": _C.YELLOW,
}

DEFAULT_COLOR = _C.MAGENTA


def _colorize(text: str, category: str) -> str:
    """Apply ANSI color based on event category."""
    color = CATEGORY_COLORS.get(category, DEFAULT_COLOR)
    return f"{color}{text}{_C.RESET}"


def _format_timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# ---------------------------------------------------------------------------
# Event monitor
# ---------------------------------------------------------------------------

class EventMonitor:
    """Real-time UDP event listener with colored console output."""

    def __init__(self, port: int = 45678, log_path: str | None = None):
        self.port = port
        self.log_path = log_path
        self._sock: socket.socket | None = None
        self._running = False
        self._event_count = 0
        self._events: list[dict[str, Any]] = []
        self._category_counts: dict[str, int] = {}

    def start(self) -> None:
        """Bind to UDP port and begin listening."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.settimeout(1.0)
        self._running = True

        print(f"{_C.BOLD}QGC Event Monitor{_C.RESET}")
        print(f"Listening on UDP port {_C.CYAN}{self.port}{_C.RESET}")
        if self.log_path:
            print(f"Logging to: {_C.CYAN}{self.log_path}{_C.RESET}")
        print(f"{_C.DIM}{'='*72}{_C.RESET}")
        print(f"{_C.DIM}{'Time':<14} {'Seq':>5}  {'Category':<10} {'Event':<20} {'Data'}{_C.RESET}")
        print(f"{_C.DIM}{'-'*72}{_C.RESET}")

    def run(self) -> None:
        """Main event loop. Blocks until stopped."""
        self.start()

        try:
            while self._running:
                try:
                    data, addr = self._sock.recvfrom(65535)
                    self._handle_packet(data, addr)
                except socket.timeout:
                    continue
                except OSError:
                    if self._running:
                        raise
                    break
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """Close socket, write log file, print summary."""
        self._running = False
        if self._sock:
            self._sock.close()
            self._sock = None

        # Write log file
        if self.log_path and self._events:
            try:
                with open(self.log_path, "w") as f:
                    json.dump({
                        "monitor_port": self.port,
                        "total_events": self._event_count,
                        "category_counts": self._category_counts,
                        "events": self._events,
                    }, f, indent=2, default=str)
                print(f"\n{_C.GREEN}Log saved: {self.log_path} "
                      f"({self._event_count} events){_C.RESET}")
            except OSError as e:
                print(f"\n{_C.RED}Failed to write log: {e}{_C.RESET}",
                      file=sys.stderr)

        # Print summary
        print(f"\n{_C.BOLD}{'='*72}{_C.RESET}")
        print(f"{_C.BOLD}Session Summary{_C.RESET}")
        print(f"  Total events: {_C.BOLD}{self._event_count}{_C.RESET}")
        if self._category_counts:
            for cat, count in sorted(self._category_counts.items()):
                color = CATEGORY_COLORS.get(cat, DEFAULT_COLOR)
                print(f"  {color}{cat:<12}{_C.RESET} {count}")
        print(f"{_C.BOLD}{'='*72}{_C.RESET}")

    def _handle_packet(self, data: bytes, addr: tuple) -> None:
        """Parse and display a single UDP packet."""
        try:
            event = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"{_C.DIM}[{_format_timestamp()}] Invalid packet from "
                  f"{addr[0]}:{addr[1]}: {e}{_C.RESET}", file=sys.stderr)
            return

        self._event_count += 1

        seq = event.get("seq", "?")
        category = event.get("category", "unknown")
        event_name = event.get("event", "?")
        event_data = event.get("data", {})

        # Track counts
        self._category_counts[category] = self._category_counts.get(category, 0) + 1

        # Store for log
        if self.log_path:
            self._events.append({
                "received_at": datetime.now(timezone.utc).isoformat(),
                "source": f"{addr[0]}:{addr[1]}",
                **event,
            })

        # Format data for display
        data_str = ""
        if event_data:
            if isinstance(event_data, dict):
                parts = [f"{k}={v}" for k, v in event_data.items()]
                data_str = ", ".join(parts)
            else:
                data_str = str(event_data)
            if len(data_str) > 50:
                data_str = data_str[:47] + "..."

        # Colored output
        ts = _format_timestamp()
        cat_colored = _colorize(f"{category:<10}", category)
        evt_colored = _colorize(f"{event_name:<20}", category)
        print(f"{_C.DIM}{ts}{_C.RESET}  {seq:>5}  {cat_colored} {evt_colored} "
              f"{_C.DIM}{data_str}{_C.RESET}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QGC Event Monitor - real-time event display",
    )
    parser.add_argument(
        "--port", type=int, default=45678,
        help="UDP port to listen on (default: 45678)",
    )
    parser.add_argument(
        "--log", default=None,
        help="Path to save events as JSON log file",
    )
    args = parser.parse_args()

    monitor = EventMonitor(port=args.port, log_path=args.log)

    def _signal_handler(signum, frame):
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    monitor.run()


if __name__ == "__main__":
    main()
