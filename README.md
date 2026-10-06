# BHTTP/1: HTTP, in binary

**Name:** Rohan Ranjan  
**Roll No.:** 24BCS10428

A binary version of HTTP over one long-lived TCP connection: a server
(`bserve`, track 1), a client (`bcurl`, track 2), and the spec they both
follow. Python 3 standard library only, with no dependencies.

Both tracks are submitted. Each one works on its own against any server
or client that follows SPEC.md.

| file | what it is |
|---|---|
| [SPEC.md](SPEC.md) | the protocol: frame header, frame types, header encoding, errors |
| [HEXDUMP.md](HEXDUMP.md) | one complete request and response, annotated byte by byte |
| `bserve` | track 1: serves files from a folder over BHTTP/1 |
| `bcurl` | track 2: fetches over BHTTP/1, body to stdout |
| `tests.py` | 26 tests, most built from raw bytes out of the spec |
| `www/` | sample files: an HTML page, a text file, a PNG |

```sh
./bserve ./www 9000                          # server
./bcurl localhost:9000/hello.txt             # body to stdout
./bcurl -v localhost:9000/index.html         # hexdump every frame to stderr
./bcurl localhost:9000/logo.png > logo.png   # binary-safe
./bcurl localhost:9000/a localhost:9000/b    # several requests, still one connection
python3 tests.py                             # starts its own server
```

## What the project asks for, and where it is

| requirement | where |
|---|---|
| fixed-size frame header, widths defended (vs HTTP/2's 24/8/8/31) | SPEC.md §2 |
| the ten header names numbered, the rest length-prefixed | SPEC.md §4 |
| unknown frame types MUST be skipped cleanly | SPEC.md §3, tested both ways |
| server: map path to a file, 404, 400 on a malformed frame, keep the connection open | `bserve` |
| client: binary request frame, body to stdout, `-v` hexdumps every frame, non-zero exit on 4xx/5xx | `bcurl` |
| client never opens a second connection | `bcurl`, tested with a counting proxy |
| binary files survive | PNG and 300 KiB random-bytes tests |
| the spec, two pages | SPEC.md |
| annotated hexdump of one request and response | HEXDUMP.md |
| optional: retries with exponential backoff, jitter and a max count (failures lecture) | SPEC.md §5, `bcurl --retry` |

## The protocol in one screen

Both sides send the 8-byte preface `BHTTP/1\n`, then frames. Every frame
has an 8-byte header:

```
| Length 24 | Type 8 | Flags 8 | Request ID 24 |  payload (Length bytes)
```

- **Types:** `0x01` HEADERS, `0x02` DATA, `0x03` GOAWAY. **Any other type
  is skipped** by reading and discarding Length bytes. That rule is what
  leaves room for a version 2.
- **Flags:** `0x01` END marks the last frame of a message.
- **Request ID:** lets responses come back in any order. Pipelining is part
  of the protocol, not an extra.
- **Headers:** the ten names peers actually send are numbered 1–10 and cost
  one byte each. Any other name is sent as a length-prefixed literal. Values
  are always length-prefixed.

HTTP/2 uses 24/8/8/31. We keep the 24-bit length, 8-bit type and 8-bit
flags, and cut the ID to 24 bits because IDs are reused once a request is
done. That makes the header exactly 8 bytes. SPEC.md section 2 defends
each width.

## bserve (track 1)

`./bserve ROOT PORT [--idle-timeout 30] [--frame-timeout 10] [--grease] [--fail-first N]`

- Maps the path to a file under `ROOT`. `/` and directories map to
  `index.html`. Paths that escape the root get `404`.
- Keeps the connection open for any number of requests and handles many
  connections at once, one thread each.
- A malformed header block gets `400` **on that request ID, and the
  connection stays open**, because the frame length already said where the
  next frame starts.
- Other status codes: `404` missing file, `405` for POST/PUT/DELETE,
  `501` unknown method code, `431` header block over 16 KiB, `413` body
  over 1 MiB.
- Connection-level problems get a GOAWAY frame with a reason, then a
  close: a frame over 1 MiB, HEADERS on ID 0, a half-sent frame after 10 s,
  or 30 s idle.
- A text HTTP client that connects by mistake gets a readable plain-text
  `400`.
- Files are streamed in 16 KiB DATA frames, never loaded whole.
- `--grease` sends an unknown frame type before every response, to prove
  clients skip it.
- `--fail-first N` answers the first N requests with `503`, to exercise
  client retries.

## bcurl (track 2)

`./bcurl [-v] [-I] [-X METHOD] [-H 'name: value'] [--retry N] [--grease] URL [URL ...]`

- URL is `host:port/path` or `bhttp://host:port/path`. The port defaults
  to 9000.
- Sends every request on **one** connection, pipelined. It refuses URLs
  for different hosts rather than open a second connection.
- Writes bodies to stdout in the order the URLs were given, even if the
  answers arrive out of order.
- `-v` hexdumps every frame, sent and received, with the decoded headers,
  to stderr. Frames over 256 bytes are cut short unless you add
  `--dump-all`.
- Exit codes: `0` success, `4` any 4xx, `5` any 5xx, `2` protocol or
  connection error (including talking to a server that isn't BHTTP/1),
  `1` bad arguments.
- Checks the server's preface and `content-length`, and skips unknown
  frame types.
- **Retries** (default 3, `--retry 0` turns them off):
  - A failed connect is retried, and so is a `500`/`502`/`503`/`504`
    answer to GET or HEAD.
  - Each wait is a random time between half of and all of
    `min(8 s, 0.2 s × 2^attempt)`. A `retry-after` header is honoured.
  - `4xx` answers and POST are never retried.
  - A retry goes on the **same** connection with a new request ID. If the
    connection itself dies, bcurl exits `2` rather than open a second one.

  ```sh
  ./bserve ./www 9000 --fail-first 2 &
  ./bcurl -v localhost:9000/hello.txt     # 503, 503, then 200 on one connection
  ```

## Testing against a partner

The only thing that should cross between a client and a server is
SPEC.md. `tests.py` does not import `bserve` or `bcurl`. It builds frames
from the spec by hand, including the exact bytes in SPEC.md section 7, so
the tests check the protocol rather than just agreeing with my own code.
To check a partner's work, run their server under `bcurl -v`, or run
`bserve` under their client.
