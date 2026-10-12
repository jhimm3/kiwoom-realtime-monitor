"""Pure, stable item/type placement and one-command-at-a-time approval.

None is the empty-item wildcard, distinct from the account item ''. No socket,
clock, thread or consumer belongs to this module.
"""
from __future__ import annotations

from dataclasses import dataclass

Groups = dict[str, list[dict[str, object]]]
Atom = tuple[str, str | None]


def group_atoms(groups: Groups, *, per_group: bool = True) -> dict[str, set[Atom]]:
    result = {}
    seen: set[Atom] = set()
    for group, parts in groups.items():
        if not isinstance(group, str) or not group.isdigit() or len(group) > 4:
            raise ValueError("realtime_group_invalid")
        atoms = {(kind, item) for part in parts for kind in part['type']
                 for item in (part['item'] or [None])}
        if atoms & seen:
            raise ValueError("realtime_registration_duplicate")
        seen.update(atoms)
        if atoms:
            result[group] = atoms
    _limits(result, per_group=per_group)
    return result


def _limits(groups, *, per_group=True):
    totals = {}
    for atoms in groups.values():
        if per_group and len({item for _,item in atoms if item is not None})>100:
            raise ValueError('realtime_group_item_limit')
        kinds = {kind for kind, _ in atoms}
        for kind in kinds:
            count = sum(item is not None for key, item in atoms if key == kind)
            if per_group and count > 100:
                raise ValueError("realtime_group_item_limit")
            totals[kind] = totals.get(kind, 0) + count
    if any(count > 200 for count in totals.values()):
        raise ValueError("realtime_type_item_limit")


def encode_groups(groups: dict[str, set[Atom]]) -> Groups:
    result = {}
    for group in sorted(groups, key=int):
        atoms = groups[group]
        if not atoms:
            continue
        parts = []
        for kind in sorted({kind for kind, _ in atoms}):
            items = [item for key, item in atoms if key == kind and item is not None]
            if items:
                parts.append({'item': sorted(items), 'type': [kind]})
            if (kind, None) in atoms:
                parts.append({'item': [], 'type': [kind]})
        result[group] = parts
    return result


def all_atoms(groups: Groups) -> set[Atom]:
    return set().union(*group_atoms(groups, per_group=False).values()) if groups else set()


def trade_sources(groups: Groups) -> set[tuple[str, str]]:
    return {(item.removesuffix('_NX').removesuffix('_AL'),
             'NXT' if item.endswith('_NX') else 'SOR' if item.endswith('_AL') else 'KRX')
            for kind, item in all_atoms(groups) if kind == '0B' and item}


def ready_codes(desired: Groups, allowed: set[Atom], codes):
    wanted = all_atoms(desired)
    result = []
    for code in codes:
        needed = {(kind, item) for kind, item in wanted if kind == '0B' and item
                  and item.removesuffix('_NX').removesuffix('_AL') == code}
        if needed and needed.issubset(allowed):
            result.append(code)
    return tuple(result)


def filter_message(message, allowed: set[Atom]):
    """Only market rows are gated; verified account handling remains with owner."""
    if str(message.get('trnm', '')).upper() != 'REAL':
        return message
    value = dict(message)
    value['data'] = [row for row in message.get('data') or () if isinstance(row, dict)
                     and (row.get('type') in {'00', '04', '02'}
                          or (str(row.get('type', '')), None) in allowed
                          or (str(row.get('type', '')), str(row.get('item', ''))) in allowed)]
    return value


@dataclass(frozen=True)
class RegistrationCommand:
    trnm: str
    group: str
    atoms: frozenset[Atom]

    def packet(self):
        value = {'trnm': self.trnm, 'grp_no': self.group,
                 'data': encode_groups({self.group: set(self.atoms)})[self.group]}
        if self.trnm == 'REG':
            value['refresh'] = '1'
        return value


@dataclass(frozen=True)
class RegistrationPlan:
    previous: Groups
    groups: Groups
    commands: tuple[RegistrationCommand, ...]

    @property
    def retained(self):
        return all_atoms(self.previous) & all_atoms(self.groups)


def plan_registration(desired: Groups, previous: Groups | None = None) -> RegistrationPlan:
    before, requested = group_atoms(previous or {}), group_atoms(desired, per_group=False)
    wanted = set().union(*requested.values()) if requested else set()
    after = {group: atoms & wanted for group, atoms in before.items()}
    placed = set().union(*after.values()) if after else set()
    bases = {kind: min(int(group) for group, atoms in requested.items()
                       if any(key == kind for key, _ in atoms))
             for kind, _ in wanted}
    for atom in sorted(wanted - placed, key=lambda value: (value[0], value[1] or '')):
        kind, item = atom
        base = bases[kind]
        group_number = base
        while True:
            group = str(group_number)
            atoms = after.setdefault(group, set())
            count = sum(key == kind and value is not None for key, value in atoms)
            items = {value for _,value in atoms if value is not None}
            if item is None or count < 100 and (item in items or len(items)<100):
                atoms.add(atom)
                break
            group_number += 1
            if group_number > 9999:
                raise ValueError('realtime_group_item_limit')
    _limits(after)
    commands = []
    for trnm in ('REMOVE', 'REG'):
        for group in sorted(set(before) | set(after), key=int):
            removed = before.get(group, set()) - after.get(group, set())
            added = after.get(group, set()) - before.get(group, set())
            delta = removed if trnm == 'REMOVE' else added
            if delta:
                commands.append(RegistrationCommand(trnm, group, frozenset(delta)))
    return RegistrationPlan(encode_groups(before), encode_groups(after), tuple(commands))


class RegistrationProgress:
    """No retries; state advances only after both successful send and ACK."""
    def __init__(self, plan: RegistrationPlan):
        self.plan = plan
        self.groups = group_atoms(plan.previous)
        self.index = 0
        self.started = self.sent = self.acked = False

    @property
    def done(self):
        return self.index == len(self.plan.commands)

    @property
    def command(self):
        return None if self.done else self.plan.commands[self.index]

    def start(self):
        if self.done or self.started:
            raise RuntimeError('realtime_command_already_started')
        self.started = True
        return self.command

    def mark_sent(self):
        if not self.started or self.sent:
            raise RuntimeError('realtime_send_without_command')
        self.sent = True
        return self._commit()

    def accept_ack(self, message):
        command = self.command
        if not self.started or self.acked or command is None:
            raise RuntimeError('realtime_ack_without_command')
        group = message.get('grp_no')
        if (str(message.get('trnm', '')).upper() != command.trnm
                or group is not None and str(group) != command.group):
            raise RuntimeError('realtime_ack_mismatch')
        code = message.get('return_code')
        if not (type(code) is int and code == 0 or type(code) is str and code == '0'):
            raise RuntimeError('realtime_subscription_rejected')
        self.acked = True
        return self._commit()

    def _commit(self):
        if not self.sent or not self.acked:
            return False
        command = self.command
        atoms = self.groups.setdefault(command.group, set())
        if command.trnm == 'REMOVE':
            atoms.difference_update(command.atoms)
        else:
            atoms.update(command.atoms)
        _limits(self.groups)
        self.index += 1
        self.started = self.sent = self.acked = False
        if self.done and encode_groups(self.groups) != self.plan.groups:
            raise RuntimeError('realtime_registration_final_state_mismatch')
        return True
