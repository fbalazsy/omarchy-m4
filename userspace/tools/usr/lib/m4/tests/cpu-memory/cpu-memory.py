#!/usr/bin/env python3
"""One admitted RAM-only SIMD/shared-cache/CoW check with explicit child results."""
from pathlib import Path
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import m4host
from m4host import same_cpu_identity

HERE = Path(__file__).resolve().parent
OUT = Path('/run/m4/state/cpu-memory.json')


def need(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def save(result):
    fd, temporary = tempfile.mkstemp(prefix='.cpu-memory-', dir=OUT.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write((json.dumps(result, indent=2) + '\n').encode())
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, OUT)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run_child(name):
    child = None
    row = dict(passed=False, safe_to_continue=False)
    try:
        child = subprocess.Popen([str(HERE / name)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, start_new_session=True)
        output, errors = child.communicate(timeout=20)
        row.update(returncode=child.returncode, child_reaped=child.returncode is not None,
                   stdout_bytes=len(output), stdout_sha256=hashlib.sha256(output).hexdigest(),
                   stderr=errors[-8192:].decode('ascii', errors='replace'))
        need(len(output) <= 16384 and not errors, 'CPU child output exceeds bound or stderr is present')
        value = json.loads(output)
        need(type(value) is dict and type(value.get('passed')) is bool and
             type(value.get('safe_to_continue')) is bool, 'CPU child positive completion missing')
        need((child.returncode, value['passed'], value['safe_to_continue']) in
             ((0, True, True), (1, False, True), (2, False, False)), 'CPU child exit/result differs')
        row.update(value)
    except Exception as error:
        row.update(error=str(error)[-1500:])
    finally:
        if child is not None and child.poll() is None:
            # A failed RAM-only parent can still own forked workers. The outer
            # transient unit independently requires an empty owned cgroup.
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.communicate(timeout=7)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    child.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    row['child_reaped'] = False
            row['child_reaped'] = child.poll() is not None
    return row


def assess_simd(row, count):
    need(row.get('passed') is True and row.get('safe_to_continue') is True and
         row.get('status') == 'PASS_CPU_SIMD_CONTEXT_MIGRATION' and
         row.get('cpus') == list(range(count)) and row.get('rounds_per_worker') == 256 and
         type(row.get('rounds_per_worker')) is int and type(row.get('q_registers')) is int and
         row.get('q_registers') == 32 and row.get('fpcr_fpsr_checked') is True and
         row.get('parent_affinity_preserved') is True and
         row.get('wait_status') == [0] * count and
         row.get('extra_core_release_attempted') is False, 'SIMD cohort/completion differs')
    workers = row.get('workers')
    need(type(row.get('cpus')) is list and all(type(value) is int for value in row['cpus']) and
         type(row.get('wait_status')) is list and all(type(value) is int for value in row['wait_status']),
         'SIMD CPU and exit-status scalar types differ')
    need(type(workers) is list and len(workers) == count, 'SIMD worker set differs')
    for cpu, worker in enumerate(workers):
        expected = [sum((round + cpu) % count == target for round in range(256))
                    for target in range(count)]
        need(type(worker) is dict and all(type(worker.get(key)) is int for key in ('worker','rounds','mismatch','error')) and
             type(worker.get('visits')) is list and all(type(value) is int for value in worker['visits']) and
             worker == dict(worker=cpu, rounds=256, visits=expected, mismatch=0, error=0),
             'SIMD per-core visits or register oracle differs')


def assess_memory(row, count):
    expected = dict(cpu_count=count, shared_bytes=65536, handoff_rounds_per_cpu=32,
                    handoff_versions=count * 32, workers_created=count, workers_joined=count,
                    cow_bytes_per_child=1048576, cow_children_reaped=count * 8,
                    cow_completed_per_cpu=[8] * count, error=0, interrupted=0)
    need(row.get('passed') is True and row.get('safe_to_continue') is True and
         row.get('status') == 'PASS_CPU_CACHED_HANDOFF_AND_COW' and
         row.get('parent_affinity_policy_nice_preserved') is True and
         all(type(row.get(key)) is type(value) and row[key] == value
             for key, value in expected.items()), 'cached handoff/CoW positive evidence differs')
    need(all(type(value) is int for value in row['cow_completed_per_cpu']),
         'CoW completion scalar types differ')
    large = row.get('large_handoff')
    expected_large = dict(shared_bytes=33554432, rounds_per_cpu=2, versions=count*2,
                          workers_created=count, workers_joined=count, buffer_unmapped=True,
                          error=0, completed_per_cpu=[2]*count)
    need(type(large) is dict and all(type(large.get(key)) is type(value) and large[key] == value
         for key, value in expected_large.items()), 'above-L2 shared handoff evidence differs')
    need(all(type(value) is int for value in large['completed_per_cpu']) and
         type(large.get('elapsed_ns')) is int and 0 < large['elapsed_ns'] < 20000000000,
         'above-L2 completion scalar types or bounded elapsed time differ')


def refused_before_work(error):
    # No once marker, report, child or CPU work exists yet. An unreadable boot
    # identity raises here instead, keeping exit 2 with no safety claim.
    return dict(schema=1, probe='cpu_memory', boot_id=str(uuid.UUID(
        Path('/proc/sys/kernel/random/boot_id').read_text().strip())),
        passed=False, safe_to_continue=True, native_qualified=False,
        status='CPU_MEMORY_REFUSED_BEFORE_WORK',
        error=(type(error).__name__ + ': ' + str(error))[-1500:])


def run():
    try:
        identity = m4host.require()
        count = identity['cpu_count']
        need(type(count) is int and 2 <= count <= 14, 'CPU count differs')
        need(os.geteuid() == 0 and sorted(os.sched_getaffinity(0)) == list(range(count)) and
             os.sched_getscheduler(0) == os.SCHED_OTHER and os.getpriority(os.PRIO_PROCESS, 0) == 0,
             'ordinary admitted unrestricted CPU owner required')
        metadata = OUT.parent.lstat()
        need(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0 and not metadata.st_mode & 0o022,
             'untrusted CPU RAM report directory')
    except Exception as error:
        return refused_before_work(error)
    fd = os.open(OUT.with_name('cpu-memory-once'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    result = dict(schema=1, probe='cpu_memory', boot_id=str(uuid.UUID(
        Path('/proc/sys/kernel/random/boot_id').read_text().strip())), cpu_identity=identity,
        passed=False, safe_to_continue=True, native_qualified=False,
        status='CPU_MEMORY_INCOMPLETE', tests={}, started_monotonic_ns=time.monotonic_ns(),
        scope='Bounded ordinary cached RAM, all32 SIMD registers/FPCR/FPSR migrations, '
              'CoW/madvise isolation and32MiB shared version handoffs on admitted cores only; '
              'handoff elapsed includes validation/writes/thread waiting, not bandwidth; '
              'no core release or hardware control.')
    save(result)
    for name, assess in (('cpu-context', assess_simd), ('cpu-memory', assess_memory)):
        row = run_child(name)
        result['tests'][name] = row
        result['safe_to_continue'] = row.get('safe_to_continue') is True and row.get('child_reaped') is True
        try:
            assess(row, count)
            row['assessment_passed'] = True
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            row.update(assessment_passed=False, assessment_error=str(error)[-1500:])
        save(result)
        if not result['safe_to_continue']:
            break
    need(same_cpu_identity(m4host.boot_identity(), identity), 'CPU ownership changed during RAM checks')
    result['passed'] = (result['safe_to_continue'] and len(result['tests']) == 2 and
                        all(row.get('assessment_passed') is True for row in result['tests'].values()))
    result.update(status='PASS_BOUNDED_COHORT_SIMD_CACHE_COW' if result['passed'] else
                  'CPU_MEMORY_FAILED_OR_REFUSED', finished_monotonic_ns=time.monotonic_ns())
    save(result)
    return result


if __name__ == '__main__':
    try:
        result = run()
        print(json.dumps(result, separators=(',', ':')), flush=True)
        raise SystemExit(0 if result['passed'] else 1 if result['safe_to_continue'] else 2)
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
