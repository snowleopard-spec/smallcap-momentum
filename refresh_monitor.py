"""
Refresh Monitor — wraps refresh.py with instrumentation and sends a daily email via Resend.

Captures:
    1. Error log with context (stderr, non-zero exit codes, tracebacks)
    2. Total and per-step run times
    3. Memory usage (RSS peak, system RAM %, swap)
    4. Which data files were actually refreshed vs skipped

Usage (cron):
    python refresh_monitor.py --yes

Requires:
    RESEND_API_KEY environment variable (set in .env or export)
    pip install resend python-dotenv
"""

import os
import sys
import re
import time
import json
import subprocess
import traceback
import threading
import platform
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
REFRESH_SCRIPT = PROJECT_DIR / "refresh.py"
PYTHON = sys.executable

# Data files and their expected refresh cadence (mirrors refresh.py / api.py)
DATA_FILES = {
    "universe":     {"file": DATA_DIR / "universe.parquet",         "cadence": "weekly (7d)"},
    "prices":       {"file": DATA_DIR / "prices_combined.parquet",  "cadence": "daily (1d)"},
    "fundamentals": {"file": DATA_DIR / "fundamentals.parquet",     "cadence": "monthly (30d)"},
    "news":         {"file": DATA_DIR / "news_attention.parquet",   "cadence": "daily (1d)"},
    "insider":      {"file": DATA_DIR / "insider_activity.parquet", "cadence": "fortnightly (14d)"},
    "watchlist":    {"file": DATA_DIR / "watchlist.parquet",        "cadence": "every run (output)"},
}

# Email
RECIPIENT = "wes.hunt1@outlook.com"  # ← change to your actual address
SENDER = "onboarding@resend.dev"  # ← must match a verified Resend domain


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_file_mtime(filepath):
    """Return mtime as datetime or None."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(filepath), tz=timezone.utc)
    except OSError:
        return None


def get_file_size_mb(filepath):
    """Return file size in MB or None."""
    try:
        return os.path.getsize(filepath) / (1024 * 1024)
    except OSError:
        return None


def snapshot_data_files():
    """Capture mtime + size for each data file before the run."""
    snap = {}
    for name, info in DATA_FILES.items():
        fpath = info["file"]
        snap[name] = {
            "mtime_before": get_file_mtime(fpath),
            "size_before": get_file_size_mb(fpath),
            "exists_before": fpath.exists(),
        }
    return snap


def diff_data_files(before_snap):
    """Compare current state against pre-run snapshot. Returns list of dicts."""
    results = []
    for name, info in DATA_FILES.items():
        fpath = info["file"]
        bef = before_snap[name]
        mtime_after = get_file_mtime(fpath)
        size_after = get_file_size_mb(fpath)
        exists_after = fpath.exists()

        if not bef["exists_before"] and exists_after:
            status = "CREATED"
        elif bef["mtime_before"] and mtime_after and mtime_after > bef["mtime_before"]:
            status = "REFRESHED"
        elif not exists_after:
            status = "MISSING"
        else:
            status = "UNCHANGED"

        results.append({
            "name": name,
            "status": status,
            "cadence": info["cadence"],
            "size_mb": f"{size_after:.1f}" if size_after else "—",
            "last_modified": mtime_after.strftime("%Y-%m-%d %H:%M UTC") if mtime_after else "—",
        })
    return results


def get_system_memory():
    """
    Read /proc/meminfo (Linux). Returns dict with total, available, used, swap info.
    Falls back gracefully on non-Linux.
    """
    mem = {}
    try:
        with open("/proc/meminfo") as f:
            raw = f.read()
        for key in ["MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree", "Buffers", "Cached"]:
            match = re.search(rf"^{key}:\s+(\d+)\s+kB", raw, re.MULTILINE)
            if match:
                mem[key] = int(match.group(1)) * 1024  # bytes
    except FileNotFoundError:
        pass
    return mem


def get_process_peak_rss(pid):
    """Read VmHWM (peak RSS) from /proc/{pid}/status. Linux only."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024  # kB → bytes
    except (FileNotFoundError, ProcessLookupError):
        pass
    return None


def fmt_bytes(b):
    """Format bytes as human-readable string."""
    if b is None:
        return "—"
    if b >= 1024 ** 3:
        return f"{b / 1024**3:.1f} GB"
    if b >= 1024 ** 2:
        return f"{b / 1024**2:.0f} MB"
    return f"{b / 1024:.0f} KB"


def extract_errors(output_text, return_code):
    """
    Parse stdout/stderr for errors. Returns list of error dicts with context.
    Each dict: {level, source, message, context}
    """
    errors = []

    if return_code != 0:
        errors.append({
            "level": "CRITICAL",
            "source": "refresh.py",
            "message": f"Process exited with return code {return_code}",
            "context": "Non-zero exit code indicates the pipeline crashed or a step failed.",
        })

    # Scan for Python tracebacks
    tb_pattern = re.compile(r'Traceback \(most recent call last\):.*?(?=\n\S|\Z)', re.DOTALL)
    for match in tb_pattern.finditer(output_text):
        tb_text = match.group().strip()
        # Extract the final error line
        lines = tb_text.strip().splitlines()
        error_line = lines[-1] if lines else "Unknown error"
        # Identify which module/file is involved
        file_matches = re.findall(r'File "([^"]+)"', tb_text)
        source_file = file_matches[-1] if file_matches else "unknown"
        errors.append({
            "level": "ERROR",
            "source": os.path.basename(source_file),
            "message": error_line.strip(),
            "context": "\n".join(lines[-6:]),  # last 6 lines of traceback for context
        })

    # Scan for common warning/error patterns in output
    for line in output_text.splitlines():
        lower = line.lower()
        if "error" in lower and "traceback" not in lower:
            if "rate limit" in lower or "429" in lower:
                errors.append({
                    "level": "WARNING",
                    "source": "API",
                    "message": line.strip(),
                    "context": "API rate limit hit. Data may be incomplete. Consider spacing requests.",
                })
            elif "timeout" in lower or "timed out" in lower:
                errors.append({
                    "level": "WARNING",
                    "source": "Network",
                    "message": line.strip(),
                    "context": "Network timeout. The external API may be slow or down.",
                })
            elif "connection" in lower:
                errors.append({
                    "level": "WARNING",
                    "source": "Network",
                    "message": line.strip(),
                    "context": "Connection error. Check network/firewall on the droplet.",
                })
        elif "✗ failed" in lower or "failed in" in lower:
            errors.append({
                "level": "ERROR",
                "source": "refresh.py step",
                "message": line.strip(),
                "context": "A pipeline step returned a non-zero exit code.",
            })
        elif "killed" in lower or "oom" in lower or "cannot allocate" in lower:
            errors.append({
                "level": "CRITICAL",
                "source": "System",
                "message": line.strip(),
                "context": "Process was killed, likely by OOM killer. Your droplet may need more RAM.",
            })

    return errors


# ── Memory Sampler ────────────────────────────────────────────────────────────

class MemorySampler:
    """
    Background thread that samples system memory every N seconds while the
    pipeline runs, tracking the peak (highest RAM usage seen during the run).
    Negligible overhead — just reads /proc/meminfo periodically.
    """

    def __init__(self, interval=2.0):
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        self.samples = []        # list of (timestamp, used_bytes, available_bytes)
        self.peak_used = 0
        self.peak_pct = 0.0
        self.peak_available = 0
        self.total_ram = 0
        self.swap_peak_used = 0
        self.swap_total = 0

    def _sample(self):
        mem = get_system_memory()
        if not mem:
            return
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", 0)
        used = total - avail if total and avail else 0
        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free if swap_total else 0

        self.total_ram = total
        self.swap_total = swap_total
        self.samples.append((time.time(), used, avail))

        if used > self.peak_used:
            self.peak_used = used
            self.peak_available = avail
            self.peak_pct = (used / total * 100) if total else 0

        if swap_used > self.swap_peak_used:
            self.swap_peak_used = swap_used

    def _run(self):
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def start(self):
        self._sample()  # immediate first sample
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._sample()  # one final sample

    def report(self):
        return {
            "total_ram": self.total_ram,
            "peak_used": self.peak_used,
            "peak_available": self.peak_available,
            "peak_pct": self.peak_pct,
            "swap_total": self.swap_total,
            "swap_peak_used": self.swap_peak_used,
            "sample_count": len(self.samples),
            "warning": None,
        }


# ── Run Pipeline ──────────────────────────────────────────────────────────────

def run_refresh(extra_args=None):
    """
    Execute refresh.py, capture all output, timing, and memory metrics.
    Memory is sampled every 2 seconds during the run to capture peak usage.
    Returns a report dict.
    """
    args = [PYTHON, str(REFRESH_SCRIPT)]
    if extra_args:
        args.extend(extra_args)

    # Snapshot data files before
    before_snap = snapshot_data_files()

    # Start memory sampler (samples every 2s in background thread)
    sampler = MemorySampler(interval=2.0)
    sampler.start()

    start_time = time.time()
    start_dt = datetime.now(timezone.utc)

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # merge stderr into stdout
            text=True,
            cwd=str(PROJECT_DIR),
        )

        output_lines = []
        for line in proc.stdout:
            output_lines.append(line)
            # Also write to the existing log so refresh.log still works
            sys.stdout.write(line)
            sys.stdout.flush()

        proc.wait()
        return_code = proc.returncode
        output_text = "".join(output_lines)

    except Exception as e:
        return_code = -1
        output_text = f"Failed to start refresh.py: {traceback.format_exc()}"

    elapsed = time.time() - start_time
    end_dt = datetime.now(timezone.utc)

    # Stop sampler and collect results
    sampler.stop()
    memory_report = sampler.report()

    # Data file diff
    file_diffs = diff_data_files(before_snap)

    # Parse errors
    errors = extract_errors(output_text, return_code)

    # Flag memory concerns based on PEAK usage during the run
    ram_pct = memory_report["peak_pct"]
    if ram_pct > 90:
        memory_report["warning"] = f"Peak RAM hit {ram_pct:.0f}% during the run — strongly consider upgrading your droplet."
    elif ram_pct > 75:
        memory_report["warning"] = f"Peak RAM hit {ram_pct:.0f}% during the run — monitor closely, may need a larger droplet soon."
    if memory_report["swap_peak_used"] > 0:
        existing = memory_report["warning"] or ""
        memory_report["warning"] = (existing + f" Swap was used ({fmt_bytes(memory_report['swap_peak_used'])}) — system is memory-constrained.").strip()

    return {
        "success": return_code == 0,
        "return_code": return_code,
        "start_time": start_dt,
        "end_time": end_dt,
        "elapsed_seconds": elapsed,
        "elapsed_human": f"{int(elapsed // 60)}m {int(elapsed % 60)}s",
        "errors": errors,
        "file_diffs": file_diffs,
        "memory": memory_report,
        "output_tail": output_text[-3000:] if len(output_text) > 3000 else output_text,
        "hostname": platform.node(),
    }


# ── Email ─────────────────────────────────────────────────────────────────────

def build_email_html(report):
    """Build a clean HTML email from the report dict."""

    status_emoji = "✅" if report["success"] else "❌"
    status_text = "SUCCESS" if report["success"] else "FAILED"
    status_color = "#22c55e" if report["success"] else "#ef4444"

    # Memory bar color (peak during run)
    ram_pct = report["memory"]["peak_pct"]
    if ram_pct > 90:
        mem_color = "#ef4444"
    elif ram_pct > 75:
        mem_color = "#f59e0b"
    else:
        mem_color = "#22c55e"

    # File diff rows
    file_rows = ""
    for f in report["file_diffs"]:
        if f["status"] == "REFRESHED":
            badge = '<span style="background:#22c55e;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">REFRESHED</span>'
        elif f["status"] == "CREATED":
            badge = '<span style="background:#3b82f6;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">CREATED</span>'
        elif f["status"] == "MISSING":
            badge = '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">MISSING</span>'
        else:
            badge = '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;font-weight:600;">UNCHANGED</span>'

        file_rows += f"""
        <tr style="border-bottom:1px solid #1e1e2e;">
            <td style="padding:8px 12px;font-weight:500;color:#e2e8f0;">{f['name']}</td>
            <td style="padding:8px 12px;">{badge}</td>
            <td style="padding:8px 12px;color:#94a3b8;font-size:12px;">{f['cadence']}</td>
            <td style="padding:8px 12px;color:#94a3b8;font-size:12px;">{f['size_mb']} MB</td>
            <td style="padding:8px 12px;color:#94a3b8;font-size:12px;">{f['last_modified']}</td>
        </tr>"""

    # Error rows
    error_section = ""
    if report["errors"]:
        error_rows = ""
        for err in report["errors"]:
            if err["level"] == "CRITICAL":
                lvl_color = "#ef4444"
            elif err["level"] == "ERROR":
                lvl_color = "#f59e0b"
            else:
                lvl_color = "#a78bfa"

            context_html = ""
            if err.get("context"):
                context_html = f'<pre style="background:#0f0f1a;padding:8px;border-radius:4px;font-size:11px;color:#94a3b8;margin-top:6px;overflow-x:auto;white-space:pre-wrap;">{err["context"]}</pre>'

            error_rows += f"""
            <div style="background:#1a1a2e;border-left:3px solid {lvl_color};padding:12px;margin-bottom:8px;border-radius:0 4px 4px 0;">
                <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;">
                    <span style="background:{lvl_color};color:#fff;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700;">{err['level']}</span>
                    <span style="color:#94a3b8;font-size:11px;">{err['source']}</span>
                </div>
                <div style="color:#e2e8f0;font-size:13px;font-family:monospace;">{err['message']}</div>
                {context_html}
            </div>"""

        error_section = f"""
        <div style="margin-top:24px;">
            <h2 style="color:#ff6a00;font-size:14px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">⚠ Error Log ({len(report['errors'])} issue{'s' if len(report['errors']) != 1 else ''})</h2>
            {error_rows}
        </div>"""
    else:
        error_section = """
        <div style="margin-top:24px;">
            <h2 style="color:#ff6a00;font-size:14px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Error Log</h2>
            <div style="background:#1a2e1a;border-left:3px solid #22c55e;padding:12px;border-radius:0 4px 4px 0;color:#86efac;font-size:13px;">
                No errors. Clean run.
            </div>
        </div>"""

    # Memory warning
    mem_warning_html = ""
    if report["memory"]["warning"]:
        mem_warning_html = f"""
        <div style="background:#2e1a1a;border-left:3px solid #f59e0b;padding:10px;margin-top:12px;border-radius:0 4px 4px 0;color:#fbbf24;font-size:12px;">
            ⚠ {report['memory']['warning']}
        </div>"""

    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="margin:0;padding:0;background:#08080f;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
        <div style="max-width:640px;margin:0 auto;background:#12121f;border:1px solid #1e1e2e;">

            <!-- Header -->
            <div style="background:linear-gradient(135deg,#1a0a1a,#0a0a1a);padding:24px;border-bottom:2px solid #ff6a0055;text-align:center;">
                <div style="font-family:'Courier New',monospace;font-size:20px;font-weight:700;color:#fff;letter-spacing:3px;">UNICORN HUNT</div>
                <div style="font-family:'Courier New',monospace;font-size:11px;color:#ff6a0099;letter-spacing:2px;margin-top:4px;">DAILY REFRESH REPORT</div>
            </div>

            <div style="padding:24px;">

                <!-- Status + Runtime -->
                <div style="display:flex;gap:16px;flex-wrap:wrap;">
                    <div style="flex:1;min-width:200px;background:#1a1a2e;border-radius:6px;padding:16px;">
                        <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Status</div>
                        <div style="font-size:24px;font-weight:700;color:{status_color};">{status_emoji} {status_text}</div>
                        <div style="color:#64748b;font-size:12px;margin-top:4px;">{report['start_time'].strftime('%Y-%m-%d %H:%M UTC')}</div>
                    </div>
                    <div style="flex:1;min-width:200px;background:#1a1a2e;border-radius:6px;padding:16px;">
                        <div style="color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">Run Time</div>
                        <div style="font-size:24px;font-weight:700;color:#e2e8f0;">{report['elapsed_human']}</div>
                        <div style="color:#64748b;font-size:12px;margin-top:4px;">{report['hostname']}</div>
                    </div>
                </div>

                <!-- Memory (peak during run, sampled every 2s) -->
                <div style="margin-top:24px;">
                    <h2 style="color:#ff6a00;font-size:14px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Peak Memory Usage</h2>
                    <div style="background:#1a1a2e;border-radius:6px;padding:16px;">
                        <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                            <span style="color:#94a3b8;font-size:12px;">Peak RAM: {fmt_bytes(report['memory']['peak_used'])} / {fmt_bytes(report['memory']['total_ram'])}</span>
                            <span style="color:{mem_color};font-size:12px;font-weight:600;">{ram_pct:.0f}%</span>
                        </div>
                        <div style="background:#0f0f1a;height:12px;border-radius:6px;overflow:hidden;">
                            <div style="background:{mem_color};height:100%;width:{min(ram_pct, 100):.0f}%;border-radius:6px;"></div>
                        </div>
                        <div style="color:#64748b;font-size:11px;margin-top:8px;">
                            Headroom at peak: {fmt_bytes(report['memory']['peak_available'])} &nbsp;|&nbsp;
                            Peak swap: {fmt_bytes(report['memory']['swap_peak_used'])} / {fmt_bytes(report['memory']['swap_total'])} &nbsp;|&nbsp;
                            {report['memory']['sample_count']} samples
                        </div>
                        {mem_warning_html}
                    </div>
                </div>

                <!-- Data Files -->
                <div style="margin-top:24px;">
                    <h2 style="color:#ff6a00;font-size:14px;text-transform:uppercase;letter-spacing:1px;margin-bottom:12px;">Data Files</h2>
                    <div style="overflow-x:auto;">
                        <table style="width:100%;border-collapse:collapse;background:#1a1a2e;border-radius:6px;font-size:13px;">
                            <thead>
                                <tr style="border-bottom:2px solid #2e2e4e;">
                                    <th style="padding:10px 12px;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;">Source</th>
                                    <th style="padding:10px 12px;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;">Status</th>
                                    <th style="padding:10px 12px;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;">Cadence</th>
                                    <th style="padding:10px 12px;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;">Size</th>
                                    <th style="padding:10px 12px;text-align:left;color:#94a3b8;font-size:11px;text-transform:uppercase;">Modified</th>
                                </tr>
                            </thead>
                            <tbody>
                                {file_rows}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Errors -->
                {error_section}

            </div>

            <!-- Footer -->
            <div style="padding:16px 24px;border-top:1px solid #1e1e2e;text-align:center;">
                <div style="color:#4a4a6a;font-size:11px;">Unicorn Hunt Monitor · {report['hostname']} · {report['end_time'].strftime('%Y-%m-%d %H:%M UTC')}</div>
            </div>

        </div>
    </body>
    </html>
    """
    return html


def send_email(report):
    """Send the report email via Resend API."""
    try:
        import resend
    except ImportError:
        print("[monitor] ERROR: 'resend' package not installed. Run: pip install resend")
        return False

    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        print("[monitor] ERROR: RESEND_API_KEY not set. Add it to .env or export it.")
        return False

    resend.api_key = api_key

    status_text = "✅ Success" if report["success"] else "❌ FAILED"
    error_count = len(report["errors"])
    subject = f"[Unicorn Hunt] {status_text} — {report['start_time'].strftime('%d %b %Y')}"
    if error_count > 0 and report["success"]:
        subject += f" ({error_count} warning{'s' if error_count != 1 else ''})"

    try:
        result = resend.Emails.send({
            "from": SENDER,
            "to": [RECIPIENT],
            "subject": subject,
            "html": build_email_html(report),
        })
        print(f"[monitor] Email sent. ID: {result.get('id', 'unknown')}")
        return True
    except Exception as e:
        print(f"[monitor] Failed to send email: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Run refresh.py with monitoring and email report")
    parser.add_argument("--yes", action="store_true", help="Pass --yes to refresh.py (skip prompts)")
    parser.add_argument("--force", action="store_true", help="Pass --force to refresh.py")
    parser.add_argument("--dry-run", action="store_true", help="Build email HTML and print, don't send")
    args = parser.parse_args()

    # Build refresh.py args
    refresh_args = []
    if args.yes:
        refresh_args.append("--yes")
    if args.force:
        refresh_args.append("--force")

    print(f"[monitor] Starting refresh at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    report = run_refresh(refresh_args)
    print(f"[monitor] Refresh finished in {report['elapsed_human']} (exit code {report['return_code']})")

    if args.dry_run:
        html = build_email_html(report)
        out_path = PROJECT_DIR / "monitor_preview.html"
        out_path.write_text(html)
        print(f"[monitor] Preview written to {out_path}")
    else:
        send_email(report)


if __name__ == "__main__":
    main()
