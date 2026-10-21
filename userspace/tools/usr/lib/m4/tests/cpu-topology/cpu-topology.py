#!/usr/bin/env python3
"""Read-only scheduler-topology evidence for the admitted CPUs.

Records, per CPU: sysfs topology (package/cluster/core ids and sibling lists), cpu_capacity,
cache sharing, /proc/cpuinfo identity; and, where the kernel exposes them without any mount or
write: scheduler domains from debugfs (if already mounted) and the boot-time "attaching
sched-domain(s)" lines from /dev/kmsg (present when booted with sched_verbose). Nothing is
written to the system, no CPU is steered, no core is released. Every section is independent and
bounded, so a missing source is recorded and the run still completes.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import m4host

OUT = Path('/run/m4/state/cpu-topology.json')
CPU_SYSFS = Path('/sys/devices/system/cpu')
PROC = Path('/proc')
DEBUGFS = Path('/sys/kernel/debug')
KMSG = Path('/dev/kmsg')

FILE_LIMIT = 4096
CPUINFO_LIMIT = 262144
KMSG_RECORD_LIMIT = 40000
KMSG_KEEP = 160
KMSG_KEEP_BYTES = 24000
MAX_CACHE_INDEX = 8
MAX_DOMAINS = 8
TIME_BUDGET_NS = 20 * 10**9
SD_ASYM = 'SD_ASYM_CPUCAPACITY'
TOPOLOGY_INTS = ('physical_package_id', 'cluster_id', 'core_id', 'die_id')
TOPOLOGY_LISTS = ('package_cpus_list', 'cluster_cpus_list', 'core_cpus_list', 'die_cpus_list',
                  'core_siblings_list', 'thread_siblings_list')
CPUINFO_KEYS = ('processor', 'BogoMIPS', 'CPU implementer', 'CPU architecture', 'CPU variant',
                'CPU part', 'CPU revision')
KMSG_PATTERN = re.compile(r'attaching (NULL )?sched-domain|^\s*domain-\d+:|^\s*groups:|'
                          r'Checking EAS|cpu_capacity|CPU capacity|topology|ERROR: ', re.I)


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def save(result):
    fd, temporary = tempfile.mkstemp(prefix='.cpu-topology-', dir=OUT.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write((json.dumps(result, indent=2) + '\n').encode())
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, OUT)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_text(path, limit=FILE_LIMIT):
    """Bounded read of one regular file; None if it is absent or unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = b''
        while len(raw) <= limit:        # seq_file files (/proc/cpuinfo) return one chunk per read
            chunk = os.read(fd, limit + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    return raw[:limit].decode('utf-8', errors='replace').strip()


def parse_cpu_list(text):
    cpus = set()
    for part in text.strip().split(','):
        low, _, high = part.partition('-')
        need(low.isdecimal() and (not high or high.isdecimal()), 'malformed CPU list')
        cpus.update(range(int(low), int(high or low) + 1))
    return sorted(cpus)


def parse_mask_list(text):
    return parse_cpu_list(text) if text else None


def read_int(path):
    value = read_text(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def cpu_sysfs(root, count):
    """Per-CPU topology, capacity and cache sharing from /sys/devices/system/cpu."""
    cpus, missing = [], []
    for cpu in range(count):
        base = root / f'cpu{cpu}'
        row = dict(cpu=cpu, online=read_text(base / 'online'),
                   cpu_capacity=read_int(base / 'cpu_capacity'), topology={}, cache=[])
        for name in TOPOLOGY_INTS:
            value = read_int(base / 'topology' / name)
            if value is not None:
                row['topology'][name] = value
        for name in TOPOLOGY_LISTS:
            value = read_text(base / 'topology' / name)
            if value is not None:
                row['topology'][name] = value
        for index in range(MAX_CACHE_INDEX):
            node = base / 'cache' / f'index{index}'
            if not node.is_dir():
                continue
            row['cache'].append(dict(index=index, level=read_int(node / 'level'),
                                     type=read_text(node / 'type'), size=read_text(node / 'size'),
                                     shared_cpu_list=read_text(node / 'shared_cpu_list')))
        for need_key in ('physical_package_id', 'cluster_id', 'core_id', 'cluster_cpus_list', 'core_cpus_list',
                         'package_cpus_list'):
            if need_key not in row['topology']:
                missing.append(f'cpu{cpu}.topology.{need_key}')
        if row['cpu_capacity'] is None:
            missing.append(f'cpu{cpu}.cpu_capacity')
        cpus.append(row)
    top = {name: read_text(root / name) for name in ('online', 'possible', 'present', 'offline', 'kernel_max')}
    return dict(cpus=cpus, system=top, missing=missing)


def cpuinfo(proc, count):
    raw = read_text(proc / 'cpuinfo', CPUINFO_LIMIT)
    if raw is None:
        return dict(available=False)
    blocks = [b for b in re.split(r'\n\s*\n', raw) if b.strip()]
    rows = []
    for block in blocks:
        fields = {}
        for line in block.splitlines():
            key, _, value = line.partition(':')
            fields.setdefault(key.strip(), value.strip())
        if 'processor' not in fields:
            continue
        row = {key: fields[key] for key in CPUINFO_KEYS if key in fields}
        features = fields.get('Features', '')
        row['feature_count'] = len(features.split())
        row['features_sha256'] = hashlib.sha256(features.encode()).hexdigest()
        rows.append(row)
    groups = {}
    for row in rows:
        ident = tuple(row.get(key) for key in ('CPU implementer', 'CPU architecture', 'CPU variant',
                                               'CPU part', 'CPU revision', 'features_sha256'))
        groups.setdefault(ident, []).append(int(row['processor']) if row['processor'].isdecimal() else None)
    classes = [dict(implementer=k[0], architecture=k[1], variant=k[2], part=k[3], revision=k[4],
                    features_sha256=k[5], cpus=sorted(c for c in v if c is not None))
               for k, v in sorted(groups.items(), key=lambda item: str(item[0]))]
    first = next((f for f in (re.search(r'^Features\s*:\s*(.*)$', b, re.M) for b in blocks) if f), None)
    first = first.group(1) if first else ''
    return dict(available=True, processors=len(rows), per_cpu=rows, classes=classes,
                first_cpu_features=first[:1200], all_features_identical=len({sha for sha in
                (r['features_sha256'] for r in rows)}) == 1,
                complete=sorted(int(r['processor']) for r in rows if r['processor'].isdecimal()) == list(range(count)))


def debugfs_domains(debugfs, count):
    root = debugfs / 'sched' / 'domains'
    if not root.is_dir():
        return dict(available=False, reason='debugfs sched/domains not present (debugfs unmounted, '
                                            'CONFIG_DEBUG_FS off, or sched_verbose not given)')
    per_cpu = {}
    for cpu in range(count):
        domains = []
        for level in range(MAX_DOMAINS):
            node = root / f'cpu{cpu}' / f'domain{level}'
            if not node.is_dir():
                break
            flags = read_text(node / 'flags')
            domains.append(dict(level=read_int(node / 'level'), name=read_text(node / 'name'),
                                flags=flags.split() if flags is not None else None,
                                groups_flags=(read_text(node / 'groups_flags') or '').split()))
        per_cpu[str(cpu)] = domains
    return dict(available=True, per_cpu=per_cpu)


def read_kmsg(kmsg):
    """Kernel ring-buffer messages, read only: one record per read(2) on /dev/kmsg."""
    try:
        fd = os.open(kmsg, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as error:
        return None, dict(available=False, reason=type(error).__name__)
    messages, records = [], 0
    try:
        while records < KMSG_RECORD_LIMIT:
            try:
                raw = os.read(fd, 8192)
            except BlockingIOError:
                break
            except OSError:      # EPIPE: the oldest record was overwritten; carry on with the next
                records += 1
                continue
            if not raw:
                break
            records += 1
            messages.append(raw)
    finally:
        os.close(fd)
    return messages, dict(available=True, records_scanned=records)


def summarize_kmsg(raw_records, info):
    """Boot-time sched_domain_debug lines (present when booted with sched_verbose)."""
    lines, kept, truncated = [], 0, False
    for raw in raw_records:
        header, _, body = raw.decode('utf-8', errors='replace').partition(';')
        message = body.split('\n', 1)[0]
        if KMSG_PATTERN.search(message):
            if len(lines) >= KMSG_KEEP or kept + len(message) > KMSG_KEEP_BYTES:
                truncated = True
                continue
            lines.append(message.rstrip())
            kept += len(message)
    parsed, cpu = {}, None
    for line in lines:
        head = re.match(r'CPU(\d+) attaching (NULL )?sched-domain', line)
        if head:
            cpu = int(head.group(1))
            parsed[str(cpu)] = []
            continue
        domain = re.match(r'\s*domain-(\d+): span=(\S+) level=(\S+)', line)
        if domain and cpu is not None:
            try:
                parsed[str(cpu)].append(dict(level=int(domain.group(1)), name=domain.group(3),
                                             span=parse_cpu_list(domain.group(2))))
            except (RuntimeError, ValueError):
                pass
    return dict(info, lines=lines, truncated=truncated, per_cpu=parsed,
                cpus_with_domains=sorted(int(c) for c, d in parsed.items() if d))


def kmsg_domains(kmsg, count):
    records, info = read_kmsg(kmsg)
    return summarize_kmsg(records, info) if records is not None else info


def groups(values):
    """cpu -> sorted tuple of the CPUs it shares with, as a sorted list of distinct groups."""
    return sorted({tuple(v) for v in values if v is not None})


def expectations(count, sys_info, domains_debugfs, domains_kmsg):
    cpus = sys_info['cpus']
    everyone = list(range(count))
    out = {}
    try:
        l2 = {}
        for row in cpus:
            index2 = [c for c in row['cache'] if c.get('level') == 2]
            l2[row['cpu']] = parse_mask_list(index2[0]['shared_cpu_list']) if index2 else None
        clusters = {r['cpu']: parse_mask_list(r['topology'].get('cluster_cpus_list')) for r in cpus}
        packages = {r['cpu']: parse_mask_list(r['topology'].get('package_cpus_list')) for r in cpus}
        caps = {r['cpu']: r['cpu_capacity'] for r in cpus}
        out['l2_groups'] = [list(g) for g in groups(l2.values())]
        out['cluster_groups'] = [list(g) for g in groups(clusters.values())]
        out['package_groups'] = [list(g) for g in groups(packages.values())]
        out['capacities'] = {str(k): v for k, v in caps.items()}
        out['capacity_values'] = sorted({v for v in caps.values() if v is not None})
        out['cluster_ids_distinct_groups'] = len({r['topology'].get('cluster_id') for r in cpus}) == len(out['cluster_groups'])
        out['single_package'] = out['package_groups'] == [everyone]
        out['clusters_match_l2_groups'] = None not in l2.values() and out['cluster_groups'] == out['l2_groups']
        out['capacity_asymmetric'] = len(out['capacity_values']) > 1 and None not in caps.values()
        small = min((len(g) for g in out['l2_groups']), default=0)
        efficiency = {c for g in out['l2_groups'] if len(g) == small and small < max(len(x) for x in out['l2_groups'])
                      for c in g}
        out['efficiency_cpus_by_l2_size'] = sorted(efficiency)
        out['capacity_ordered_efficiency_below_performance'] = bool(
            efficiency and out['capacity_asymmetric'] and
            max(caps[c] for c in efficiency) < min(caps[c] for c in everyone if c not in efficiency))
    except (KeyError, TypeError, ValueError, RuntimeError, IndexError) as error:
        out['evaluation_error'] = str(error)[-300:]
    spans = {}
    source = None
    if domains_debugfs.get('available'):
        source = 'debugfs'
        for cpu, rows in domains_debugfs['per_cpu'].items():
            spans[cpu] = [(r['name'], r['flags'] or []) for r in rows]
        out['asym_cpucapacity_flag_on_a_domain'] = any(SD_ASYM in flags for rows in spans.values()
                                                       for _, flags in rows)
        out['asym_cpucapacity_flag_on_every_cpu'] = bool(spans) and all(
            any(SD_ASYM in flags for _, flags in rows) for rows in spans.values())
    elif domains_kmsg.get('available') and domains_kmsg.get('cpus_with_domains'):
        source = 'kmsg'
    out['domains_source'] = source
    domains = domains_kmsg.get('per_cpu') or {}
    if domains and 'l2_groups' in out:
        by_cpu = {int(c): rows for c, rows in domains.items()}
        mc_ok = all(any(r['name'] == 'MC' and r['span'] == l2.get(c) for r in rows) for c, rows in by_cpu.items()
                    if rows) and bool(by_cpu)
        out['mc_domain_span_equals_l2_group'] = mc_ok
        out['top_domain_spans_all_cpus'] = all(rows and rows[-1]['span'] == everyone
                                               for rows in by_cpu.values() if rows) and bool(by_cpu)
        out['domain_levels'] = sorted({r['name'] for rows in by_cpu.values() for r in rows})
    active = out.get('clusters_match_l2_groups') is True and out.get('capacity_asymmetric') is True
    out['scheduler_topology_active'] = active
    out['note'] = ('cluster ids equal the L2 groups and the capacities are asymmetric: the cpu-map and '
                   'capacity-dmips-mhz reached the kernel.' if active else
                   'cpu-map and/or capacity-dmips-mhz are not in effect, or the '
                   'kernel did not parse them.')
    return out


def collect(count, sysfs=CPU_SYSFS, proc=PROC, debugfs=DEBUGFS, kmsg=KMSG, deadline_ns=None):
    """All independent sections; each failure is recorded and does not stop the others."""
    deadline = deadline_ns or time.monotonic_ns() + TIME_BUDGET_NS
    sections, errors = {}, {}
    for name, function in (('sysfs', lambda: cpu_sysfs(sysfs, count)),
                           ('cpuinfo', lambda: cpuinfo(proc, count)),
                           ('sched_domains_debugfs', lambda: debugfs_domains(debugfs, count)),
                           ('sched_domains_kmsg', lambda: kmsg_domains(kmsg, count))):
        if time.monotonic_ns() > deadline:
            errors[name] = 'time budget exhausted'
            sections[name] = dict(available=False)
            continue
        try:
            sections[name] = function()
        except Exception as error:            # evidence only: never stop the suite for one source
            errors[name] = (type(error).__name__ + ': ' + str(error))[-300:]
            sections[name] = dict(available=False)
    if 'cpus' in sections['sysfs']:
        sections['expectations'] = expectations(count, sections['sysfs'], sections['sched_domains_debugfs'],
                                                sections['sched_domains_kmsg'])
    return sections, errors


def assess(sections, count):
    """Recording complete for every admitted CPU; says nothing about what the topology is."""
    sysfs = sections.get('sysfs', {})
    info = sections.get('cpuinfo', {})
    need(len(sysfs.get('cpus', [])) == count and not sysfs.get('missing'),
         'topology/capacity files missing: ' + ', '.join(sysfs.get('missing', [])[:8]))
    need(info.get('available') is True and info.get('complete') is True and info.get('processors') == count,
         'cpuinfo does not list every admitted CPU')
    need(sysfs['system'].get('online') == f'0-{count - 1}', 'online set differs from the admitted cohort')


def refused_before_work(error):
    return dict(schema=1, probe='cpu_topology', boot_id=str(uuid.UUID(
        Path('/proc/sys/kernel/random/boot_id').read_text().strip())),
        passed=False, safe_to_continue=True, native_qualified=False,
        status='CPU_TOPOLOGY_REFUSED_BEFORE_WORK',
        error=(type(error).__name__ + ': ' + str(error))[-1500:])


def run():
    try:
        identity = m4host.require()
        count = identity['cpu_count']
        need(type(count) is int and 2 <= count <= 14, 'CPU count differs')
        need(os.geteuid() == 0, 'root owner required')
        metadata = OUT.parent.lstat()
        need(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022,
             'untrusted CPU topology report directory')
    except Exception as error:
        return refused_before_work(error)
    fd = os.open(OUT.with_name('cpu-topology-once'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    result = dict(schema=1, probe='cpu_topology', boot_id=str(uuid.UUID(
        Path('/proc/sys/kernel/random/boot_id').read_text().strip())), cpu_identity=identity,
        passed=False, safe_to_continue=True, native_qualified=False,
        status='CPU_TOPOLOGY_INCOMPLETE', started_monotonic_ns=time.monotonic_ns(),
        scope='Read-only record of the topology, capacities and scheduler domains the running kernel '
              'derived from the device tree. It does not test scheduling behaviour, steer any CPU or '
              'change idle, frequency or hotplug.')
    save(result)
    sections, errors = collect(count)
    result.update(sections)
    result['collection_errors'] = errors
    try:
        assess(sections, count)
        result['assessment_passed'] = True
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        result.update(assessment_passed=False, assessment_error=str(error)[-1500:])
    save(result)
    # Safe to continue unless the owned cohort moved under the read.
    result['safe_to_continue'] = gate_identity_unchanged(identity)
    result['passed'] = result['safe_to_continue'] and result['assessment_passed']
    result.update(status='PASS_CPU_TOPOLOGY_RECORDED' if result['passed'] else 'CPU_TOPOLOGY_FAILED_OR_REFUSED',
                  finished_monotonic_ns=time.monotonic_ns())
    save(result)
    return result


def gate_identity_unchanged(identity):
    from m4host import same_cpu_identity
    try:
        return same_cpu_identity(m4host.boot_identity(), identity)
    except Exception:
        return False


if __name__ == '__main__':
    try:
        result = run()
        print(json.dumps(result, separators=(',', ':')), flush=True)
        raise SystemExit(0 if result['passed'] else 1 if result['safe_to_continue'] else 2)
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
