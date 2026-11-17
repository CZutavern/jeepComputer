import time, tempfile, pathlib, threading
from collections import deque
import sensors.tach as T
T.EVENTS_FILE = pathlib.Path(tempfile.mkdtemp()) / "e.csv"
t = T.Tach.__new__(T.Tach)
t.lock = threading.Lock(); t.periods = deque(maxlen=T.PERIODS); t.last_tick = None; t.last_spark = None
t.sparks = 0; t.restarts = 0; t.events = deque(maxlen=5000); t.cylinders = 8; t.fall_tick = None; t.widths = deque(maxlen=T.PERIODS)
tick = 0
for i in range(12):
    t._edge(tick); t._rise(tick + 1_800_000); tick += 100_000_000
print("rpm", t.rpm(), "width_us", sorted(set(t.widths)))
time.sleep(0.1); t.flush_events()
rows = open(T.EVENTS_FILE).read().splitlines(); print(len(rows), rows[:3])
t._edge(tick)                      # a spark with no rising edge yet
t.flush_events()                   # too fresh: must stay pending
print("pending", len(t.events))
