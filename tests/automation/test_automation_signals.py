"""Tests for core/automation/signals.py — known signal collection."""

from __future__ import annotations

import json

from core.automation import signals


class _FakeDB:
    def __init__(self, automations):
        self._autos = automations

    def get_automations(self, profile_id=None):
        # Default scope is the requesting profile only (Admin) — must not be used.
        return [a for a in self._autos if a.get('profile_id', 1) == 1]

    def get_all_automations(self):
        return self._autos


def test_no_automations_returns_empty():
    db = _FakeDB([])
    assert signals.collect_known_signals(db) == []


def test_signal_received_trigger_collected():
    db = _FakeDB([
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'job_done'}), 'then_actions': '[]'},
    ])
    assert signals.collect_known_signals(db) == ['job_done']


def test_fire_signal_then_action_collected():
    db = _FakeDB([
        {'trigger_type': 'schedule', 'trigger_config': '{}',
         'then_actions': json.dumps([{'type': 'fire_signal', 'config': {'signal_name': 'cleanup_done'}}])},
    ])
    assert signals.collect_known_signals(db) == ['cleanup_done']


def test_collected_signals_are_sorted_and_deduped():
    db = _FakeDB([
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'zebra'}), 'then_actions': '[]'},
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'apple'}), 'then_actions': '[]'},
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'apple'}), 'then_actions': '[]'},
    ])
    assert signals.collect_known_signals(db) == ['apple', 'zebra']


def test_empty_signal_name_skipped():
    db = _FakeDB([
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': ''}), 'then_actions': '[]'},
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': '   '}), 'then_actions': '[]'},
    ])
    assert signals.collect_known_signals(db) == []


def test_malformed_trigger_config_swallowed():
    db = _FakeDB([
        {'trigger_type': 'signal_received', 'trigger_config': 'not json', 'then_actions': '[]'},
    ])
    assert signals.collect_known_signals(db) == []


def test_malformed_then_actions_swallowed():
    db = _FakeDB([
        {'trigger_type': 'schedule', 'trigger_config': '{}', 'then_actions': 'not json'},
    ])
    assert signals.collect_known_signals(db) == []


def test_db_failure_returns_empty():
    class _BrokenDB:
        def get_all_automations(self):
            raise RuntimeError("db dead")
    assert signals.collect_known_signals(_BrokenDB()) == []


def test_mixed_trigger_and_action_signals_merged():
    db = _FakeDB([
        {'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'sig_a'}),
         'then_actions': json.dumps([{'type': 'fire_signal', 'config': {'signal_name': 'sig_b'}}])},
    ])
    assert signals.collect_known_signals(db) == ['sig_a', 'sig_b']


def test_non_signal_then_action_ignored():
    db = _FakeDB([
        {'trigger_type': 'schedule', 'trigger_config': '{}',
         'then_actions': json.dumps([
             {'type': 'discord', 'config': {'webhook_url': 'http://x'}},
             {'type': 'fire_signal', 'config': {'signal_name': 'real_sig'}},
         ])},
    ])
    assert signals.collect_known_signals(db) == ['real_sig']


def test_every_profiles_signals_collected():
    db = _FakeDB([
        {'profile_id': 1, 'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'admin_sig'}), 'then_actions': '[]'},
        {'profile_id': 2, 'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'k_in'}),
         'then_actions': json.dumps([{'type': 'fire_signal', 'config': {'signal_name': 'k_out'}}])},
        {'profile_id': 3, 'trigger_type': 'signal_received', 'trigger_config': json.dumps({'signal_name': 'k_in'}),
         'then_actions': json.dumps([{'type': 'fire_signal', 'config': {'signal_name': 't_out'}}])},
    ])
    assert signals.collect_known_signals(db) == ['admin_sig', 'k_in', 'k_out', 't_out']
