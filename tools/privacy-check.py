#!/usr/bin/env python3
"""Refuse private values in the repo (pre-commit hook and CI). This repo is meant to be publishable.

  privacy-check.py --staged        check the files staged for commit (the git hook)
  privacy-check.py PATH...         check files or directories

Fails on (1) any value from local.conf or from the optional, gitignored private-patterns.txt (one regex per line: SSIDs, hostnames, IPs, ...)
and (2) private-looking patterns: MAC addresses, UUIDs, /home/<user>, LAN IPv4, email addresses, key material,
unless the exact match is listed in tools/privacy-allow.txt (placeholders, examples, public upstream contacts)."""
import os, re, subprocess, sys
from pathlib import Path
TOP = Path(__file__).resolve().parents[1]
GENERIC = {
    'MAC address': r'\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b',
    'UUID': r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b',
    'home path': r'/home/(?!youruser\b|builder\b|user\b)[a-z][a-z0-9_-]*',
    'LAN IPv4': r'\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b',
    'email': r'[A-Za-z0-9._%+-]+@(?!localhost\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b',
    'key material': r'BEGIN (?:OPENSSH|RSA|EC|DSA) PRIVATE KEY|\bgh[opsu]_[A-Za-z0-9]{20,}',
}
def lines(path):
    try:
        return [l.strip() for l in Path(path).read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')]
    except OSError:
        return []
def private_values():
    vals = []
    for l in lines(TOP/'local.conf'):
        k, _, v = l.partition('=')
        v = v.split('#', 1)[0].strip().strip('"\'')
        if v and not re.fullmatch(r'[0:-]+|youruser', v):
            vals.append(re.escape(v)); vals.append(re.escape(v.replace(':', '')))
    return vals + lines(TOP/'private-patterns.txt')
def targets(args):
    if args == ['--staged']:
        out = subprocess.run(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z'], cwd=TOP, capture_output=True, check=True).stdout
        return [TOP/p for p in out.decode().split('\0') if p]
    found = []
    for a in args:
        a = Path(a)
        if a.is_dir():
            found += [Path(dp, f) for dp, dn, fn in os.walk(a) if '.git' not in dp.split(os.sep) for f in fn]
        else:
            found.append(a)
    return found
allow = set(lines(TOP/'tools/privacy-allow.txt'))
private = [re.compile(p, re.I) for p in private_values()]
generic = {k: re.compile(v) for k, v in GENERIC.items()}
bad = 0
for f in targets(sys.argv[1:] or ['.']):
    if f.is_symlink() or not f.is_file() or f.name in ('local.conf', 'private-patterns.txt') or f.stat().st_size > 8 * 1024 * 1024:
        continue
    data = f.read_bytes()
    if b'\0' in data[:8192]:
        if any(rx.search(data.decode('latin-1')) for rx in private):
            print('%s: binary file contains a private value' % f); bad += 1
        continue
    for n, line in enumerate(data.decode('utf-8', 'replace').splitlines(), 1):
        for rx in private:
            if rx.search(line):
                print('%s:%d: private value from local.conf/private-patterns.txt' % (f, n)); bad += 1
        for kind, rx in generic.items():
            for m in rx.finditer(line):
                if m.group(0) not in allow:
                    print('%s:%d: %s %r (add it to tools/privacy-allow.txt only if it is not private)' % (f, n, kind, m.group(0))); bad += 1
if bad:
    sys.exit('privacy-check: %d finding(s); nothing private may be committed' % bad)
print('privacy-check: clean')
