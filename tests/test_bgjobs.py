import shlex

from claude_companion import bgjobs


def _proc(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("btime 1000\n")

    def add(pid, ppid, started, command, comm="process"):
        folder = proc / str(pid)
        folder.mkdir()
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(started)]
        (folder / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (folder / "cmdline").write_bytes(b"\0".join(part.encode() for part in command) + b"\0")

    return proc, add


def _shell(command):
    script = ("source /tmp/.claude/shell-snapshots/snapshot-bash-7.sh && eval "
              + shlex.quote(command))
    return ["/bin/bash", "-c", script]


def test_scan_lists_direct_snapshot_shells_with_command_and_age(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(201, 101, 200, _shell("pytest -q tests"), "bash worker")
    add(202, 101, 300, ["/bin/bash", "-c", "echo foreground"], "bash")
    add(203, 999, 100, _shell("sleep 60"), "bash")
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)

    assert bgjobs.scan({101}, str(proc), now=1105) == {
        101: [{"cmd": "pytest -q tests", "age_s": 103}],
    }


def test_command_labels_redact_and_bound_sensitive_commands():
    command = _shell("curl --header=Authorization: Bearer secret-value")

    label = bgjobs.command_of(command, limit=30)

    assert "secret-value" not in label
    assert "[redacted]" in label
    assert len(label) <= 30


def test_annotate_scans_all_public_rows_once(tmp_path, monkeypatch):
    proc, add = _proc(tmp_path)
    add(201, 101, 200, _shell("pytest -q"), "bash")
    monkeypatch.setattr(bgjobs, "_clock_ticks", lambda: 100)
    rows = [{"pid": 101}, {"pid": 102}, {"pid": 101, "private": True}]

    bgjobs.annotate(rows, str(proc), now=1105)

    assert rows[0]["bg_jobs"] == 1
    assert rows[0]["bg_job_list"][0]["cmd"] == "pytest -q"
    assert rows[1] == {"pid": 102, "bg_jobs": 0, "bg_job_list": []}
    assert rows[2]["bg_jobs"] == 0 and rows[2]["bg_job_list"] == []


def test_redact_hides_underscored_names_flags_bearer_and_url_userinfo():
    cases = {
        "GH_TOKEN=abc123 gh pr list": "abc123",
        "DB_PASSWORD=hunter2 ./run": "hunter2",
        "mytool --api-token s3cret": "s3cret",
        "mysql --password=pw1 db": "pw1",
        "curl -u admin:pw2 https://x.test": "pw2",
        "curl -H 'Authorization: Bearer zzz' https://x.test": "zzz",
        "git clone https://user:pw3@example.com/r.git": "pw3",
        "git clone https://u:a@b@example.com/r.git": "a@b",
        "curl -uadmin:pw4 https://x.test": "pw4",
        "mysql -ppw5 db": "pw5",
        "tool --pass pw6": "pw6",
        "curl --proxy-user u:pw7 https://x.test": "pw7",
        "curl -su u:pw8 https://x.test": "pw8",
        "curl https://x.test/?a=1&access_token=pw9": "pw9",
        "curl https://x.test/?x=1&password=pw10": "pw10",
        "run a=1,password=pw11": "pw11",
        "curl -U a:pw12 https://x.test": "pw12",
        "curl --header 'X: pw13' https://x.test": "pw13",
        "smbclient -U user%pw14 //h/s": "pw14",
    }
    for command, secret in cases.items():
        assert secret not in bgjobs.redact(command), command
    assert bgjobs.redact("pytest -q tests") == "pytest -q tests"


def test_redact_is_bounded_on_a_huge_command():
    assert len(bgjobs.redact("a" * 100000)) <= bgjobs.SCAN_LIMIT
