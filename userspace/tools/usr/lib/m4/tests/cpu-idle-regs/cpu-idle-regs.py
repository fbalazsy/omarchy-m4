#!/usr/bin/env python3
"""Read-only decoder for the loader's E0 record /chosen/m4,cpu-idle-regs (the loader's CPU idle register capture).

The loader read CYC_OVRD, ACC_CFG and ACC_OVRD once on the primary CPU, each under a recoverable exception guard,
and wrote the values (or the fault) into the device tree. This helper only reads that property, decodes the fields
with the m1n1/Asahi definitions and states what the values would mean for WFI idle. It writes nothing except its own
result file, starts nothing, and a missing property is an ordinary result. It never says WFI works: that needs E1.

  cpu-idle-regs.py                     decode /sys/firmware/devicetree/base/chosen/m4,cpu-idle-regs
  M4_IDLE_REGS_PROPERTY=<file> M4_IDLE_REGS_OUT=<file> cpu-idle-regs.py     (test hooks)

Exit: 0 record decoded, 1 record absent or malformed (safe_to_continue stays true), 2 never used.
"""
from pathlib import Path
import json, os, struct, tempfile, uuid

PROPERTY = Path(os.environ.get('M4_IDLE_REGS_PROPERTY', '/sys/firmware/devicetree/base/chosen/m4,cpu-idle-regs'))
OUT = Path(os.environ.get('M4_IDLE_REGS_OUT', '/run/m4/state/cpu-idle-regs.json'))
MAGIC = 0x4d3449444c455231          # "M4IDLER1"
HEADER_WORDS, RECORD_WORDS, COUNT = 8, 6, 3
LIMIT = 4096
STATE = {0: 'READ', 1: 'FAULT_RECORDED', 2: 'GUARD_REFUSED', 3: 'NOT_ATTEMPTED'}
NAMES = {0: ('CYC_OVRD', 's3_5_c15_c5_0'), 1: ('ACC_CFG', 's3_5_c15_c4_0'), 2: ('ACC_OVRD', 's3_5_c15_c6_0')}

def bits(value, high, low):
    return (value >> low) & ((1 << (high - low + 1)) - 1)

# m1n1 / Asahi definitions.
FIQ_IRQ_MODE = {
    0: 'unmasked: FIQ/IRQ wake a WFI (m1n1 sets 0 for normal idle)',
    2: 'masked: FIQ/IRQ do not wake a WFI (m1n1 cpu_sleep light-sleep value)',
}
WFI_MODE = {
    0: 'not overridden (m1n1 normal idle sets 2)',
    2: 'up: clock-gated idle with retention (m1n1 normal idle)',
    3: 'deep: core may power down; state lost, restart at reset vector (m1n1 deep_wfi / cpu_sleep)',
}

def decode_cyc_ovrd(v):
    f = dict(disable_wfi_ret=bits(v, 0, 0), fiq_mode=bits(v, 21, 20), irq_mode=bits(v, 23, 22),
             wfi_mode=bits(v, 25, 24), dsbl_snoop_time=bits(v, 31, 30))
    note = dict(
        fiq_mode=FIQ_IRQ_MODE.get(f['fiq_mode'], 'value not used by m1n1: unknown'),
        irq_mode=FIQ_IRQ_MODE.get(f['irq_mode'], 'value not used by m1n1: unknown'),
        wfi_mode=WFI_MODE.get(f['wfi_mode'], 'value not used by m1n1: unknown'),
        disable_wfi_ret='WFI retention disabled: deepest sleep, state lost' if f['disable_wfi_ret']
                        else 'WFI retention enabled (m1n1 normal idle)')
    return f, note

def decode_acc_cfg(v):
    f = dict(bp_sleep=bits(v, 3, 2), deep_sleep=bits(v, 24, 24), skip_init=bits(v, 30, 30))
    note = dict(bp_sleep='branch-predictor state retention across ACC sleep: ' + ('on (3)' if f['bp_sleep'] == 3 else 'value %d' % f['bp_sleep']),
                deep_sleep='ACC deep sleep ARMED (m1n1 disables it for A7-class parts)' if f['deep_sleep'] else 'ACC deep sleep off',
                skip_init='skip-init set' if f['skip_init'] else 'skip-init clear')
    return f, note

ACC_OVRD_FIELDS = (
    ('pre_reset_clk_cnts', 3, 0), ('frc_psm_cond_l3_d_on', 4, 4), ('ssb_divs_upd_req', 6, 5), ('ssb_divs_sel', 8, 7),
    ('dom_can_be_on_in_ref_clk', 9, 9), ('halt_llc_bfr_run', 10, 10), ('dis_tb_push', 11, 11), ('misr_en', 12, 12),
    ('pwr_dn_srm', 14, 13), ('dis_l2_flush_acc_sleep', 16, 15), ('train_down_link', 18, 17), ('dvfm_safe_vol', 19, 19),
    ('frc_zout_amx_pwr_dn_tmr', 20, 20), ('rc_ack_intr_lock', 21, 21), ('convert_siq_to_irq', 22, 22),
    ('vol_upd_ack', 24, 23), ('power_down_cpm', 26, 25), ('cpm_wake_up', 28, 27), ('disable_clk_dtr', 29, 29),
    ('disable_psw_off_abort', 30, 30), ('reset_cpm', 31, 31), ('disable_pio_on_wfi_cpu', 32, 32), ('deep_sleep', 34, 34),
    ('c2_wake_up', 35, 35), ('bypass_llc_flush_if_empty', 36, 36), ('disable_retention', 37, 37),
    ('disable_next_power_gate', 38, 38))

def decode_acc_ovrd(v):
    f = {name: bits(v, hi, lo) for name, hi, lo in ACC_OVRD_FIELDS}
    note = dict(deep_sleep='ACC deep-sleep override set' if f['deep_sleep'] else 'ACC deep-sleep override clear',
                disable_retention='state retention disabled in ACC sleep' if f['disable_retention'] else 'state retention not disabled',
                power_down_cpm='CPM power-down override value %d (m1n1 global deep sleep writes 3)' % f['power_down_cpm'])
    return f, note

DECODERS = {0: decode_cyc_ovrd, 1: decode_acc_cfg, 2: decode_acc_ovrd}

def interpret(registers):
    """What the recorded values imply for a plain WFI. Interpretation only: E1 (a bounded WFI) is the test."""
    cyc, cfg, ovr = (registers[i] for i in range(3))
    unread = [r['name'] for r in (cyc, cfg, ovr) if r['state'] != 'READ']
    reasons, ok = [], True
    if cyc['state'] == 'READ':
        f = cyc['fields']
        if f['fiq_mode'] != 0 or f['irq_mode'] != 0:
            ok = False
            reasons.append('FIQ/IRQ wake mode is not 0 (fiq=%d irq=%d): a masked interrupt may not wake WFI; the timer tick and fast IPIs arrive as FIQ' % (f['fiq_mode'], f['irq_mode']))
        if f['wfi_mode'] == 3 or f['disable_wfi_ret']:
            ok = False
            reasons.append('WFI mode is deep or retention is disabled: a WFI could lose core state (resume at the reset vector)')
        if f['wfi_mode'] not in (0, 2, 3):
            reasons.append('WFI mode %d is not a value m1n1 uses: unknown semantics' % f['wfi_mode'])
    if cfg['state'] == 'READ' and cfg['fields']['deep_sleep']:
        ok = False
        reasons.append('ACC_CFG deep sleep is armed: WFI may enter a deep state')
    if ovr['state'] == 'READ' and (ovr['fields']['deep_sleep'] or ovr['fields']['disable_retention']):
        reasons.append('ACC_OVRD has a deep-sleep/retention-disable bit set: check against the M4 definition before any WFI')
    if unread:
        verdict = 'UNKNOWN_REGISTER_NOT_READABLE'
        reasons.insert(0, 'not readable by the loader at EL2: ' + ', '.join(unread) + ' (fault or refusal recorded); wake configuration cannot be inspected')
    elif not ok:
        verdict = 'WFI_UNSAFE_AS_RECORDED'
    else:
        verdict = 'WFI_STATE_PLAUSIBLE_NOT_PROVEN'
        reasons.append('values match what m1n1 sets for normal idle on t6000/t8112 (wake unmasked, retention on); this does not prove WFI wakes on T6040')
    return dict(verdict=verdict, reasons=reasons,
                scope='Interpretation of one boot-time read on the primary CPU with m1n1 field definitions written for earlier '
                      'SoCs. The M4 layout may differ. idle=nop stays until E1 (one bounded WFI) passes.')

def parse(raw):
    need = lambda ok, why: (_ for _ in ()).throw(ValueError(why)) if not ok else None
    need(len(raw) == 8 * (HEADER_WORDS + RECORD_WORDS * COUNT), 'record size differs')
    words = struct.unpack('>%dQ' % (len(raw) // 8), raw)
    need(words[0] == MAGIC, 'magic differs')
    need(words[1] == 1, 'unsupported version')
    need(words[2] == len(raw), 'recorded size differs')
    need(words[7] == COUNT, 'register count differs')
    registers = {}
    for i in range(COUNT):
        w = words[HEADER_WORDS + RECORD_WORDS * i: HEADER_WORDS + RECORD_WORDS * (i + 1)]
        need(w[0] == i, 'register id order differs')
        need(w[1] in STATE, 'unknown state')
        name, encoding = NAMES[i]
        row = dict(name=name, encoding=encoding, state=STATE[w[1]])
        if w[1] == 0:
            row.update(value='0x%016x' % w[2])
            row['fields'], row['notes'] = DECODERS[i](w[2])
        else:
            ec = bits(w[3], 31, 26)
            row.update(esr='0x%x' % w[3], esr_ec='0x%x' % ec, elr='0x%x' % w[4], far='0x%x' % w[5],
                       esr_meaning={0: 'undefined instruction / unknown reason', 0x18: 'trapped system register',
                                    0x25: 'data abort, same EL', 0x21: 'instruction abort, same EL'}.get(ec, 'other'))
        registers[i] = row
    header = dict(version=words[1], flags=words[3], mpidr='0x%x' % words[4], midr='0x%x' % words[5],
                  current_el=words[6], writes_made=False)
    return header, registers

def save(result):
    fd, tmp = tempfile.mkstemp(prefix='.cpu-idle-regs-', dir=str(OUT.parent))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write((json.dumps(result, indent=2) + '\n').encode())
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, OUT)
    except BaseException:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def run():
    boot = str(uuid.UUID(Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
    result = dict(schema=1, probe='cpu_idle_regs', boot_id=boot, passed=False, safe_to_continue=True,
                  native_qualified=False, record_present=False,
                  scope='Read-only decode of the loader-recorded idle/wake registers of the primary CPU. '
                        'No register is read or written by Linux; nothing is steered; idle behaviour is unchanged.')
    try:
        raw = PROPERTY.read_bytes()[:LIMIT + 1]
    except FileNotFoundError:
        result.update(status='CPU_IDLE_REGS_RECORD_ABSENT',
                      note='Loader without E0 (or the property was not published). Not an error.')
        return result
    except OSError as error:
        result.update(status='CPU_IDLE_REGS_RECORD_UNREADABLE', error=str(error)[-300:])
        return result
    try:
        if len(raw) > LIMIT:
            raise ValueError('record oversized')
        header, registers = parse(raw)
    except ValueError as error:
        result.update(status='CPU_IDLE_REGS_RECORD_MALFORMED', error=str(error), record_present=True, raw_hex=raw.hex()[:512])
        return result
    result.update(record_present=True, passed=True, status='PASS_CPU_IDLE_REGS_RECORDED', cpu=header,
                  registers={registers[i]['name']: registers[i] for i in range(COUNT)},
                  interpretation=interpret(registers))
    return result

if __name__ == '__main__':
    result = run()
    try:
        if OUT.parent.is_dir():
            save(result)
    except OSError as error:
        result['save_error'] = str(error)[-200:]
    print(json.dumps(result, separators=(',', ':')), flush=True)
    raise SystemExit(0 if result['passed'] else 1)
