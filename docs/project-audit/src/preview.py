import sys, subprocess, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import importlib
mod, fn = sys.argv[1], sys.argv[2]
m = importlib.import_module(mod)
svg = getattr(m, fn)()
out = pathlib.Path(__file__).parent.parent / "diagrams"
out.mkdir(exist_ok=True)
h = out / f"{fn}.html"
h.write_text(f'<!doctype html><meta charset="utf-8"><body style="margin:0;background:#fff">{svg}</body>')
import re
vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
w, hh = int(vb.group(1)), int(vb.group(2))
png = out / f"{fn}.png"
subprocess.run(["chromium","--headless","--no-sandbox","--disable-gpu",f"--window-size={w},{hh}",f"--screenshot={png}",f"file://{h}"],capture_output=True,timeout=60)
print(png)
