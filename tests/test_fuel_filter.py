import json, tempfile, os, random
from sensors.fuel_filter import FuelFilter, WARMUP_S

def mk(last=None):
    d = tempfile.mkdtemp(); p = os.path.join(d, "s.json")
    if last is not None: open(p, "w").write(json.dumps({"fuel_pct": last}))
    return FuelFilter(state_file=p), p

def run(f, t0, secs, fn):
    out = []
    for i in range(secs):
        out.append(f.update(fn(i), t0 + i))
    return out

def test_warmup_ramp_ignored():
    f, _ = mk(55)
    out = run(f, 1000, 400, lambda i: min(52, i * 0.8) + random.uniform(-2, 2))
    assert all(v == 55 for v in out[:WARMUP_S]), out[:5]
    assert 49 <= out[-1] <= 55

def test_outliers_and_only_drops():
    f, _ = mk(50)
    run(f, 0, 200, lambda i: 50)
    out = run(f, 200, 600, lambda i: 50 + (99 if i % 97 == 0 else 0) * 0 + (49 if i % 97 == 0 else 0) - (50 if i % 131 == 0 else 0) + random.uniform(-6, 6))
    assert max(out) <= 50.01 and min(out) >= 40

def test_fill_up_key_off_accepted():
    f, _ = mk(20)
    out = run(f, 0, 300, lambda i: min(95, i * 2) + random.uniform(-2, 2))
    assert out[-1] > 85

def test_slosh_rise_after_key_on_ignored():
    f, _ = mk(40)
    out = run(f, 0, 300, lambda i: min(44, i) + random.uniform(-1, 1))
    assert out[-1] == 40

def test_fill_up_key_on():
    f, _ = mk(20)
    run(f, 0, 200, lambda i: 20)
    out = run(f, 200, 400, lambda i: 90)
    assert out[-1] > 85 and out[100] < 30

def test_gap_restarts_warmup():
    f, _ = mk(60)
    run(f, 0, 300, lambda i: 60)
    assert f.update(None, 400) is None
    out = run(f, 500, 200, lambda i: i * 0.5)
    assert all(v == 60 for v in out[:WARMUP_S])

def test_no_history_none_until_settled():
    f, _ = mk()
    out = run(f, 0, 200, lambda i: min(30, i * .5))
    assert out[0] is None and out[-1] is not None
