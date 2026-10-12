from __future__ import annotations

import unittest

from kiwoom_monitor.infrastructure.kiwoom_rest.realtime_registration import (
    RegistrationProgress, all_atoms, group_atoms, plan_registration,
)


def desired(codes):
    return {str(1000 + index): [{'item': codes[start:start+100], 'type': ['0B']}]
            for index, start in enumerate(range(0, len(codes), 100))}


class RegistrationTests(unittest.TestCase):
    def test_reorder_has_no_commands(self):
        codes = [f'{index:06d}' for index in range(101)]
        before = plan_registration(desired(codes)).groups
        plan = plan_registration(desired(list(reversed(codes))), before)
        self.assertEqual((), plan.commands)
        self.assertEqual(before, plan.groups)

    def test_full_replacement_preserves_retained_group_and_never_exceeds_limit(self):
        for count in (99, 100, 101, 200):
            with self.subTest(count=count):
                codes = [f'{index:06d}' for index in range(count)]
                before = plan_registration(desired(codes)).groups
                plan = plan_registration(desired(codes[1:] + ['999999']), before)
                old_positions = {atom: group for group, atoms in group_atoms(before).items() for atom in atoms}
                new_positions = {atom: group for group, atoms in group_atoms(plan.groups).items() for atom in atoms}
                for atom in all_atoms(before) & all_atoms(plan.groups):
                    self.assertEqual(old_positions[atom], new_positions[atom])
                self.assertEqual(['REMOVE', 'REG'], [command.trnm for command in plan.commands])
                progress = RegistrationProgress(plan)
                while not progress.done:
                    command = progress.start()
                    self.assertFalse(progress.mark_sent())
                    self.assertTrue(progress.accept_ack({'trnm': command.trnm, 'return_code': 0}))
                    self.assertLessEqual(sum(len(atoms) for atoms in progress.groups.values()), 200)

    def test_ack_before_send_completion_does_not_approve(self):
        progress = RegistrationProgress(plan_registration(desired(['005930'])))
        progress.start()
        self.assertFalse(progress.accept_ack({'trnm': 'REG', 'return_code': '0'}))
        self.assertFalse(progress.done)
        self.assertEqual({}, progress.groups)
        self.assertTrue(progress.mark_sent())
        self.assertTrue(progress.done)
        with self.assertRaisesRegex(RuntimeError, 'ack_without_command'):
            progress.accept_ack({'trnm': 'REG', 'return_code': 0})

    def test_invalid_or_mismatched_ack_cannot_commit(self):
        for message in ({'trnm': 'REG'}, {'trnm': 'REG', 'return_code': False},
                        {'trnm': 'REG', 'return_code': 0.0}, {'trnm': 'REG', 'return_code': 1},
                        {'trnm': 'REMOVE', 'return_code': 0},
                        {'trnm': 'REG', 'return_code': 0, 'grp_no': '1001'}):
            with self.subTest(message=message):
                progress = RegistrationProgress(plan_registration(desired(['005930'])))
                progress.start()
                progress.mark_sent()
                with self.assertRaises(RuntimeError):
                    progress.accept_ack(message)
                self.assertEqual({}, progress.groups)

    def test_removal_only_and_wildcard_are_real_commands(self):
        before = {'3000': [{'item': [], 'type': ['1h']}, {'item': [''], 'type': ['00']}],
                  **desired(['005930'])}
        plan = plan_registration({'3000': [{'item': [''], 'type': ['00']}]}, before)
        self.assertTrue(all(command.trnm == 'REMOVE' for command in plan.commands))
        self.assertIn({'item': [], 'type': ['1h']}, plan.commands[-1].packet()['data'])
        self.assertEqual({('00', '')}, all_atoms(plan.groups))

    def test_over_limit_is_rejected_before_any_command(self):
        with self.assertRaisesRegex(ValueError, 'type_item_limit'):
            plan_registration(desired([f'{index:06d}' for index in range(201)]))

    def test_mixed_pc_group_splits_before_unique_items_exceed_group_limit(self):
        codes = [f'{index:06d}' for index in range(100)]
        plan = plan_registration({'1':[{'item':codes,'type':['0B','0g']},
                                      {'item':[f'{code}_AL' for code in codes],'type':['0w']},
                                      {'item':[''],'type':['00']}]})
        self.assertEqual(301,len(all_atoms(plan.groups)))
        for atoms in group_atoms(plan.groups).values():
            self.assertLessEqual(len({item for _,item in atoms if item is not None}),100)
