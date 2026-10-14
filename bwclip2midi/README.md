# bwclip2midi — Bitwig Studio .bwclip → Standard MIDI File Converter

Converts MIDI clips exported from Bitwig Studio (`.bwclip`) into standard
MIDI files (`.mid`) that open in any DAW or sequencer.

## Usage

```bash
# Single file (writes a .mid next to the input)
python bwclip2midi.py path/to/clip.bwclip

# Explicit output path
python bwclip2midi.py clip.bwclip my.mid

# Batch-convert a whole folder
python bwclip2midi.py bwclip_sample output

# Change the MIDI resolution (default: 960 PPQ)
python bwclip2midi.py clip.bwclip --ppq 480

# Automation interpolation step in beats (default 0.125; 0 = control points only)
python bwclip2midi.py clip.bwclip --interp-step 0.25
```

On Windows you can also simply drag `.bwclip` files (or a folder of them)
onto `bwclip2midi.bat`.

No third-party dependencies — pure Python 3 standard library.

## What Gets Converted

| bwclip data | MIDI output |
|---|---|
| Notes (pitch / start / duration / velocity) | Note On / Note Off |
| Pitch bend automation (range -1..1) | Pitch Bend (14-bit, 0 → centered 8192) |
| CC automation (e.g. Expression #11, range 0..1) | Control Change (7-bit) |
| Aftertouch / pressure automation | Channel Pressure |
| BPM (document root property) | Set Tempo meta event |
| Beats per bar (default 4) | Time Signature 4/4 meta event |
| Clip name | Track Name meta event |

- Resolution is 960 ticks per quarter note by default; positions are converted
  as "1 beat = quarter note".
- Velocity is `round(velocity × 127)`, clamped to 1..127.
- Notes shorter than 1 tick are clamped to 1 tick so Note Off never precedes
  Note On.
- Automation curves are exported as a dense connected polyline: every source
  control point is emitted, and segments are sampled every `--interp-step`
  beats (default 1/48 ≈ 0.0208, i.e. every 20 ticks at 960 PPQ) so the
  imported curve reads as one continuous line in any DAW. Set
  `--interp-step 0` to emit control points only.
- Output is SMF format 0 (single track), notes and controller events merged
  in time order.

### Known Limitations

- Bitwig's Bézier curve handles are approximated as linear segments (all
  control points themselves are preserved).
- Per-note MPE expression (per-note pressure/timbre) is not converted;
  a single-track standard MIDI file cannot express per-note controllers.
- Automation targets are recognized by their Bitwig display names
  (`"Ch. N Pitch Bend"`, `"Expression (#11)"`, etc.). Device-parameter
  automation (volume envelopes etc.) is not MIDI data and is not converted.

## The .bwclip Format (Reverse-Engineering Notes)

Recovered by decompiling `Bitwig Studio\bin\bitwig.jar` (6.1.3,
document version 198).

### File Layout

```
"BtWg" + 38 ASCII hex characters
  ├─ 4 chars: file format version (3)
  ├─ 4 chars: serialization format ID (2 = binary)
  ├─ 4 chars: document version = number of registered converters (198)
  └─ 8 chars: absolute offset where the document body starts
[meta object]        name / BPM / application version etc.
[5000 spaces + \n]   reserved area (lets Bitwig rewrite the meta in place)
[document tree]      the actual clip content
```

### Binary Grammar (big-endian throughout)

- Integers are stored in the smallest type that fits: type bytes 1/2/3/4
  select int8/int16/int32/int64.
- String = `int32(length, high bit set = UTF-16)` + characters (2 bytes each
  for UTF-16, 1 byte otherwise).
- Object = `int32 class ID` + property list + `int32 0` terminator.
  A class ID of 4 means "a string class name follows" (e.g. the root
  object is named `"meta"`).
- Property = `int32 property ID` + 1 type byte + value.
  A property ID of 1 means "a string property name follows".
- Type bytes: 1 int8, 2 int16, 3 int32, 4 int64, 5 bool, 6 float, 7 double,
  8 string, 9 nested object, 10 null, 11 object ref (int32), 12 external ref
  (string), 13–17 arrays, 18 object list, 20 string→object map, 21 UUID
  (16 bytes), 22 color (4×float RGBA), 23–25 arrays, 26 relative reference.
- Object lists end with an element-kind value of 3.

### Where the Notes Live

Notes sit in "pitch lane" objects (class ID 66):

```
#238 = MIDI pitch
#543 = list of notes; each note (class ID 102) carries:
    #687 = start (beats)   #38 = duration (beats)   #239 = velocity (0..1)
```

BPM is on the document root (class ID 479), property `#5925` (double).

### Where the Automation Lives

Automation (pitch bend / CC / pressure) sits in the automation container
(class ID 4002), property `#1210` — a list of channels, each an object of
class ID 573:

```
#675.#4922 = parameter display name, e.g. "Ch. 1 Pitch Bend",
             "Ch. 1 Expression (#11)"
#11661 → class 3650 → #561 → class 3638 → #576 = region (class 3472)
    region #687 = start within the clip (beats)
    #10985 → class 3772 → #4329 (class 60) → #1166 = curve (class 110)
    curve points (class 264): #687 = position (beats, relative to region),
                              #655 = value
    pitch bend values span -1..1 (0 = centered); CC / pressure span 0..1
```

## Verification

Sample `1.bwclip` (a 16-note sixteenth-note chromatic phrase), `4.bwclip`
(4 quarter notes) and `222.bwclip` (4 notes + 83-point pitch bend + 73-point
CC#11 curves) all parse to the exact end of file with the grammar above.
Converted files were re-parsed with an independent SMF reader: notes match
one-for-one, and every controller event falls within the source curve's value
range at its position (≤ 1 LSB rounding tolerance).

## Repository Layout

- `bwclip2midi.py` — the converter (parser included, single file)
- `bwclip2midi.bat` — Windows drag-and-drop entry point
- `analysis/` — reverse-engineering artifacts (decompiled sources,
  prototype parser, verification scripts)
- `bwclip_sample/` — sample inputs
- `output/` — conversion output
