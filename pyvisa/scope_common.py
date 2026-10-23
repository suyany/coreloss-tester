import pyvisa
import math
import numpy as np
import os
import json
import sys
from datetime import datetime          # fix: was "import datetime", so datetime.now() failed
from contextlib import contextmanager


# ---------------------------------------------------------------- constants
SKEW_CMD = "PROBe:SKEW"     # [A] unverified until the lab. Alternative: DESKew. One constant, so every skew function agrees.
SKEW_STEP_S = 2.5e-12       # skew step of the MSO-X 3024T
TOL_PCT = 0.1               # fix: ONE tolerance for numeric rows, in percent (was 0.001 and a local 0.1)
ZERO_ABS_TOL = 1e-12        # fix: when the expected value is 0 a percentage is meaningless, so use this absolute tolerance. Your call to change.
CLIP_FRACTION = 0.98        # [A] a peak above 98% of the half-screen (4 divisions) counts as clipped
MIN_SAMPLES_PER_CYCLE = 50  # new: from the settings table (Part 2): ':ACQuire:SRATe? must give at least 50 samples per cycle'


# ---------------------------------------------------------------- exceptions
class SetupAbort(Exception):       # fix: moved up so everything below can use it
    def __init__(self, message, aborts=0, warnings=0):      # new: the spec says it carries message, aborts, warnings
        super().__init__(message)
        self.message = message
        self.aborts = aborts
        self.warnings = warnings


class CaptureError(Exception):
    pass


# ---------------------------------------------------------------- connection helpers
_rm = None


def get_rm():
    # fix: the spec says importing this file must not touch VISA, so the ResourceManager is made on first use
    global _rm
    if _rm is None:
        _rm = pyvisa.ResourceManager()
    return _rm


def print_devices():
    resources = get_rm().list_resources()
    list_size = len(resources)
    if list_size == 0:
        print("Found 0 instruments. Check: scope on, USB cable in the rear USB DEVICE port.")
    else:
        print(f"Found {list_size} instrument(s):")
        for i in resources:
            print(i)


def connect_device():
    # NOTE: left over from the A-files. open_scope below replaces it; consider deleting this one.
    device_string = input("Enter device string: ")
    device = get_rm().open_resource(device_string)
    device.timeout = 5000
    try:
        print(f"Connected: {device.query('*IDN?').strip()}")
    except pyvisa.errors.VisaIOError:
        print("[ABORT] No reply within 5 s.")
        print("ABORTED")
    return device


# ---------------------------------------------------------------- pure helpers
def scpi_number(x):
    return f"{x:.6E}"


def parse_error(reply):
    reply = reply.strip()            # fix: strip first, so a trailing newline can't leak in
    try:
        code, message = reply.split(",", 1)
        code = int(code)
    except ValueError:
        raise ValueError(f"malformed error reply: {reply!r}")     # fix: quote the reply
    message = message.strip().replace('"', "")
    return (code, message)


def values_match(expected, reply, tol_percent=None, tol_abs=None):
    # fix: never raises (unparseable reply -> False); expected 0 uses ZERO_ABS_TOL; optional absolute tolerance
    if tol_percent is None and tol_abs is None:
        return str(expected).strip().upper() == str(reply).strip().upper()
    try:
        expected = float(expected)
        reply = float(reply)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(expected) and math.isfinite(reply)):
        return False
    error = abs(reply - expected)
    if tol_abs is not None and error <= tol_abs:
        return True
    if tol_percent is not None:
        if expected == 0:
            return error <= ZERO_ABS_TOL
        return 100 * error / abs(expected) <= tol_percent
    return False


def timebase_for(freq_hz, cycles):
    if freq_hz <= 0:
        raise ValueError("freq_hz must be greater than 0")
    if cycles <= 0:                                   # fix: was missing
        raise ValueError("cycles must be greater than 0")
    total_time = cycles / freq_hz
    return total_time / 10


def scpi_timebase(freq_hz, cycles):
    scale = scpi_number(timebase_for(freq_hz, cycles))
    return f":TIMebase:SCALe {scale}"


def wrap_phase(x):
    wraps = x // (2 * np.pi)
    x = x - (wraps * 2 * np.pi)
    if x >= np.pi:
        x = x - (2 * np.pi)
    if x < (np.pi) * -1:
        x = x + (2 * np.pi)
    return x


def shift_from_phases(phi_a, phi_b, f_hz):
    if f_hz <= 0:                                     # fix: was missing
        raise ValueError("f_hz must be greater than 0")
    return wrap_phase(phi_a - phi_b) / (2 * np.pi * f_hz)


def fit_sine(t, y, f_hz):
    """Fit y = A*cos(2*pi*f*t + phi) + c at a KNOWN frequency f_hz.

    t: sample times in seconds (1-D array).  y: sample values in volts (same length).
    Returns (amplitude_v, phase_rad, rms_residual_v). Amplitude is the PEAK value.
    """
    # Step 1: check the inputs
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    if t.ndim != 1 or t.shape != y.shape:
        raise ValueError(f"t and y must be 1-D and the same length, got {t.shape} and {y.shape}")
    if f_hz <= 0:
        raise ValueError(f"f_hz must be greater than 0, got {f_hz}")

    # Step 2: keep only a whole number of cycles of f_hz
    dt = (t[-1] - t[0]) / (len(t) - 1)                 # time between samples
    whole_cycles = int(np.floor(len(t) * dt * f_hz))   # how many full cycles fit in the record
    if whole_cycles < 3:
        raise ValueError(f"need at least 3 whole cycles, the record holds {whole_cycles}")
    keep = (t - t[0]) < whole_cycles / f_hz
    tt = t[keep]
    yy = y[keep]

    # Step 3: build the three known curves as columns
    w = 2 * np.pi * f_hz
    columns = np.column_stack([np.cos(w * tt), -np.sin(w * tt), np.ones_like(tt)])

    # Step 4: least squares finds P, Q, c so that  P*cos - Q*sin + c  is closest to the data
    (p, q, c), *_ = np.linalg.lstsq(columns, yy, rcond=None)

    # Step 5: turn P and Q back into amplitude and phase
    amplitude = np.hypot(p, q)
    phase = np.arctan2(q, p)

    # Step 6: what is left over after subtracting the fit
    leftover = yy - columns @ np.array([p, q, c])
    rms_residual = np.sqrt(np.mean(leftover ** 2))

    return float(amplitude), float(phase), float(rms_residual)


# ---------------------------------------------------------------- logging
log_path = None
c_warn = 0
c_abort = 0


def start_run(test_name, folder="data"):
    global log_path, c_warn, c_abort
    os.makedirs(folder, exist_ok=True)
    c_warn = 0
    c_abort = 0
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')       # fix: single quotes inside the f-string work on Python < 3.12 too
    log_path = os.path.join(folder, f"{test_name}_{stamp}.log")
    with open(log_path, "x", encoding="utf-8"):
        pass
    return log_path


def report(status, text):
    global c_warn, c_abort                                   # fix: without this, ABORT and WARN raised UnboundLocalError
    if log_path is None:
        raise RuntimeError("Call start_run first")
    status = status.strip().upper()
    if status not in ("OK", "WARN", "ABORT"):
        raise ValueError(f"Invalid status: '{status}'. Must be 'OK', 'WARN', or 'ABORT'.")
    if status == "ABORT":
        c_abort += 1
    elif status == "WARN":
        c_warn += 1
    message = f"{f'[{status}]':<8}{text}"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{message}\n")
    print(message)


def summary_line():
    # fix: now RETURNS the text (as the spec says), and also prints it and logs it
    if c_abort > 0:
        text = f"SETUP FAILED: {c_abort} aborts, {c_warn} warnings"
    else:
        text = f"SETUP COMPLETE: {c_abort} aborts, {c_warn} warnings"
    print(text)
    if log_path is not None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return text


# ---------------------------------------------------------------- opening the scope
def open_scope(resource=None, timeout_ms=10000):
    rm = get_rm()
    if resource is None:
        resource_list = rm.list_resources()
        for i in resource_list:
            if "USB" in i:
                resource = i
                break
        if resource is None:
            raise SetupAbort("No USB device found")
    try:                                                     # fix: a failing open_resource is now a SetupAbort too
        scope = rm.open_resource(resource)
    except Exception as e:
        raise SetupAbort(f"Could not open {resource}") from e
    try:
        scope.timeout = timeout_ms
        scope.clear()
        scope.write("*CLS")
        idn = scope.query('*IDN?').strip()
    except Exception as e:
        scope.close()
        raise SetupAbort("Error Setup Aborted") from e
    if "MSO-X 3024T" not in idn:
        scope.close()
        raise SetupAbort("MSO-X 3024T not found in *IDN?")
    return (scope, idn)


# ---------------------------------------------------------------- rows
def _row(send, query, expected, tol=None, severity="abort", tol_abs=None):
    return {"send": send, "query": query, "expected": expected,
            "tol": tol, "tol_abs": tol_abs, "severity": severity}     # fix: tol_abs added (used by the skew row)


def build_rows(freq_hz, cycles, ch1_v, ch2_v, averages=64, skew_s=None):
    # ======================= YOUR PART =======================
    # 1. Check the inputs: ValueError if ch1_v or ch2_v is 0 or less.
    # 2. Build these five, each one made with _row(...):
    #      ch1_scale   the :CHANnel1:SCALe row
    #      ch2_scale   the :CHANnel2:SCALe row
    #      tb_scale    the :TIMebase:SCALe row   (seconds/div from timebase_for)
    #      avg_count   the :ACQuire:COUNt row
    #      skew_rows   [] when skew_s is None, else the skew row(s)
    # Use scpi_number for numbers in "send" where you can, and TOL_PCT as the tol.
    # =========================================================

    if (ch1_v <= 0) or (ch2_v <= 0):
        raise ValueError("Channel scale needs to be greater than 0")

    rows = []
    skew_row = []

    ch1_scale = _row(f":CHANnel1:SCALe {scpi_number(ch1_v)}", ":CHANnel1:SCALe?", ch1_v, TOL_PCT)
    ch2_scale = _row(f":CHANnel2:SCALe {scpi_number(ch2_v)}", ":CHANnel2:SCALe?", ch2_v, TOL_PCT)
    tb_scale = _row(scpi_timebase(freq_hz, cycles), ":TIMebase:SCALe?", timebase_for(freq_hz, cycles), TOL_PCT)
    avg_count = _row(f":ACQuire:COUNt {scpi_number(averages)}", ":ACQuire:COUNt?", averages, TOL_PCT)

    if skew_s is not None:
        # fix: the scope rounds skew to its 2.5 ps step, so the tolerance is ABSOLUTE (one step), not a percentage
        skew_row += [_row(f":CHANnel2:{SKEW_CMD} {scpi_number(skew_s)}", f":CHANnel2:{SKEW_CMD}?",
                          skew_s, None, tol_abs=SKEW_STEP_S)]

    # ---- channels 1 and 2 ----
    for n, scale_row in ((1, ch1_scale), (2, ch2_scale)):
        c = f":CHANnel{n}"
        rows += [
            _row(f"{c}:DISPlay ON",       f"{c}:DISPlay?",   "1"),
            _row(f"{c}:COUPling DC",      f"{c}:COUPling?",  "DC"),
            _row(f"{c}:IMPedance ONEMeg", f"{c}:IMPedance?", "ONEM"),
            _row(f"{c}:BWLimit OFF",      f"{c}:BWLimit?",   "0"),
            _row(f"{c}:INVert OFF",       f"{c}:INVert?",    "0"),
            _row(f"{c}:UNITs VOLT",       f"{c}:UNITs?",     "VOLT"),
            _row(f"{c}:PROBe 10",         f"{c}:PROBe?",     10.0, TOL_PCT),
            _row(f"{c}:VERNier ON",       f"{c}:VERNier?",   "1"),
            _row(f"{c}:OFFSet 0",         f"{c}:OFFSet?",    0.0, TOL_PCT, "warn"),
            scale_row,                    # after vernier: order matters
        ]

    # ---- channels 3 and 4, digital pods: off ----
    for name in (":CHANnel3", ":CHANnel4", ":POD1", ":POD2"):
        rows.append(_row(f"{name}:DISPlay OFF", f"{name}:DISPlay?", "0"))

    # ---- timebase ----
    rows += [
        _row(":TIMebase:MODE MAIN",        ":TIMebase:MODE?",      "MAIN"),
        _row(":TIMebase:REFerence CENTer", ":TIMebase:REFerence?", "CENT"),
        _row(":TIMebase:POSition 0",       ":TIMebase:POSition?",  0.0, TOL_PCT),
        _row(":TIMebase:VERNier ON",       ":TIMebase:VERNier?",   "1"),
        tb_scale,
    ]

    # ---- acquisition ----
    rows += [
        _row(":ACQuire:MODE RTIMe",   ":ACQuire:MODE?",     "RTIM"),
        _row(":ACQuire:TYPE AVERage", ":ACQuire:TYPE?",     "AVER"),
        avg_count,                    # after type: order matters
        _row(":ACQuire:COMPlete 100", ":ACQuire:COMPlete?", 100.0, TOL_PCT),
    ]

    # ---- trigger ----
    rows += [
        _row(":TRIGger:MODE EDGE",             ":TRIGger:MODE?",                "EDGE"),
        _row(":TRIGger:SWEep NORMal",          ":TRIGger:SWEep?",               "NORM"),
        _row(":TRIGger:EDGE:SOURce CHANnel1",  ":TRIGger:EDGE:SOURce?",         "CHAN1"),
        _row(":TRIGger:EDGE:SLOPe POSitive",   ":TRIGger:EDGE:SLOPe?",          "POS"),
        _row(":TRIGger:EDGE:LEVel 0,CHANnel1", ":TRIGger:EDGE:LEVel? CHANnel1", 0.0, TOL_PCT),
        _row(":TRIGger:EDGE:COUPling DC",      ":TRIGger:EDGE:COUPling?",       "DC"),
        _row(":TRIGger:EDGE:REJect OFF",       ":TRIGger:EDGE:REJect?",         "OFF"),
        _row(":TRIGger:HFReject OFF",          ":TRIGger:HFReject?",            "0"),
        _row(":TRIGger:NREJect OFF",           ":TRIGger:NREJect?",             "0"),
        _row(":TRIGger:HOLDoff 1E-7",          ":TRIGger:HOLDoff?",             1e-7, TOL_PCT),
    ]

    rows += skew_row

    # ---- run last: send-only row (query None), skipped by verify_rows ----
    rows.append(_row(":RUN", None, None))
    return rows


# ---------------------------------------------------------------- talking to the scope
def drain_error_queue(device, limit=20):
    # fix: returns a list of (code, message) tuples WITHOUT the final "No error"; capped; prints nothing.
    # (The old "errors += reply" spread the tuple into the list, so the list was never empty.)
    errors = []
    for _ in range(limit):
        code, message = parse_error(device.query(":SYSTem:ERRor?"))
        if code == 0:
            break
        errors.append((code, message))
    return errors


def apply_rows(scope, rows):
    # fix: range/enumerate instead of "for i in len(rows)"; drains up to 5 errors per row;
    # counts through report() so no global is needed here
    problems = []
    drain_error_queue(scope)
    for i, row in enumerate(rows, start=1):
        scope.write(row["send"])
        for code, message in drain_error_queue(scope, limit=5):
            problems.append((i, row["send"], code, message))
            report("ABORT", f"row {i}: {row['send']}  -> {code} {message}")
    return problems


def verify_rows(scope, rows):
    # fix: returns Result rows (row + got + status), skips send-only rows, honours severity,
    # turns a timeout into "NO REPLY" instead of crashing, and reports one line per row.
    results = []
    drain_error_queue(scope)
    for row in rows:
        if row.get("query") is None:
            continue
        try:
            got = scope.query(row["query"]).strip()
        except pyvisa.errors.VisaIOError:
            got = "NO REPLY"
            status = "ABORT"
            drain_error_queue(scope)
        else:
            if values_match(row["expected"], got, row.get("tol"), row.get("tol_abs")):
                status = "OK"
            elif row.get("severity") == "warn":
                status = "WARN"
            else:
                status = "ABORT"
        expected = row["expected"]
        shown = scpi_number(expected) if isinstance(expected, (int, float)) else str(expected)
        results.append(dict(row, got=got, status=status))
        report(status, f"{row['query']:<28}expected {shown}  got {got}")
    return results


def try_read(device, query):
    try:
        return device.query(query).strip()
    except pyvisa.errors.VisaIOError:        # fix: was the bare name VisaIOError (NameError)
        return None


def read_float(device, query):
    # new: for numbers that MUST be there. A missing reply is a clear SetupAbort, not "float(None)".
    reply = try_read(device, query)
    if reply is None:
        raise SetupAbort(f"No reply to {query}")
    try:
        return float(reply)
    except ValueError:
        raise SetupAbort(f"Reply to {query} is not a number: {reply!r}")


def read_only_checks(scope, freq_hz=None, expected_skews=None):      # new: freq_hz may be None (no target frequency)
    srate_hz = read_float(scope, ":ACQuire:SRATe?")
    memory_points = int(read_float(scope, ":ACQuire:POINts?"))
    cal_date = try_read(scope, ":CALibrate:DATE?")
    cal_temp_change = try_read(scope, ":CALibrate:TEMPerature?")
    probe_id = {"1": try_read(scope, ":CHANnel1:PROBe:ID?"),         # fix: string keys everywhere, so a JSON snapshot reloads identically
                "2": try_read(scope, ":CHANnel2:PROBe:ID?")}
    skew_s = {"1": read_float(scope, f":CHANnel1:{SKEW_CMD}?"),
              "2": read_float(scope, f":CHANnel2:{SKEW_CMD}?")}

    if expected_skews is not None:
        expected = {str(k): v for k, v in expected_skews.items()}     # accepts {1: ...} or {"1": ...}
        for ch, want in expected.items():
            # fix: compare within one skew step (not float !=), and count it as an ABORT via report()
            if ch in skew_s and abs(skew_s[ch] - want) > SKEW_STEP_S:
                report("ABORT", f"Channel {ch} skew {skew_s[ch]:+.4E} s differs from expected {want:+.4E} s")

    return {
        "srate_hz": srate_hz,
        "samples_per_cycle": None if freq_hz is None else srate_hz / freq_hz,   # NOTE: in NORMal points mode the returned data can be thinner than this
        "memory_points": memory_points,
        "cal_date": cal_date,
        "cal_temp_change": cal_temp_change,
        "probe_id": probe_id,
        "skew_s": skew_s,
        "errors": drain_error_queue(scope),
    }


def check_signal(scope, target_hz, channels=(1, 2)):
    scope.write(":DIGitize")
    scope.query("*OPC?")                           # fix: wait for the acquisition (spec: *OPC?, never sleep)
    error_pct = None                               # fix: defined up front, so NO SIGNAL no longer raises UnboundLocalError
    measured_f = read_float(scope, ":MEASure:FREQuency? CHANnel1")
    if measured_f > 1E37:
        report("ABORT", "No signal: frequency reads as the 9.9E+37 sentinel")
        measured_f = None
    else:
        error_pct = (100 * (measured_f - target_hz)) / target_hz
        if abs(error_pct) > 1:
            report("ABORT", f"Frequency {measured_f:.6E} Hz is {error_pct:+.2f} % from the target {target_hz:.6E} Hz")
    fill = {}
    for ch in channels:                            # fix: loops over the channels asked for; the old code checked fill1 twice
        vpp = read_float(scope, f":MEASure:VPP? CHANnel{ch}")
        scale = read_float(scope, f":CHANnel{ch}:SCALe?")
        if vpp > 1E37:
            fill[str(ch)] = None
            report("ABORT", f"Channel {ch}: no Vpp reading")
            continue
        fill[str(ch)] = vpp / scale
        if fill[str(ch)] > 7.5:
            report("ABORT", f"Channel {ch} Vpp fills {fill[str(ch)]:.1f} of 8 divisions (limit 7.5)")
    return {"freq_hz": measured_f, "error_pct": error_pct, "fill": fill}


def save_snapshot(path, info):
    folder = os.path.dirname(path)
    if folder:                                      # fix: a bare filename has no folder to create
        os.makedirs(folder, exist_ok=True)
    clash = {"timestamp", "versions"} & set(info)
    if clash:                                       # fix: the caller can no longer silently override these two keys
        raise ValueError(f"info must not contain {sorted(clash)}; save_snapshot adds them")
    record = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
              "versions": {"python": sys.version, "pyvisa": pyvisa.__version__}} | dict(info)
    info_json = json.dumps(record, indent=2)        # built first: a non-JSON value fails before any file exists
    with open(path, "x", encoding="utf-8") as f:
        f.write(info_json)
    return path


# ---------------------------------------------------------------- skew
def read_skew(scope, ch):
    return read_float(scope, f":CHANnel{ch}:{SKEW_CMD}?")       # fix: returns seconds as a float (it returned text)


def set_skew(scope, ch, seconds):
    # fix: raises SetupAbort instead of printing, drains the error queue, reads back once, returns the read-back
    drain_error_queue(scope)
    scope.write(f":CHANnel{ch}:{SKEW_CMD} {scpi_number(seconds)}")
    errors = drain_error_queue(scope)
    if errors:
        raise SetupAbort(f"Channel {ch} skew write was rejected: {errors}")
    got = read_skew(scope, ch)
    if abs(got - seconds) > SKEW_STEP_S:
        raise SetupAbort(f"Channel {ch} skew reads {got:+.4E} s, asked for {seconds:+.4E} s")
    return got


@contextmanager
def injected_skew(scope, ch, seconds):
    # The value is ABSOLUTE (the skew the channel will have inside the with-block), not an offset.
    original = read_skew(scope, ch)
    try:
        set_skew(scope, ch, seconds)
        yield
    finally:
        set_skew(scope, ch, original)               # a failed restore raises SetupAbort, loudly


def save_skew_file(path, ch2_skew_s, set_on):
    # fix: rewritten as three flat blocks (read, update, write) so a bug can never damage the file:
    # the real file is only touched by os.replace, after the new content is complete.
    if not math.isfinite(ch2_skew_s):
        raise ValueError(f"ch2_skew_s must be a finite number, got {ch2_skew_s}")
    if hasattr(set_on, "isoformat"):                # accepts a date or text
        set_on = set_on.isoformat()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)

    # 1. read
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            previous = {"ch2_skew_s": old["ch2_skew_s"], "set_on": old["set_on"]}
            history = list(old.get("history", []))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ValueError(f"{path} is damaged, not overwriting it") from e
        history.append(previous)                    # append ONE entry (+= would add the dict's keys)
    else:
        history = []

    # 2. update
    record = {"ch2_skew_s": ch2_skew_s, "set_on": set_on, "history": history}

    # 3. write
    text = json.dumps(record, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def load_skew_file(path):
    # new: the other half of save_skew_file. Returns {"ch2_skew_s": float, "set_on": text}.
    # FileNotFoundError if the file is missing; ValueError if it is damaged or the values are wrong.
    with open(path, "r", encoding="utf-8") as f:            # raises FileNotFoundError by itself
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON") from e
    if not isinstance(data, dict) or "ch2_skew_s" not in data or "set_on" not in data:
        raise ValueError(f"{path} needs the keys 'ch2_skew_s' and 'set_on'")
    skew = data["ch2_skew_s"]
    if isinstance(skew, bool) or not isinstance(skew, (int, float)) or not math.isfinite(skew):
        raise ValueError(f"{path}: ch2_skew_s must be a finite number, got {skew!r}")
    return {"ch2_skew_s": float(skew), "set_on": data["set_on"]}

# ---------------------------------------------------------------- capture
PREAMBLE_FIELDS = ("format", "type", "points", "count",
                   "x_increment", "x_origin", "x_reference",
                   "y_increment", "y_origin", "y_reference")


def parse_preamble(pre):
    """Ten preamble numbers -> dict with names. Whole-number fields become int."""
    try:
        values = [float(x) for x in pre]
    except (TypeError, ValueError):
        raise CaptureError(f"preamble is not ten numbers: {pre!r}")
    if len(values) != len(PREAMBLE_FIELDS):
        raise CaptureError(
            f"preamble has {len(values)} fields, expected {len(PREAMBLE_FIELDS)}: {pre!r}")
    if not all(math.isfinite(x) for x in values):
        raise CaptureError(f"preamble has a non-finite value: {pre!r}")
    p = dict(zip(PREAMBLE_FIELDS, values))
    for key in ("format", "type", "points", "count"):
        p[key] = int(p[key])
    if p["points"] <= 0:
        raise CaptureError(f"preamble says {p['points']} points")
    if p["x_increment"] <= 0:
        raise CaptureError(f"preamble x_increment is {p['x_increment']}, must be positive")
    return p


def raw_to_units(raw, pre):
    """Raw counts + preamble -> (t_seconds, volts, parsed_preamble)."""
    p = parse_preamble(pre)
    if len(raw) != p["points"]:
        raise CaptureError(
            f"got {len(raw)} samples but the preamble says {p['points']}")
    raw = np.asarray(raw, dtype=float)      # float first: no integer wrap-around
    index = np.arange(p["points"])

    t = (index - p["x_reference"]) * p["x_increment"] + p["x_origin"]
    v = (raw - p["y_reference"]) * p["y_increment"] + p["y_origin"]
    return t, v, p


def capture_pair(scope, n_points, channels=(1, 2), averages_expected=64, timeout_ms=30000, points_mode="NORMal"):
    og_timeout = scope.timeout
    try:
        scope.timeout = timeout_ms
        drain_error_queue(scope)                    # fix: anything left from before is not this capture's fault
        try:                                        # fix: a pyvisa timeout becomes a CaptureError that says where
            scope.write(f":DIGitize CHANnel{channels[0]}, CHANnel{channels[1]}; *OPC?")
            scope.read()
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            scope.write(":WAVeform:FORMat WORD")
            scope.write(":WAVeform:BYTeorder LSBFirst")
            scope.write(":WAVeform:UNSigned OFF")
            scope.write(f":WAVeform:POINts:MODE {points_mode}")
            scope.write(f":WAVeform:POINts {n_points}")
            scope.write(f":WAVeform:SOURce CHANnel{channels[0]}")
            ch1_pream = scope.query_ascii_values(":WAVeform:PREamble?")
            ch1_data = scope.query_binary_values(":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array)
            ch1_scale = read_float(scope, f":CHANnel{channels[0]}:SCALe?")
            scope.write(f":WAVeform:SOURce CHANnel{channels[1]}")
            ch2_pream = scope.query_ascii_values(":WAVeform:PREamble?")
            ch2_data = scope.query_binary_values(":WAVeform:DATA?", datatype="h", is_big_endian=False, container=np.array)
            ch2_scale = read_float(scope, f":CHANnel{channels[1]}:SCALe?")
            read_points_mode = try_read(scope, ":WAVeform:POINts:MODE?")
        except pyvisa.errors.VisaIOError as e:
            raise CaptureError(f"scope did not answer within {timeout_ms} ms during the capture") from e

        queued = drain_error_queue(scope)           # fix: the result used to be thrown away
        if queued:
            raise CaptureError(f"scope reported errors during the capture: {queued}")

        ch1_t, ch1_v, ch1_output = raw_to_units(ch1_data, ch1_pream)
        ch2_t, ch2_v, ch2_output = raw_to_units(ch2_data, ch2_pream)

        if ch1_output["points"] != ch2_output["points"]:
            raise CaptureError("points mismatch")
        if ch1_output["x_increment"] != ch2_output["x_increment"]:
            raise CaptureError("x_increment mismatch")
        if ch1_output["x_origin"] != ch2_output["x_origin"]:
            raise CaptureError("x_origin mismatch")
        if ch1_output["x_reference"] != ch2_output["x_reference"]:
            raise CaptureError("x_reference mismatch")
        if ch1_output["count"] < averages_expected:
            raise CaptureError("channel 1 average count lower than expected")
        if ch2_output["count"] < averages_expected:
            raise CaptureError("channel 2 average count lower than expected")

        # fix: the two checks that were missing (spec: samples at the converter limits; mode read-back)
        for ch, v, scale in ((channels[0], ch1_v, ch1_scale), (channels[1], ch2_v, ch2_scale)):
            if np.max(np.abs(v)) >= CLIP_FRACTION * 4 * scale:
                raise CaptureError(f"channel {ch} peaks at the edge of the screen (clipping)")
        if read_points_mode is None or read_points_mode[:4].upper() != points_mode[:4].upper():
            report("WARN", f"points mode: asked for {points_mode}, scope says {read_points_mode}")

        meta = {
            "preambles": {str(channels[0]): ch1_output, str(channels[1]): ch2_output},      # fix: keys follow the channels asked for
            "sample_rate_hz": (1 / ch1_output["x_increment"]),
            "points_mode": read_points_mode,
            "n_points": ch1_output["points"],
            "n_points_requested": n_points,
            "average_count": min(ch1_output["count"], ch2_output["count"]),               # fix: the lower of the two
            "volts_per_div": {str(channels[0]): ch1_scale, str(channels[1]): ch2_scale},
            "timestamp": timestamp,
        }

        return ch1_t, ch1_v, ch2_v, meta

    finally:
        scope.timeout = og_timeout


# ---------------------------------------------------------------- saving a capture
def save_capture(prefix, t, v1, v2, meta, folder="data"):
    # new: writes <folder>/<prefix>_<timestamp>.npz (t, v1, v2 as float64) and the meta as a .json beside it.
    # Create-only. Returns the .npz path. Call it BEFORE any analysis.
    # 1. check everything before any file exists
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("prefix must be a non-empty string")
    if any(bad in prefix for bad in ("/", "\\", "..")) or os.path.basename(prefix) != prefix:
        raise ValueError(f"prefix must be a plain name, not a path: {prefix!r}")
    arrays = {}
    for name, a in (("t", t), ("v1", v1), ("v2", v2)):
        a = np.asarray(a, dtype=np.float64)                  # float64 on purpose: never downcast measurement data
        if a.ndim != 1 or a.size == 0:
            raise ValueError(f"{name} must be a non-empty 1-D array")
        if not np.isfinite(a).all():
            raise ValueError(f"{name} contains nan or inf")
        arrays[name] = a
    if not (len(arrays["t"]) == len(arrays["v1"]) == len(arrays["v2"])):
        raise ValueError("t, v1 and v2 must have the same length")
    meta_json = json.dumps(meta, indent=2)                    # built first: a non-JSON value (numpy int) fails before any file exists

    # 2. names; refuse early if either exists, so we never leave a half pair
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"     # no colons: legal in Windows filenames
    folder = folder or "."                                    # os.makedirs("") would fail
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, f"{prefix}_{stamp}")
    npz_path, json_path = base + ".npz", base + ".json"
    for path in (npz_path, json_path):
        if os.path.exists(path):
            raise FileExistsError(path)

    # 3. write. np.savez(name) would overwrite and add ".npz" itself, so open the file ourselves in create-only mode
    with open(npz_path, "xb") as f:
        np.savez(f, t=arrays["t"], v1=arrays["v1"], v2=arrays["v2"])
    try:
        with open(json_path, "x", encoding="utf-8") as f:
            f.write(meta_json)
    except OSError as e:
        raise OSError(f"data saved to {npz_path} but the meta file {json_path} failed: {e}") from e

    # 4. read back: a short or corrupt write fails now, not months later
    with np.load(npz_path) as back:
        for name, a in arrays.items():
            if not np.array_equal(back[name], a):
                raise OSError(f"read-back of {name} from {npz_path} differs from what was saved")
    return npz_path


# ---------------------------------------------------------------- the whole start-of-run sequence
def _abort_exc(message, aborts_before, warns_before):
    # new: counts the abort (so summary_line is right) and builds the exception the test files catch
    report("ABORT", message)
    return SetupAbort(message, c_abort - aborts_before, c_warn - warns_before)


def open_and_verify(rows, test_name, target_hz=None, skew_file=None):
    # new. Sequence: skew file, connect, apply, verify, read-only checks, signal check, snapshot.
    # Every row is printed BEFORE the decision; then SetupAbort is raised if anything aborted (scope closed).
    # Call start_run(test_name) first. On success the caller closes the scope.
    if target_hz is not None and target_hz <= 0:
        raise ValueError("target_hz must be greater than 0")
    aborts0, warns0 = c_abort, c_warn

    expected_skews = None
    if skew_file is not None:                                # before the scope is opened: a bad file costs nothing
        try:
            saved = load_skew_file(skew_file)
        except (OSError, ValueError) as e:
            raise _abort_exc(f"Skew file problem: {e}", aborts0, warns0) from e
        expected_skews = {"1": 0.0, "2": saved["ch2_skew_s"]}      # [A] CH1 is never skewed in this project
        report("OK", f"Skew file: ch2 {saved['ch2_skew_s']:+.4E} s, set on {saved['set_on']}")

    try:
        scope, idn = open_scope()
    except SetupAbort as e:
        raise _abort_exc(e.message, aborts0, warns0) from e

    try:
        report("OK", f"Connected: {idn}")                    # inside the try: if report fails, the scope still gets closed
        apply_rows(scope, rows)                              # reports its own ABORT lines
        results = verify_rows(scope, rows)                   # reports one line per row

        readonly = None
        try:
            readonly = read_only_checks(scope, target_hz, expected_skews)
        except SetupAbort as e:
            report("ABORT", e.message)
        if readonly is not None:
            srate = readonly["srate_hz"]
            spc = readonly["samples_per_cycle"]
            if spc is None:
                report("OK", f"Sample rate {srate:.4E} Sa/s")
            elif spc < MIN_SAMPLES_PER_CYCLE:
                report("ABORT", f"Sample rate {srate:.4E} Sa/s gives {spc:.0f} samples per cycle (need {MIN_SAMPLES_PER_CYCLE})")
            else:
                report("OK", f"Sample rate {srate:.4E} Sa/s, {spc:.0f} samples per cycle")
            if readonly["errors"]:
                report("ABORT", f"Error queue not empty after setup: {readonly['errors']}")
            else:
                report("OK", "Error queue empty after setup")

        signal = None
        if target_hz is not None:
            try:
                signal = check_signal(scope, target_hz)      # reports its own ABORT lines
            except SetupAbort as e:
                report("ABORT", e.message)
            if signal is not None:
                if signal["freq_hz"] is not None and abs(signal["error_pct"]) <= 1:
                    report("OK", f"Signal: f = {signal['freq_hz'] / 1e6:.6f} MHz (target {target_hz / 1e6:.6f} MHz, error {signal['error_pct']:+.2f} %)")
                for ch, fill in signal["fill"].items():
                    if fill is not None and fill <= 7.5:
                        report("OK", f"Signal: CH{ch} fills {fill:.1f} of 8 divisions")

        snapshot_path = os.path.splitext(log_path)[0] + "_settings.json"      # pairs with the run's log file
        snapshot = {"test_name": test_name, "idn": idn, "target_hz": target_hz,
                    "skew_file": skew_file, "expected_skews": expected_skews,
                    "aborts_so_far": c_abort - aborts0, "rows": results,
                    "read_only": readonly, "signal": signal}
        try:
            save_snapshot(snapshot_path, snapshot)
            report("OK", f"Snapshot saved: {snapshot_path}")
        except (OSError, ValueError, TypeError) as e:
            report("ABORT", f"Snapshot not saved: {e}")
            snapshot_path = None
    except BaseException:                                    # a crash must not leave the scope open
        scope.close()
        raise

    aborts, warns = c_abort - aborts0, c_warn - warns0
    if aborts:
        scope.close()
        raise SetupAbort(f"{aborts} abort(s), {warns} warning(s) during setup", aborts, warns)
    info = {"idn": idn, "snapshot_path": snapshot_path,
            "srate_hz": None if readonly is None else readonly["srate_hz"],
            "aborts": aborts, "warnings": warns,
            "freq_hz": None if signal is None else signal["freq_hz"],      # new: the measured frequency the test files give fit_sine
            "fill": None if signal is None else signal["fill"]}
    return scope, info