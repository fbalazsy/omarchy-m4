"""Shared macOS/Linux Bose link key: import before bluetoothd and stale-bond handling.

macOS and Linux share the controller named in /etc/m4/bluetooth.conf, so the headset there holds exactly one key for it. The
macOS-side tool share-bt-key.py leaves /var/lib/m4-shared/bluetooth/<headset address without colons>.json on this root file system; this module
validates it and writes the key into the BlueZ info file before the owned session starts bluetoothd. Key bytes are
never logged or returned: every result carries only the order name, BlueZ key type and a 4-hex sha256 fingerprint.
"""
from pathlib import Path
import hashlib, json, os, re, socket, stat, struct, time

CONF = Path('/etc/m4/bluetooth.conf')


def conf_values(path=CONF):
    """The key=value lines of bluetooth.conf (controller=, device=) that have a value; empty when the file is absent."""
    values = {}
    try:
        for line in Path(path).read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and value.strip() and not key.strip().startswith('#'):
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


_CONF = conf_values()
HOST_ADDRESS = _CONF.get('controller', '')
TARGET = _CONF.get('device', '')
SHARED = Path('/var/lib/m4-shared/bluetooth')
SOURCE = SHARED/(TARGET.replace(':', '') + '.json')
STATE_FILE = SHARED/(TARGET.replace(':', '') + '.import.json')
BACKUP = SHARED/(TARGET.replace(':', '') + '.info.pre-import')
BLUEZ = Path('/var/lib/bluetooth')
ORDERS = ('as_stored', 'reversed')
# Bluetooth Core v5.4 Vol 4 Part E 7.7.24 Link_Key_Type == BlueZ doc/mgmt-api.txt Load Link Keys Key_Type ==
# BlueZ info [LinkKey] Type. 3 (debug combination) is skipped by the kernel loader, so it is refused here.
TYPES = {0: 'combination', 1: 'local_unit', 2: 'remote_unit', 4: 'unauthenticated_p192', 5: 'authenticated_p192',
         6: 'changed_combination', 7: 'unauthenticated_p256', 8: 'authenticated_p256'}
MGMT_LOAD_LINK_KEYS = 0x0012
AUTH_MARKERS = ('permission denied', 'authentication', 'auth failed', 'key-missing', 'key missing', 'insufficient security')
QUICK_MARKERS = ('br-connection-unknown',)
QUICK_SECONDS = 2.0
ROOT_UID = 0


class KeyShareError(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise KeyShareError(message)


def fingerprint(key):
    return hashlib.sha256(key).hexdigest()[:4]


def secure_read(path, limit=65536):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    need(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == ROOT_UID and not metadata.st_mode & 0o077 and
         metadata.st_nlink == 1 and 0 < metadata.st_size <= limit, 'shared key file is not a root-owned 0600 regular file: ' + path.name)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def atomic_write(path, data, mode=0o600):
    path = Path(path)
    tmp = path.with_name('.' + path.name + '.tmp')
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        if os.geteuid() == 0:
            os.fchown(fd, 0, 0)
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))
    directory = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_shared(path=None, controller=HOST_ADDRESS, device=TARGET):
    """Return the validated shared key record, or None when the file is absent."""
    raw = secure_read(path or SOURCE)
    if raw is None:
        return None
    try:
        row = json.loads(raw.decode('ascii'))
    except (ValueError, UnicodeDecodeError):
        raise KeyShareError('shared key file is not ASCII JSON')
    need(isinstance(row, dict) and row.get('schema') == 1, 'shared key schema differs')
    need(row.get('controller_address') == controller and row.get('device_address') == device,
         'shared key addresses differ from this controller/device')
    need(type(row.get('type')) is int and row['type'] in TYPES, 'shared key type is not a usable BlueZ link-key type')
    need(row.get('primary_order') in ORDERS, 'shared key primary order differs')
    keys = {}
    for order, field in (('as_stored', 'key_as_stored'), ('reversed', 'key_reversed')):
        text = row.get(field)
        need(isinstance(text, str) and re.fullmatch('[0-9A-F]{32}', text), 'shared key field is not 32 uppercase hex: ' + field)
        keys[order] = bytes.fromhex(text)
    need(keys['as_stored'] == keys['reversed'][::-1] and any(keys['as_stored']), 'shared key orders are inconsistent or all zero')
    return dict(keys=keys, type=row['type'], type_name=TYPES[row['type']], primary=row['primary_order'],
                fingerprint=fingerprint(keys[row['primary_order']]), created=row.get('created'))


def load_state(path=None):
    try:
        raw = secure_read(path or STATE_FILE)
        row = json.loads(raw.decode('ascii')) if raw else None
    except (KeyShareError, ValueError, UnicodeDecodeError):
        return None
    return row if isinstance(row, dict) and row.get('schema') == 1 else None


def record_outcome(shared, outcome, order, tried, path=None, now=time.time):
    """Remember what the controller did with this exact key so later boots neither loop nor repeat a rejected order."""
    row = dict(schema=1, fingerprint=shared['fingerprint'], outcome=outcome, order=order, tried=list(tried),
               type=shared['type'], recorded_epoch=int(now()))
    atomic_write(path or STATE_FILE, (json.dumps(row, indent=2) + '\n').encode('ascii'))
    return row


def first_order(shared, state):
    """(order, skip_reason). A key already rejected in every order is not re-imported until it is re-shared."""
    if state and state.get('fingerprint') == shared['fingerprint']:
        if state.get('outcome') == 'rejected_all':
            return None, 'SKIPPED_SHARED_KEY_PREVIOUSLY_REJECTED_IN_ALL_ORDERS'
        if state.get('outcome') == 'accepted' and state.get('order') in ORDERS:
            return state['order'], None
    return shared['primary'], None


def info_key(text):
    section = None
    for line in text.splitlines():
        if line.startswith('['):
            section = line.strip()
        elif section == '[LinkKey]' and line.startswith('Key='):
            return line[4:].strip().upper()
    return None


def rewrite_info(text, key_hex, key_type, pin_length=0):
    """Replace Key/Type/PINLength inside [LinkKey]; every other line and section is kept byte for byte."""
    need('\0' not in text and len(text) <= 65536, 'BlueZ info file is not text of bounded size')
    new = ['Key=' + key_hex, 'Type=' + str(key_type), 'PINLength=' + str(pin_length)]
    lines = text.split('\n')
    out, section, seen, done = [], None, False, False
    for line in lines:
        if line.startswith('['):
            if section == '[LinkKey]' and not done:
                out += new
                done = True
            section = line.strip()
            seen = seen or section == '[LinkKey]'
            out.append(line)
        elif section == '[LinkKey]' and re.match(r'(Key|Type|PINLength)=', line):
            continue
        else:
            out.append(line)
    if section == '[LinkKey]' and not done:
        while out and out[-1] == '':
            out.pop()
        out += new + ['']
        done = True
    result = '\n'.join(out)
    if not seen:
        result = result.rstrip('\n') + ('\n\n' if result.strip() else '') + '[LinkKey]\n' + '\n'.join(new) + '\n'
    return result if result.endswith('\n') else result + '\n'


def device_dir(bluez=None, controller=HOST_ADDRESS, device=TARGET):
    return Path(bluez or BLUEZ)/controller/device


def install_key(key, key_type, name=None, bluez=None, backup=None, controller=HOST_ADDRESS, device=TARGET):
    """Write the key into the BlueZ info file (bluetoothd stopped, or already running with the kernel reloaded)."""
    adapter = Path(bluez or BLUEZ)/controller
    need(adapter.is_dir() and not adapter.is_symlink(), 'BlueZ adapter directory is absent; refusing to create adapter storage')
    folder = adapter/device
    info = folder/'info'
    if info.exists() or info.is_symlink():
        old = secure_read(info)
        text = old.decode('utf-8')
        if backup is not False and not Path(backup or BACKUP).exists():
            Path(backup or BACKUP).parent.mkdir(parents=True, exist_ok=True)
            atomic_write(Path(backup or BACKUP), old)
    else:
        folder.mkdir(mode=0o700, exist_ok=True)
        text = ('[General]\nName=' + (name or '') + '\nAddressType=BR/EDR\nSupportedTechnologies=BR/EDR;\n'
                'Trusted=true\nBlocked=false\n')
    atomic_write(info, rewrite_info(text, key.hex().upper(), key_type).encode('utf-8'))
    need(info_key(info.read_text()) == key.hex().upper(), 'BlueZ info key read-back differs')


def import_before_daemon(name=None, source=None, state=None, bluez=None, backup=None, now=time.time):
    """Called once before `systemctl start bluetooth.service`; never raises, never returns key bytes."""
    if source is None and not (HOST_ADDRESS and TARGET):
        return dict(status='NOT_CONFIGURED', used=False)
    result = dict(status='NO_SHARED_KEY', used=False)
    try:
        shared = load_shared(source)
        if shared is None:
            return result
        result.update(fingerprint=shared['fingerprint'], type=shared['type'], type_name=shared['type_name'])
        order, reason = first_order(shared, load_state(state))
        if reason:
            result.update(status=reason)
            return result
        key = shared['keys'][order]
        folder = device_dir(bluez)
        current = None
        if (folder/'info').exists():
            current = info_key((folder/'info').read_text())
        if current != key.hex().upper():
            install_key(key, shared['type'], name=name, bluez=bluez, backup=backup)
            result.update(status='IMPORTED_' + order.upper(), used=True)
        else:
            result.update(status='ALREADY_CURRENT_' + order.upper(), used=True)
        result.update(order=order, orders_available=list(ORDERS), source_key_order_primary=shared['primary'])
    except Exception as error:
        result.update(status='IMPORT_ERROR_BOND_UNCHANGED_OR_ATOMIC', error=str(error)[-300:], used=False)
    return result


def alternate(source=None, state=None, tried=()):
    """The untried order for a retry after an authentication rejection, or None."""
    shared = load_shared(source)
    if shared is None:
        return None
    for order in ORDERS:
        if order not in tried:
            return dict(order=order, key=shared['keys'][order], type=shared['type'], shared=shared)
    return None


def is_auth_rejection(message, elapsed):
    """BlueZ reports a rejected key as org.bluez.Error.Failed br-connection-unknown after about 0.2 s; a page timeout
    takes seconds. Explicit authentication wording is accepted at any speed."""
    text = str(message).lower()
    return any(m in text for m in AUTH_MARKERS) or (any(m in text for m in QUICK_MARKERS) and elapsed < QUICK_SECONDS)


def mgmt_packet(index, key, key_type, device=TARGET):
    """MGMT_OP_LOAD_LINK_KEYS: debug_keys=0, key_count=1, {bdaddr LE, addr_type 0 BR/EDR, type, value[16], pin_len 0}."""
    need(len(key) == 16 and key_type in TYPES, 'mgmt link key arguments differ')
    params = struct.pack('<BH', 0, 1) + bytes.fromhex(device.replace(':', ''))[::-1] + struct.pack('<BB', 0, key_type) + key + b'\0'
    return struct.pack('<HHH', MGMT_LOAD_LINK_KEYS, index, len(params)) + params


def mgmt_send(packet):
    channel = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
    try:
        channel.settimeout(3)
        channel.bind((0xffff, 3))
        channel.send(packet)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            data = channel.recv(512)
            if len(data) >= 9 and struct.unpack_from('<H', data)[0] in (1, 2) and struct.unpack_from('<H', data, 6)[0] == MGMT_LOAD_LINK_KEYS:
                return data[8]
        raise KeyShareError('mgmt Load Link Keys reply did not arrive')
    finally:
        channel.close()


def load_into_kernel(index, key, key_type, send=None):
    """Replace the controller's link-key list with this one key. The kernel answers HCI Link Key Requests itself from
    that list, so bluetoothd (and the owned session's daemon identity) need no restart. Scope: the owned session holds
    only the selected Bose bond."""
    status = (send or mgmt_send)(mgmt_packet(index, key, key_type))
    need(status == 0, 'mgmt Load Link Keys status ' + str(status))
    return dict(method='mgmt_load_link_keys', status=0, controller_index=index)
