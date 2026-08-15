"""``cyclaw-gen-cert``: write a self-signed TLS cert with hostname + LAN SAN.

Offline helper for Stage 4 of docs/AUTHENTICATION_DESIGN.md. Wraps
``openssl req -x509`` (stdlib cannot mint X.509 + subjectAltName). Does
not add a runtime dependency.

Usage::

    python -m utils.gen_cert
    python -m utils.gen_cert --certfile data/tls/cert.pem --keyfile data/tls/key.pem

Exit codes: 0 ok, 2 openssl failed, 3 openssl missing / bad args.
"""

from __future__ import annotations

from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

import argparse  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CERT = "data/tls/cert.pem"
_DEFAULT_KEY = "data/tls/key.pem"
_WINDOWS_OPENSSL = (
    Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
    Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
)


def find_openssl() -> Path | None:
    which = shutil.which("openssl")
    if which:
        return Path(which)
    for candidate in _WINDOWS_OPENSSL:
        if candidate.is_file():
            return candidate
    return None


def _lan_ipv4() -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 80))
        ip = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    if ip.startswith("127."):
        return None
    return ip


def subject_alt_names(hostname: str) -> str:
    parts = [
        f"DNS:{hostname}",
        "DNS:localhost",
        "IP:127.0.0.1",
        "IP:0:0:0:0:0:0:0:1",
    ]
    lan = _lan_ipv4()
    if lan and lan not in {"127.0.0.1"}:
        parts.append(f"IP:{lan}")
    return ",".join(parts)


def _anchor(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyclaw-gen-cert")
    parser.add_argument("--certfile", default=_DEFAULT_CERT)
    parser.add_argument("--keyfile", default=_DEFAULT_KEY)
    parser.add_argument("--hostname", default=socket.gethostname())
    parser.add_argument("--days", type=int, default=825)
    args = parser.parse_args(argv)

    openssl = find_openssl()
    if openssl is None:
        print(
            "openssl not found. Install OpenSSL or Git for Windows "
            "(usr\\bin\\openssl.exe), then retry.",
            file=sys.stderr,
        )
        return EXIT_ENV
    if args.days <= 0:
        print("--days must be a positive integer", file=sys.stderr)
        return EXIT_ENV

    certfile = _anchor(args.certfile)
    keyfile = _anchor(args.keyfile)
    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    san = subject_alt_names(args.hostname)
    cmd = [
        str(openssl),
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        str(args.days),
        "-nodes",
        "-keyout",
        str(keyfile),
        "-out",
        str(certfile),
        "-subj",
        f"/CN={args.hostname}",
        "-addext",
        f"subjectAltName={san}",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 - argv list; openssl from which/fixed path
            cmd, check=False, capture_output=True, text=True,
        )
    except OSError as exc:
        print(f"failed to run openssl: {exc}", file=sys.stderr)
        return EXIT_FAIL
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        print(err or "openssl req failed", file=sys.stderr)
        return EXIT_FAIL
    try:
        keyfile.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort hardening only: on Windows the POSIX mode bits are a
        # partial no-op against ACLs anyway, and a failed chmod must not
        # report failure for a cert pair that was written successfully.
        pass
    print(f"wrote {certfile}")
    print(f"wrote {keyfile}")
    print(f"SAN {san}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
