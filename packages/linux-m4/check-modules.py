#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Static module checks for linux-m4 and the module list for the VM load test.

usage: check-modules.py <dir with .ko files (searched recursively)> [--release R] [--json out.json]
                        [--loadtest out.json] [--expect-file list.txt]

Per module (everything the kernel's module loader validates before init runs):
  - exactly one .modinfo section, flags exactly "A" (a second one appears when a Rust #[used] static gets
    SHF_GNU_RETAIN and `ld -r` keeps both; the kernel refuses such a module with "Invalid ELF");
  - exactly one .gnu.linkonce.this_module, no SHF_GNU_RETAIN ("R") section flag anywhere, at most one __versions;
  - no .stack_sizes / .comment-only oddities that a stripped kernel build does not produce;
  - modinfo vermagic starts with the release; a license is present.
The load-test list gives, per module: its dependencies (modinfo "depends"), one parameter with a type-invalid value,
(finit_module() must fail with -EINVAL after symbol resolution and before init). Modules without an int/bool-like
parameter are listed with invalid_parameter=null: either a pure library (load for real in the VM) or one of ours
that needs a parameter (see the 'no_parameter' list).
"""
import argparse, json, os, re, subprocess, sys, tempfile

INVALID = {  # parameter type -> a value the parameter parser must refuse
    'bool': 'maybe', 'int': 'x', 'uint': 'x', 'long': 'x', 'ulong': 'x', 'ullong': 'x', 'short': 'x',
    'ushort': 'x', 'byte': 'x', 'hexint': 'x',
    # Rust module parameters carry the Rust type name
    'u8': 'x', 'u16': 'x', 'u32': 'x', 'u64': 'x', 'usize': 'x',
    'i8': 'x', 'i16': 'x', 'i32': 'x', 'i64': 'x', 'isize': 'x',
}
# NB: an *unknown* parameter name is only warned about and ignored by the module loader, so it is no test:
# the module would load and run its init. Only a type-invalid value of a declared parameter stops the load
# (-EINVAL from parse_args) after the ELF, vermagic and symbol checks and before init.


def sections(ko):
    out = subprocess.run(['readelf', '-S', '-W', ko], capture_output=True, text=True, check=True).stdout
    rows = []
    for line in out.splitlines():
        m = re.match(r'\s*\[\s*\d+\]\s+(\S+)\s+\S+\s+[0-9a-f]+\s+[0-9a-f]+\s+[0-9a-f]+\s+\S+\s+([A-Z]*)\s+\d+\s+\d+\s+\d+', line)
        if m:
            rows.append((m.group(1), m.group(2)))
    return rows


def modinfo(ko, field):
    r = subprocess.run(['modinfo', '-F', field, ko], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l]


def check(ko, release):
    secs = sections(ko)
    problems = []
    mi = [f for n, f in secs if n == '.modinfo']
    if len(mi) != 1:
        problems.append(f'{len(mi)} .modinfo sections (need exactly 1)')
    elif mi[0] != 'A':
        problems.append(f'.modinfo flags {mi[0]!r} (need "A")')
    tm = [n for n, f in secs if n == '.gnu.linkonce.this_module']
    if len(tm) != 1:
        problems.append(f'{len(tm)} .gnu.linkonce.this_module sections (need exactly 1)')
    if sum(1 for n, f in secs if n == '__versions') > 1:
        problems.append('more than one __versions section')
    retain = [n for n, f in secs if 'R' in f]
    if retain:
        problems.append('SHF_GNU_RETAIN sections: ' + ','.join(sorted(set(retain))))
    if any(n == '.stack_sizes' for n, f in secs):
        problems.append('.stack_sizes section present')
    vm = modinfo(ko, 'vermagic')
    if not vm or not vm[0].startswith(release + ' '):
        problems.append(f'vermagic {vm[:1]} does not start with {release!r}')
    if not modinfo(ko, 'license'):
        problems.append('no license')
    return problems


def changed_dirs(root, base):
    if not base:
        return None
    r = subprocess.run(['git', 'diff', '--name-only', base, 'HEAD'], cwd=root, capture_output=True, text=True)
    if r.returncode:
        return None
    return {os.path.dirname(l) for l in r.stdout.splitlines() if l}


def loadtest_entry(ko, name, ours=None):
    parms = modinfo(ko, 'parm')
    parmtype = {}
    for l in modinfo(ko, 'parmtype'):
        n, _, t = l.partition(':')
        parmtype[n] = t
    pick = None
    for n, t in parmtype.items():
        if t in INVALID:
            pick = (n, t, INVALID[t])
            break
        if t.startswith('array of ') and t[9:] in INVALID:
            pick = (n, t, 'x')
            break
    is_ours = None
    if ours is not None:
        d = os.path.dirname(os.path.relpath(ko))
        is_ours = any(c == d or c.startswith(d + '/') for c in ours)
    mode = 'invalid_parameter' if pick else 'real_load'
    return {
        'module': name,
        'path': os.path.relpath(ko),
        'changed_by_us': is_ours,
        'load_mode': mode,
        'depends': [d for d in (modinfo(ko, 'depends') or [''])[0].split(',') if d],
        'softdep': modinfo(ko, 'softdep'),
        'alias_count': len(modinfo(ko, 'alias')),
        'parameters': sorted(parmtype),
        'invalid_parameter': ({'name': pick[0], 'type': pick[1], 'value': pick[2],
                               'expect': 'finit_module fails -EINVAL before init'} if pick else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--release', required=True)
    ap.add_argument('--json')
    ap.add_argument('--loadtest')
    ap.add_argument('--expect-file', help='file with module names that must be present, one per line')
    ap.add_argument('--base', help='git base commit: mark modules whose directory we changed')
    a = ap.parse_args()
    kos = {}
    tmp = tempfile.mkdtemp(prefix='m4-ko-')   # .ko.zst (installed modules) are decompressed here
    for d, _, fs in os.walk(a.root):
        for f in fs:
            if f.endswith('.ko'):
                kos[f[:-3].replace('-', '_')] = os.path.join(d, f)
            elif f.endswith('.ko.zst'):
                out = os.path.join(tmp, os.path.relpath(d, a.root).replace(os.sep, '_'), f[:-4])
                os.makedirs(os.path.dirname(out), exist_ok=True)
                subprocess.run(['zstd', '-dqf', os.path.join(d, f), '-o', out], check=True)
                kos[f[:-7].replace('-', '_')] = out
    bad = {}
    entries = []
    ours = changed_dirs(a.root, a.base)
    for name in sorted(kos):
        p = check(kos[name], a.release)
        if p:
            bad[name] = p
        entries.append(loadtest_entry(kos[name], name, ours))
    missing = []
    if a.expect_file:
        want = [l.strip().replace('-', '_') for l in open(a.expect_file) if l.strip() and not l.startswith('#')]
        missing = [w for w in want if w not in kos]
    res = {'release': a.release, 'modules': len(kos), 'failed': bad, 'missing': missing,
           'status': 'PASS' if not bad and not missing else 'FAIL'}
    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1)
    if a.loadtest:
        json.dump({'release': a.release, 'method': 'finit_module() of each .ko; see the entry fields',
                   'modules': entries}, open(a.loadtest, 'w'), indent=1)
    print(json.dumps({k: v for k, v in res.items() if k != 'failed'} | {'failed': len(bad)}))
    for n, p in bad.items():
        print('FAIL', n, p)
    return 0 if res['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
