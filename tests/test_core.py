"""Run with:  python -m unittest discover tests   (or: pytest)"""
import unittest

from labwatch.actions import render_actions
from labwatch.baseline import RuleBasedClient
from labwatch.config import load_layout, load_runbook
from labwatch.pipeline import run_scenario, score
from labwatch.report import validate_report
from labwatch.scenarios import SCENARIOS
from labwatch.simulator import Simulator
from labwatch.tools import Investigation

LAYOUT, RUNBOOK = load_layout(), load_runbook()


def baseline(layout, incident):
    return RuleBasedClient(layout, incident)


class SimulatorTests(unittest.TestCase):
    def test_normal_rooms_hold_pressure(self):
        sim = Simulator(LAYOUT)
        sim.run(60)
        self.assertLess(sim.rooms["412"].dp, -2.5)
        self.assertGreater(sim.rooms["418"].dp, 2.5)

    def test_ahu_failure_only_hits_its_rooms(self):
        sim = Simulator(LAYOUT)
        sim.run(10)
        sim.inject("ahu_failure", ahu="AHU-4")
        sim.run(30)
        for r in ("412", "414", "418"):
            self.assertLess(abs(sim.rooms[r].dp), 1.0, r)
        self.assertLess(sim.rooms["420"].dp, -2.5)

    def test_door_ajar_fools_the_door_sensor(self):
        sim = Simulator(LAYOUT)
        sim.run(10)
        sim.inject("door_ajar", room="414")
        sim.run(20)
        self.assertFalse(sim.rooms["414"].reported_door_open)
        self.assertGreater(sim.rooms["414"].dp, -2.5)

    def test_repair_recovers(self):
        sim = Simulator(LAYOUT)
        sim.inject("ahu_failure", ahu="AHU-4")
        sim.run(30)
        sim.repair()
        sim.run(30)
        self.assertLess(sim.rooms["412"].dp, -2.5)


class SafetyCheckTests(unittest.TestCase):
    def test_valid_action_renders_runbook_text(self):
        out, errors = render_actions([{"id": "N95_ENTRY", "params": {"rooms": ["412"]}}], LAYOUT, RUNBOOK)
        self.assertEqual(errors, [])
        self.assertEqual(out[0]["text"], "N95 respirator required to enter 412.")

    def test_invented_action_rejected(self):
        _, errors = render_actions([{"id": "EVACUATE_FLOOR", "params": {}}], LAYOUT, RUNBOOK)
        self.assertTrue(errors)

    def test_invented_room_rejected(self):
        _, errors = render_actions([{"id": "DOORS_CLOSED", "params": {"rooms": ["999"]}}], LAYOUT, RUNBOOK)
        self.assertTrue(errors)

    def test_backup_room_must_be_isolation(self):
        _, errors = render_actions([{"id": "PREPARE_BACKUP_ROOM", "params": {"backup_room": "422"}}],
                                   LAYOUT, RUNBOOK)
        self.assertTrue(errors)

    def test_fabricated_evidence_rejected(self):
        inv = Investigation(Simulator(LAYOUT), LAYOUT, RUNBOOK)
        inv.sim.run(5)
        inv.call("get_layout", {"rooms": ["412"]})  # creates E1 only
        report = {"status": "diagnosed", "confidence": "high", "affected_rooms": ["412"],
                  "root_cause": {"category": "sensor_fault", "asset": "412", "summary": "x",
                                 "evidence": ["E7"]},
                  "findings": [], "ruled_out": [], "actions": []}
        _, errors = validate_report(report, inv)
        self.assertTrue(any("E7" in e for e in errors))

    def test_tool_errors_are_not_evidence(self):
        inv = Investigation(Simulator(LAYOUT), LAYOUT, RUNBOOK)
        result = inv.call("get_layout", {"rooms": ["payment-svc-2"]})
        self.assertIn("error", result)
        self.assertEqual(inv.ledger, {})


class PipelineTests(unittest.TestCase):
    def test_baseline_solves_every_scenario(self):
        for name in SCENARIOS:
            s = score(run_scenario(name, baseline, seed=3))
            self.assertTrue(s["root_cause_correct"], name)


if __name__ == "__main__":
    unittest.main()
