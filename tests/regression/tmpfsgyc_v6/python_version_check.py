import subprocess

def test_python_version_check():
    r = subprocess.run(
        "python3 --version",
        shell=True, capture_output=True, text=True, timeout=30
    )
    assert r.returncode == 0, f'exit {r.returncode}, want 0: {r.stderr}'
