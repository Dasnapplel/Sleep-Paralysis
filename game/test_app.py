import unittest

from app import GameSession


class GameSessionTests(unittest.TestCase):
    def enter_grin(self) -> GameSession:
        session = GameSession(0.0)
        session.blink(0.4)
        session.blink(0.8)
        session.blink(1.2)
        session.blink(1.6)
        self.assertEqual(session.encounter.name, "The Pale Grin")
        return session

    def enter_fog(self) -> GameSession:
        session = self.enter_grin()
        session.update(6.6, 0.05, False)
        self.assertEqual(session.encounter.name, "The Fog")
        return session

    def test_default_demon_takes_one_blink_and_boss_takes_three(self) -> None:
        session = GameSession(0.0)
        session.blink(0.4)
        self.assertEqual(session.encounter.name, "The Boss")

        session.blink(0.8)
        session.blink(1.2)
        self.assertEqual(session.encounter.name, "The Boss")
        session.blink(1.6)
        self.assertEqual(session.encounter.name, "The Pale Grin")

    def test_blink_demons_time_out_after_five_seconds(self) -> None:
        session = GameSession(0.0)
        session.update(5.0, 0.05, False)
        self.assertIsNone(session.result)
        session.update(5.01, 0.05, False)
        self.assertEqual(session.result, "lost")

    def test_grin_requires_five_seconds_without_blinking(self) -> None:
        session = self.enter_grin()
        session.blink(2.0)
        session.update(6.99, 0.05, False)
        self.assertEqual(session.encounter.name, "The Pale Grin")
        session.update(7.0, 0.05, False)
        self.assertEqual(session.encounter.name, "The Fog")

    def test_fog_requires_a_continuous_five_second_space_hold(self) -> None:
        session = self.enter_fog()
        session.update(7.6, 1.0, True)
        session.update(8.6, 1.0, True)
        session.update(8.7, 0.1, False)
        self.assertEqual(session.closed_eyes_progress, 0.0)

        for index in range(5):
            session.update(9.7 + index, 1.0, True)
        self.assertEqual(session.result, "won")

    def test_twentieth_blink_causes_the_angel_to_catch_the_player(self) -> None:
        session = GameSession(0.0)
        for index in range(20):
            session.blink(index * 0.4)
        self.assertEqual(session.angel_blinks, 20)
        self.assertEqual(session.result, "lost")


if __name__ == "__main__":
    unittest.main()
