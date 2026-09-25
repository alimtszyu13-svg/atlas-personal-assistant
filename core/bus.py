from dataclasses import dataclass, field
import queue, threading, time, fnmatch, itertools

@dataclass(order=True)
class Event:
    priority: int
    seq: int
    topic: str = field(compare=False)
    data: dict = field(compare=False, default_factory=dict)
    ts: float = field(compare=False, default_factory=time.time)

class Bus:
    def __init__(self):
        self._q = queue.PriorityQueue()
        self._subs = []                      # (pattern, handler)
        self._seq = itertools.count()
        threading.Thread(target=self._loop, daemon=True).start()

    def subscribe(self, pattern, handler):
        self._subs.append((pattern, handler))

    def publish(self, topic, priority=5, **data):
        self._q.put(Event(priority, next(self._seq), topic, data))

    def _loop(self):
        while True:
            ev = self._q.get()
            for pat, h in self._subs:
                if fnmatch.fnmatch(ev.topic, pat):
                    try: h(ev)
                    except Exception as e: print(f"[bus] {pat}: {e}")

bus = Bus()