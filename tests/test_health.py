import json
import stat
from pathlib import Path
from types import SimpleNamespace

from claude_companion import health


def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _cpu(total_busy: int, idle: int) -> str:
    return f"cpu {total_busy} 0 0 {idle} 0 0 0 0 0 0\n"


def _process(pid: int, command: str, ticks: int, started: int) -> str:
    fields = ["0"] * 21
    fields[0] = "S"
    fields[11] = str(ticks)
    fields[12] = "0"
    fields[19] = str(started)
    return f"{pid} ({command}) " + " ".join(fields) + "\n"


def _proc_tree(tmp_path: Path, *, wsl=False) -> Path:
    proc = tmp_path / "proc"
    _write(proc / "stat", _cpu(150, 800))
    _write(proc / "loadavg", "0.25 0.50 0.75 1/2 3\n")
    _write(proc / "meminfo", "MemTotal: 1000 kB\nMemAvailable: 300 kB\nSwapTotal: 500 kB\nSwapFree: 250 kB\n")
    _write(proc / "version", "Linux microsoft" if wsl else "Linux fixture")
    _write(proc / "42" / "stat", _process(42, "safe-worker", 10, 9))
    _write(proc / "42" / "comm", "safe-worker\n")
    return proc


def _statvfs(path):
    return SimpleNamespace(f_frsize=1, f_blocks=100, f_bavail=28)


def test_snapshot_uses_retained_samples_and_never_sleeps(tmp_path):
    proc = _proc_tree(tmp_path, wsl=True)
    reader = health.HealthReader(proc, tmp_path / "sys", statvfs=_statvfs, which=lambda name: None, clock=lambda: 0,
                                 cpu_count=1)

    first = reader.snapshot(configured_disks=(Path("/extra"),))
    assert first["cpu"]["reason"] == "Waiting for the next CPU sample."
    assert first["processes"]["reason"] == "Waiting for the next CPU sample."
    assert first["temperature"] == {"reason": "not visible inside WSL"}
    assert first["memory"]["percent"] == 70.0 and first["swap"]["percent"] == 50.0
    assert first["load"] == {"one": 0.25, "five": 0.5, "fifteen": 0.75}
    assert [row["path"] for row in first["disks"]] == ["/", "/extra"]
    assert all(row["percent"] == 72.0 for row in first["disks"])

    _write(proc / "stat", _cpu(170, 830))
    _write(proc / "42" / "stat", _process(42, "safe-worker", 20, 9))
    second = reader.snapshot(configured_disks=(Path("/extra"),))
    assert second["cpu"]["percent"] == 40.0
    assert second["processes"] == [{"command": "safe-worker", "percent": 20.0}]


def test_temperature_uses_sys_sensors_and_marks_the_configured_warning(tmp_path):
    proc = _proc_tree(tmp_path)
    sys_root = tmp_path / "sys"
    _write(sys_root / "class" / "thermal" / "thermal_zone0" / "temp", "87000\n")
    _write(sys_root / "class" / "hwmon" / "hwmon0" / "temp1_input", "51000\n")
    reader = health.HealthReader(proc, sys_root, statvfs=_statvfs, which=lambda name: None)

    got = reader.snapshot(warning_lines={"hot_c": 85.0, "disk_full_pct": 90.0, "mem_full_pct": 92.0})
    assert got["temperature"] == {"c": 87.0, "warning": True}
    assert not got["memory"].get("warning") and not got["disks"][0].get("warning")


def _program(path: Path, body: str):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_nvidia_reading_is_cached_for_fifteen_seconds(tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command = bin_dir / "nvidia-smi"
    _program(command, "printf '34, 61, 1024, 8192\\n'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    clock = [0.0]
    reader = health.HealthReader(proc, tmp_path / "sys", statvfs=_statvfs, clock=lambda: clock[0])

    assert reader.snapshot()["gpu"] == {"vendor": "NVIDIA", "gpus": [{"utilization_pct": 34.0, "temperature_c": 61.0, "memory_used_mib": 1024.0, "memory_total_mib": 8192.0}]}
    _program(command, "printf '90, 91, 2048, 8192\\n'\n")
    clock[0] = 14.0
    assert reader.snapshot()["gpu"]["gpus"][0]["utilization_pct"] == 34.0
    clock[0] = 15.0
    assert reader.snapshot()["gpu"]["gpus"][0]["utilization_pct"] == 90.0


def test_rocm_reading_is_used_when_nvidia_is_not_on_path(tmp_path, monkeypatch):
    proc = _proc_tree(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _program(bin_dir / "rocm-smi", "printf '%s\\n' '{\"card0\": {\"GPU use (%)\": \"22\", \"Temperature (Sensor edge) (C)\": \"57\", \"VRAM Total Used Memory (B)\": \"104857600\", \"VRAM Total Memory (B)\": \"1073741824\"}}'\n")
    monkeypatch.setenv("PATH", str(bin_dir))
    reader = health.HealthReader(proc, tmp_path / "sys", statvfs=_statvfs)

    assert reader.snapshot()["gpu"] == {"vendor": "AMD", "gpus": [{"utilization_pct": 22.0, "temperature_c": 57.0, "memory_used_mib": 100.0, "memory_total_mib": 1024.0}]}


def _hot_snapshot():
    return {"temperature": {"c": 90.0}, "memory": {"percent": 95.0},
            "disks": [{"path": "/", "percent": 91.0}], "gpu": {"gpus": []}}


def test_push_watcher_sends_on_crossings_and_rate_limits_each_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_HEALTH_PUSH", "true")
    sent = []
    reader = type("Reader", (), {"snapshot": lambda self: _hot_snapshot()})()
    watcher = health.Watcher(reader, lambda *args: sent.append(args) or True, tmp_path / "health-alerts.json")

    first = watcher.tick(now=1_000_000)
    assert first == ["cpu-temperature", "memory", "disk:/"]
    assert len(sent) == 3 and watcher.tick(now=1_000_001) == []
    reader.snapshot = lambda: {"temperature": {}, "memory": {}, "disks": [], "gpu": {"gpus": []}}
    assert watcher.tick(now=1_000_002) == []
    reader.snapshot = lambda: _hot_snapshot()
    assert watcher.tick(now=1_000_003) == []
    reader.snapshot = lambda: {"temperature": {}, "memory": {}, "disks": [], "gpu": {"gpus": []}}
    watcher.tick(now=1_000_004)
    reader.snapshot = lambda: _hot_snapshot()
    assert watcher.tick(now=1_000_000 + health.ALERT_GAP_S) == ["cpu-temperature", "memory", "disk:/"]
    assert json.loads((tmp_path / "health-alerts.json").read_text())["readings"]["memory"]["above"] is True


def test_watcher_never_starts_during_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_COMPANION_HEALTH_PUSH", "true")
    watcher = health.Watcher(type("Reader", (), {"snapshot": lambda self: {}})(), lambda *args: True, tmp_path / "state")
    watcher.start()
    assert watcher.thread is None


def test_health_route_declares_the_owner_read_guard():
    import pytest

    pytest.importorskip("fastapi")
    from claude_companion.server import auth
    from claude_companion.server import health as health_route

    route = next(route for route in health_route.router.routes if route.path == "/api/health")
    assert auth.require_token in {dependency.call for dependency in route.dependant.dependencies}
