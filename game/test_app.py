import unittest

from app import GameSession


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

    def test_creep_jumpscare_waits_two_to_five_seconds(self) -> None:
        session = self.enter_creep()
        delay = session.creep_jumpscare_at - session.encounter_started_at
        self.assertGreaterEqual(delay, 2.0)
        self.assertLessEqual(delay, 5.0)

        session.update(session.creep_jumpscare_at - 0.01, 0.01, False)
        self.assertFalse(session.creep_jumpscared)
        session.update(session.creep_jumpscare_at, 0.01, False)
        self.assertTrue(session.creep_jumpscared)

    def test_creep_requires_five_seconds_without_blinking(self) -> None:
        session = self.enter_creep()
        session.update(session.creep_jumpscare_at, 0.01, False)
        stare_interrupted_at = session.stare_started_at + 0.4
        session.blink(stare_interrupted_at)
        session.update(stare_interrupted_at + 4.99, 0.05, False)
        self.assertEqual(session.encounter.name, "Creep")
        session.update(stare_interrupted_at + 5.001, 0.05, False)
        self.assertEqual(session.encounter.name, "The Fog")

    def test_fog_must_be_closed_within_two_seconds(self) -> None:
        session = self.enter_fog()
        session.update(session.encounter_started_at + 2.01, 0.01, False)
        self.assertEqual(session.result, "lost")

    def test_opening_eyes_before_five_seconds_causes_a_loss(self) -> None:
        session = self.enter_fog()
        fog_started_at = session.encounter_started_at
        session.update(fog_started_at + 1.0, 1.0, True)
        session.update(fog_started_at + 1.1, 0.1, False)
        self.assertEqual(session.result, "lost")

    def test_closing_eyes_in_time_and_holding_for_five_seconds_wins(self) -> None:
        session = self.enter_fog()
        fog_started_at = session.encounter_started_at
        session.update(fog_started_at + 1.0, 1.0, True)
        for elapsed in range(2, 6):
            session.update(fog_started_at + elapsed, 1.0, True)
        self.assertEqual(session.result, "won")

    def test_twentieth_blink_causes_the_angel_to_catch_the_player(self) -> None:
        session = GameSession(0.0)
        for index in range(20):
            session.blink(index * 0.4)
        self.assertEqual(session.angel_blinks, 20)
        self.assertEqual(session.result, "lost")


if __name__ == "__main__":
    unittest.main()
