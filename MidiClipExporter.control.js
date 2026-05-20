// =============================================================
// MIDI Clip Exporter -- Bitwig JS Controller Script
//
// Usage:
//   1) Select a MIDI clip in the Arranger (cursor must be on the clip)
//   2) Open the Studio I/O Panel (top-right toolbar, or View menu)
//   3) Expand this controller -- you will see the export controls there
//   4) Click "Export to .mid"
//   5) The .mid file is saved to "Export directory" with a timestamp.
//
// Note: settings live in DocumentState, so they are saved per-project
//       (a fresh project starts with the default directory).
//
// Compatible with Bitwig 6.0.x (API 23)
// =============================================================

loadAPI(23);

host.defineController(
    /* vendor  */ "Generic",
    /* product */ "MIDI Clip Exporter",
    /* version */ "1.2",
    /* uuid    */ "0f3a6d1e-7b4a-4c9e-9c6b-0a8a7e2f1d12",
    /* author  */ "LenShang"
);

host.defineMidiPorts(0, 0);

// ---- Scan parameters ----
var GRID_W = 128;             // steps per scan window
var GRID_H = 128;             // full MIDI key range
var STEP_BEATS = 0.25;        // 1/16 note = 0.25 beat; window = 32 beats
var PPQ = 480;                // SMF PPQ
var SCAN_DELAY_MS = 250;      // ms to wait for the observer after each scroll

// ---- Runtime state ----
var cursorClip = null;
var exportDir = null;
var noteMap = {};             // continuously tracked NoteOn cells
var windowBaseStep = 0;
var observerHits = 0;
var exporting = false;

// ---- Java interop ----
var JSystem = Java.type("java.lang.System");
var JPaths = Java.type("java.nio.file.Paths");
var JFiles = Java.type("java.nio.file.Files");
var JLocalDateTime = Java.type("java.time.LocalDateTime");
var JDateTimeFormatter = Java.type("java.time.format.DateTimeFormatter");
var JSequence = Java.type("javax.sound.midi.Sequence");
var JShortMessage = Java.type("javax.sound.midi.ShortMessage");
var JMetaMessage = Java.type("javax.sound.midi.MetaMessage");
var JMidiEvent = Java.type("javax.sound.midi.MidiEvent");
var JMidiSystem = Java.type("javax.sound.midi.MidiSystem");

// =============================================================
// init / flush / exit
// =============================================================

function init() {
    cursorClip = host.createArrangerCursorClip(GRID_W, GRID_H);
    cursorClip.exists().markInterested();
    cursorClip.getLoopLength().markInterested();
    cursorClip.getPlayStart().markInterested();

    cursorClip.setStepSize(STEP_BEATS);
    cursorClip.scrollToKey(0);
    cursorClip.scrollToStep(0);
    windowBaseStep = 0;

    cursorClip.addNoteStepObserver(onNoteStep);
    println("[MidiExport] addNoteStepObserver registered");

    // Use DocumentState so the controls appear in the Studio I/O Panel.
    var docState = host.getDocumentState();
    var defaultDir = JPaths.get(
        JSystem.getProperty("user.home"),
        "Documents", "BitwigExports"
    ).toString();
    exportDir = docState.getStringSetting(
        "Export directory", "MIDI Export", 200, defaultDir);

    var signal = docState.getSignalSetting(
        "Export current Arranger clip", "MIDI Export", "Export to .mid");
    signal.addSignalObserver(onExportClicked);

    host.showPopupNotification("MIDI Clip Exporter (JS) ready");
    println("[MidiExport] init done, default dir = " + defaultDir);
}

function flush() { /* no hardware output */ }

function exit() {
    host.showPopupNotification("MIDI Clip Exporter (JS) exited");
}

// =============================================================
// NoteStep observer -- keep noteMap in sync continuously
// =============================================================

function onNoteStep(step) {
    observerHits++;
    var stateStr;
    try { stateStr = String(step.state()); } catch (e) { stateStr = "<err:" + e + ">"; }

    var x = step.x();
    var y = step.y();
    var ch = step.channel();
    var absStep = windowBaseStep + x;
    var key = ch + "_" + absStep + "_" + y;

    // GraalJS may return "NoteOn" or a fully-qualified name; use indexOf.
    // Treat NoteSustain as a continuation -- ignore it here.
    if (stateStr.indexOf("NoteOn") >= 0 && stateStr.indexOf("Sustain") < 0) {
        noteMap[key] = {
            start: absStep * STEP_BEATS,
            pitch: y,
            channel: ch,
            velocity: step.velocity(),
            duration: step.duration()
        };
    } else {
        delete noteMap[key];
    }
}

// =============================================================
// Export trigger
// =============================================================

function onExportClicked() {
    if (exporting) {
        host.showPopupNotification("Export already running, please wait...");
        return;
    }
    if (!cursorClip.exists().get()) {
        host.showPopupNotification("No MIDI clip selected in Arranger");
        return;
    }
    exporting = true;

    var loopLen = cursorClip.getLoopLength().get();
    var windowBeats = STEP_BEATS * GRID_W;

    println("[MidiExport] export triggered, loopLen=" + loopLen.toFixed(3)
        + ", observerHits so far=" + observerHits
        + ", noteMap size=" + countKeys(noteMap));

    // Single window already covers the whole clip -> dump immediately
    if (loopLen <= windowBeats) {
        finishExport(loopLen);
        return;
    }

    // Multi-window: chunk 0 already pushed by the observer; scan from 1
    var chunks = Math.ceil(loopLen / windowBeats);
    scanChunk(1, chunks, loopLen);
}

function scanChunk(index, total, loopLen) {
    if (index >= total) {
        host.scheduleTask(function () {
            // Reset to chunk 0 for subsequent interactions
            windowBaseStep = 0;
            cursorClip.scrollToStep(0);
            host.scheduleTask(function () {
                finishExport(loopLen);
            }, SCAN_DELAY_MS);
        }, SCAN_DELAY_MS);
        return;
    }
    windowBaseStep = index * GRID_W;
    cursorClip.scrollToStep(windowBaseStep);
    host.scheduleTask(function () {
        scanChunk(index + 1, total, loopLen);
    }, SCAN_DELAY_MS);
}

function countKeys(o) {
    var n = 0;
    for (var k in o) { if (o.hasOwnProperty(k)) n++; }
    return n;
}

// =============================================================
// Write SMF (Type 1)
// =============================================================

function finishExport(loopLen) {
    try {
        var dir = JPaths.get(exportDir.get());
        JFiles.createDirectories(dir);

        var ts = JLocalDateTime.now().format(
            JDateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
        var out = dir.resolve("clip_" + ts + ".mid");

        var seq = new JSequence(JSequence.PPQ, PPQ);
        var track = seq.createTrack();

        // Tempo meta: fixed 120 BPM
        var tempo = new JMetaMessage();
        var mpq = 500000;
        var tempoData = Java.to([
            (mpq >>> 16) & 0xff,
            (mpq >>> 8) & 0xff,
            mpq & 0xff
        ], "byte[]");
        tempo.setMessage(0x51, tempoData, 3);
        track.add(new JMidiEvent(tempo, 0));

        // Collect from noteMap, sorted by start time
        var notes = [];
        for (var k in noteMap) {
            if (noteMap.hasOwnProperty(k)) notes.push(noteMap[k]);
        }
        notes.sort(function (a, b) { return a.start - b.start; });

        var written = 0;
        for (var i = 0; i < notes.length; i++) {
            var n = notes[i];
            if (n.pitch < 0 || n.pitch > 127) continue;

            var tickStart = Math.max(0, Math.round(n.start * PPQ));
            var tickEnd = Math.round(
                (n.start + Math.max(n.duration, STEP_BEATS * 0.25)) * PPQ);
            if (tickEnd <= tickStart) tickEnd = tickStart + 1;

            var vel = Math.round(n.velocity * 127);
            if (vel < 1) vel = 1;
            if (vel > 127) vel = 127;
            var ch = Math.max(0, Math.min(15, n.channel));

            var on = new JShortMessage();
            on.setMessage(JShortMessage.NOTE_ON, ch, n.pitch, vel);
            track.add(new JMidiEvent(on, tickStart));

            var off = new JShortMessage();
            off.setMessage(JShortMessage.NOTE_OFF, ch, n.pitch, 0);
            track.add(new JMidiEvent(off, tickEnd));
            written++;
        }

        // End Of Track
        var endMeta = new JMetaMessage();
        endMeta.setMessage(0x2F, Java.to([], "byte[]"), 0);
        var endTick = Math.max(1, Math.round(loopLen * PPQ));
        track.add(new JMidiEvent(endMeta, endTick));

        JMidiSystem.write(seq, 1, out.toFile());

        host.showPopupNotification(
            "Exported " + written + " notes -> " + out.getFileName());
        println("[MidiExport] saved: " + out.toAbsolutePath()
            + " (" + written + " notes, observerHits=" + observerHits + ")");
    } catch (e) {
        host.errorln("[MidiExport] failed: " + e);
        host.showPopupNotification("Export failed: " + e);
    } finally {
        exporting = false;
    }
}
