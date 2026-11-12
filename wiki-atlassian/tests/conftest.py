"""Use the same explicit code roots as the isolated installed launcher."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT), str(ROOT.parent / 'plugin/scripts')]
