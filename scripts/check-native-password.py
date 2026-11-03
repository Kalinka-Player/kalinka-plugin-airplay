#!/usr/bin/env python3
"""Exercise classic password enforcement in the shipped AP2 binary.

Run in an isolated container/network namespace with D-Bus and Avahi running:
    python3 scripts/check-native-password.py /path/to/shairport-sync
No audio hardware or Python dependencies are required. Uses RTSP port 5000.
"""

import hashlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PASSWORD = "kalinka-test-only"
URI = "rtsp://127.0.0.1/123"
SDP = (
    "v=0\r\no=iTunes 123 0 IN IP4 127.0.0.1\r\ns=AirTunes\r\n"
    "c=IN IP4 127.0.0.1\r\nt=0 0\r\nm=audio 0 RTP/AVP 96\r\n"
    "a=rtpmap:96 L16/44100/2\r\n"
)


def exchange(connection, method, sequence, authorization=None):
    body = SDP if method == "ANNOUNCE" else ""
    headers = [
        f"{method} {URI} RTSP/1.0",
        f"CSeq: {sequence}",
        "Content-Type: application/sdp",
        f"Content-Length: {len(body)}",
    ]
    if authorization:
        headers.append(f"Authorization: {authorization}")
    connection.sendall(("\r\n".join(headers) + "\r\n\r\n" + body).encode())
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("Receiver closed the control connection")
        response += chunk
    text = response.decode()
    return int(text.split()[1]), text


def announce(password=None, *, options_first=False):
    with socket.create_connection(("127.0.0.1", 5000), timeout=2) as connection:
        if options_first:
            assert exchange(connection, "OPTIONS", 1)[0] == 200
        status, challenge = exchange(connection, "ANNOUNCE", 2)
        if password is None:
            return status
        assert status == 401, f"Expected a password challenge, received {status}"
        nonce = re.search(r'nonce="([^"]+)"', challenge).group(1)
        realm = re.search(r'realm="([^"]+)"', challenge).group(1)

        def digest(value):
            return hashlib.md5(value.encode()).hexdigest()

        response = digest(
            f'{digest("test:" + realm + ":" + password)}:{nonce}:'
            f'{digest("ANNOUNCE:" + URI)}'
        )
        header = (
            f'Digest username="test", realm="{realm}", nonce="{nonce}", '
            f'uri="{URI}", response="{response}"'
        )
        return exchange(connection, "ANNOUNCE", 3, header)[0]


def check(binary, directory, protected):
    config = directory / "shairport.conf"
    pipe = directory / "metadata"
    if not pipe.exists():
        os.mkfifo(pipe, 0o600)
    metadata_fd = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
    password = f'password = "{PASSWORD}";' if protected else ""
    config.write_text(
        'general = { name = "Kalinka password check"; service_type = "classic"; '
        f'output_backend = "stdout"; interpolation = "basic"; {password} }};\n'
        'stdout = { output_rate = (44100, 48000); output_format = "S24_3LE"; '
        'output_channels = 2; };\n'
        f'metadata = {{ enabled = "yes"; pipe_name = "{pipe}"; }};\n'
    )
    config.chmod(0o600)
    with (directory / "receiver.log").open("w+") as log:
        process = subprocess.Popen(
            [binary, "-c", str(config)], stdout=subprocess.DEVNULL, stderr=log
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None or time.monotonic() >= deadline:
                    log.seek(0)
                    raise RuntimeError("Receiver did not start: " + log.read())
                try:
                    status = announce()
                    break
                except OSError:
                    time.sleep(0.1)
            expected = 401 if protected else 200
            assert status == expected, (status, expected)
            assert announce(options_first=True) == expected
            if protected:
                assert announce("incorrect", options_first=True) == 401
                assert announce(PASSWORD, options_first=True) == 200
                # A successful connection cannot authorize a different connection.
                assert announce() == 401
                print("PASS: absent/wrong passwords refused; correct password accepted; reconnect protected")
            else:
                print("PASS: clearing the password permits a new classic session")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            os.close(metadata_fd)


if __name__ == "__main__":
    binary = str(Path(sys.argv[1]).resolve(strict=True))
    with tempfile.TemporaryDirectory(prefix="kalinka-password-check-") as directory:
        for protected in (True, False):
            check(binary, Path(directory), protected)
