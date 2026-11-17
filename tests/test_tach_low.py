import time, tempfile, pathlib, threading
from collections import deque
import sensors.tach as T
T.EVENTS_FILE = pathlib.Path(tempfile.mkdtemp())/"e.csv"
t = T.Tach.__new__(T.Tach)
t.lock=threading.Lock(); t.periods=deque(maxlen=T.PERIODS); t.last_tick=None; t.last_spark=None; t.sparks=0; t.restarts=0; t.events=deque(maxlen=5000); t.cylinders=8
for hz,name in [(10,"cranking ~150 rpm"),(3,"~45 rpm"),(1.2,"~18 rpm"),(0.9,"~13 rpm (below 1 s gap)")]:
    t.periods.clear(); t.last_tick=None; tick=0
    for i in range(12):
        t._edge(tick); tick+=int(1e9/hz)
    print(name, "->", t.rpm(), "sparks so far", t.sparks)
# two or three isolated sparks, as a sputtering ignition would give
t.periods.clear(); t.last_tick=None
for tick in (0, 120_000_000, 260_000_000):
    t._edge(tick)
print("3 sparks only -> rpm", t.rpm(), "(0 until 4 sparks, but 'sparks' still counts them)")
t.flush_events(); rows=open(T.EVENTS_FILE).read().splitlines(); print(len(rows), rows[:2])
