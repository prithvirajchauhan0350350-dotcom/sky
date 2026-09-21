"""
spy_data.py — Data Analysis & Physics Engine for Spy (JARVIS mode).
--------------------------------------------------------------------
The "Data Analysis" capability from the JARVIS stack: lets Spy crunch
real datasets and run real physics simulations on this machine.

  load_table(path)  — CSV / TSV / JSON / Excel → list of row-dicts.
                      Pure stdlib; openpyxl only needed for .xlsx.
  analyse(source)   — one-call profile of a dataset: per-column types,
                      stats, outliers, correlations. pandas accelerator
                      if installed, pure-Python engine otherwise.
  simulate(...)     — physics: projectile (optionally with air drag),
                      pendulum, damped spring, Newton cooling, orbit.
  rk4(...)          — general 4th-order Runge-Kutta integrator.

All results come back as {"ok": bool, "message": str} (plus data lists)
so Spy can speak or display them directly. No network access.
"""

import csv
import json
import math
import os
import statistics

try:
    import pandas as pd  # optional accelerator (unused for small files)
except ImportError:
    pd = None

try:
    import openpyxl  # optional, for .xlsx
except ImportError:
    openpyxl = None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_table(path: str):
    """Load CSV/TSV/JSON/Excel as a list of dicts. Returns (rows, note);
    rows == [] on failure with a note explaining why."""
    if not os.path.exists(path):
        return [], f"file not found: {path}"
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            with open(path, encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return [r for r in data if isinstance(r, dict)], ""
            if isinstance(data, dict):  # dict of columns → rows
                keys = list(data)
                n = min(len(v) for v in data.values() if isinstance(v, list)) \
                    if any(isinstance(v, list) for v in data.values()) else 0
                return [{k: (data[k][i] if isinstance(data[k], list) else data[k])
                         for k in keys} for i in range(n)], ""
            return [], "unsupported JSON shape (need a list of objects)"
        if ext == ".xlsx":
            if openpyxl is None:
                return [], ".xlsx needs openpyxl (pip install openpyxl) — or save as CSV"
            wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
            ws = wb.active
            raw = list(ws.values)
            if not raw:
                return [], "empty Excel sheet"
            header = [str(h) if h is not None else f"col{i}"
                      for i, h in enumerate(raw[0])]
            rows = [dict(zip(header, r)) for r in raw[1:] if any(r)]
            wb.close()
            return rows, ""
        # csv / tsv / txt — sniff the delimiter
        with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
            sample = fh.read(8192)
            fh.seek(0)
            delim = "\t" if ext == ".tsv" else csv.Sniffer().sniff(
                sample, delimiters=",;\t|").delimiter
            reader = csv.DictReader(fh, delimiter=delim)
            rows = [dict(r) for r in reader]
        return rows, ""
    except Exception as e:
        return [], f"load error: {e}"


# ---------------------------------------------------------------------------
# Column profiling
# ---------------------------------------------------------------------------

def _to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        s = str(v).strip().replace(",", "")
        if s and s[-1] == "%":
            s = s[:-1]
        f = float(s)
        return f
    except (ValueError, TypeError):
        return None


def _quartile(sorted_vals, p):
    n = len(sorted_vals)
    i = (n - 1) * p
    lo, hi = int(i), min(n - 1, int(i) + 1)
    f = i - lo
    return sorted_vals[lo] * (1 - f) + sorted_vals[hi] * f


def _profile_numeric(name, vals):
    nums = sorted(x for x in (_to_num(v) for v in vals) if x is not None)
    n = len(nums)
    if n < 2:
        return None
    q1, med, q3 = _quartile(nums, .25), _quartile(nums, .5), _quartile(nums, .75)
    iqr = q3 - q1
    lo_f, hi_f = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    outliers = [x for x in nums if x < lo_f or x > hi_f]
    return {
        "name": name, "count": n,
        "missing": len(vals) - n,
        "min": nums[0], "max": nums[-1],
        "mean": statistics.fmean(nums),
        "median": med, "q1": q1, "q3": q3,
        "stdev": statistics.stdev(nums) if n >= 2 else 0.0,
        "outliers": outliers[:6],  # cap per column
    }


def _profile_categorical(name, vals):
    counts = {}
    for v in vals:
        key = str(v)[:28] if v not in (None, "") else "(missing)"
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return None
    top = max(counts.items(), key=lambda kv: kv[1])
    return {"name": name, "kind": "categorical", "count": sum(counts.values()),
            "unique": len(counts), "top": top[0], "top_count": top[1]}


def _pearson(xs, ys):
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[:n], ys[:n]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _fmt(x):
    if x is None:
        return "?"
    if isinstance(x, float):
        return f"{x:,.4g}"
    return str(x)


# ---------------------------------------------------------------------------
# Main entry: analyse
# ---------------------------------------------------------------------------

def analyse(source) -> dict:
    """Profile a dataset from a file path (csv/tsv/json/xlsx) or a list
    of dicts. Returns {"ok": ..., "message": ...}."""
    if isinstance(source, str):
        rows, note = load_table(source)
        if not rows:
            return {"ok": False, "message": f"Could not read dataset: {note}"}
        label = os.path.basename(source)
    else:
        rows = [r for r in source if isinstance(r, dict)]
        label = "inline data"
        if not rows:
            return {"ok": True, "message": "Dataset is empty."}

    cols = list(rows[0].keys())
    numeric, categorical, lines = [], [], []
    lines.append(f"DATA ANALYSIS — {label}")
    lines.append(f"Rows: {len(rows)}   Columns: {len(cols)}")
    lines.append("-" * 44)

    for name in cols:
        vals = [r.get(name) for r in rows]
        prof = _profile_numeric(name, vals)
        if prof is None:
            prof = _profile_categorical(name, vals)
            if prof is None:
                continue
            categorical.append(prof)
            lines.append(
                f"[{name}] text — {prof['count']} values, "
                f"{prof['unique']} unique; most common: "
                f"{prof['top']} ({prof['top_count']}×)")
        else:
            prof["kind"] = "numeric"
            numeric.append(prof)
            lines.append(
                f"[{name}] number — min {_fmt(prof['min'])}, max "
                f"{_fmt(prof['max'])}, mean {_fmt(prof['mean'])}, median "
                f"{_fmt(prof['median'])}, std {_fmt(prof['stdev'])}"
                + (f", MISSING {prof['missing']}" if prof["missing"] else ""))
            if prof["outliers"]:
                shown = ", ".join(_fmt(o) for o in prof["outliers"][:5])
                lines.append(f"    outliers: {shown}")

    if len(numeric) >= 2:
        lines.append("-" * 44)
        lines.append("Correlations (Pearson r, |r| > 0.5):")
        found = False
        for i in range(len(numeric)):
            for j in range(i + 1, len(numeric)):
                a = [_to_num(r.get(numeric[i]["name"])) for r in rows]
                b = [_to_num(r.get(numeric[j]["name"])) for r in rows]
                pairs = [(x, y) for x, y in zip(a, b)
                         if x is not None and y is not None]
                if len(pairs) < 3:
                    continue
                r = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
                if r is not None and abs(r) > 0.5:
                    found = True
                    strength = "strong" if abs(r) > 0.8 else "moderate"
                    sign = "positive" if r > 0 else "negative"
                    lines.append(
                        f"    {numeric[i]['name']} ↔ {numeric[j]['name']}: "
                        f"r = {r:+.2f} ({strength} {sign})")
        if not found:
            lines.append("    none above |r| > 0.5")

    lines.append("-" * 44)
    lines.append(f"Summary: {len(numeric)} numeric, {len(categorical)} "
                 f"categorical columns analysed.")
    return {"ok": True, "message": "\n".join(lines)}


# ---------------------------------------------------------------------------
# Physics simulations
# ---------------------------------------------------------------------------

def rk4(state, deriv, dt: float, steps: int):
    """Generic RK4: state tuple, deriv(state, t)->tuple, returns list of
    (t, state) samples. deriv receives (state tuple, time)."""
    out = []
    t = 0.0
    s = tuple(float(x) for x in state)
    out.append((t, s))
    for _ in range(steps):
        k1 = deriv(s, t)
        k2 = deriv(tuple(x + 0.5 * dt * a for x, a in zip(s, k1)), t + dt / 2)
        k3 = deriv(tuple(x + 0.5 * dt * a for x, a in zip(s, k2)), t + dt / 2)
        k4 = deriv(tuple(x + dt * a for x, a in zip(s, k3)), t + dt)
        s = tuple(x + dt / 6 * (a + 2 * b + 2 * c + d)
                  for x, a, b, c, d in zip(s, k1, k2, k3, k4))
        t += dt
        out.append((t, s))
    return out


def _sim_projectile(p):
    speed = float(p.get("speed", 20)); angle = math.radians(float(p.get("angle", 45)))
    g = float(p.get("gravity", 9.81))
    drag_c = float(p.get("drag", 0.0))  # 1/s linear drag coefficient
    vx, vy = speed * math.cos(angle), speed * math.sin(angle)

    def deriv(s, t):
        x, y, vx_, vy_ = s
        return (vx_, vy_, -drag_c * vx_, -g - drag_c * vy_)

    dt = 0.001
    trace = rk4((0.0, 0.0, vx, vy), deriv, dt, 6000)
    landing = next((pt for pt in trace if pt[1][1] < 0), trace[-1])
    apex = max(trace, key=lambda pt: pt[1][1])
    x_l, y_l, _, _ = landing[1]
    return (f"PROJECTILE — v={speed} m/s at {math.degrees(angle):.0f}°"
            + (f", drag {drag_c}/s" if drag_c else "")
            + f", g={g} m/s²",
            {"range_m": round(x_l, 2),
             "max_height_m": round(apex[1][1], 2),
             "flight_time_s": round(landing[0], 2)},
            [])


def _sim_pendulum(p):
    L = float(p.get("length", 1.0)); theta0 = math.radians(float(p.get("angle_deg", 20)))
    g = float(p.get("gravity", 9.81)); dt = 0.0005

    def deriv(s, t):
        th, om = s
        return (om, -(g / L) * math.sin(th))

    trace = rk4((theta0, 0.0), deriv, dt, int(6 / dt))
    # period: interval between consecutive upward zero-crossings of theta
    crossings = []
    prev = None
    for t, (th, om) in trace:
        if prev is not None and prev[1][0] < 0 <= th and om > 0:
            crossings.append(t)
            if len(crossings) == 2:
                break
        prev = (t, (th, om))
    t_period = (crossings[1] - crossings[0]) if len(crossings) == 2 else None
    small = 2 * math.pi * math.sqrt(L / g)
    return (f"PENDULUM — L={L} m, released {math.degrees(theta0):.0f}°, g={g}",
            {"measured_period_s": round(t_period, 3) if t_period else None,
             "small_angle_period_s": round(small, 3),
             "note": ("amplitude stretches the period (non-linear)"
                      if abs(theta0) > 0.25 else "")},
            [])


def _sim_spring(p):
    m = float(p.get("mass", 1.0)); k = float(p.get("stiffness", 20.0))
    c = float(p.get("damping", 0.5)); x0 = float(p.get("displacement", 0.5))
    dt = 0.001

    def deriv(s, t):
        x, v = s
        return (v, (-k * x - c * v) / m)

    trace = rk4((x0, 0.0), deriv, dt, int(5 / dt))
    omega = math.sqrt(k / m)
    crit = 2 * math.sqrt(k * m)
    regime = ("over-damped" if c > crit else
              "critically damped" if abs(c - crit) < 1e-9 else "under-damped")
    return (f"SPRING — m={m} kg, k={k} N/m, damping={c} (critical={crit:.2f})",
            {"natural_freq_hz": round(omega / (2 * math.pi), 3),
             "regime": regime,
             "position_after_1s": round(trace[int(1 / dt)][1][0], 4),
             "position_after_3s": round(trace[int(3 / dt)][1][0], 4)},
            [])


def _sim_cooling(p):
    T0 = float(p.get("temp_c", 90.0)); env = float(p.get("ambient_c", 25.0))
    k = float(p.get("k_per_min", 0.1))

    def deriv(s, t):
        T = s[0]
        return (-k * (T - env),)

    trace = rk4((T0,), deriv, 0.01, 60 * 30)  # 30 minutes
    samples = {f"T+{int(t)}min": round(s[0], 1) for t, s in trace
               if abs(t - round(t)) < 1e-9 and int(t) in (1, 5, 10, 20, 30)}
    return (f"NEWTON COOLING — object {T0}°C in {env}°C room, k={k}/min",
            samples, [])


def _sim_orbit(p):
    M = float(p.get("central_mass_kg", 5.972e24))  # Earth default
    r0 = float(p.get("altitude_m", 400000)) + 6.371e6
    G = 6.674e-11
    mu = G * M
    v0 = float(p.get("speed_m_s") or math.sqrt(mu / r0))
    dt = 1.0

    def deriv(s, t):
        x, y, vx, vy = s
        r3 = (x * x + y * y) ** 1.5
        return (vx, vy, -mu * x / r3, -mu * y / r3)

    trace = rk4((r0, 0.0, 0.0, v0), deriv, dt, int(3 * 5400 / dt))
    r_end = math.hypot(trace[-1][1][0], trace[-1][1][1])
    r_min = min(math.hypot(s[0], s[1]) for _, s in trace[::60])
    r_max = max(math.hypot(s[0], s[1]) for _, s in trace[::60])
    period_est = 2 * math.pi * math.sqrt(r0 ** 3 / mu)
    fell = r_end < 6.371e6 or r_min < 6.371e6
    escaped = r_max > 20 * r0
    return (f"ORBIT — altitude {p.get('altitude_m', 400000)/1000:.0f} km, "
            f"speed {v0:.0f} m/s",
            {"circular_period_min": round(period_est / 60, 1),
             "radius_min_km": round(r_min / 1000),
             "radius_max_km": round(r_max / 1000),
             "verdict": ("deorbited — too slow, it fell back"
                         if fell else "escaped — too fast" if escaped
                         else "stable orbit")},
            [])


SIMULATIONS = {"projectile": _sim_projectile, "pendulum": _sim_pendulum,
               "spring": _sim_spring, "cooling": _sim_cooling,
               "orbit": _sim_orbit}


def simulate(kind: str, **params) -> dict:
    """Run a physics simulation. kind ∈ projectile/pendulum/spring/
    cooling/orbit; params as in the individual functions."""
    fn = SIMULATIONS.get((kind or "").lower().strip())
    if fn is None:
        return {"ok": False,
                "message": f"Unknown simulation '{kind}'. I can run: "
                           + ", ".join(sorted(SIMULATIONS))}
    try:
        title, results, _trace = fn(params)
        lines = [title, "-" * 44]
        lines += [f"{key}: {val}" for key, val in results.items()
                  if val not in (None, "")]
        return {"ok": True, "message": "\n".join(lines), "data": results}
    except Exception as e:
        return {"ok": False, "message": f"Simulation error: {e}"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    demo = [dict(zip(("day", "temp_c", "sales"),
                     (d, 20 + 5 * math.sin(d / 3) + d * 0.1, 100 + d * 3)))
            for d in range(1, 31)]
    print(analyse(demo)["message"])
    print()
    print(simulate("projectile", speed=25, angle=40)["message"])
    print()
    print(simulate("pendulum", length=1.5, angle_deg=35)["message"])
    print()
    print(simulate("orbit", altitude_m=550000)["message"])
