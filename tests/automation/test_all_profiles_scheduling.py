"""Non-admin profiles' automations must be armed and fired (fork fix).

``get_automations()`` defaults to profile 1, and the engine called it with no
argument, so automations owned by any other profile were never scheduled, never
entered the event cache, and never ran. Real DB, real seeded system rows.
"""

from __future__ import annotations

import json

import pytest

from core.automation import api as automation_api
from core.automation_engine import AutomationEngine
from core.profile_context import get_background_profile
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    return MusicDatabase(str(tmp_path / 'automations.db'))


@pytest.fixture()
def engine(db):
    eng = AutomationEngine(db)
    return eng


def _mk(db, name, profile_id, trigger='schedule', trigger_config=None, action='scan_library'):
    cfg = json.dumps(trigger_config if trigger_config is not None else {'interval': 6, 'unit': 'hours'})
    aid = db.create_automation(name, trigger, cfg, action, '{}', profile_id=profile_id)
    assert aid
    return aid


def test_get_all_automations_returns_every_profile_once(db):
    a1 = _mk(db, 'admin', 1)
    a2 = _mk(db, 'k', 2)
    a3 = _mk(db, 't', 3)
    ids = [a['id'] for a in db.get_all_automations()]
    assert {a1, a2, a3} <= set(ids)
    assert len(ids) == len(set(ids))


def test_get_automations_explicit_profile_still_isolates(db):
    _mk(db, 'admin', 1)
    k = _mk(db, 'k', 2)
    t = _mk(db, 't', 3)
    ids = {a['id'] for a in db.get_automations(2)}
    assert k in ids and t not in ids
    assert {a['id'] for a in db.get_automations()}.isdisjoint({k, t})


def test_list_automations_api_stays_per_profile(db):
    _mk(db, 'admin', 1)
    k = _mk(db, 'k', 2)
    t = _mk(db, 't', 3)
    ids = {a['id'] for a in automation_api.list_automations(db, 2)}
    assert k in ids and t not in ids


def test_start_arms_every_profiles_automations_exactly_once(db, engine):
    a1 = _mk(db, 'admin', 1)
    a2 = _mk(db, 'k', 2)
    a3 = _mk(db, 't', 3)
    armed = []
    engine.schedule_automation = armed.append
    engine.start()
    engine.stop()
    system_ids = [a['id'] for a in db.get_automations() if a.get('is_system')
                  and a.get('enabled') and a.get('trigger_type') in engine._trigger_handlers]
    assert system_ids, 'expected seeded scheduled system automations'
    for aid in (a1, a2, a3, *system_ids):
        assert armed.count(aid) == 1, f'automation {aid} armed {armed.count(aid)} times'


def test_event_cache_includes_non_admin_automations(db, engine):
    k = _mk(db, 'k event', 2, trigger='batch_complete', trigger_config={})
    s = _mk(db, 'k signal', 3, trigger='signal_received', trigger_config={'signal_name': 'go'})
    engine._rebuild_event_cache()
    assert k in engine._event_automations.get('batch_complete', [])
    assert s in engine._event_automations.get('signal:go', [])


def test_event_triggered_non_admin_automation_runs_as_its_own_profile(db, engine):
    seen = []
    engine._action_handlers['scan_library'] = {
        'handler': lambda config: seen.append(get_background_profile()) or {'status': 'completed'},
        'guard': None,
    }
    engine._running = True
    k = _mk(db, 'k event', 2, trigger='batch_complete', trigger_config={})
    engine._run_event_automation(db.get_automation(k), k, {})
    assert seen == [2]


def test_create_time_cycle_check_sees_other_profiles(db):
    t =db.create_automation('t listens x', 'signal_received', json.dumps({'signal_name': 'x'}),
                             'scan_library', '{}', profile_id=3,
                             then_actions=json.dumps([{'type': 'fire_signal', 'config': {'signal_name': 'y'}}]))
    assert t
    eng = AutomationEngine(db)
    then = [{'type': 'fire_signal', 'config': {'signal_name': 'x'}}]
    cycle = automation_api._check_create_cycle(
        eng, db, 2, 'signal_received', json.dumps({'signal_name': 'y'}), json.dumps(then), then)
    assert cycle


def test_non_admin_automation_runs_as_its_own_profile(db, engine):
    seen = []
    engine._action_handlers['scan_library'] = {
        'handler': lambda config: seen.append(get_background_profile()) or {'status': 'completed'},
        'guard': None,
    }
    engine._running = True
    engine.schedule_automation = lambda automation_id: None
    k = _mk(db, 'k', 2)
    engine.run_automation(k, skip_delay=True)
    assert seen == [2]
