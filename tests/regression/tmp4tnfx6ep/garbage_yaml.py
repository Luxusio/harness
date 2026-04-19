import subprocess

def test_garbage_yaml():
    r = subprocess.run(
        "[not: valid",
        shell=True, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f'exit {r.returncode}, want 0: {r.stderr}'
