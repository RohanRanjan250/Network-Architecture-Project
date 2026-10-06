# Annotated hexdump: `GET /hello.txt`

One complete request and response, captured with
`./bcurl -v localhost:9000/hello.txt` against `./bserve ./www 9000`. The
file holds the 23 bytes `hello over binary http\n`. Every byte below
appears in the order it went on the wire. Field references point to
[SPEC.md](SPEC.md).

## Client → server

### Preface (8 bytes, once per connection)

```
42 48 54 54 50 2f 31 0a        "BHTTP/1\n"
```

### Request: HEADERS frame (8 + 48 = 56 bytes)

```
00000000  00 00 30 01 01 00 00 01  01 00 0a 2f 68 65 6c 6c  |..0......../hell|
00000010  6f 2e 74 78 74 01 00 0e  6c 6f 63 61 6c 68 6f 73  |o.txt...localhos|
00000020  74 3a 39 30 30 30 02 00  09 62 63 75 72 6c 2f 31  |t:9000...bcurl/1|
00000030  2e 30 03 00 03 2a 2f 2a                           |.0...*/*|
```

| offset | bytes | meaning |
|---|---|---|
| 00–02 | `00 00 30` | **Length** = 48 bytes of payload |
| 03 | `01` | **Type** = HEADERS |
| 04 | `01` | **Flags** = END: no body follows |
| 05–07 | `00 00 01` | **Request ID** = 1 |
| 08 | `01` | method 1 = GET |
| 09–0A | `00 0a` | path length = 10 |
| 0B–14 | `2f 68 … 74` | path `/hello.txt` |
| 15 | `01` | field, static #1 = `host` |
| 16–17 | `00 0e` | value length = 14 |
| 18–25 | `6c 6f … 30` | `localhost:9000` |
| 26 | `02` | field, static #2 = `user-agent` |
| 27–28 | `00 09` | value length = 9 |
| 29–31 | `62 63 … 30` | `bcurl/1.0` |
| 32 | `03` | field, static #3 = `accept` |
| 33–34 | `00 03` | value length = 3 |
| 35–37 | `2a 2f 2a` | `*/*` |

Check: 1 + 2 + 10 + (3 + 14) + (3 + 9) + (3 + 3) = 48, the Length field.

## Server → client

### Preface (8 bytes)

```
42 48 54 54 50 2f 31 0a        "BHTTP/1\n"
```

### Response: HEADERS frame (8 + 125 = 133 bytes)

```
00000000  00 00 7d 01 00 00 00 01  00 c8 04 00 19 74 65 78  |..}..........tex|
00000010  74 2f 70 6c 61 69 6e 3b  20 63 68 61 72 73 65 74  |t/plain; charset|
00000020  3d 75 74 66 2d 38 05 00  02 32 33 07 00 1d 46 72  |=utf-8...23...Fr|
00000030  69 2c 20 32 35 20 53 65  70 20 32 30 32 36 20 31  |i, 25 Sep 2026 1|
00000040  34 3a 34 36 3a 33 37 20  47 4d 54 08 00 0a 62 73  |4:46:37 GMT...bs|
00000050  65 72 76 65 2f 31 2e 30  06 00 1d 46 72 69 2c 20  |erve/1.0...Fri, |
00000060  32 35 20 53 65 70 20 32  30 32 36 20 31 34 3a 34  |25 Sep 2026 14:4|
00000070  35 3a 31 34 20 47 4d 54  09 00 0a 6d 61 78 2d 61  |5:14 GMT...max-a|
00000080  67 65 3d 36 30                                    |ge=60|
```

| offset | bytes | meaning |
|---|---|---|
| 00–02 | `00 00 7d` | **Length** = 125 |
| 03 | `01` | **Type** = HEADERS |
| 04 | `00` | **Flags** = none: DATA frames follow |
| 05–07 | `00 00 01` | **Request ID** = 1, the answer to the request above |
| 08–09 | `00 c8` | status = 200 |
| 0A | `04` | static #4 = `content-type` |
| 0B–0C | `00 19` | length 25 |
| 0D–25 | `74 65 … 38` | `text/plain; charset=utf-8` |
| 26 | `05` | static #5 = `content-length` |
| 27–28 | `00 02` | length 2 |
| 29–2A | `32 33` | `23` |
| 2B | `07` | static #7 = `date` |
| 2C–2D | `00 1d` | length 29 |
| 2E–4A | `46 72 … 54` | `Fri, 25 Sep 2026 14:46:37 GMT` |
| 4B | `08` | static #8 = `server` |
| 4C–4D | `00 0a` | length 10 |
| 4E–57 | `62 73 … 30` | `bserve/1.0` |
| 58 | `06` | static #6 = `last-modified` |
| 59–5A | `00 1d` | length 29 |
| 5B–77 | `46 72 … 54` | `Fri, 25 Sep 2026 14:45:14 GMT` |
| 78 | `09` | static #9 = `cache-control` |
| 79–7A | `00 0a` | length 10 |
| 7B–84 | `6d 61 … 30` | `max-age=60` |

Check: 2 + (3 + 25) + (3 + 2) + (3 + 29) + (3 + 10) + (3 + 29) + (3 + 10) = 125.

### Response: DATA frame (8 + 23 = 31 bytes)

```
00000000  00 00 17 02 01 00 00 01  68 65 6c 6c 6f 20 6f 76  |........hello ov|
00000010  65 72 20 62 69 6e 61 72  79 20 68 74 74 70 0a     |er binary http.|
```

| offset | bytes | meaning |
|---|---|---|
| 00–02 | `00 00 17` | **Length** = 23, the same as `content-length` |
| 03 | `02` | **Type** = DATA |
| 04 | `01` | **Flags** = END: the response is complete |
| 05–07 | `00 00 01` | **Request ID** = 1 |
| 08–1E | `68 65 … 0a` | `hello over binary http\n` |

The client reads exactly 8 + 23 bytes and stops. The next byte on the
connection, if any, is the header of the next frame. The connection stays
open.

## Compared with HTTP/1.1 text

| | BHTTP/1 | HTTP/1.1 text | saved |
|---|---|---|---|
| request | 56 | 85 | 34% |
| response (headers + body) | 164 | 233 | 30% |

Every header name costs 1 byte instead of its full name plus `": "` and
`"\r\n"`. The two dates are most of what remains. That is the obvious
target for version 2: send them as 4-byte timestamps. Version 2 has its own
preface (SPEC section 1), so a version 1 peer never sees the new encoding.
