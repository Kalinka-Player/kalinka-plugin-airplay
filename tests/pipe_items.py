"""Metadata items framed exactly as shairport-sync's metadata/pipe.c writes them."""

import base64


def item(kind: str, code: str, data: bytes = b"") -> bytes:
    head = (
        f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
        f"<length>{len(data)}</length>"
    ).encode()
    if not data:
        return head + b"</item>\n"
    # 57 bytes at a time, each group encoded on its own and run together.
    groups = b"".join(
        base64.b64encode(data[i : i + 57]) for i in range(0, len(data), 57)
    )
    return head + b'\n<data encoding="base64">\n' + groups + b"</data></item>\n"


def ssnc(code: str, data: bytes = b"") -> bytes:
    return item("ssnc", code, data)


def core(code: str, data: bytes) -> bytes:
    return item("core", code, data)


def track(title: str, artist: str, album: str) -> bytes:
    return (
        ssnc("mdst", b"1000")
        + core("minm", title.encode())
        + core("asar", artist.encode())
        + core("asal", album.encode())
        + ssnc("mden", b"1000")
    )
