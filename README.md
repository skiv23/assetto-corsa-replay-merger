# acreplay_merge

Merge two or more Assetto Corsa `.acreplay` files into a single continuous replay.

Each source replay contributes one run. Runs play back-to-back with a single frame-cut between them — the car teleports to the next run's starting position. This is useful for reviewing multiple drift runs in sequence without switching files.

---

## Requirements

Python 3.10+ — no third-party dependencies.

---

## Usage

```bash
python acreplay_merge.py <output.acreplay> \
    <replay1.acreplay> <car_index> \
    <replay2.acreplay> <car_index> \
    [<replay3.acreplay> <car_index> ...]
```

`car_index` is the 0-based index of the car to extract from each file. A single-car replay always uses index `0`. Multi-car session files may contain several cars — use the index to pick which one.

### Examples

```bash
# Two replays, one car each
python acreplay_merge.py merged.acreplay run1.acreplay 0 run2.acreplay 0

# Three replays
python acreplay_merge.py merged.acreplay run1.acreplay 0 run2.acreplay 0 run3.acreplay 0

# Two cars from the same session file
python acreplay_merge.py merged.acreplay session.acreplay 0 session.acreplay 1
```

### Sample output

```
Loading 3 replays …

  bondar3.acreplay        car[0]  gravygarage_street_e46 | 'Sergey Bondar' | 969 frames @ 49391µs
  khasinevich3.acreplay   car[0]  gravygarage_street_e46 | 'Simway PRO'    | 2200 frames @ 52198µs
  khasinevich3.acreplay   car[1]  gravygarage_street_e46 | 'skiv23'        | 2200 frames @ 52198µs

Reference: 0.3930 m/frame  prelude=49391µs
  Run 1: 380.9m / 969 frames  →  969 frames  0.3930 m/frame  (reference)
  Run 2: 406.7m / 2200 frames → 1034 frames  0.3929 m/frame  subsampled 2200→1034
  Run 3: 371.1m / 2200 frames →  944 frames  0.3926 m/frame  subsampled 2200→944

Total: 2947 frames  145.6s (2.4 min)

Wrote: merged.acreplay  (0.84 MB)
  Run 1: frames 0–968     t=0.0s–47.9s
  Run 2: frames 969–2002  t=47.9s–98.9s
  Run 3: frames 2003–2946 t=98.9s–145.6s
```

---

## How it works

### File format (reverse engineered)

The `.acreplay` format is undocumented. The structure discovered through binary analysis:

```
[Global header]
  uint32   version
  uint32   unknown
  float32  unknown
  string   weather      (uint32 length prefix + UTF-8 bytes)
  string   track
  string   layout
  uint32   num_cars
  uint32   unknown
  uint32   num_frame_intervals
  uint32[] frame_interval_table   (microseconds per tick)

[Car block, repeated num_cars times]
  uint32   prelude      (avg frame interval — controls playback speed)
  string   car_model
  string   driver_name
  string   nation
  uint32   unknown
  string   color
  uint32   v1           (frame count — controls playback length)
  uint32   num_wheels
  byte[16] padding
  byte[]   frame_data   (v1 × 292 bytes)

[Config trailer]
  INI text starting with [BENCHMARK]
```

Each **frame record** is exactly 292 bytes:

| Offset | Size | Type      | Field                          |
|--------|------|-----------|-------------------------------|
| +0     | 4    | int32     | Frame flags                   |
| +4     | 12   | 3×float32 | Body position XYZ (metres)    |
| +16    | 2    | int16     | Heading sin (÷32767)          |
| +18    | 2    | int16     | Heading cos (÷32767)          |
| +20    | 4    | 2×int16   | Unknown                       |
| +24    | 48   | 4×3×float32 | Wheel hub positions XYZ     |
| +72    | 24   | 6×int16   | Compressed rotation matrix    |
| +96    | 48   | 4×3×float32 | Wheel contact positions XYZ |
| +144   | 104  | mixed     | Physics data (partial)        |
| +248   | 1    | uint8     | Throttle input [0–255]        |
| +249   | 3    | —         | Padding                       |
| +252   | 4    | uint32    | Handbrake (0=off, 1024=on)    |
| +256   | 36   | —         | Unknown (gear/RPM candidate)  |

Heading is decoded as `atan2(sin/32767, cos/32767)`. Steering angle, brake pedal, and engine RPM were not conclusively identified.

### Merge process

1. **Parse** each source file — extract the header, car metadata, raw frame bytes, and interval table.
2. **Reference run** — run 1 sets the target metres-per-frame density and the prelude interval used for the whole merged file.
3. **Subsample** each subsequent run so its frames contain the same metres-of-movement per frame as run 1. This normalises playback speed across runs recorded at different capture rates.
4. **Build interval table** — `[0, prelude, prelude, …]`, one leading zero followed by the constant prelude for every remaining frame.
5. **Write** — one car block with updated `v1` (total frame count) and concatenated frame bytes.

### Key fields in the merged output

Three values are kept in sync and all set to `total_frames`:

- `num_frame_intervals` in the global header
- `v1` in the car metadata block
- actual byte length of the frame data (`total_frames × 292`)

---

## Known limitations

- **Frame cut** — the car teleports between runs. There is no position interpolation at the seam.
- **Metadata from run 1** — the merged replay carries the car model, driver name, and color from the first source. These are cosmetic in AC's replay viewer.
- **Steering / brake / RPM** — these fields were not fully decoded in the format analysis and are carried through unchanged from each source frame.
- **Single car output** — the merger always produces a one-car replay regardless of how many cars were in the source session files.

---

## Background

This tool was built as part of a reverse engineering investigation into the `.acreplay` binary format. No official documentation exists. The format was decoded entirely through hex analysis, pattern matching, and behavioral verification against Assetto Corsa's replay player.
