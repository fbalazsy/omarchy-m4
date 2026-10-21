#!/usr/bin/python3 -B
"""m4-bt-keyshare.service: import the shared macOS/Linux headset link key into BlueZ's info file before bluetoothd starts.
Never fails the boot; prints only the status and a 4-hex fingerprint (the key file is read by bt_keyshare, nowhere else)."""
import json
import sys

sys.path.insert(0, '/usr/lib/m4')
import bt_keyshare   # noqa: E402

if __name__ == '__main__':
    print('M4_BT_KEYSHARE ' + json.dumps(bt_keyshare.import_before_daemon(), sort_keys=True), flush=True)
    sys.exit(0)
