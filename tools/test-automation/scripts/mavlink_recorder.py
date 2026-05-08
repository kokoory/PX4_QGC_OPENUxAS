#!/usr/bin/env python3
"""MAVLink Recorder.

Records ALL MAVLink messages from a PX4 SITL instance to:
  - CSV file with columns: timestamp_us, elapsed_s, msg_type, src_system,
    src_component, seq, payload_json
  - Raw binary .mavlog file (tlog format, timestamped raw bytes)
  - Summary JSON at end with message type counts and session metadata

Periodic status output every 10 seconds shows message rates and counts.

Usage:
    python3 mavlink_recorder.py --instance 0 --output-dir data/
    python3 mavlink_recorder.py --instance 0 --output-dir data/ --duration 300
    python3 mavlink_recorder.py --instance 0 --output-dir data/ --duration 60 --rate 10
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import signal
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pymavlink import mavutil
except ImportError:
    print("ERROR: pymavlink is required.  Install with: pip install pymavlink",
          file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"

# Base port
MAVLINK_BASE_PORT = 14540

# CSV header
CSV_COLUMNS = [
    "timestamp_us",
    "elapsed_s",
    "msg_type",
    "src_system",
    "src_component",
    "seq",
    "payload_json",
]


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class MAVLinkRecorder:
    """Records all MAVLink messages to CSV and raw binary formats."""

    def __init__(
        self,
        instance: int = 0,
        host: str = "127.0.0.1",
        output_dir: str = "data",
        duration: float | None = None,
        status_interval: float = 10.0,
        rate_limit: float | None = None,
    ):
        self.instance = instance
        self.host = host
        self.port = MAVLINK_BASE_PORT + instance
        self.output_dir = Path(output_dir)
        self.duration = duration
        self.status_interval = status_interval
        self.rate_limit = rate_limit  # minimum seconds between messages (1/Hz)

        self.conn: mavutil.mavlink_connection | None = None
        self._running = False

        # Stats
        self._msg_count = 0
        self._type_counts: dict[str, int] = {}
        self._start_time: float = 0
        self._start_time_us: int = 0
        self._last_status_time: float = 0
        self._last_status_count: int = 0
        self._bytes_written: int = 0

        # File handles
        self._csv_writer: csv.writer | None = None
        self._csv_file: io.TextIOWrapper | None = None
        self._raw_file: io.BufferedWriter | None = None

        # Generate timestamped filenames
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._csv_path = self.output_dir / f"mavlink_inst{instance}_{ts}.csv"
        self._raw_path = self.output_dir / f"mavlink_inst{instance}_{ts}.mavlog"
        self._summary_path = self.output_dir / f"mavlink_inst{instance}_{ts}_summary.json"

    def connect(self) -> None:
        connstr = f"udpin:{self.host}:{self.port}"
        print(f"{_C.CYAN}[recorder] Connecting to {connstr} ...{_C.RESET}")
        self.conn = mavutil.mavlink_connection(connstr)
        self.conn.wait_heartbeat(timeout=30)
        print(f"{_C.GREEN}[recorder] Heartbeat received "
              f"(sys={self.conn.target_system}, "
              f"comp={self.conn.target_component}){_C.RESET}")

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def _open_files(self) -> None:
        """Open CSV and raw output files."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._csv_file = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(CSV_COLUMNS)

        self._raw_file = open(self._raw_path, "wb")

        print(f"{_C.CYAN}[recorder] CSV output:  {self._csv_path}{_C.RESET}")
        print(f"{_C.CYAN}[recorder] Raw output:  {self._raw_path}{_C.RESET}")

    def _close_files(self) -> None:
        """Close output files."""
        if self._csv_file:
            self._csv_file.close()
            self._csv_file = None
        if self._raw_file:
            self._raw_file.close()
            self._raw_file = None

    def run(self) -> dict[str, Any]:
        """Main recording loop. Returns summary dict."""
        self._open_files()
        self._running = True
        self._start_time = time.time()
        self._start_time_us = int(self._start_time * 1e6)
        self._last_status_time = self._start_time

        min_interval = (1.0 / self.rate_limit) if self.rate_limit else 0.0
        last_msg_time = 0.0

        print(f"\n{_C.BOLD}{'='*60}{_C.RESET}")
        print(f"{_C.BOLD}  Recording MAVLink messages (instance {self.instance}){_C.RESET}")
        if self.duration:
            print(f"{_C.BOLD}  Duration: {self.duration}s{_C.RESET}")
        else:
            print(f"{_C.BOLD}  Duration: unlimited (Ctrl+C to stop){_C.RESET}")
        if self.rate_limit:
            print(f"{_C.BOLD}  Rate limit: {self.rate_limit} Hz{_C.RESET}")
        print(f"{_C.BOLD}{'='*60}{_C.RESET}\n")

        try:
            while self._running:
                # Check duration limit
                now = time.time()
                elapsed = now - self._start_time
                if self.duration and elapsed >= self.duration:
                    print(f"\n{_C.YELLOW}[recorder] Duration limit reached "
                          f"({self.duration}s).{_C.RESET}")
                    break

                # Rate limiting
                if min_interval > 0:
                    since_last = now - last_msg_time
                    if since_last < min_interval:
                        time.sleep(min_interval - since_last)

                # Receive message
                msg = self.conn.recv_match(blocking=True, timeout=0.5)
                if msg is None:
                    self._maybe_print_status()
                    continue

                now = time.time()
                last_msg_time = now
                self._record_message(msg, now)
                self._maybe_print_status()

        except KeyboardInterrupt:
            print(f"\n{_C.YELLOW}[recorder] Interrupted by user.{_C.RESET}")
        finally:
            self._running = False
            self._close_files()

        summary = self._write_summary()
        return summary

    def _record_message(self, msg: Any, now: float) -> None:
        """Write a single message to CSV and raw files."""
        mtype = msg.get_type()
        if mtype == "BAD_DATA":
            return

        self._msg_count += 1
        self._type_counts[mtype] = self._type_counts.get(mtype, 0) + 1

        timestamp_us = int(now * 1e6)
        elapsed_s = round(now - self._start_time, 6)

        # Extract fields for CSV
        src_system = msg.get_srcSystem()
        src_component = msg.get_srcComponent()
        seq = msg.get_seq()

        # Build payload dict from message fields
        payload: dict[str, Any] = {}
        if hasattr(msg, '_fieldnames'):
            for field_name in msg._fieldnames:
                val = getattr(msg, field_name, None)
                # Convert bytes to hex for JSON serialisation
                if isinstance(val, (bytes, bytearray)):
                    val = val.hex()
                elif isinstance(val, list):
                    val = [v.hex() if isinstance(v, (bytes, bytearray)) else v for v in val]
                payload[field_name] = val

        payload_json = json.dumps(payload, default=str, separators=(",", ":"))

        # Write CSV row
        if self._csv_writer:
            self._csv_writer.writerow([
                timestamp_us,
                elapsed_s,
                mtype,
                src_system,
                src_component,
                seq,
                payload_json,
            ])

        # Write raw binary (tlog format: 8-byte timestamp + raw MAVLink bytes)
        if self._raw_file:
            raw_bytes = msg.get_msgbuf()
            if raw_bytes:
                # tlog format: uint64 microseconds (little-endian) + raw message
                ts_bytes = struct.pack("<Q", timestamp_us)
                self._raw_file.write(ts_bytes + bytes(raw_bytes))
                self._bytes_written += len(ts_bytes) + len(raw_bytes)

    def _maybe_print_status(self) -> None:
        """Print periodic status update."""
        now = time.time()
        if now - self._last_status_time < self.status_interval:
            return

        elapsed = now - self._start_time
        interval_count = self._msg_count - self._last_status_count
        interval_time = now - self._last_status_time
        rate = interval_count / interval_time if interval_time > 0 else 0

        # Top message types in this interval
        top_types = sorted(self._type_counts.items(), key=lambda x: -x[1])[:5]
        top_str = ", ".join(f"{t}:{c}" for t, c in top_types)

        size_kb = self._bytes_written / 1024

        remaining = ""
        if self.duration:
            left = max(0, self.duration - elapsed)
            remaining = f"  remaining={left:.0f}s"

        print(f"{_C.DIM}[{elapsed:7.1f}s] msgs={self._msg_count:>7}  "
              f"rate={rate:5.0f}/s  raw={size_kb:7.1f}KB  "
              f"top=[{top_str}]{remaining}{_C.RESET}")

        self._last_status_time = now
        self._last_status_count = self._msg_count

    def _write_summary(self) -> dict[str, Any]:
        """Write and return a summary JSON."""
        elapsed = time.time() - self._start_time
        avg_rate = self._msg_count / elapsed if elapsed > 0 else 0

        summary: dict[str, Any] = {
            "instance": self.instance,
            "host": self.host,
            "port": self.port,
            "start_time": datetime.fromtimestamp(
                self._start_time, tz=timezone.utc
            ).isoformat(),
            "duration_s": round(elapsed, 3),
            "total_messages": self._msg_count,
            "average_rate_hz": round(avg_rate, 1),
            "unique_types": len(self._type_counts),
            "bytes_written": self._bytes_written,
            "csv_file": str(self._csv_path),
            "raw_file": str(self._raw_path),
            "message_types": dict(
                sorted(self._type_counts.items(), key=lambda x: -x[1])
            ),
        }

        try:
            self._summary_path.parent.mkdir(parents=True, exist_ok=True)
            self._summary_path.write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
            print(f"\n{_C.GREEN}[recorder] Summary saved: {self._summary_path}{_C.RESET}")
        except OSError as e:
            print(f"{_C.RED}[recorder] Failed to write summary: {e}{_C.RESET}",
                  file=sys.stderr)

        # Print summary to console
        print(f"\n{_C.BOLD}{'='*60}{_C.RESET}")
        print(f"{_C.BOLD}  Recording Summary{_C.RESET}")
        print(f"{_C.BOLD}{'='*60}{_C.RESET}")
        print(f"  Duration:       {elapsed:.1f}s")
        print(f"  Total messages: {self._msg_count}")
        print(f"  Average rate:   {avg_rate:.1f} msg/s")
        print(f"  Unique types:   {len(self._type_counts)}")
        print(f"  Bytes written:  {self._bytes_written:,}")
        print(f"  CSV file:       {self._csv_path}")
        print(f"  Raw file:       {self._raw_path}")
        print(f"  Summary file:   {self._summary_path}")

        if self._type_counts:
            print(f"\n  {_C.BOLD}Message type breakdown:{_C.RESET}")
            for mtype, count in sorted(self._type_counts.items(), key=lambda x: -x[1]):
                pct = (count / self._msg_count * 100) if self._msg_count else 0
                bar_len = int(pct / 2)
                bar = "#" * bar_len
                print(f"    {mtype:<30} {count:>7}  ({pct:5.1f}%)  {_C.DIM}{bar}{_C.RESET}")

        print(f"{_C.BOLD}{'='*60}{_C.RESET}\n")
        return summary

    def signal_stop(self) -> None:
        """Signal the recorder to stop gracefully."""
        self._running = False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MAVLink Recorder - capture all messages to CSV + raw binary",
    )
    parser.add_argument(
        "--instance", type=int, default=0,
        help="PX4 SITL instance number (default: 0, port = 14540 + instance)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="MAVLink host IP address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--output-dir", default="data",
        help="Output directory for recorded files (default: data/)",
    )
    parser.add_argument(
        "--duration", type=float, default=None,
        help="Recording duration in seconds (default: unlimited)",
    )
    parser.add_argument(
        "--rate", type=float, default=None,
        help="Maximum message rate in Hz (default: unlimited)",
    )
    parser.add_argument(
        "--status-interval", type=float, default=10.0,
        help="Seconds between status output lines (default: 10)",
    )
    args = parser.parse_args()

    recorder = MAVLinkRecorder(
        instance=args.instance,
        host=args.host,
        output_dir=args.output_dir,
        duration=args.duration,
        status_interval=args.status_interval,
        rate_limit=args.rate,
    )

    def _signal_handler(signum, frame):
        print(f"\n{_C.YELLOW}[recorder] Signal {signum} received, stopping ...{_C.RESET}")
        recorder.signal_stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        recorder.connect()
        summary = recorder.run()
        sys.exit(0)
    except Exception as exc:
        print(f"{_C.RED}[recorder] Fatal error: {exc}{_C.RESET}", file=sys.stderr)
        sys.exit(1)
    finally:
        recorder.close()


if __name__ == "__main__":
    main()
