"""Minimal telnet client: tn.py '<command>' [seconds]. Refuses all telnet options.

The frame's address comes from the SKYLIGHT_HOST environment variable, for example
SKYLIGHT_HOST='fe80::xxxx%en26' (IPv6 link-local address, then % and the Mac's interface).
"""
import os, socket, sys, time, select
host = os.environ.get("SKYLIGHT_HOST") or sys.exit("FAIL: set SKYLIGHT_HOST to the frame's address, e.g. fe80::xxxx%en26")
ai = socket.getaddrinfo(host, 23, socket.AF_INET6, socket.SOCK_STREAM)[0]
s = socket.socket(*ai[:3]); s.settimeout(8); s.connect(ai[4])
def pump(t):
    out = b""; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([s], [], [], 0.3)
        if not r: continue
        d = s.recv(65536)
        if not d: break
        i = 0; clean = b""
        while i < len(d):
            if d[i] == 255 and i + 2 < len(d) and d[i+1] in (251, 252, 253, 254):
                reply = 254 if d[i+1] in (251, 252) else 252   # WILL/WONT -> DONT, DO/DONT -> WONT
                s.sendall(bytes([255, reply, d[i+2]])); i += 3
            else:
                clean += d[i:i+1]; i += 1
        out += clean
        if b"__DONE__\r" in out or out.endswith(b"__DONE__\n"): 
            if out.count(b"__DONE__") >= 2: break
    return out
pump(1.0)
s.sendall((sys.argv[1] + "; echo __DONE__\n").encode())
out = pump(float(sys.argv[2]) if len(sys.argv) > 2 else 6).decode(errors="replace").replace("\r", "")
sys.stdout.write(out)
