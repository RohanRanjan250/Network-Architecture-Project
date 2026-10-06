# BHTTP/1: HTTP in binary

**Author:** Rohan Ranjan (24BCS10428) · **Status:** version 1

BHTTP/1 carries HTTP/1.1 meaning (methods, paths, status codes, headers)
in length-prefixed binary frames over one long-lived TCP connection. A
receiver never searches for delimiters. Every frame says how long it is
before it says anything else.

All integers are unsigned and **big-endian** (network byte order). "u8",
"u16" and "u24" mean 1-, 2- and 3-byte integers.

## 1. Connection

1. The client opens one TCP connection and immediately sends the 8-byte
   **preface** `42 48 54 54 50 2F 31 0A` (ASCII `BHTTP/1\n`). It may send
   request frames right behind it without waiting.
2. The server reads 8 bytes. If they are the preface, it replies with the
   same 8 bytes and then frames. If not, it closes the connection. It MAY
   first write a plain-text HTTP/1.1 `400` response, since the peer is most
   likely a text HTTP client.
3. The client MUST check the server's preface before trusting any frame.
4. The connection stays open for any number of requests. The client ends it
   by closing TCP, or by sending the `connection: close` header on a
   request. In that case the server answers, sends GOAWAY, and closes.

A future version 2 uses a different preface (`BHTTP/2\n`). A version 1
server closes on it, which tells a version 2 client to fall back.

## 2. Frame header (8 bytes)

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-----------------------------------------------+---------------+
|                 Length (24)                   |   Type (8)    |
+---------------+-------------------------------+---------------+
|   Flags (8)   |               Request ID (24)                 |
+---------------+-----------------------------------------------+
|                    Payload (Length bytes) ...
```

| field | width | meaning |
|---|---|---|
| Length | 24 | payload bytes after this 8-byte header (0 to 16 777 215) |
| Type | 8 | `0x01` HEADERS, `0x02` DATA, `0x03` GOAWAY; everything else is unknown |
| Flags | 8 | `0x01` END: last frame of this message. Other bits: send 0, ignore on receipt |
| Request ID | 24 | which request this frame belongs to. `0` means the connection itself |

**Why these widths.** HTTP/2 uses 24/8/8/31 (plus 1 reserved bit, 9 bytes).
We keep its 24-bit length, 8-bit type and 8-bit flags, and cut the ID to 24
bits, which makes the header exactly 8 bytes.

- **Length comes first,** so any receiver can find the next frame without
  understanding this one. That is the whole mechanism behind the MUST-skip
  rule in section 3.
- **24 bits of length** allows 16 MiB frames. That is room to grow, while
  section 6 keeps real frames small, so one big file is sent as many DATA
  frames and never forces a huge buffer on the receiver.
- **8 bits of type and 8 of flags** give 253 unused types and 7 unused
  flags, enough for many versions.
- **24 bits of ID** is plenty because IDs may be reused once a request is
  finished (section 5). HTTP/2 needs 31 bits because it never reuses stream
  IDs and splits them into odd and even for server push. We need neither.

## 3. Frame types

**HEADERS (0x01).** Starts a message. The payload is a header block
(section 4). The whole block MUST fit in one frame. END set means the
message has no body.

**DATA (0x02).** Body bytes. Zero or more DATA frames follow HEADERS, and
the last frame of the message carries END. A zero-length DATA frame with
END is legal.

**GOAWAY (0x03).** ID 0. Payload: `code u16`, then an optional UTF-8
reason. The sender will close the connection next. Codes: `0` NO_ERROR,
`1` PROTOCOL_ERROR, `2` FRAME_TOO_LARGE, `3` TIMEOUT, `4` INTERNAL_ERROR.

**Unknown types.** A receiver that meets a frame type it does not know
**MUST read and discard exactly Length payload bytes and carry on.** It
MUST NOT close the connection or fail the request. This is what lets
version 2 add frame types without breaking version 1 peers.

## 4. Header block

A request block and a response block differ only in their first fields:

```
request:   method u8 | path_len u16 | path (UTF-8) | field*
response:  status u16                              | field*
```

Methods: `1` GET, `2` HEAD, `3` POST, `4` PUT, `5` DELETE, `6` OPTIONS.
The path is raw UTF-8 and starts with `/`, with no percent-encoding
needed. The status is the HTTP status code (`200`, `404`, ...).

Fields run until the end of the payload. Each field starts with one byte:

| first byte | then | meaning |
|---|---|---|
| `0x01`–`0x0A` | `value_len u16`, value | name taken from the static table below |
| `0x00` | `name_len u8`, name, `value_len u16`, value | literal name, lowercase ASCII, 1–255 bytes |
| `0x0B`–`0xFF` | nothing | reserved: the header block is malformed |

The static table lists the ten names BHTTP/1 peers actually send. Each one
costs 1 byte on the wire instead of its full name plus `: ` and CRLF.

| # | name | # | name |
|---|---|---|---|
| 1 | host | 6 | last-modified |
| 2 | user-agent | 7 | date |
| 3 | accept | 8 | server |
| 4 | content-type | 9 | cache-control |
| 5 | content-length | 10 | connection |

Values are UTF-8 text with HTTP/1.1 meaning (`content-length` is decimal
ASCII, dates are HTTP-dates). A header block is **malformed** if it ends in
the middle of a field, uses a reserved first byte, has a zero-length name,
has a path that does not start with `/`, or has invalid UTF-8.

## 5. Requests and responses

- A client picks a Request ID from 1 to 16 777 215 for each request. It
  MUST NOT reuse an ID until it has received the END of that ID's response.
- A request is HEADERS (with END if there is no body), optionally followed
  by DATA frames, the last one with END. The server answers when it has
  seen END.
- A response is HEADERS carrying the same ID, then DATA frames, the last
  one with END. A response to HEAD, or one with no body, is HEADERS with
  END. A server SHOULD send `content-length`, and a client SHOULD check it.
- **Pipelining is built in.** A client may send many requests without
  waiting. A server may answer them in any order, and may interleave frames
  of different responses. The client matches every frame by its ID, never
  by position.
- A receiver MUST discard DATA frames for an ID that is not open, for
  example the rest of a request the server already rejected.
- **Retries.** A client MAY retry a request that got `500`, `502`, `503` or
  `504`, or a connect attempt that failed.
  - It MUST NOT retry a `4xx`, because the request itself was wrong, or a
    POST, because it might not be safe to repeat.
  - A retry is a new request with a new ID **on the same connection**. A
    client never opens a second connection to retry.
  - Before attempt *n*, it waits a random time between half of and all of
    `min(8 s, 0.2 s × 2^n)`. The cap and the jitter stop thousands of
    clients from retrying in step. A `retry-after: <seconds>` header
    replaces the computed wait, still capped at 8 s.
  - It gives up after a fixed number of retries (bcurl: 3).

## 6. Errors and limits

| situation | receiver does | connection |
|---|---|---|
| unknown frame type | skip Length bytes | stays open |
| malformed header block on ID ≠ 0 | respond `400` on that ID | stays open |
| unknown method code | respond `501` | stays open |
| known method the server does not serve | respond `405` | stays open |
| path not found, or outside the root | respond `404` | stays open |
| header block > 16 KiB | respond `431` | stays open |
| request body > 1 MiB | respond `413` | stays open |
| server temporarily unable to answer | respond `503` (client may retry, §5) | stays open |
| HEADERS or DATA on ID 0 | GOAWAY `1`, close | closed |
| frame Length > 1 MiB | GOAWAY `2`, close | closed |
| frame started but not finished in 10 s | GOAWAY `3`, close | closed |
| no frame for the idle timeout (server default 30 s) | GOAWAY `0`, close | closed |

A malformed payload never costs the connection. The frame header already
said where the payload ends, so the next frame is still found correctly.
Only a broken frame header, or a sender that stops mid-frame, ends the
connection.

Senders MUST NOT send frames larger than 1 MiB and SHOULD send DATA in
16 KiB frames. Receivers MUST accept frames up to 1 MiB.

## 7. Example: `GET /hi` answered with `ok`

The smallest legal exchange. A real server usually adds `content-type`,
`date` and `server` to the response. [HEXDUMP.md](HEXDUMP.md) shows a full
capture of that.

```
C→S  42 48 54 54 50 2F 31 0A      preface "BHTTP/1\n"
C→S  00 00 0A 01 01 00 00 01      HEADERS, length 10, END, id 1
     01                           method 1 = GET
     00 03 2F 68 69               path, 3 bytes: "/hi"
     01 00 01 68                  field #1 host, 1 byte: "h"
S→C  42 48 54 54 50 2F 31 0A      preface
S→C  00 00 06 01 00 00 00 01      HEADERS, length 6, no flags, id 1
     00 C8                        status 200
     05 00 01 32                  field #5 content-length, 1 byte: "2"
S→C  00 00 02 02 01 00 00 01      DATA, length 2, END, id 1
     6F 6B                        "ok"
```

Leaving out the prefaces, which are sent once per connection, the request
is 18 bytes and the response 24, so 42 in all. The same exchange in
HTTP/1.1 text is 69 bytes, and every extra header widens the gap:
`content-type: text/html` costs 12 bytes here and 25 in text.
