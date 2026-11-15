import asyncio
import queue
import sys
import threading
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import app
from app import GameSession, calculate_score


class BleStreamTests(unittest.TestCase):
    def test_octopus_connection_uses_timeout_and_enables_notifications(self) -> None:
        device = SimpleNamespace(name="Octopus-16-001D", address="00:11:22:33:44:55")
        advertisement = SimpleNamespace(
            service_uuids=[],
            local_name="Octopus-16-001D",
        )
        client_state = {}

        class FakeScanner:
            @staticmethod
            async def find_device_by_filter(match_filter, timeout):
                self.assertEqual(timeout, 10.0)
                self.assertTrue(match_filter(device, advertisement))
                return device

        class FakeClient:
            is_connected = True

            def __init__(self, found_device, timeout):
                client_state["device"] = found_device
                client_state["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return None

            async def start_notify(self, characteristic, _callback):
                client_state["characteristic"] = characteristic

            async def stop_notify(self, characteristic):
                client_state["stopped"] = characteristic

        fake_bleak = ModuleType("bleak")
        fake_bleak.BleakClient = FakeClient
        fake_bleak.BleakScanner = FakeScanner
        stop_event = threading.Event()
        stop_event.set()
        ui_events = queue.Queue()

        with patch.dict(sys.modules, {"bleak": fake_bleak}):
            asyncio.run(app.stream_ble(stop_event, ui_events))

        self.assertEqual(client_state["device"], device)
        self.assertEqual(client_state["timeout"], 30.0)
        self.assertEqual(client_state["characteristic"], app.NOTIFY_CHAR_UUID)
        self.assertEqual(client_state["stopped"], app.NOTIFY_CHAR_UUID)
        event_types = []
        while not ui_events.empty():
            event_types.append(ui_events.get_nowait()[0])
        self.assertEqual(event_types, ["status", "status", "status", "connected"])


class GameSessionTests(unittest.TestCase):
    def enter_creep(self) -> GameSession:
        session = GameSession(0.0)
        session.blink(0.4)
        session.blink(0.8)
        session.blink(1.2)
        session.blink(1.6)
        self.assertEqual(session.encounter.name, "Creep")
        return session

    def enter_fog(self) -> GameSession:
        session = self.enter_creep()
        session.update(session.creep_jumpscare_at, 0.05, False)
        session.update(session.stare_started_at + 5.001, 0.05, False)
        self.assertEqual(session.encounter.name, "The Fog")
        return session

    def test_default_demon_takes_one_blink_and_boss_takes_three(self) -> None:
        session = GameSession(0.0)
        session.blink(0.4)
        self.assertEqual(session.encounter.name, "Big Meatball")

        session.blink(0.8)
        session.blink(1.2)
        self.assertEqual(session.encounter.name, "Big Meatball")
        session.blink(1.6)
        self.assertEqual(session.encounter.name, "Creep")

    def test_blink_demons_time_out_after_five_seconds(self) -> None:
        session = GameSession(0.0)
        session.update(5.0, 0.05, False)
        self.assertIsNone(session.result)
        session.update(5.01, 0.05, False)
        self.assertEqual(session.result, "lost")
        self.assertIn("Meatball", session.message)

    def test_creep_jumpscare_waits_two_to_five_seconds(self) -> None:
        session = self.enter_creep()
        delay = session.creep_jumpscare_at - session.encounter_started_at
        self.assertGreaterEqual(delay, 2.0)
        self.assertLessEqual(delay, 5.0)

        session.update(session.creep_jumpscare_at - 0.01, 0.01, False)
        self.assertFalse(session.creep_jumpscared)
        session.update(session.creep_jumpscare_at, 0.01, False)
        self.assertTrue(session.creep_jumpscared)

    def test_blinking_while_staring_at_creep_causes_a_loss(self) -> None:
        session = self.enter_creep()
        session.update(session.creep_jumpscare_at, 0.01, False)
        session.blink(session.stare_started_at + 0.4)
        self.assertEqual(session.result, "lost")
        self.assertIn("Creep", session.message)

    def test_creep_disappears_after_five_seconds_without_blinking(self) -> None:
        session = self.enter_creep()
        session.update(session.creep_jumpscare_at, 0.01, False)
        session.update(session.stare_started_at + 4.99, 0.05, False)
        self.assertEqual(session.encounter.name, "Creep")
        session.update(session.stare_started_at + 5.001, 0.05, False)
        self.assertEqual(session.encounter.name, "The Fog")

    def test_weep_resets_when_creep_and_fog_arrive(self) -> None:
        session = self.enter_creep()
        self.assertEqual(session.angel_blinks, 0)

        session.blink(session.encounter_started_at + 0.5)
        self.assertEqual(session.angel_blinks, 1)
        session.update(session.creep_jumpscare_at, 0.01, False)
        session.update(session.stare_started_at + 5.001, 0.05, False)

        self.assertEqual(session.encounter.name, "The Fog")
        self.assertEqual(session.angel_blinks, 0)

    def test_fog_must_be_closed_within_two_seconds(self) -> None:
        session = self.enter_fog()
        session.update(session.encounter_started_at + 2.01, 0.01, False)
        self.assertEqual(session.result, "lost")
        self.assertIn("Fog", session.message)

    def test_random_fog_gives_five_seconds_to_close_eyes(self) -> None:
        session = self.enter_fog()
        tutorial_fog_started_at = session.encounter_started_at
        with patch.object(app.random, "randrange", return_value=3):
            session.update(tutorial_fog_started_at + 1.0, 1.0, True)
            for elapsed in range(2, 6):
                session.update(tutorial_fog_started_at + elapsed, 1.0, True)

        self.assertEqual(session.round_number, 2)
        self.assertEqual(session.encounter.name, "The Fog")
        random_fog_started_at = session.encounter_started_at
        session.update(random_fog_started_at + 2.01, 0.01, False)
        self.assertIsNone(session.result)
        session.update(random_fog_started_at + 5.01, 0.01, False)
        self.assertEqual(session.result, "lost")
        self.assertIn("Fog", session.message)

    def test_opening_eyes_before_five_seconds_causes_a_loss(self) -> None:
        session = self.enter_fog()
        fog_started_at = session.encounter_started_at
        session.update(fog_started_at + 1.0, 1.0, True)
        session.update(fog_started_at + 1.1, 0.1, False)
        self.assertEqual(session.result, "lost")
        self.assertIn("Fog", session.message)

    def test_first_round_fog_advances_to_the_random_phase(self) -> None:
        session = self.enter_fog()
        fog_started_at = session.encounter_started_at
        with patch.object(app.random, "randrange", return_value=0):
            session.update(fog_started_at + 1.0, 1.0, True)
            for elapsed in range(2, 6):
                session.update(fog_started_at + elapsed, 1.0, True)

        self.assertIsNone(session.result)
        self.assertEqual(session.events_defeated, 4)
        self.assertEqual(session.round_number, 2)
        self.assertEqual(session.encounter.name, "Meatball")
        self.assertEqual(session.message, "")

    def test_random_encounters_continue_until_the_player_loses(self) -> None:
        session = self.enter_fog()
        fog_started_at = session.encounter_started_at
        choices = [0, 1, 0, 3]
        with patch.object(
            app.random,
            "randrange",
            side_effect=choices,
        ) as choose_encounter:
            session.update(fog_started_at + 1.0, 1.0, True)
            for elapsed in range(2, 6):
                session.update(fog_started_at + elapsed, 1.0, True)
            self.assertEqual(session.encounter.name, "Meatball")

            now = session.encounter_started_at
            for _ in range(5):
                now += 0.4
                session.blink(now)

        self.assertEqual(session.encounter.name, "The Fog")
        self.assertEqual(session.events_defeated, 7)
        self.assertEqual(session.round_number, 2)
        self.assertIsNone(session.result)
        self.assertEqual(choose_encounter.call_count, 4)

    def test_score_rewards_time_played_and_cleared_encounters(self) -> None:
        self.assertEqual(calculate_score(30.0, 2), 2300)
        self.assertGreater(calculate_score(31.0, 2), calculate_score(30.0, 2))
        self.assertGreater(calculate_score(30.0, 3), calculate_score(30.0, 2))

    def test_twelfth_blink_causes_the_angel_to_catch_the_player(self) -> None:
        session = GameSession(0.0)
        session.round_number = 2
        with patch.object(app.random, "randrange", return_value=0):
            for index in range(12):
                session.blink(index * 0.4)
        self.assertEqual(session.angel_blinks, 12)
        self.assertEqual(session.result, "lost")
        self.assertIn("Weep", session.message)


if __name__ == "__main__":
    unittest.main()
