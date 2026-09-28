"""
Every arrival, placement decision, burst-detector verdict, migration, and
completion gets logged here as a flat record. Timestamps are floats
(simulation ms) so sub-millisecond burst events are distinguishable.
"""


class EventLog:
    def __init__(self):
        self.events = []

    def log(self, t, kind, **fields):
        rec = {"t": t, "kind": kind}
        rec.update(fields)
        self.events.append(rec)

    def filter(self, kind):
        return [e for e in self.events if e["kind"] == kind]

    def to_csv(self, path):
        import csv
        if not self.events:
            return
        keys = sorted({k for e in self.events for k in e.keys()})
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for e in self.events:
                w.writerow(e)