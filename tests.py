#!/usr/bin/env python3
import os, random, shutil, socket, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
BSERVE, BCURL = os.path.join(HERE, "bserve"), os.path.join(HERE, "bcurl")
PREFACE = bytes.fromhex("42 48 54 54 50 2F 31 0A")
TESTS = []
PORT = ROOT = None


def test(name):
    def register(fn):
        TESTS.append((name, fn))
        return fn
    return register


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def start(root, port, *extra):
    proc = subprocess.Popen([sys.executable, BSERVE, root, str(port), "--host", "127.0.0.1",
                             *extra], stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    sys.exit("bserve did not start")


def frame(ftype, flags, rid, payload=b""):
    return len(payload).to_bytes(3, "big") + bytes([ftype, flags]) + rid.to_bytes(3, "big") + payload


def get(rid, path, method=1, fields=b"\x01\x00\x01h", end=True):
    p = path.encode()
    return frame(1, 1 if end else 0, rid, bytes([method]) + len(p).to_bytes(2, "big") + p + fields)


class Peer:
    def __init__(self, port=None):
        self.s = socket.create_connection(("127.0.0.1", port or PORT))
        self.s.settimeout(5)
        self.buf = bytearray()

    def send(self, data):
        self.s.sendall(data)

    def read(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d:
                raise EOFError("server closed")
            self.buf += d
        out = bytes(self.buf[:n]); del self.buf[:n]
        return out

    def frame(self):
        h = self.read(8)
        n = int.from_bytes(h[:3], "big")
        return h[3], h[4], int.from_bytes(h[5:8], "big"), self.read(n)

    def response(self):
        ftype, flags, rid, p = self.frame()
        assert ftype == 1, f"expected HEADERS, got type {ftype}"
        status, body = int.from_bytes(p[:2], "big"), bytearray()
        while not flags & 1:
            ftype, flags, rid2, d = self.frame()
            assert ftype == 2 and rid2 == rid
            body += d
        return rid, status, bytes(body)

    def eof(self):
        try:
            while True:
                d = self.s.recv(65536)
                if not d:
                    return True
                self.buf += d
        except (socket.timeout, ConnectionResetError):
            return False

    def still_open(self):
        self.s.setblocking(False)
        try:
            return self.s.recv(1, socket.MSG_PEEK) != b""
        except BlockingIOError:
            return True
        finally:
            self.s.setblocking(True)


def hello():
    p = Peer(); p.send(PREFACE); assert p.read(8) == PREFACE
    return p


def bcurl(*args, port=None):
    r = subprocess.run([sys.executable, BCURL, *args], capture_output=True, timeout=20)
    return r.returncode, r.stdout, r.stderr.decode()


def url(path, port=None):
    return f"127.0.0.1:{port or PORT}{path}"


@test("SPEC.md section 7 bytes, sent verbatim, get the documented answer")
def _():
    p = Peer()
    p.send(bytes.fromhex("42 48 54 54 50 2F 31 0A  00 00 0A 01 01 00 00 01  01 00 03 2F 68 69  01 00 01 68"))
    assert p.read(8) == PREFACE
    ftype, flags, rid, block = p.frame()
    assert (ftype, flags, rid) == (1, 0, 1) and block[:2] == b"\x00\xc8"
    assert b"\x05\x00\x01\x32" in block, "content-length should be field #5 = '2'"
    assert p.frame() == (2, 1, 1, b"ok")


@test("bcurl fetches a text file, body to stdout, exit 0")
def _():
    code, out, _ = bcurl(url("/hello.txt"))
    assert code == 0 and out == b"hello over binary http\n", (code, out)


@test("bcurl fetches a 300 KiB random binary file byte-for-byte (many DATA frames)")
def _():
    code, out, _ = bcurl(url("/blob.bin"))
    assert code == 0 and out == open(os.path.join(ROOT, "blob.bin"), "rb").read()


@test("404 -> exit 4 with the error text; / maps to index.html")
def _():
    code, out, err = bcurl(url("/missing"))
    assert code == 4 and b"not found" in out, (code, out)
    code, out, _ = bcurl(url("/"))
    assert code == 0 and b"<h1>" in out


@test("bcurl -v hexdumps every frame, in both directions")
def _():
    code, _, err = bcurl("-v", url("/hi"))
    for marker in ["> PREFACE", "> HEADERS id=1", "< PREFACE", "< HEADERS id=1", "< DATA id=1",
                   "00000000  00 00"]:
        assert marker in err, marker


@test("bcurl with 4 URLs opens exactly ONE TCP connection")
def _():
    lst = socket.socket(); lst.bind(("127.0.0.1", 0)); lst.listen(8)
    pport, accepts = lst.getsockname()[1], []

    def proxy():
        while True:
            try:
                c, _ = lst.accept()
            except OSError:
                return
            accepts.append(c)
            u = socket.create_connection(("127.0.0.1", PORT))
            for a, b in ((c, u), (u, c)):
                threading.Thread(target=pipe, args=(a, b), daemon=True).start()

    def pipe(a, b):
        try:
            while (d := a.recv(65536)):
                b.sendall(d)
            b.shutdown(socket.SHUT_WR)
        except OSError:
            pass
    threading.Thread(target=proxy, daemon=True).start()
    code, out, _ = bcurl(*[url(p, pport) for p in ["/hi", "/hello.txt", "/missing", "/hi"]])
    lst.close()
    assert len(accepts) == 1, f"{len(accepts)} connections"
    assert out.startswith(b"ok" + b"hello over binary http\n") and out.endswith(b"ok")
    assert code == 4


@test("pipelining: 5 requests in one write, answers matched by id, socket stays open")
def _():
    p = hello()
    p.send(b"".join(get(rid, "/hi") for rid in (7, 3, 9, 1, 500000)))
    got = {p.response()[0] for _ in range(5)}
    assert got == {7, 3, 9, 1, 500000} and p.still_open()


@test("unknown frame types from the client are skipped cleanly")
def _():
    p = hello()
    p.send(frame(0x42, 0, 1, b"from the future") + frame(0xFF, 0xFF, 0, b"")
           + frame(0x7A, 0, 0, bytes(5000)) + get(1, "/hi"))
    assert p.response() == (1, 200, b"ok") and p.still_open()


@test("bcurl skips unknown frame types from a --grease server")
def _():
    port = free_port()
    proc = start(ROOT, port, "--grease")
    try:
        code, out, err = bcurl("-v", url("/hello.txt", port), url("/hi", port))
        assert code == 0 and out == b"hello over binary http\nok", (code, out)
        assert "UNKNOWN(0x7A)" in err and "skipped" in err
    finally:
        proc.terminate()


@test("malformed header blocks -> 400 on that id, connection survives")
def _():
    p = hello()
    bad = [frame(1, 1, 1, b"\x01\x00\x09/hi"),
           frame(1, 1, 2, b"\x01\x00\x03/hi\xEE\x00\x00"),
           frame(1, 1, 3, b"\x01\x00\x03/hi\x00\x00\x00\x00"),
           frame(1, 1, 4, b"\x01\x00\x02hi"),
           frame(1, 1, 5, b"\x01\x00\x03/\xff\xfe"),
           frame(1, 1, 6, b"\x01")]
    p.send(b"".join(bad) + get(7, "/hi"))
    for rid in range(1, 7):
        r = p.response()
        assert r[:2] == (rid, 400), r
    assert p.response() == (7, 200, b"ok")


@test("methods: HEAD has headers only, POST -> 405, unknown code -> 501")
def _():
    p = hello()
    p.send(get(1, "/hello.txt", method=2))
    ftype, flags, rid, block = p.frame()
    assert ftype == 1 and flags & 1 and b"\x05\x00\x0223" in block
    p.send(get(2, "/hi", method=3) + get(3, "/hi", method=99) + get(4, "/hi"))
    assert p.response()[1] == 405 and p.response()[1] == 501 and p.response()[1] == 200


@test("request with a body: DATA frames consumed exactly, then answered")
def _():
    p = hello()
    p.send(get(1, "/hi", end=False) + frame(2, 0, 1, frame(1, 1, 9, b"looks like a frame"))
           + frame(2, 1, 1, b"tail") + get(2, "/hello.txt"))
    assert p.response() == (1, 200, b"ok")
    assert p.response()[:2] == (2, 200)


@test("DATA for an id that is not open is discarded")
def _():
    p = hello()
    p.send(frame(2, 1, 77, b"stray") + get(1, "/hi"))
    assert p.response() == (1, 200, b"ok")


@test("paths outside the root -> 404")
def _():
    p = hello()
    p.send(get(1, "/../../../../etc/passwd") + get(2, "/%2e%2e/secret") + get(3, "/sub/../hi"))
    assert p.response()[1] == 404 and p.response()[1] == 404 and p.response()[1] == 200


@test("text HTTP client gets a plain-text 400 and a close")
def _():
    p = Peer()
    p.send(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert p.read(12) == b"HTTP/1.1 400" and p.eof()


@test("HEADERS on id 0 -> GOAWAY PROTOCOL_ERROR, close")
def _():
    p = hello()
    p.send(get(0, "/hi"))
    ftype, _, rid, pl = p.frame()
    assert (ftype, rid, pl[:2]) == (3, 0, b"\x00\x01") and p.eof()


@test("frame length over 1 MiB -> GOAWAY FRAME_TOO_LARGE, close")
def _():
    p = hello()
    p.send(b"\xff\xff\xff\x01\x01\x00\x00\x01")
    ftype, _, _, pl = p.frame()
    assert ftype == 3 and pl[:2] == b"\x00\x02" and p.eof()


@test("connection: close -> answer, GOAWAY NO_ERROR, close")
def _():
    p = hello()
    p.send(get(1, "/hi", fields=b"\x01\x00\x01h\x0a\x00\x05close"))
    assert p.response() == (1, 200, b"ok")
    ftype, _, _, pl = p.frame()
    assert ftype == 3 and pl[:2] == b"\x00\x00" and p.eof()


@test("idle timeout -> GOAWAY NO_ERROR, close; half a frame -> GOAWAY TIMEOUT")
def _():
    port = free_port()
    proc = start(ROOT, port, "--idle-timeout", "1", "--frame-timeout", "1")
    try:
        p = Peer(port); p.send(PREFACE + get(1, "/hi")); p.read(8); p.response()
        time.sleep(0.5); assert p.still_open()
        ftype, _, _, pl = p.frame()
        assert ftype == 3 and pl[:2] == b"\x00\x00" and p.eof()
        p = Peer(port); p.send(PREFACE + b"\x00\x00"); p.read(8)
        ftype, _, _, pl = p.frame()
        assert ftype == 3 and pl[:2] == b"\x00\x03" and p.eof()
    finally:
        proc.terminate()


@test("bcurl against a text HTTP server: clear error, exit 2")
def _():
    lst = socket.socket(); lst.bind(("127.0.0.1", 0)); lst.listen(1)

    def serve():
        c, _ = lst.accept(); c.recv(1024)
        c.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"); c.close()
    threading.Thread(target=serve, daemon=True).start()
    code, _, err = bcurl(url("/", lst.getsockname()[1]))
    lst.close()
    assert code == 2 and "not a BHTTP/1 server" in err, (code, err)


@test("20 clients at once, each on its own connection")
def _():
    peers = [hello() for _ in range(20)]
    for i, p in enumerate(peers):
        p.send(get(i + 1, "/hi"))
    for i, p in enumerate(peers):
        assert p.response() == (i + 1, 200, b"ok")



def flaky(n, *extra):
    port = free_port()
    return port, start(ROOT, port, "--fail-first", str(n), *extra)


@test("retry: 503 twice, then 200 - same connection, new request ids")
def _():
    port, proc = flaky(2)
    try:
        code, out, err = bcurl("-v", url("/hello.txt", port))
        assert code == 0 and out == b"hello over binary http\n", (code, out)
        assert err.count("on the same connection") == 2 and "after 2 retries" in err
        assert "> HEADERS id=3" in err and "1 response(s) on 1 connection" in err
    finally:
        proc.terminate()


@test("retry: backoff doubles from 0.2s with jitter, gives up after --retry N")
def _():
    import re
    port, proc = flaky(100)
    try:
        code, out, err = bcurl("-v", "--retry", "4", url("/hi", port))
        delays = [float(d) for d in re.findall(r"in ([\d.]+)s on the same", err)]
        assert len(delays) == 4, delays
        for k, d in enumerate(delays):
            cap = min(8.0, 0.2 * 2 ** k)
            assert cap / 2 <= d <= cap, (k, d)
        assert code == 5 and b"warming up" in out, (code, out)
    finally:
        proc.terminate()


@test("retry: never for 4xx, never for POST, off with --retry 0")
def _():
    code, _, err = bcurl("-v", url("/missing"))
    assert code == 4 and "retry" not in err
    port, proc = flaky(100)
    try:
        code, _, err = bcurl("-v", "-X", "POST", url("/hi", port))
        assert code == 5 and "retry" not in err, err
        code, _, err = bcurl("-v", "--retry", "0", url("/hi", port))
        assert code == 5 and "retry" not in err
    finally:
        proc.terminate()


@test("retry: output stays in URL order when the first request is retried")
def _():
    port, proc = flaky(1)
    try:
        code, out, _ = bcurl(url("/hello.txt", port), url("/hi", port), url("/hi", port))
        assert code == 0 and out == b"hello over binary http\nokok", out
    finally:
        proc.terminate()


@test("retry: server not up yet - connect is retried with backoff")
def _():
    port = free_port()
    c = subprocess.Popen([sys.executable, BCURL, "-v", "--retry", "5", url("/hi", port)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(0.3)
    proc = start(ROOT, port)
    try:
        out, err = c.communicate(timeout=20)
        assert c.returncode == 0 and out == b"ok", (c.returncode, out, err)
        assert b"connect failed" in err
    finally:
        proc.terminate()

if __name__ == "__main__":
    ROOT = tempfile.mkdtemp()
    try:
        shutil.copytree(os.path.join(HERE, "www"), ROOT, dirs_exist_ok=True)
        open(os.path.join(ROOT, "hi"), "wb").write(b"ok")
        open(os.path.join(ROOT, "blob.bin"), "wb").write(random.randbytes(300 * 1024))
        os.makedirs(os.path.join(ROOT, "sub"), exist_ok=True)
        PORT = free_port()
        proc = start(ROOT, PORT)
        failed = 0
        try:
            for name, fn in TESTS:
                try:
                    fn()
                    print(f"  PASS  {name}")
                except Exception as e:
                    failed += 1
                    print(f"  FAIL  {name}\n        {type(e).__name__}: {e}")
        finally:
            proc.terminate()
        print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
        sys.exit(1 if failed else 0)
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
