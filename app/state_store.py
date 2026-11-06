"""Per-record Redis state, with nested mutation tracking and batched writes."""
import asyncio
import json
import math
import time


def tracked(value, changed):
    if isinstance(value, dict):
        return TrackedDict(value, changed)
    if isinstance(value, list):
        return TrackedList(value, changed)
    return value


class TrackedDict(dict):
    def __init__(self, values, changed):
        self.changed = changed
        dict.__init__(self, ((k, tracked(v, changed)) for k, v in values.items()))

    def __setitem__(self, key, value):
        if key not in self or self[key] != value:
            dict.__setitem__(self, key, tracked(value, self.changed))
            self.changed()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self.changed()

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def pop(self, key, *default):
        if key in self:
            result = dict.pop(self, key)
            self.changed()
            return result
        if default:
            return default[0]
        raise KeyError(key)

    def clear(self):
        if self:
            dict.clear(self)
            self.changed()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]


class TrackedList(list):
    def __init__(self, values, changed):
        self.changed = changed
        list.__init__(self, (tracked(v, changed) for v in values))

    def append(self, value):
        list.append(self, tracked(value, self.changed))
        self.changed()

    def extend(self, values):
        values = list(values)
        if values:
            list.extend(self, (tracked(v, self.changed) for v in values))
            self.changed()

    def __setitem__(self, key, value):
        value = [tracked(v, self.changed) for v in value] if isinstance(key, slice) else tracked(value, self.changed)
        list.__setitem__(self, key, value)
        self.changed()

    def __delitem__(self, key):
        list.__delitem__(self, key)
        self.changed()

    def pop(self, index=-1):
        result = list.pop(self, index)
        self.changed()
        return result

    def clear(self):
        if self:
            list.clear(self)
            self.changed()

    def insert(self, index, value):
        list.insert(self, index, tracked(value, self.changed))
        self.changed()

    def remove(self, value):
        list.remove(self, value)
        self.changed()


class Table(dict):
    def __init__(self, store, name):
        self.store, self.name = store, name
        dict.__init__(self)

    def __setitem__(self, key, value):
        if key not in self or self[key] != value:
            dict.__setitem__(self, key, tracked(value, lambda: self.store.mark(self.name, key)))
            self.store.mark(self.name, key)

    def update(self, values):
        for key, value in values.items():
            self[key] = value

    def pop(self, key, *default):
        if key in self:
            value = dict.pop(self, key)
            self.store.mark(self.name, key)
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def clear(self):
        for key in list(self):
            self.pop(key)


class StateStore:
    def __init__(self, prefix='media-site:v2', on_change=None):
        self.prefix = prefix
        self.tables = {name: Table(self, name) for name in ('jobs', 'contents', 'limits')}
        self.dirty = {}
        self.revision = 0
        self.lock = asyncio.Lock()
        self.on_change = on_change
        self.wake = asyncio.Event()
        self.redis = None
        self.writes = 0

    def mark(self, table, key):
        self.revision += 1
        self.dirty[table, key] = self.revision
        self.wake.set()
        if self.on_change:
            self.on_change(table, key)

    def key(self, table, key):
        return f'{self.prefix}:{table}:{key}'

    @staticmethod
    def ttl(table, value):
        if table == 'limits':
            return max(1, math.ceil(max(value, default=time.time()) + 600 - time.time()))
        if table == 'jobs':
            if value.get('state') not in ('done','partial','failed','cancelled'):
                # Covers queue + one-hour processing, including restart recovery.
                return 7200
            return max(1, math.ceil(value.get('finished_at',value.get('time',time.time())) + 3600 - time.time()))
        return max(1, math.ceil(value.get('time', time.time()) + 3600 - time.time()))

    async def load(self, client, legacy_key=None):
        self.redis = client
        self.dirty.clear()
        for table in self.tables.values():
            dict.clear(table)
        initialized = await client.exists(f'{self.prefix}:initialized')
        if not initialized and legacy_key:
            raw = await client.get(legacy_key)
            if raw:
                legacy = json.loads(raw)
                for name, table in self.tables.items():
                    table.update(legacy.get(name, {}))
                await self.flush()
        # A SCAN is only needed once, on startup. Reads afterwards stay in memory.
        for name, table in self.tables.items():
            batch = []
            async for key in client.scan_iter(match=f'{self.prefix}:{name}:*', count=100):
                batch.append(key)
                if len(batch) >= 100:
                    await self._load_batch(name, table, batch)
                    batch = []
            if batch:
                await self._load_batch(name, table, batch)
        self.dirty.clear()
        self.wake.clear()
        await client.set(f'{self.prefix}:initialized', '1')
        if legacy_key:
            await client.delete(legacy_key)

    async def _load_batch(self, name, table, keys):
        values = await self.redis.mget(keys)
        prefix = f'{self.prefix}:{name}:'
        for key, raw in zip(keys, values):
            if raw is None:
                continue
            item = key[len(prefix):]
            value = json.loads(raw)
            dict.__setitem__(table, item, tracked(value, lambda name=name, item=item: self.mark(name, item)))

    async def flush(self):
        async with self.lock:
            snapshot = dict(self.dirty)
            if not snapshot:
                return 0
            pipe = self.redis.pipeline(transaction=False)
            for (name, key), revision in snapshot.items():
                table = self.tables[name]
                redis_key = self.key(name, key)
                if key in table:
                    value = table[key]
                    pipe.set(redis_key, json.dumps(value, ensure_ascii=False, separators=(',', ':')), ex=self.ttl(name, value))
                else:
                    pipe.delete(redis_key)
            # Only acknowledge versions actually written. A mutation during this
            # await remains dirty and is saved by the next pass.
            await pipe.execute()
            for item, revision in snapshot.items():
                if self.dirty.get(item) == revision:
                    del self.dirty[item]
            if not self.dirty:
                self.wake.clear()
            self.writes += len(snapshot)
            return len(snapshot)
