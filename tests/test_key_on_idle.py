from dashboard.key_on_idle import KeyOnIdle, WARN_S, CRITICAL_S

def mk():
    t = [0.0]
    k = KeyOnIdle(clock=lambda: t[0])
    return k, t

def step(k, t, dt, **kw):
    t[0] += dt
    args = dict(analog_ok=True, ignition_off=False, engine_running=False, rpm=0)
    args.update(kw)
    return k.update(**args)

def test_warns_after_a_minute():
    k, t = mk()
    assert k.level(step(k, t, 0)) is None
    assert k.level(step(k, t, WARN_S - 1)) is None
    assert k.level(step(k, t, 2)) == "warning"
    assert k.level(step(k, t, CRITICAL_S)) == "critical"

def test_running_or_off_resets():
    k, t = mk(); step(k, t, 0); step(k, t, 100)
    assert step(k, t, 1, engine_running=True) == 0
    assert step(k, t, 1) == 0.0
    assert k.level(step(k, t, WARN_S + 1)) == "warning"
    assert step(k, t, 1, ignition_off=True) == 0

def test_cranking_restarts_timer():
    k, t = mk(); step(k, t, 0); step(k, t, 100)
    assert step(k, t, 1, rpm=120) == 0
    assert k.level(step(k, t, 30)) is None     # first idle sample after cranking starts the clock
    assert k.level(step(k, t, 40)) is None     # 40 s
    assert k.level(step(k, t, 25)) == "warning"  # 65 s

def test_sensors_offline_no_verdict():
    k, t = mk(); step(k, t, 0); step(k, t, 200)
    assert step(k, t, 1, analog_ok=False) == 0
