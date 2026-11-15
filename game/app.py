import asyncio
from array import array
from bisect import bisect_right
import math
import queue
import random
import threading
import time
from pathlib import Path
from typing import NamedTuple, Optional

import pygame
from PIL import Image

SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
NOTIFY_CHAR_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"
PACKET_SIZE = 51
START_MARKER = 0xA0
END_MARKER = 0xC0
NUM_CHANNELS = 16
UV_SCALE = (1.2 / 4.0 / 8388607.0) * 1_000_000.0
BLINK_THRESHOLD_UV = 115.0
BLINK_COOLDOWN_SECONDS = 0.35

WIDTH = 1280
HEIGHT = 720
ENCOUNTER_SECONDS = 5.0
STARE_SECONDS = 5.0
CREEP_JUMPSCARE_MIN_SECONDS = 2.0
CREEP_JUMPSCARE_MAX_SECONDS = 5.0
FOG_CLOSE_DEADLINE_SECONDS = 2.0
CLOSED_EYES_SECONDS = 5.0
ANGEL_BLINK_LIMIT = 20
SCORE_PER_SECOND = 10
SCORE_PER_EVENT = 1000
SOUND_SAMPLE_RATE = 22050

PALE = (235, 231, 221)
AMBER = (255, 190, 104)
RED = (228, 107, 111)
GREEN = (158, 220, 174)
WHITE = (255, 255, 255)


def calculate_score(time_played: float, events_defeated: int) -> int:
    time_points = round(max(0.0, time_played) * SCORE_PER_SECOND)
    event_points = max(0, events_defeated) * SCORE_PER_EVENT
    return time_points + event_points


class Encounter(NamedTuple):
    name: str
    background: str
    sprite: Optional[str]
    input_type: str
    blink_goal: int = 0


ENCOUNTERS = (
    Encounter(
        "Meatball",
        "Moonlit Space-Themed Bedroom.png",
        "demon_transparent.gif",
        "blink",
        1,
    ),
    Encounter(
        "Big Meatball",
        "Moonlit Space-Themed Bedroom.png",
        "boss_demon.gif",
        "blink",
        3,
    ),
    Encounter(
        "Creep",
        "Moonlit Bedroom and Open Door.png",
        "pale_grin_demon_still_face.gif",
        "stare",
    ),
    Encounter(
        "The Fog",
        "Moonlit Starry Bedroom with Open Window.png",
        None,
        "hold",
    ),
)


class GameSession:
    def __init__(self, now: float) -> None:
        self.encounter_index = 0
        self.encounter_serial = 0
        self.round_number = 1
        self.encounter_started_at = now
        self.stare_started_at = now
        self.last_blink_at = float("-inf")
        self.blink_progress = 0
        self.angel_blinks = 0
        self.events_defeated = 0
        self.creep_jumpscared = False
        self.creep_jumpscare_at: Optional[float] = None
        self.closed_eyes_started = False
        self.closed_eyes_progress = 0.0
        self.result: Optional[str] = None
        self.message = ""
        self._set_encounter_message()

    @property
    def encounter(self) -> Encounter:
        return ENCOUNTERS[self.encounter_index]

    def blink(self, now: float) -> None:
        if self.result is not None or now - self.last_blink_at < BLINK_COOLDOWN_SECONDS:
            return

        self.last_blink_at = now
        self.angel_blinks += 1
        if self.angel_blinks >= ANGEL_BLINK_LIMIT:
            self.result = "lost"
            self.message = "Weep reached you. Press R to try again."
            return

        if self.encounter.input_type == "blink":
            self.blink_progress += 1
            if self.blink_progress >= self.encounter.blink_goal:
                self._advance(now)
            else:
                self.message = (
                    f"{self.encounter.name}: {self.blink_progress}/"
                    f"{self.encounter.blink_goal} blinks. Weep moves closer."
                )
        elif self.encounter.input_type == "stare":
            if self.creep_jumpscared:
                self.result = "lost"
                self.message = "You blinked while staring at Creep. Press R to try again."
            else:
                self.message = "The open door waits. Watch for Creep."
        else:
            self.message = "Weep moves closer. Keep holding SPACE to close your eyes."

    def update(self, now: float, dt: float, eyes_closed: bool) -> None:
        if self.result is not None:
            return

        if self.encounter.input_type == "blink":
            if now - self.encounter_started_at > ENCOUNTER_SECONDS:
                self.result = "lost"
                self.message = f"{self.encounter.name} stayed too long. Press R to try again."
        elif self.encounter.input_type == "stare":
            if not self.creep_jumpscared and self.creep_jumpscare_at is not None:
                if now >= self.creep_jumpscare_at:
                    self.creep_jumpscared = True
                    self.stare_started_at = now
                    self.message = "Creep jumpscares you! Do not blink for 5 seconds."
            elif now - self.stare_started_at >= STARE_SECONDS:
                self._advance(now)
        elif self.encounter.input_type == "hold":
            close_deadline_passed = (
                now - self.encounter_started_at > FOG_CLOSE_DEADLINE_SECONDS
            )
            if not self.closed_eyes_started and close_deadline_passed:
                self.result = "lost"
                self.message = "The fog caught you. Press R to try again."
                return
            if eyes_closed:
                self.closed_eyes_started = True
                self.closed_eyes_progress = min(
                    CLOSED_EYES_SECONDS,
                    self.closed_eyes_progress + dt,
                )
            elif self.closed_eyes_started:
                self.result = "lost"
                self.message = "You opened your eyes too soon. Press R to try again."
                return
            if self.closed_eyes_progress >= CLOSED_EYES_SECONDS:
                self._advance(now)

    def _advance(self, now: float) -> None:
        self.events_defeated += 1
        if self.round_number == 1 and self.encounter_index + 1 < len(ENCOUNTERS):
            self.encounter_index += 1
        else:
            self.round_number = 2
            self.encounter_index = random.randrange(len(ENCOUNTERS))
        self.encounter_serial += 1
        self.encounter_started_at = now
        self.stare_started_at = now
        self.blink_progress = 0
        self.creep_jumpscared = False
        self.creep_jumpscare_at = None
        self.closed_eyes_started = False
        self.closed_eyes_progress = 0.0
        if self.encounter.input_type in ("stare", "hold"):
            self.angel_blinks = 0
        if self.encounter.input_type == "stare":
            delay = random.uniform(
                CREEP_JUMPSCARE_MIN_SECONDS,
                CREEP_JUMPSCARE_MAX_SECONDS,
            )
            self.creep_jumpscare_at = now + delay
        self._set_encounter_message()

    def _set_encounter_message(self) -> None:
        if self.round_number > 1:
            self.message = ""
            return
        if self.encounter.input_type == "blink":
            self.message = (
                f"{self.encounter.name} is here. Blink "
                f"{self.encounter.blink_goal} time"
                f"{'s' if self.encounter.blink_goal != 1 else ''} to banish it."
            )
        elif self.encounter.input_type == "stare":
            self.message = "The door opens. Creep will strike in 2–5 seconds."
        else:
            self.message = (
                "The Fog is coming! Close your eyes within 2 seconds, "
                "then hold SPACE closed for 5."
            )


class AnimatedSprite:
    def __init__(self, path: Path, size: tuple[int, int]) -> None:
        self.frames: list[pygame.Surface] = []
        durations: list[int] = []
        with Image.open(path) as image:
            for index in range(image.n_frames):
                image.seek(index)
                duration = max(20, int(image.info.get("duration", 50)))
                if index % 2 == 0:
                    frame = image.convert("RGBA")
                    frame.thumbnail(size, Image.Resampling.LANCZOS)
                    surface = pygame.image.fromstring(
                        frame.tobytes(),
                        frame.size,
                        "RGBA",
                    ).convert_alpha()
                    self.frames.append(surface)
                    durations.append(duration)
                else:
                    durations[-1] += duration

        self.frame_boundaries: list[int] = []
        total_duration = 0
        for duration in durations:
            total_duration += duration
            self.frame_boundaries.append(total_duration)
        self.total_duration = total_duration
        self.started_at = time.monotonic()
        self.scaled_frame_index: Optional[int] = None
        self.scaled_frame_size: Optional[tuple[int, int]] = None
        self.scaled_frame: Optional[pygame.Surface] = None

    def frame_at(
        self,
        now: float,
        size: Optional[tuple[int, int]] = None,
    ) -> pygame.Surface:
        elapsed_ms = int((now - self.started_at) * 1000) % self.total_duration
        frame_index = bisect_right(self.frame_boundaries, elapsed_ms)
        frame = self.frames[frame_index]
        if size is None:
            return frame
        if frame_index != self.scaled_frame_index or size != self.scaled_frame_size:
            self.scaled_frame = pygame.transform.smoothscale(frame, size)
            self.scaled_frame_index = frame_index
            self.scaled_frame_size = size
        assert self.scaled_frame is not None
        return self.scaled_frame


class SoundEffects:
    def __init__(self, assets_dir: Path) -> None:
        self.door_open = pygame.mixer.Sound(
            str(assets_dir / "DOOR CREAKING SOUND EFFECT - FREE.mp3")
        )
        self.window_open = pygame.mixer.Sound(
            str(assets_dir / "Open and Close Window - Sound Effect for editing.mp3")
        )
        self.cricket = self._make_sound(self._cricket_samples())
        self.door_open.set_volume(0.55)
        self.window_open.set_volume(0.45)
        self.cricket.set_volume(0.12)

    @staticmethod
    def _make_sound(samples: array) -> pygame.mixer.Sound:
        return pygame.mixer.Sound(buffer=samples.tobytes())

    @staticmethod
    def _cricket_samples() -> array:
        duration = 0.72
        chirp_starts = (0.04, 0.22, 0.40, 0.58)
        chirp_duration = 0.055
        samples = array("h")
        for index in range(round(SOUND_SAMPLE_RATE * duration)):
            t = index / SOUND_SAMPLE_RATE
            value = 0.0
            for start in chirp_starts:
                local_t = t - start
                if 0.0 <= local_t <= chirp_duration:
                    fade_in = min(1.0, local_t / 0.004)
                    fade_out = min(1.0, (chirp_duration - local_t) / 0.008)
                    envelope = fade_in * fade_out
                    frequency = 4700.0 + 180.0 * math.sin(local_t * 45.0)
                    value += 0.25 * math.sin(2.0 * math.pi * frequency * local_t) * envelope
            samples.append(round(32767 * max(-1.0, min(1.0, value))))
        return samples


def decode_octopus16_packet(packet: bytes) -> Optional[list[float]]:
    if len(packet) < PACKET_SIZE:
        return None
    if packet[0] != START_MARKER or packet[PACKET_SIZE - 1] != END_MARKER:
        return None

    channels = []
    for channel in range(NUM_CHANNELS):
        offset = 2 + channel * 3
        raw = (packet[offset] << 16) | (packet[offset + 1] << 8) | packet[offset + 2]
        if raw & 0x800000:
            raw -= 1 << 24
        channels.append(raw * UV_SCALE)
    return channels


async def stream_ble(stop_event: threading.Event, ui_events: queue.Queue) -> None:
    from bleak import BleakClient, BleakScanner

    def matches_octopus(device, advertisement) -> bool:
        service_uuids = [value.lower() for value in (advertisement.service_uuids or [])]
        if SERVICE_UUID in service_uuids:
            return True
        name = (device.name or advertisement.local_name or "").lower()
        return name.startswith(("octopus", "octopos", "bioron", "pieeg"))

    ui_events.put(("status", "Searching for Octopus 16..."))
    device = await BleakScanner.find_device_by_filter(matches_octopus, timeout=10.0)
    if device is None:
        raise RuntimeError("No Octopus 16 found. Check power and Bluetooth, then try again.")

    ui_events.put(("status", f"Found {device.name or 'Octopus 16'}; connecting..."))
    last_blink_time = 0.0
    async with BleakClient(
        device,
        timeout=30.0,
    ) as client:
        ui_events.put(("status", "Connected; enabling blink notifications..."))

        def on_notification(_sender, data: bytearray) -> None:
            nonlocal last_blink_time
            for offset in range(0, len(data) - PACKET_SIZE + 1, PACKET_SIZE):
                channels = decode_octopus16_packet(data[offset:offset + PACKET_SIZE])
                if channels is None:
                    continue
                peak_uv = 0.0
                for value in channels:
                    magnitude = abs(value)
                    if magnitude > peak_uv:
                        peak_uv = magnitude
                now = time.monotonic()
                if peak_uv >= BLINK_THRESHOLD_UV and now - last_blink_time >= BLINK_COOLDOWN_SECONDS:
                    last_blink_time = now
                    ui_events.put(("blink", peak_uv))

        await client.start_notify(NOTIFY_CHAR_UUID, on_notification)
        ui_events.put(("connected", device.name or "Octopus 16"))
        while client.is_connected and not stop_event.is_set():
            await asyncio.sleep(0.1)
        if client.is_connected:
            await client.stop_notify(NOTIFY_CHAR_UUID)


def main() -> None:
    assets_dir = Path(__file__).resolve().parent / "assets"
    pygame.mixer.pre_init(
        frequency=SOUND_SAMPLE_RATE,
        size=-16,
        channels=1,
        buffer=512,
    )
    pygame.init()
    audio_error: Optional[str] = None
    sound_effects: Optional[SoundEffects] = None
    try:
        if pygame.mixer.get_init() != (SOUND_SAMPLE_RATE, -16, 1):
            pygame.mixer.quit()
            pygame.mixer.init(
                frequency=SOUND_SAMPLE_RATE,
                size=-16,
                channels=1,
                buffer=512,
            )
        pygame.mixer.set_num_channels(8)
        sound_effects = SoundEffects(assets_dir)
    except pygame.error as error:
        audio_error = f"Audio unavailable: {error}"

    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Sleep Paralyzer — A PiEEG Blink-Controlled Horror Game")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)
    small_font = pygame.font.SysFont("consolas", 14)
    screen_title_font = pygame.font.SysFont("consolas", 64, bold=True)
    section_title_font = pygame.font.SysFont("consolas", 42, bold=True)
    menu_button_font = pygame.font.SysFont("consolas", 24, bold=True)
    def load_background(filename: str) -> tuple[pygame.Surface, tuple[int, int]]:
        image = pygame.image.load(str(assets_dir / filename)).convert()
        image_width, image_height = image.get_size()
        scale = max(WIDTH / image_width, HEIGHT / image_height)
        scaled_size = (round(image_width * scale), round(image_height * scale))
        image = pygame.transform.smoothscale(image, scaled_size)
        offset = ((WIDTH - scaled_size[0]) // 2, (HEIGHT - scaled_size[1]) // 2)
        return image, offset

    backgrounds = {
        encounter.background: load_background(encounter.background)
        for encounter in ENCOUNTERS
    }
    sprites = {
        "demon_transparent.gif": AnimatedSprite(
            assets_dir / "demon_transparent.gif",
            (340, 340),
        ),
        "boss_demon.gif": AnimatedSprite(
            assets_dir / "boss_demon.gif",
            (410, 410),
        ),
        "pale_grin_demon_still_face.gif": AnimatedSprite(
            assets_dir / "pale_grin_demon_still_face.gif",
            (1500, 1500),
        ),
    }
    stone_angel = pygame.image.load(
        str(assets_dir / "stone_angel_animatronic.png")
    ).convert_alpha()
    angel_frames: dict[tuple[int, int], pygame.Surface] = {}

    ui_events: queue.Queue = queue.Queue()
    ble_stop = threading.Event()
    ble_thread: Optional[threading.Thread] = None
    running = True
    input_mode = "SIMULATOR"
    connection_status = "Simulator ready — SPACE = blink"
    now = time.monotonic()
    session = GameSession(now)
    eyes_closed_held = False
    screen_state = "title"
    game_started_at = now
    game_time_played = 0.0
    next_cricket_at = now + random.uniform(5.0, 9.0)

    play_button = pygame.Rect(500, 284, 280, 62)
    credits_button = pygame.Rect(500, 365, 280, 56)
    credits_back_button = pygame.Rect(500, 490, 280, 56)
    connect_button = pygame.Rect(55, 590, 350, 48)
    simulator_button = pygame.Rect(465, 590, 350, 48)
    disconnect_button = pygame.Rect(875, 590, 350, 48)
    restart_button = pygame.Rect(385, 480, 230, 58)
    result_menu_button = pygame.Rect(665, 480, 230, 58)
    menu_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    menu_overlay.fill((0, 0, 0, 170))
    hud_panel = pygame.Surface((WIDTH, 104), pygame.SRCALPHA)
    hud_panel.fill((8, 12, 22, 220))
    fog_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    creep_flash_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)

    def start_ble() -> None:
        nonlocal ble_thread, input_mode, connection_status
        if ble_thread is not None and ble_thread.is_alive():
            connection_status = "BLE connection is already starting or active."
            return
        ble_stop.clear()
        input_mode = "BLE CONNECTING"
        connection_status = "Searching for Octopus 16..."

        def run_connection() -> None:
            try:
                asyncio.run(stream_ble(ble_stop, ui_events))
                ui_events.put(("status", "BLE disconnected. Simulator is still available."))
            except TimeoutError:
                ui_events.put(
                    (
                        "error",
                        "BLE connection timed out. Check that the Octopus is powered, "
                        "nearby, and not connected to another app.",
                    )
                )
            except Exception as error:
                detail = str(error).strip() or type(error).__name__
                ui_events.put(("error", detail))

        ble_thread = threading.Thread(target=run_connection, daemon=True)
        ble_thread.start()

    def switch_to_simulator() -> None:
        nonlocal input_mode, connection_status
        ble_stop.set()
        input_mode = "SIMULATOR"
        connection_status = "Simulator ready — SPACE = blink"

    def draw_button(rect: pygame.Rect, label: str, selected: bool = False) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        if hovered:
            fill = (88, 119, 152) if selected else (52, 75, 102)
            border = AMBER
            border_width = 3
            text_color = WHITE
        else:
            fill = (64, 88, 112) if selected else (31, 42, 60)
            border = (151, 166, 178)
            border_width = 2
            text_color = PALE
        pygame.draw.rect(screen, fill, rect)
        pygame.draw.rect(screen, border, rect, border_width)
        text = menu_button_font.render(label, True, text_color)
        screen.blit(text, text.get_rect(center=rect.center))

    def draw_menu_background() -> None:
        background, background_offset = backgrounds["Moonlit Space-Themed Bedroom.png"]
        screen.blit(background, background_offset)
        screen.blit(menu_overlay, (0, 0))

    def draw_title_screen() -> None:
        draw_menu_background()
        title = screen_title_font.render("SLEEP PARALYZER", True, PALE)
        screen.blit(title, title.get_rect(center=(WIDTH // 2, 170)))
        draw_button(play_button, "PLAY")
        draw_button(credits_button, "CREDITS")

        status_color = GREEN if input_mode == "SIMULATOR" else AMBER
        status = small_font.render(connection_status, True, status_color)
        status_y = 535 if audio_error else 550
        screen.blit(status, status.get_rect(center=(WIDTH // 2, status_y)))
        if audio_error:
            warning = small_font.render(audio_error, True, RED)
            screen.blit(warning, warning.get_rect(center=(WIDTH // 2, 558)))
        draw_button(connect_button, "Connect Octopus 16 (BLE)", input_mode.startswith("BLE"))
        draw_button(simulator_button, "Simulator Mode", input_mode == "SIMULATOR")
        draw_button(disconnect_button, "Disconnect")

    def draw_credits_screen() -> None:
        draw_menu_background()
        heading = section_title_font.render("CREDITS", True, PALE)
        screen.blit(heading, heading.get_rect(center=(WIDTH // 2, 165)))
        for index, name in enumerate(("Diego de Jong", "Ryuk Jones", "Victor Nwokoye")):
            text = menu_button_font.render(name, True, PALE)
            screen.blit(text, text.get_rect(center=(WIDTH // 2, 270 + index * 64)))
        draw_button(credits_back_button, "BACK")

    def draw_game_hud(now: float) -> None:
        screen.blit(hud_panel, (0, 0))
        if session.round_number == 1:
            encounter_label = (
                f"{session.encounter.name.upper()}  "
                f"{session.encounter_index + 1:02d}/{len(ENCOUNTERS)}"
            )
        else:
            encounter_label = (
                f"{session.encounter.name.upper()}  "
                f"#{session.events_defeated + 1:02d}"
            )
        screen.blit(font.render(encounter_label, True, PALE), (28, 20))
        weep_label = f"WEEP  {session.angel_blinks:02d}/{ANGEL_BLINK_LIMIT}"
        screen.blit(
            small_font.render(
                weep_label,
                True,
                RED if session.angel_blinks >= 15 else AMBER,
            ),
            (360, 24),
        )
        input_label = small_font.render(f"INPUT  {input_mode}", True, GREEN if input_mode == "SIMULATOR" else AMBER)
        screen.blit(input_label, (600, 24))
        show_instructions = session.round_number == 1

        if session.encounter.input_type == "blink":
            remaining = max(
                0.0,
                ENCOUNTER_SECONDS - (now - session.encounter_started_at),
            )
            progress_label = (
                f"TIME TO ACT {remaining:0.1f}s"
                if show_instructions
                else f"THREAT {remaining:0.1f}s"
            )
            progress = 1.0 - remaining / ENCOUNTER_SECONDS
            progress_color = AMBER if remaining > 2.0 else RED
        elif (
            session.encounter.input_type == "stare"
            and not session.creep_jumpscared
            and session.creep_jumpscare_at is not None
        ):
            delay = session.creep_jumpscare_at - session.encounter_started_at
            remaining = max(0.0, session.creep_jumpscare_at - now)
            progress_label = (
                f"CREEP IN {remaining:0.1f}s"
                if show_instructions
                else f"CREEP {remaining:0.1f}s"
            )
            progress = 1.0 - remaining / delay
            progress_color = AMBER if remaining > 1.0 else RED
        elif session.encounter.input_type == "stare":
            stared = min(STARE_SECONDS, now - session.stare_started_at)
            progress_label = (
                f"NO BLINK {stared:0.1f}/{STARE_SECONDS:.0f}s"
                if show_instructions
                else f"STARE {stared:0.1f}/{STARE_SECONDS:.0f}s"
            )
            progress = stared / STARE_SECONDS
            progress_color = GREEN
        elif not session.closed_eyes_started:
            remaining = max(
                0.0,
                FOG_CLOSE_DEADLINE_SECONDS - (now - session.encounter_started_at),
            )
            progress_label = (
                f"CLOSE EYES {remaining:0.1f}s"
                if show_instructions
                else f"FOG IN {remaining:0.1f}s"
            )
            progress = 1.0 - remaining / FOG_CLOSE_DEADLINE_SECONDS
            progress_color = AMBER if remaining > 1.0 else RED
        else:
            progress_label = (
                f"HOLD CLOSED {session.closed_eyes_progress:0.1f}/"
                f"{CLOSED_EYES_SECONDS:.0f}s"
                if show_instructions
                else f"FOG {session.closed_eyes_progress:0.1f}/"
                f"{CLOSED_EYES_SECONDS:.0f}s"
            )
            progress = session.closed_eyes_progress / CLOSED_EYES_SECONDS
            progress_color = GREEN

        screen.blit(small_font.render(progress_label, True, progress_color), (980, 20))
        pygame.draw.rect(screen, (46, 49, 62), pygame.Rect(980, 48, 260, 14))
        pygame.draw.rect(
            screen,
            progress_color,
            pygame.Rect(980, 48, int(260 * max(0.0, min(progress, 1.0))), 14),
        )

        if show_instructions:
            message_box = pygame.Rect(110, HEIGHT - 78, WIDTH - 220, 52)
            pygame.draw.rect(screen, (8, 12, 22), message_box)
            pygame.draw.rect(screen, (123, 137, 151), message_box, 2)
            text = font.render(session.message, True, PALE)
            screen.blit(text, text.get_rect(center=message_box.center))

    def format_play_time(seconds: float) -> str:
        minutes, remaining_seconds = divmod(max(0, int(seconds)), 60)
        return f"{minutes:02d}:{remaining_seconds:02d}"

    def draw_result_screen() -> None:
        draw_menu_background()
        result_title = (
            "GAME OVER" if session.result == "lost" else "YOU SURVIVED"
        )
        title = screen_title_font.render(result_title, True, PALE)
        screen.blit(title, title.get_rect(center=(WIDTH // 2, 235)))
        score = calculate_score(game_time_played, session.events_defeated)
        score_text = section_title_font.render(f"SCORE {score:,}", True, PALE)
        screen.blit(score_text, score_text.get_rect(center=(WIDTH // 2, 325)))
        encounters = small_font.render(
            f"ENCOUNTERS CLEARED  {session.events_defeated}",
            True,
            PALE,
        )
        screen.blit(encounters, encounters.get_rect(center=(WIDTH // 2, 380)))
        elapsed = small_font.render(
            f"TIME PLAYED  {format_play_time(game_time_played)}",
            True,
            PALE,
        )
        screen.blit(elapsed, elapsed.get_rect(center=(WIDTH // 2, 420)))
        draw_button(restart_button, "RESTART")
        draw_button(result_menu_button, "MAIN MENU")

    def draw_enemy(now: float) -> None:
        if session.result == "won" or session.encounter.sprite is None:
            return
        if session.encounter.input_type == "stare" and not session.creep_jumpscared:
            return
        is_creep = session.encounter.input_type == "stare"
        size = (900, 900) if is_creep else None
        sprite = sprites[session.encounter.sprite].frame_at(now, size)
        rect = sprite.get_rect(center=(WIDTH // 2, 400))
        screen.blit(sprite, rect)
        if (
            session.encounter.input_type == "stare"
            and session.creep_jumpscare_at is not None
            and 0.0 <= now - session.creep_jumpscare_at < 0.3
        ):
            alpha = round(150 * (1.0 - (now - session.creep_jumpscare_at) / 0.3))
            creep_flash_overlay.fill((255, 235, 235, alpha))
            screen.blit(creep_flash_overlay, (0, 0))

    def draw_stone_angel() -> None:
        if session.result == "won":
            return
        progress = min(1.0, session.angel_blinks / ANGEL_BLINK_LIMIT)
        height = round(126 + 440 * progress)
        width = round(stone_angel.get_width() * height / stone_angel.get_height())
        x = round(10 + 350 * progress)
        y = round(142 + 170 * progress)
        size = (width, height)
        if size not in angel_frames:
            angel_frames[size] = pygame.transform.smoothscale(stone_angel, size)
        angel = angel_frames[size]
        screen.blit(angel, (x, y))

    def start_game(now: float) -> None:
        nonlocal session, eyes_closed_held, game_started_at, game_time_played
        nonlocal next_cricket_at, screen_state
        session = GameSession(now)
        eyes_closed_held = False
        game_started_at = now
        game_time_played = 0.0
        next_cricket_at = now + random.uniform(5.0, 9.0)
        screen_state = "game"

    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        now = time.monotonic()
        previous_encounter_serial = session.encounter_serial
        if screen_state == "game":
            session.update(now, dt, eyes_closed_held)

        while True:
            try:
                event_type, detail = ui_events.get_nowait()
            except queue.Empty:
                break
            if event_type == "connected":
                input_mode = "BLE"
                connection_status = f"Connected: {detail}"
            elif event_type == "blink" and screen_state == "game":
                session.blink(now)
            elif event_type == "error":
                input_mode = "BLE ERROR"
                connection_status = f"BLE unavailable: {detail}"
            elif event_type == "status":
                connection_status = detail

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif (
                    screen_state == "game"
                    and event.key == pygame.K_SPACE
                    and not getattr(event, "repeat", False)
                ):
                    eyes_closed_held = True
                    session.blink(now)
                elif screen_state == "title" and event.key == pygame.K_RETURN:
                    start_game(now)
                elif screen_state == "result" and event.key == pygame.K_r:
                    start_game(now)
                elif screen_state == "title" and event.key == pygame.K_b:
                    start_ble()
                elif screen_state == "title" and event.key == pygame.K_s:
                    switch_to_simulator()
                elif screen_state == "title" and event.key == pygame.K_d:
                    ble_stop.set()
                    input_mode = "SIMULATOR"
                    connection_status = "Disconnected. Simulator ready — SPACE = blink"
            elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                eyes_closed_held = False
            elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                eyes_closed_held = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if screen_state == "title":
                    if play_button.collidepoint(event.pos):
                        start_game(now)
                    elif credits_button.collidepoint(event.pos):
                        screen_state = "credits"
                    elif connect_button.collidepoint(event.pos):
                        start_ble()
                    elif simulator_button.collidepoint(event.pos):
                        switch_to_simulator()
                    elif disconnect_button.collidepoint(event.pos):
                        ble_stop.set()
                        input_mode = "SIMULATOR"
                        connection_status = "Disconnected. Simulator ready — SPACE = blink"
                elif screen_state == "credits" and credits_back_button.collidepoint(event.pos):
                    screen_state = "title"
                elif screen_state == "result":
                    if restart_button.collidepoint(event.pos):
                        start_game(now)
                    elif result_menu_button.collidepoint(event.pos):
                        screen_state = "title"

        if screen_state == "game" and session.result is not None:
            screen_state = "result"
            game_time_played = max(0.0, now - game_started_at)

        if screen_state == "game" and session.encounter_serial != previous_encounter_serial:
            if sound_effects is not None and session.encounter.input_type == "stare":
                sound_effects.door_open.play()
            elif sound_effects is not None and session.encounter.input_type == "hold":
                sound_effects.window_open.play()

        if screen_state == "game" and now >= next_cricket_at:
            if sound_effects is not None:
                sound_effects.cricket.play()
            next_cricket_at = now + random.uniform(6.0, 12.0)

        if screen_state == "title":
            draw_title_screen()
        elif screen_state == "credits":
            draw_credits_screen()
        elif screen_state == "result":
            draw_result_screen()
        else:
            if session.result == "won":
                screen.fill(WHITE)
            else:
                background, background_offset = backgrounds[session.encounter.background]
                screen.blit(background, background_offset)
                draw_enemy(now)
                draw_stone_angel()
                if session.encounter.input_type == "hold":
                    deadline_progress = min(
                        1.0,
                        max(
                            0.0,
                            (now - session.encounter_started_at)
                            / FOG_CLOSE_DEADLINE_SECONDS,
                        ),
                    )
                    closing_progress = (
                        session.closed_eyes_progress / CLOSED_EYES_SECONDS
                    )
                    alpha = max(
                        round(70 * deadline_progress),
                        round(255 * closing_progress),
                    )
                    fog_overlay.fill((WHITE[0], WHITE[1], WHITE[2], alpha))
                    screen.blit(fog_overlay, (0, 0))
            draw_game_hud(now)
        pygame.display.flip()

    ble_stop.set()
    if ble_thread is not None and ble_thread.is_alive():
        ble_thread.join(timeout=2.0)
    pygame.quit()


if __name__ == "__main__":
    main()
