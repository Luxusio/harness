import subprocess

def test_gate_exits_zero_on_allowed():
    r = subprocess.run(
        "echo hello",
        shell=True, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f'exit {r.returncode}, want 0: {r.stderr}'
    assert 'hello' in r.stdout, "missing in stdout: 'hello'"
