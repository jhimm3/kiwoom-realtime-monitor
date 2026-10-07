"""Private deferred delivery layout; the durable event schema stays unchanged.

Retention counters belong to diagnostic_trace and are accessed under its lock.
There is no interning registry: the native message and subscriber own contexts.
"""
from collections.abc import Mapping
import sys
MISSING = object()


def _size(value):
    # Only exact builtin values created by our receipt boundary are accepted.
    if type(value) in (str, int, bool, type(None)):
        return sys.getsizeof(value)
    if type(value) is tuple:
        return sys.getsizeof(value) + sum(_size(item) for item in value)
    raise TypeError('invalid_delivery_record_value')


class Context:
    __slots__ = ('epoch', 'names', 'values', 'children', 'charge', '_refs')

    def __init__(self, epoch, names, values, children=()):
        for name, value in (('epoch', epoch), ('names', names), ('values', values),
                            ('children', children), ('_refs', 0)):
            object.__setattr__(self, name, value)
        # Include allocator/deque slack. Keys are fixed module-level constants.
        object.__setattr__(self, 'charge',
            (sys.getsizeof(self) + _size(values) + sys.getsizeof(children) + 128) * 5 // 4)

    def __setattr__(self, name, value):
        raise AttributeError('immutable_delivery_context')


SOURCE_NAMES = ('source_complete', 'source_component', 'message_id', 'parent_input_ids')
CONTROL_SOURCE_NAMES = SOURCE_NAMES + ('source_kind',)
SUBSCRIBER_NAMES = ('delivery_version', 'trace_id', 'workload_id', 'producer_component', 'subscriber_id')
IDENTITY_NAMES = ('delivery_id', 'delivery_sequence', 'event_kind', 'parser_ordinal')
UNTRACKED_NAMES = ('delivery_id', 'delivery_sequence', 'event_kind', 'source_complete')


class DeliveryIdentity(Mapping):
    __slots__ = ('node',)

    def __init__(self, epoch, values, subscriber, source=None):
        names = IDENTITY_NAMES if source is not None else UNTRACKED_NAMES
        self.node = Context(epoch, names, values, (subscriber, source) if source is not None else (subscriber,))
        object.__setattr__(self.node, 'charge', self.node.charge + sys.getsizeof(self) * 5 // 4)

    def __iter__(self):
        yield from self.node.names
        for child in self.node.children:
            yield from child.names

    def __len__(self):
        return len(self.node.names) + sum(len(child.names) for child in self.node.children)

    def __getitem__(self, key):
        for node in (self.node, *self.node.children):
            try:
                return node.values[node.names.index(key)]
            except ValueError:
                pass
        raise KeyError(key)


class DeliveryStage(Mapping):
    __slots__ = ('identity', 'stage', 'outcome', 'wall_ns', 'mono_ns', 'seq',
                 'producer_id', 'own_charge', 'admission_charge')

    def __init__(self, identity, stage, outcome, wall_ns, mono_ns):
        self.identity, self.stage, self.outcome = identity, stage, outcome
        self.wall_ns, self.mono_ns = wall_ns, mono_ns
        self.seq, self.producer_id, self.admission_charge = 0, '', 0
        self.own_charge = (sys.getsizeof(self) + 3 * sys.getsizeof(wall_ns)
                           + _size(stage) + (0 if outcome is MISSING else _size(outcome)) + 192) * 5 // 4

    def __iter__(self):
        yield from self.identity
        yield from ('stage', 'wall_ns', 'mono_ns', 'event_type', 'seq', 'producer_id',
                    '_memory_charge', '_scalar_charge')
        if self.outcome is not MISSING:
            yield 'outcome'

    def __len__(self):
        return len(self.identity) + 8 + (self.outcome is not MISSING)

    def __getitem__(self, key):
        if key == 'event_type':
            return 'top20_delivery'
        if key in ('_memory_charge', '_scalar_charge'):
            return self.admission_charge
        if key in ('stage', 'wall_ns', 'mono_ns', 'seq', 'producer_id'):
            return getattr(self, key)
        if key == 'outcome' and self.outcome is not MISSING:
            return self.outcome
        return self.identity[key]


def additional_charge(record):
    """Preview an admission without changing ownership, even when rejected."""
    def missing(node):
        if node._refs:
            return 0
        return node.charge + sum(missing(child) for child in node.children)
    return record.own_charge + missing(record.identity.node)


def retain(record):
    def acquire(node):
        object.__setattr__(node, '_refs', node._refs + 1)
        if node._refs == 1:
            for child in node.children:
                acquire(child)
    acquire(record.identity.node)


def release(record):
    def relinquish(node):
        if node._refs <= 0:
            raise RuntimeError('delivery_retention_underflow')
        object.__setattr__(node, '_refs', node._refs - 1)
        return (node.charge + sum(relinquish(child) for child in node.children)
                if node._refs == 0 else 0)
    return record.own_charge + relinquish(record.identity.node)
