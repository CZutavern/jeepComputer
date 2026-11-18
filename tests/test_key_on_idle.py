from dashboard.key_on_idle import KeyOnIdle, WARN_S, CRITICAL_S, CHIME_FIRST_S, CHIME_REPEAT_S, fuel_gauge_off


def mk():
    t = [0.0]
    return KeyOnIdle(clock=lambda: t[0]), t


def step(k, t, dt, **kw):
    t[0] += dt
    args = dict(analog_ok=True, ignition_off=False, engine_running=False, rpm=0)
    args.update(kw)
    return k.update(**args)


def test_warns_and_chimes_at_30_s():
    k, t = mk()
    assert k.level(step(k, t, 0)) is None
    assert not k.chime_due(step(k, t, CHIME_FIRST_S - 1))
    idle = step(k, t, 2)
    assert k.level(idle) == "warning" and k.chime_due(idle)
    assert not k.chime_due(step(k, t, 10))              # not again right away
    assert k.chime_due(step(k, t, CHIME_REPEAT_S))      # repeats
    assert k.level(step(k, t, CRITICAL_S)) == "critical"


def test_running_or_off_resets():
    k, t = mk()
    step(k, t, 0)
    step(k, t, 100)
    assert step(k, t, 1, engine_running=True) == 0
    assert step(k, t, 1) == 0.0
    assert k.level(step(k, t, WARN_S + 1)) == "warning"
    assert step(k, t, 1, ignition_off=True) == 0


def test_cranking_restarts_timer():
    k, t = mk()
    step(k, t, 0)
    step(k, t, 100)
    assert step(k, t, 1, rpm=120) == 0
    assert k.level(step(k, t, 10)) is None
    assert k.level(step(k, t, 15)) is None              # 15 s
    assert k.level(step(k, t, 20)) == "warning"         # 35 s


def test_sensors_offline_no_verdict():
    k, t = mk()
    step(k, t, 0)
    step(k, t, 200)
    assert step(k, t, 1, analog_ok=False) == 0


def test_accessory_counts_as_off_by_the_fuel_gauge():
    assert fuel_gauge_off({"A0": {"voltage": 0.01}, "A2": {"voltage": 0.9}})   # ACC: stray oil voltage, gauges off
    assert not fuel_gauge_off({"A0": {"voltage": 0.55}})
    assert fuel_gauge_off({})
