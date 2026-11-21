#!/usr/bin/env python3
import argparse
import re
import socket
import sys
import threading
import time

DEFAULT_HOST = "10.24.54.1"
DEFAULT_USER = "admin"
DEFAULT_PASS = "admin"

ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
PROMPT_RE = re.compile(rb"[\w.-]+:(?:admin|user)#\s*$")
PAGER_RE = re.compile(rb"Next Page|Next Entry|CTRL\+C", re.I)
LOGIN_OK_RE = re.compile(r"[\w.-]+:(admin|user)#\s*$")


class SwitchTelnet:
    def __init__(self, host, port=23, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(0.3)

    def _recv(self, seconds=0.3):
        data = b""
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = self.sock.recv(16384)
                if not chunk:
                    break
                data += chunk
            except socket.timeout:
                pass
            except OSError:
                break
        return data

    def _read_until(self, pattern, seconds=8, auto_pager=True):
        out = b""
        pager_handled = 0
        end = time.time() + seconds
        while time.time() < end:
            chunk = self._recv(0.3)
            if chunk:
                out += chunk
                if auto_pager:
                    match = PAGER_RE.search(out[pager_handled:])
                    if match:
                        pager_handled = len(out)
                        try:
                            self.sock.sendall(b"a")
                        except OSError:
                            pass
                        continue
                found = (
                    pattern.search(out)
                    if hasattr(pattern, "search")
                    else re.search(pattern, out, re.I)
                )
                if found:
                    break
        return out

    @staticmethod
    def clean(data):
        return ANSI_RE.sub(b"", data).decode("utf-8", "replace").replace("\r", "")

    def login(self, user=DEFAULT_USER, password=DEFAULT_PASS):
        banner = self._read_until(rb"UserName:", 8)
        self.sock.sendall(user.encode() + b"\r\n")
        prompt = self._read_until(rb"PassWord:", 5)
        self.sock.sendall(password.encode() + b"\r\n")
        result = self._read_until(PROMPT_RE, 8)
        full = self.clean(banner + prompt + result)
        if not LOGIN_OK_RE.search(full):
            raise RuntimeError("Login failed. Server said:\n" + full[-500:])
        return full

    def command(self, cmd, seconds=4):
        self.sock.sendall(cmd.encode() + b"\r\n")
        return self.clean(self._read_until(PROMPT_RE, seconds))

    def interactive(self):
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                data = self._recv(0.3)
                if data:
                    sys.stdout.write(self.clean(data))
                    sys.stdout.flush()

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        print('Interactive mode. Type CLI commands, "exit" or Ctrl+D to quit.')
        try:
            while True:
                line = sys.stdin.readline()
                if not line or line.strip().lower() in ("exit", "quit"):
                    break
                self.sock.sendall(line.rstrip("\n").encode() + b"\r\n")
                time.sleep(0.3)
        finally:
            stop.set()
            thread.join(timeout=1)

    def close(self):
        self.sock.close()


def build_parser():
    parser = argparse.ArgumentParser(
        description="Telnet CLI helper for D-Link DGS-3627G / DGS-3600 series"
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="switch IP (default: %(default)s)"
    )
    parser.add_argument(
        "--port", type=int, default=23, help="telnet port (default: %(default)s)"
    )
    parser.add_argument(
        "-u", "--user", default=DEFAULT_USER, help="login (default: %(default)s)"
    )
    parser.add_argument(
        "-p",
        "--password",
        default=DEFAULT_PASS,
        help="password (default: %(default)s)",
    )
    parser.add_argument(
        "-i", "--interactive", action="store_true", help="interactive shell"
    )
    parser.add_argument("--script", help="file with commands, one per line")
    parser.add_argument("commands", nargs="*", help="commands to run")
    return parser


def read_script(path):
    with open(path, encoding="utf-8") as handle:
        return [
            line.strip()
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        ]


def main():
    args = build_parser().parse_args()

    if not args.interactive and not args.commands and not args.script:
        args.commands = ["show account"]

    try:
        switch = SwitchTelnet(args.host, port=args.port)
        switch.login(args.user, args.password)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.interactive:
        switch.interactive()
    else:
        commands = list(args.commands)
        if args.script:
            commands += read_script(args.script)
        for command in commands:
            print(f"\n>>> {command}")
            print(switch.command(command))
    switch.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
