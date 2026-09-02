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
import os  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3

# RSA-2048 keygen + self-sign is near-instant on any real machine; this only
# bounds the pathological case (e.g. an entropy-starved container stalling
# openssl's RNG read) so the operator gets a diagnostic instead of a silent
# indefinite hang -- matching the timeout convention every other subprocess
# call in this codebase already follows (sync/runner.py, agentic/gh_client.py,
# agentic/writer.py, agentic/netconnect/client.py).
_OPENSSL_TIMEOUT_SEC = 30

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


def _san_extra_ok(entry: str) -> bool:
    """Refuse values that would split or inject the openssl -addext SAN list."""
    if not entry or any(ch in entry for ch in (",", "\n", "\r", "\x00")):
        return False
    return entry.startswith("DNS:") or entry.startswith("IP:")


def subject_alt_names(hostname: str, extra: list[str] | None = None) -> str:
    parts = [
        f"DNS:{hostname}",
        "DNS:localhost",
        "IP:127.0.0.1",
        "IP:0:0:0:0:0:0:0:1",
    ]
    lan = _lan_ipv4()
    if lan and lan not in {"127.0.0.1"}:
        parts.append(f"IP:{lan}")
    if extra:
        parts.extend(extra)
    return ",".join(parts)


def _anchor(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def _keyfile_location_is_safe(keyfile: Path) -> bool:
    """Allow repo-local private keys only in the ignored TLS output directory."""
    repo_root = _REPO_ROOT.resolve()
    resolved_keyfile = keyfile.resolve()
    tls_output_dir = (repo_root / "data" / "tls").resolve()
    return not resolved_keyfile.is_relative_to(repo_root) or resolved_keyfile.is_relative_to(tls_output_dir)


def _temporary_output_path(target: Path) -> Path:
    """Reserve a private same-directory path for an OpenSSL output file."""
    fd, raw_path = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(fd)
    return Path(raw_path)


def _remove_temporary_output(path: Path | None) -> None:
    """Best-effort cleanup that reports a leftover private temporary file."""
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"warning: could not remove temporary certificate output: {exc}", file=sys.stderr)


def _backup_existing_output(target: Path) -> Path | None:
    """Copy an existing output aside so a failed pair install can be rolled back."""
    if not target.exists():
        return None
    backup = _temporary_output_path(target)
    try:
        shutil.copy2(target, backup)
    except OSError:
        _remove_temporary_output(backup)
        raise
    return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyclaw-gen-cert")
    parser.add_argument("--certfile", default=_DEFAULT_CERT)
    parser.add_argument("--keyfile", default=_DEFAULT_KEY)
    parser.add_argument("--hostname", default=socket.gethostname())
    parser.add_argument("--days", type=int, default=825)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing cert/key pair (otherwise refuse)",
    )
    parser.add_argument(
        "--san",
        action="append",
        default=[],
        metavar="ENTRY",
        help="extra SAN entry, e.g. IP:10.0.0.5 or DNS:box.local (repeatable)",
    )
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
    for entry in args.san:
        if not _san_extra_ok(entry):
            print(
                f"invalid --san {entry!r}: must be DNS:... or IP:... with no commas",
                file=sys.stderr,
            )
            return EXIT_ENV

    certfile = _anchor(args.certfile)
    keyfile = _anchor(args.keyfile)
    if not _keyfile_location_is_safe(keyfile):
        print(
            "refusing repository-local --keyfile outside data/tls; use an absolute path outside the repo",
            file=sys.stderr,
        )
        return EXIT_ENV
    if not args.force and (certfile.exists() or keyfile.exists()):
        print(
            f"refusing to overwrite {certfile} / {keyfile} (pass --force to replace)",
            file=sys.stderr,
        )
        return EXIT_ENV
    certfile.parent.mkdir(parents=True, exist_ok=True)
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    temp_certfile: Path | None = None
    temp_keyfile: Path | None = None
    try:
        temp_certfile = _temporary_output_path(certfile)
        temp_keyfile = _temporary_output_path(keyfile)
    except OSError as exc:
        _remove_temporary_output(temp_certfile)
        print(f"failed to prepare certificate outputs: {exc}", file=sys.stderr)
        return EXIT_FAIL
    san = subject_alt_names(args.hostname, extra=args.san)
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
        str(temp_keyfile),
        "-out",
        str(temp_certfile),
        "-subj",
        f"/CN={args.hostname}",
        "-addext",
        f"subjectAltName={san}",
    ]
    # Restrict the openssl-created key to owner-only on POSIX. Windows umask
    # is a no-op; chmod below remains best-effort against NTFS ACLs.
    previous_umask: int | None = None
    try:
        previous_umask = os.umask(0o077)
    except (AttributeError, OSError):
        previous_umask = None
    try:
        try:
            completed = subprocess.run(  # noqa: S603 - argv list; openssl from which/fixed path
                cmd, check=False, capture_output=True, text=True, timeout=_OPENSSL_TIMEOUT_SEC,
            )
        except subprocess.TimeoutExpired as exc:
            print(f"openssl req timed out after {_OPENSSL_TIMEOUT_SEC}s: {exc}", file=sys.stderr)
            return EXIT_FAIL
        except OSError as exc:
            print(f"failed to run openssl: {exc}", file=sys.stderr)
            return EXIT_FAIL
        finally:
            if previous_umask is not None:
                os.umask(previous_umask)
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or "").strip()
            print(err or "openssl req failed", file=sys.stderr)
            return EXIT_FAIL
        previous_certfile: Path | None = None
        previous_keyfile: Path | None = None
        cert_installed = False
        try:
            previous_certfile = _backup_existing_output(certfile)
            previous_keyfile = _backup_existing_output(keyfile)
            os.replace(temp_certfile, certfile)
            cert_installed = True
            os.replace(temp_keyfile, keyfile)
        except OSError as exc:
            if cert_installed:
                try:
                    if previous_certfile is None:
                        certfile.unlink()
                    else:
                        os.replace(previous_certfile, certfile)
                except OSError as rollback_exc:
                    print(
                        f"failed to restore previous certificate after install failure: {rollback_exc}",
                        file=sys.stderr,
                    )
            print(f"failed to install certificate pair: {exc}", file=sys.stderr)
            return EXIT_FAIL
        finally:
            _remove_temporary_output(previous_certfile)
            _remove_temporary_output(previous_keyfile)
    finally:
        _remove_temporary_output(temp_certfile)
        _remove_temporary_output(temp_keyfile)
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
