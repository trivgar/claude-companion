"""Local machine health readings for the dashboard.

The collector reads this machine only.  Counter-based readings keep their last
sample in memory, so asking for health never waits for a second sample.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from hashlib import sha256
from pathlib import Path

from . import config

GPU_CACHE_S = 15.0
GPU_TIMEOUT_S = 3.0
ALERT_GAP_S = 6 * 60 * 60


def _number(value):
    """A finite float, or None.  Hardware commands use several spellings of N/A."""
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _percent(used, total):
    if total is None or total <= 0 or used is None:
        return None
    return round(max(0.0, min(100.0, 100.0 * used / total)), 1)


def _setting_number(key: str, default: float, low: float, high: float) -> float:
    value = _number(config.get(key, default))
    return value if value is not None and low <= value <= high else default


def thresholds() -> dict[str, float]:
    """Configured warning lines, with deliberately conservative fallbacks."""
    return {
        "hot_c": _setting_number("health_hot_c", 85.0, 30.0, 130.0),
        "disk_full_pct": _setting_number("health_disk_full_pct", 90.0, 1.0, 100.0),
        "mem_full_pct": _setting_number("health_mem_full_pct", 92.0, 1.0, 100.0),
    }


def push_enabled() -> bool:
    value = config.get("health_push", False)
    return value is True or (isinstance(value, str) and value.strip().lower() in ("1", "true", "yes", "on"))


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def _stat_fields(text: str) -> tuple[str, list[int]] | None:
    """Read /proc/<pid>/stat without letting spaces in comm shift its fields."""
    right = text.rfind(")")
    left = text.find("(")
    if left < 0 or right <= left:
        return None
    command = text[left + 1:right]
    fields = text[right + 2:].split()
    try:
        # After state: utime is index 11, stime index 12, starttime index 19.
        return command, [int(fields[index]) for index in (11, 12, 19)]
    except (IndexError, ValueError):
        return None


class HealthReader:
    """Read local, inexpensive health facts and retain counter samples."""

    def __init__(self, proc: Path | str = "/proc", sys_root: Path | str = "/sys", *,
                 statvfs=os.statvfs, which=shutil.which, run=subprocess.run, clock=time.monotonic,
                 cpu_count=None):
        self.proc = Path(proc)
        self.sys_root = Path(sys_root)
        self.statvfs = statvfs
        self.which = which
        self.run = run
        self.clock = clock
        self.cpu_count = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
        self._cpu_before: tuple[int, int] | None = None
        self._process_before: dict[tuple[int, int], int] = {}
        self._gpu_before = -GPU_CACHE_S
        self._gpu_cache: dict | None = None
        self._lock = threading.Lock()

    def _cpu_ticks(self) -> tuple[int, int] | None:
        text = _read(self.proc / "stat")
        if not text:
            return None
        line = next((line for line in text.splitlines() if line.startswith("cpu ")), "")
        try:
            ticks = [int(value) for value in line.split()[1:]]
        except ValueError:
            return None
        if len(ticks) < 4:
            return None
        total = sum(ticks)
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        return total, idle

    def _cpu(self) -> tuple[dict, int | None]:
        current = self._cpu_ticks()
        if current is None:
            return {"reason": "Could not read CPU counters from /proc/stat."}, None
        before, self._cpu_before = self._cpu_before, current
        if before is None:
            return {"reason": "Waiting for the next CPU sample."}, None
        total = current[0] - before[0]
        idle = current[1] - before[1]
        if total <= 0 or idle < 0:
            return {"reason": "CPU counters did not advance yet."}, None
        return {"percent": round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)}, total

    def _load(self) -> dict:
        text = _read(self.proc / "loadavg")
        if not text:
            return {"reason": "Could not read load averages from /proc/loadavg."}
        values = [_number(value) for value in text.split()[:3]]
        if len(values) != 3 or any(value is None for value in values):
            return {"reason": "Load averages in /proc/loadavg were not usable."}
        return {"one": values[0], "five": values[1], "fifteen": values[2]}

    def _memory(self) -> tuple[dict, dict]:
        text = _read(self.proc / "meminfo")
        if not text:
            missing = {"reason": "Could not read memory details from /proc/meminfo."}
            return missing, dict(missing)
        values = {}
        for line in text.splitlines():
            key, _, value = line.partition(":")
            number = _number(value.split()[0] if value.split() else None)
            if key and number is not None:
                values[key] = int(number) * 1024
        total, available = values.get("MemTotal"), values.get("MemAvailable")
        if total and available is None:
            available = sum(values.get(key, 0) for key in ("MemFree", "Buffers", "Cached", "SReclaimable"))
        memory = {"reason": "Memory availability was not reported."}
        if total and available is not None:
            used = max(0, total - available)
            memory = {"total_bytes": total, "used_bytes": used, "percent": _percent(used, total)}
        swap_total, swap_free = values.get("SwapTotal"), values.get("SwapFree")
        if swap_total is None or swap_free is None:
            swap = {"reason": "Swap details were not reported."}
        elif swap_total == 0:
            swap = {"reason": "No swap is configured."}
        else:
            used = max(0, swap_total - swap_free)
            swap = {"total_bytes": swap_total, "used_bytes": used, "percent": _percent(used, swap_total)}
        return memory, swap

    def _disk(self, path: Path) -> dict:
        try:
            stat = self.statvfs(path)
            total = stat.f_frsize * stat.f_blocks
            available = stat.f_frsize * stat.f_bavail
        except OSError:
            return {"path": str(path), "reason": "This path is not available to the dashboard."}
        if total <= 0:
            return {"path": str(path), "reason": "This filesystem did not report a usable size."}
        used = max(0, total - available)
        return {"path": str(path), "total_bytes": total, "used_bytes": used, "percent": _percent(used, total)}

    def _disks(self, configured: tuple[Path, ...] | None) -> list[dict]:
        paths = (Path("/"),) + (configured if configured is not None else config.paths("health_disks"))
        unique = []
        for path in paths:
            shown = str(path)
            if shown not in {str(item) for item in unique}:
                unique.append(path)
        return [self._disk(path) for path in unique]

    def _inside_wsl(self) -> bool:
        if (self.proc / "sys" / "fs" / "binfmt_misc" / "WSLInterop").exists():
            return True
        version = _read(self.proc / "version")
        return bool(version and "microsoft" in version.lower())

    def _temperature(self) -> dict:
        if self._inside_wsl():
            return {"reason": "not visible inside WSL"}
        readings = []
        thermal = self.sys_root / "class" / "thermal"
        hwmon = self.sys_root / "class" / "hwmon"
        for root, pattern in ((thermal, "thermal_zone*/temp"), (hwmon, "hwmon*/temp*_input")):
            try:
                paths = sorted(root.glob(pattern))
            except OSError:
                paths = []
            for path in paths:
                value = _number(_read(path))
                if value is None or value <= 0:
                    continue
                readings.append(value / 1000.0 if value > 1000 else value)
        if not readings:
            return {"reason": "CPU temperature sensors are not exposed by this system."}
        return {"c": round(max(readings), 1)}

    def _nvidia(self, executable: str) -> dict:
        try:
            result = self.run([executable, "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=GPU_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"reason": "NVIDIA GPU query timed out."}
        except OSError:
            return {"reason": "NVIDIA GPU query could not start."}
        if result.returncode != 0:
            return {"reason": "NVIDIA GPU query failed."}
        gpus = []
        for line in result.stdout.splitlines():
            values = [_number(value) for value in line.split(",")]
            if len(values) != 4 or any(value is None for value in values):
                continue
            gpus.append({"utilization_pct": values[0], "temperature_c": values[1],
                         "memory_used_mib": values[2], "memory_total_mib": values[3]})
        return {"vendor": "NVIDIA", "gpus": gpus} if gpus else {"reason": "NVIDIA did not report usable GPU readings."}

    @staticmethod
    def _rocm_value(row: dict, words: tuple[str, ...], without: tuple[str, ...] = ()):
        for key, value in row.items():
            key = str(key).lower()
            if all(word in key for word in words) and not any(word in key for word in without):
                found = _number(value)
                if found is not None:
                    return found, key
        return None, ""

    def _rocm(self, executable: str) -> dict:
        try:
            result = self.run([executable, "--showuse", "--showtemp", "--showmeminfo", "vram", "--json"],
                              capture_output=True, text=True, timeout=GPU_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"reason": "AMD GPU query timed out."}
        except OSError:
            return {"reason": "AMD GPU query could not start."}
        if result.returncode != 0:
            return {"reason": "AMD GPU query failed."}
        try:
            data = json.loads(result.stdout)
        except (TypeError, ValueError):
            return {"reason": "AMD GPU query returned invalid data."}
        rows = data.values() if isinstance(data, dict) else []
        gpus = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            utilization, _ = self._rocm_value(row, ("gpu", "use"))
            temperature, _ = self._rocm_value(row, ("temperature",))
            used, used_key = self._rocm_value(row, ("vram", "used"))
            total, total_key = self._rocm_value(row, ("vram", "total", "memory"), ("used",))
            if used is not None and "(b)" in used_key:
                used /= 1024 * 1024
            if total is not None and "(b)" in total_key:
                total /= 1024 * 1024
            item = {}
            if utilization is not None:
                item["utilization_pct"] = utilization
            if temperature is not None:
                item["temperature_c"] = temperature
            if used is not None:
                item["memory_used_mib"] = round(used, 1)
            if total is not None:
                item["memory_total_mib"] = round(total, 1)
            if item:
                gpus.append(item)
        return {"vendor": "AMD", "gpus": gpus} if gpus else {"reason": "AMD did not report usable GPU readings."}

    def _gpu_read(self) -> dict:
        nvidia = self.which("nvidia-smi")
        if nvidia:
            return self._nvidia(nvidia)
        rocm = self.which("rocm-smi")
        if rocm:
            return self._rocm(rocm)
        return {"reason": "No NVIDIA or AMD GPU command was found on PATH."}

    def _gpu(self) -> dict:
        now = self.clock()
        if self._gpu_cache is None or now - self._gpu_before >= GPU_CACHE_S:
            self._gpu_cache = self._gpu_read()
            self._gpu_before = now
        return dict(self._gpu_cache)

    def _processes(self, total_ticks: int | None) -> list[dict] | dict:
        try:
            entries = list(self.proc.iterdir())
        except OSError:
            return {"reason": "Could not read the process list from /proc."}
        current = {}
        rows = []
        for entry in entries:
            try:
                pid = int(entry.name)
            except ValueError:
                continue
            parsed = _stat_fields(_read(entry / "stat") or "")
            if not parsed:
                continue
            stat_command, (utime, stime, started) = parsed
            key, ticks = (pid, started), utime + stime
            current[key] = ticks
            before = self._process_before.get(key)
            if before is None or ticks < before or total_ticks is None:
                continue
            used = ticks - before
            if used <= 0:
                continue
            command = (_read(entry / "comm") or stat_command).splitlines()[0].strip()
            if not command:
                command = "unknown command"
            rows.append({"command": command[:120], "percent": round(100.0 * self.cpu_count * used / total_ticks, 1)})
        self._process_before = current
        if total_ticks is None:
            return {"reason": "Waiting for the next CPU sample."}
        return sorted(rows, key=lambda row: row["percent"], reverse=True)[:5]

    @staticmethod
    def _warnings(result: dict, lines: dict[str, float]) -> None:
        if result.get("temperature", {}).get("c") is not None:
            if result["temperature"]["c"] >= lines["hot_c"]:
                result["temperature"]["warning"] = True
        if result.get("memory", {}).get("percent") is not None:
            if result["memory"]["percent"] >= lines["mem_full_pct"]:
                result["memory"]["warning"] = True
        for disk in result.get("disks", []):
            if disk.get("percent") is not None:
                if disk["percent"] >= lines["disk_full_pct"]:
                    disk["warning"] = True
        for gpu in result.get("gpu", {}).get("gpus", []):
            if gpu.get("temperature_c") is not None:
                if gpu["temperature_c"] >= lines["hot_c"]:
                    gpu["warning"] = True

    def snapshot(self, configured_disks: tuple[Path, ...] | None = None,
                 warning_lines: dict[str, float] | None = None) -> dict:
        """Return a JSON-ready snapshot.  No call sleeps for a counter sample."""
        with self._lock:
            cpu, total_ticks = self._cpu()
            memory, swap = self._memory()
            result = {"cpu": cpu, "load": self._load(), "memory": memory, "swap": swap,
                      "disks": self._disks(configured_disks), "temperature": self._temperature(),
                      "gpu": self._gpu(), "processes": self._processes(total_ticks)}
            self._warnings(result, warning_lines or thresholds())
            return result


def alert_readings(snapshot: dict, lines: dict[str, float] | None = None) -> list[dict]:
    """Warning readings eligible for a machine-health push."""
    lines = lines or thresholds()
    out = []
    temperature = snapshot.get("temperature") or {}
    if temperature.get("c") is not None and temperature["c"] >= lines["hot_c"]:
        out.append({"id": "cpu-temperature", "title": "Machine CPU is hot",
                    "body": f"CPU temperature is {temperature['c']:.0f}C.", "tag": "health-cpu-temperature"})
    memory = snapshot.get("memory") or {}
    if memory.get("percent") is not None and memory["percent"] >= lines["mem_full_pct"]:
        out.append({"id": "memory", "title": "Machine memory is filling",
                    "body": f"Memory is {memory['percent']:.0f}% used.", "tag": "health-memory"})
    for disk in snapshot.get("disks") or []:
        if disk.get("percent") is not None and disk["percent"] >= lines["disk_full_pct"]:
            path = str(disk.get("path") or "disk")
            out.append({"id": f"disk:{path}", "title": "Machine disk is filling",
                        "body": f"{path} is {disk['percent']:.0f}% used.",
                        "tag": "health-disk-" + sha256(path.encode()).hexdigest()[:10]})
    for index, gpu in enumerate((snapshot.get("gpu") or {}).get("gpus") or [], start=1):
        if gpu.get("temperature_c") is not None and gpu["temperature_c"] >= lines["hot_c"]:
            out.append({"id": f"gpu-temperature:{index}", "title": "Machine GPU is hot",
                        "body": f"GPU {index} temperature is {gpu['temperature_c']:.0f}C.", "tag": f"health-gpu-{index}"})
    return out


class Watcher:
    """Poll local health and send one optional push for each warning crossing."""

    def __init__(self, reader: HealthReader, send, path: Path, every_s: float = 60.0):
        self.reader, self.send, self.path, self.every_s = reader, send, Path(path), every_s
        self._stop = threading.Event()
        self.thread = None

    def _state(self) -> dict:
        try:
            value = json.loads(self.path.read_text())
            return value if isinstance(value, dict) else {"readings": {}}
        except (OSError, ValueError):
            return {"readings": {}}

    def _save(self, state: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(state))
            os.replace(tmp, self.path)
        except OSError:
            pass

    def tick(self, now: float | None = None) -> list[str]:
        if not push_enabled():
            return []
        now = time.time() if now is None else now
        try:
            due = alert_readings(self.reader.snapshot())
        except Exception:
            return []
        state = self._state()
        previous = state.get("readings") if isinstance(state.get("readings"), dict) else {}
        active = {reading["id"]: reading for reading in due}
        saved, sent = {}, []
        for id_, reading in active.items():
            before = previous.get(id_) if isinstance(previous.get(id_), dict) else {}
            crossing = not bool(before.get("above"))
            last = _number(before.get("pushed_at")) or 0.0
            pushed = False
            if crossing and now - last >= ALERT_GAP_S:
                try:
                    pushed = bool(self.send(reading["title"], reading["body"], "/?view=health", reading["tag"]))
                except Exception:
                    pushed = False
                if pushed:
                    sent.append(id_)
            saved[id_] = {"above": True, "pushed_at": now if pushed else last}
        for id_, before in previous.items():
            if id_ not in saved and isinstance(before, dict):
                saved[id_] = {"above": False, "pushed_at": _number(before.get("pushed_at")) or 0.0}
        self._save({"readings": saved})
        return sent

    def _loop(self) -> None:
        while not self._stop.wait(self.every_s):
            self.tick()

    def start(self) -> None:
        if "pytest" in sys.modules or not push_enabled():
            return
        self.thread = threading.Thread(target=self._loop, name="machine-health", daemon=True)
        self.thread.start()
