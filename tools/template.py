#!/usr/bin/env python3
"""Fill the @M4_*@ placeholders of a file tree from local.conf (this machine's values; never committed).

  template.py --conf local.conf --in-place DIR      replace inside DIR (package build)
  template.py --conf local.conf --copy SRC DST      copy SRC to DST, then replace (tests)

local.conf is KEY=value lines (see local.conf.example). The Bluetooth keys are optional: left out (or --conf /dev/null), they fill
in empty, which turns the macOS-shared headset key and the headset connect off (a machine-neutral build). Derived value:
M4_BT_HEADSET_ID = M4_BT_HEADSET without colons. Refuses a malformed address, or when any @M4_...@ placeholder is left."""
import argparse, os, re, shutil, sys
from pathlib import Path
KEYS = ('M4_BT_CONTROLLER', 'M4_BT_HEADSET')
TOKEN = re.compile(rb'@(M4_[A-Z0-9_]+)@')

def load(conf):
    values = {}
    for line in Path(conf).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition('=')
        values[key.strip()] = value.strip().strip('"').strip("'")
    for k in KEYS:
        values.setdefault(k, '')
        if values[k] and not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', values[k]):
            sys.exit('template: Bluetooth addresses must be uppercase XX:XX:XX:XX:XX:XX')
    values['M4_BT_HEADSET_ID'] = values['M4_BT_HEADSET'].replace(':', '')
    return {k.encode(): v.encode() for k, v in values.items()}

def fill(root, values):
    changed = 0
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = Path(dp, f)
            if p.is_symlink() or not p.is_file():
                continue
            data = p.read_bytes()
            if b'@M4_' not in data:
                continue
            def sub(m):
                if m.group(1) not in values:
                    sys.exit('template: unknown placeholder @%s@ in %s' % (m.group(1).decode(), p))
                return values[m.group(1)]
            new = TOKEN.sub(sub, data)
            if new != data:
                mode = p.stat().st_mode
                p.write_bytes(new); os.chmod(p, mode); changed += 1
    return changed

ap = argparse.ArgumentParser()
ap.add_argument('--conf', required=True)
g = ap.add_mutually_exclusive_group(required=True)
g.add_argument('--in-place', metavar='DIR')
g.add_argument('--copy', nargs=2, metavar=('SRC', 'DST'))
a = ap.parse_args()
values = load(a.conf)
root = a.in_place
if a.copy:
    shutil.copytree(a.copy[0], a.copy[1], symlinks=True)
    root = a.copy[1]
n = fill(root, values)
print('template: %d file(s) filled in %s' % (n, root))
