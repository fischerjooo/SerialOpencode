"""Build the portable, dependency-free HTML from maintained web sources."""
from pathlib import Path
import argparse
ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--check', action='store_true')
args = parser.parse_args()
html = (ROOT / 'web/index.template.html').read_text()
for marker, name in [('/* APP_STYLES */', 'styles.css'), ('/* APP_SCRIPT */', 'app.js')]:
    html = html.replace(marker, (ROOT / 'web' / name).read_text().rstrip())
output = ROOT / 'index.html'
if args.check:
    if output.read_text() != html:
        raise SystemExit('index.html is stale. Run python scripts/build_web.py')
else:
    output.write_text(html)
