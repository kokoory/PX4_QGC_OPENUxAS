#!/usr/bin/env python3
"""QGC Test Report Generator.

Reads JSON test results produced by test_orchestrator.py and generates:
  - Colored console summary (ANSI colors)
  - Self-contained HTML report (embedded CSS, no external dependencies)

Usage:
    python3 test_report.py --input result.json [--html report.html]
    python3 test_report.py --input results_dir/
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import sys
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

class _C:
    """ANSI color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

    @staticmethod
    def ok(text: str) -> str:
        return f"{_C.GREEN}{text}{_C.RESET}"

    @staticmethod
    def fail(text: str) -> str:
        return f"{_C.RED}{text}{_C.RESET}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{_C.YELLOW}{text}{_C.RESET}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{_C.BOLD}{text}{_C.RESET}"

    @staticmethod
    def dim(text: str) -> str:
        return f"{_C.DIM}{text}{_C.RESET}"


# ---------------------------------------------------------------------------
# Result loading
# ---------------------------------------------------------------------------

def load_results(path: str) -> list[dict[str, Any]]:
    """Load one or more JSON result files.

    *path* may point to a single .json file or a directory containing
    multiple result .json files.
    """
    p = pathlib.Path(path)
    if p.is_file():
        with open(p, "r") as f:
            data = json.load(f)
        # Wrap single result in a list for uniform handling
        if isinstance(data, dict):
            data = [data]
        return data

    if p.is_dir():
        results: list[dict[str, Any]] = []
        for fp in sorted(p.glob("*.json")):
            with open(fp, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                results.append(data)
            elif isinstance(data, list):
                results.extend(data)
        return results

    print(f"ERROR: Path does not exist: {path}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------

def print_console_report(results: list[dict[str, Any]]) -> None:
    """Print a colored summary to the terminal."""
    total_scenarios = len(results)
    passed_scenarios = sum(1 for r in results if r.get("passed", False))
    failed_scenarios = total_scenarios - passed_scenarios

    print()
    print(_C.bold("=" * 72))
    print(_C.bold("  QGC TEST REPORT"))
    print(_C.bold("=" * 72))
    print()

    for i, result in enumerate(results):
        scenario = result.get("scenario", "unknown")
        ts = result.get("timestamp", "")
        passed = result.get("passed", False)
        total_steps = result.get("total_steps", 0)
        passed_steps = result.get("passed_steps", 0)
        failed_steps = result.get("failed_steps", 0)
        duration = result.get("duration", 0)

        badge = _C.ok("PASS") if passed else _C.fail("FAIL")
        print(f"  {_C.bold(scenario)}  [{badge}]")
        print(f"    Timestamp:  {_C.dim(ts)}")
        print(f"    Instance:   {result.get('instance', '?')}")
        print(f"    Duration:   {duration:.1f}s")
        print(f"    Steps:      {_C.ok(str(passed_steps))} passed / "
              f"{_C.fail(str(failed_steps)) if failed_steps else str(failed_steps)} failed / "
              f"{total_steps} total")
        print()

        # Steps table
        steps = result.get("steps", [])
        if steps:
            hdr = f"    {'#':>3}  {'Step Name':<30} {'Action':<18} {'Result':>8}  {'Time':>7}  Details"
            print(_C.dim(hdr))
            print(_C.dim("    " + "-" * 100))
            for j, step in enumerate(steps):
                sname = step.get("name", "?")[:30]
                saction = step.get("action", "?")[:18]
                spassed = step.get("passed", False)
                sduration = step.get("duration", 0)
                sdetails = step.get("details", "")
                serror = step.get("error", "")

                tag = _C.ok("PASS") if spassed else _C.fail("FAIL")
                detail_str = serror if serror else sdetails
                if len(detail_str) > 60:
                    detail_str = detail_str[:57] + "..."

                print(f"    {j+1:>3}  {sname:<30} {saction:<18} {tag:>17}  "
                      f"{sduration:>6.2f}s  {detail_str}")

            print()

        # Errors section
        errors = [s for s in steps if s.get("error")]
        if errors:
            print(f"    {_C.fail('Errors:')}")
            for s in errors:
                print(f"      - {_C.bold(s.get('name', '?'))}: {_C.fail(s['error'])}")
            print()

        if i < len(results) - 1:
            print("    " + "-" * 72)
            print()

    # Batch summary
    print(_C.bold("=" * 72))
    overall_badge = _C.ok("ALL PASSED") if failed_scenarios == 0 else _C.fail("FAILURES DETECTED")
    print(f"  Summary: {passed_scenarios}/{total_scenarios} scenarios passed  [{overall_badge}]")

    total_duration = sum(r.get("duration", 0) for r in results)
    total_steps_all = sum(r.get("total_steps", 0) for r in results)
    passed_steps_all = sum(r.get("passed_steps", 0) for r in results)
    print(f"  Total steps: {passed_steps_all}/{total_steps_all} passed, "
          f"Total time: {total_duration:.1f}s")
    print(_C.bold("=" * 72))
    print()


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QGC Test Report</title>
<style>
  :root {{
    --bg: #1a1a2e;
    --card-bg: #16213e;
    --text: #e0e0e0;
    --text-dim: #8899aa;
    --green: #00c853;
    --red: #ff1744;
    --yellow: #ffc107;
    --blue: #2979ff;
    --border: #2a3a5e;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
    background: var(--bg);
    color: var(--text);
    padding: 2rem;
    line-height: 1.6;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
  h2 {{ font-size: 1.3rem; margin: 1.5rem 0 0.8rem 0; color: var(--blue); }}
  .header {{
    display: flex; justify-content: space-between; align-items: center;
    border-bottom: 2px solid var(--border); padding-bottom: 1rem; margin-bottom: 1.5rem;
  }}
  .badge {{
    display: inline-block; padding: 0.3rem 1rem; border-radius: 4px;
    font-weight: bold; font-size: 1.1rem; text-transform: uppercase;
  }}
  .badge-pass {{ background: var(--green); color: #000; }}
  .badge-fail {{ background: var(--red); color: #fff; }}
  .meta {{ color: var(--text-dim); font-size: 0.9rem; }}
  .summary-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 1rem; margin-bottom: 1.5rem;
  }}
  .summary-card {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 6px; padding: 1rem; text-align: center;
  }}
  .summary-card .label {{ color: var(--text-dim); font-size: 0.8rem; text-transform: uppercase; }}
  .summary-card .value {{ font-size: 1.6rem; font-weight: bold; margin-top: 0.3rem; }}
  table {{
    width: 100%; border-collapse: collapse; margin-bottom: 1.5rem;
    background: var(--card-bg); border-radius: 6px; overflow: hidden;
  }}
  th {{
    background: #0d1b3e; text-align: left; padding: 0.7rem 1rem;
    font-size: 0.85rem; text-transform: uppercase; color: var(--text-dim);
    border-bottom: 2px solid var(--border);
  }}
  td {{ padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }}
  tr:last-child td {{ border-bottom: none; }}
  .pass {{ color: var(--green); font-weight: bold; }}
  .fail {{ color: var(--red); font-weight: bold; }}
  .errors {{
    background: #2d1520; border: 1px solid #5c2030; border-radius: 6px;
    padding: 1rem; margin-bottom: 1.5rem;
  }}
  .errors h3 {{ color: var(--red); margin-bottom: 0.5rem; }}
  .errors ul {{ padding-left: 1.5rem; }}
  .errors li {{ margin-bottom: 0.3rem; }}
  .scenario-block {{
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem;
  }}
  .scenario-header {{
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 1rem;
  }}
  .footer {{
    text-align: center; color: var(--text-dim); font-size: 0.8rem;
    margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>QGC Test Report</h1>
      <div class="meta">Generated: {generated_date}</div>
    </div>
    <span class="badge {overall_badge_class}">{overall_status}</span>
  </div>

  <div class="summary-grid">
    <div class="summary-card">
      <div class="label">Scenarios</div>
      <div class="value">{total_scenarios}</div>
    </div>
    <div class="summary-card">
      <div class="label">Passed</div>
      <div class="value" style="color: var(--green)">{passed_scenarios}</div>
    </div>
    <div class="summary-card">
      <div class="label">Failed</div>
      <div class="value" style="color: var(--red)">{failed_scenarios}</div>
    </div>
    <div class="summary-card">
      <div class="label">Total Steps</div>
      <div class="value">{total_steps}</div>
    </div>
    <div class="summary-card">
      <div class="label">Duration</div>
      <div class="value">{total_duration}</div>
    </div>
  </div>

  {scenario_blocks}

  <div class="footer">
    QGroundControl Test Automation &mdash; Report generated by test_report.py
  </div>
</div>
</body>
</html>
"""

_SCENARIO_BLOCK = """\
  <div class="scenario-block">
    <div class="scenario-header">
      <h2>{scenario_name}</h2>
      <span class="badge {badge_class}">{badge_text}</span>
    </div>
    <div class="meta" style="margin-bottom: 1rem;">
      Timestamp: {timestamp} &nbsp;|&nbsp;
      Instance: {instance} &nbsp;|&nbsp;
      Duration: {duration}s &nbsp;|&nbsp;
      Steps: {passed_steps}/{total_steps} passed
    </div>

    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Step Name</th>
          <th>Action</th>
          <th>Result</th>
          <th>Duration</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {step_rows}
      </tbody>
    </table>

    {errors_section}
  </div>
"""


def _esc(text: Any) -> str:
    """HTML-escape a value."""
    return html.escape(str(text)) if text else ""


def generate_html_report(results: list[dict[str, Any]]) -> str:
    """Generate a self-contained HTML report string."""
    total_scenarios = len(results)
    passed_scenarios = sum(1 for r in results if r.get("passed", False))
    failed_scenarios = total_scenarios - passed_scenarios
    total_steps = sum(r.get("total_steps", 0) for r in results)
    total_duration = sum(r.get("duration", 0) for r in results)

    overall_status = "PASS" if failed_scenarios == 0 else "FAIL"
    overall_badge_class = "badge-pass" if failed_scenarios == 0 else "badge-fail"

    scenario_blocks_html = []
    for result in results:
        scenario_name = _esc(result.get("scenario", "unknown"))
        passed = result.get("passed", False)
        badge_class = "badge-pass" if passed else "badge-fail"
        badge_text = "PASS" if passed else "FAIL"

        steps = result.get("steps", [])
        step_rows = []
        for j, step in enumerate(steps):
            spassed = step.get("passed", False)
            result_class = "pass" if spassed else "fail"
            result_text = "PASS" if spassed else "FAIL"
            details = _esc(step.get("error", "") or step.get("details", ""))
            if len(details) > 120:
                details = details[:117] + "..."

            step_rows.append(
                f'<tr>'
                f'<td>{j+1}</td>'
                f'<td>{_esc(step.get("name", "?"))}</td>'
                f'<td>{_esc(step.get("action", "?"))}</td>'
                f'<td class="{result_class}">{result_text}</td>'
                f'<td>{step.get("duration", 0):.2f}s</td>'
                f'<td>{details}</td>'
                f'</tr>'
            )

        # Errors section
        errors = [s for s in steps if s.get("error")]
        errors_section = ""
        if errors:
            error_items = "\n".join(
                f'<li><strong>{_esc(s.get("name", "?"))}:</strong> {_esc(s["error"])}</li>'
                for s in errors
            )
            errors_section = (
                f'<div class="errors">\n'
                f'  <h3>Errors</h3>\n'
                f'  <ul>{error_items}</ul>\n'
                f'</div>'
            )

        scenario_blocks_html.append(_SCENARIO_BLOCK.format(
            scenario_name=scenario_name,
            badge_class=badge_class,
            badge_text=badge_text,
            timestamp=_esc(result.get("timestamp", "")),
            instance=result.get("instance", "?"),
            duration=result.get("duration", 0),
            passed_steps=result.get("passed_steps", 0),
            total_steps=result.get("total_steps", 0),
            step_rows="\n        ".join(step_rows),
            errors_section=errors_section,
        ))

    return _HTML_TEMPLATE.format(
        generated_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        overall_badge_class=overall_badge_class,
        overall_status=overall_status,
        total_scenarios=total_scenarios,
        passed_scenarios=passed_scenarios,
        failed_scenarios=failed_scenarios,
        total_steps=total_steps,
        total_duration=f"{total_duration:.1f}s",
        scenario_blocks="\n".join(scenario_blocks_html),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="QGC Test Report Generator - console & HTML reports"
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to a JSON result file or directory of JSON result files",
    )
    parser.add_argument(
        "--html", default=None,
        help="Path to write HTML report (optional)",
    )
    args = parser.parse_args()

    results = load_results(args.input)
    if not results:
        print("ERROR: No results found.", file=sys.stderr)
        sys.exit(1)

    # Always print console report
    print_console_report(results)

    # Optionally generate HTML
    if args.html:
        html_content = generate_html_report(results)
        out_path = pathlib.Path(args.html)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html_content, encoding="utf-8")
        print(f"HTML report written to: {out_path}")

    # Exit code based on results
    all_passed = all(r.get("passed", False) for r in results)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
