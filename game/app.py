import asyncio
from bisect import bisect_right
import queue
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
CLOSED_EYES_SECONDS = 5.0
ANGEL_BLINK_LIMIT = 20

PALE = (235, 231, 221)
AMBER = (255, 190, 104)
RED = (228, 107, 111)
GREEN = (158, 220, 174)
WHITE = (255, 255, 255)


class Encounter(NamedTuple):
    name: str
    background: str
    sprite: Optional[str]
    input_type: str
    blink_goal: int = 0


ENCOUNTERS = (
    Encounter(
        "The Visitor",
        "Moonlit Space-Themed Bedroom.png",
        "demon_transparent.gif",
        "blink",
        1,
    ),
    Encounter(
        "The Boss",
        "Moonlit Space-Themed Bedroom.png",
        "boss_demon.gif",
        "blink",
        3,
    ),
    Encounter(
        "The Pale Grin",
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
        self.encounter_started_at = now
        self.stare_started_at = now
        self.last_blink_at = float("-inf")
        self.blink_progress = 0
        self.angel_blinks = 0
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
            self.message = "The stone angel reached you. Press R to try again."
            return

        if self.encounter.input_type == "blink":
            self.blink_progress += 1
            if self.blink_progress >= self.encounter.blink_goal:
                self._advance(now)
            else:
                self.message = (
                    f"{self.encounter.name}: {self.blink_progress}/"
                    f"{self.encounter.blink_goal} blinks. The angel moves closer."
                )
        elif self.encounter.input_type == "stare":
            self.stare_started_at = now
            self.message = "You blinked. Keep staring at the grin for 5 seconds."
        else:
            self.message = "The angel moves closer. Keep holding SPACE to close your eyes."

    def update(self, now: float, dt: float, eyes_closed: bool) -> None:
        if self.result is not None:
            return

        if self.encounter.input_type == "blink":
            if now - self.encounter_started_at > ENCOUNTER_SECONDS:
                self.result = "lost"
                self.message = f"{self.encounter.name} stayed too long. Press R to try again."
        elif self.encounter.input_type == "stare":
            if now - self.stare_started_at >= STARE_SECONDS:
                self._advance(now)
        elif self.encounter.input_type == "hold":
            if eyes_closed:
                self.closed_eyes_progress = min(
                    CLOSED_EYES_SECONDS,
                    self.closed_eyes_progress + dt,
                )
            else:
                self.closed_eyes_progress = 0.0
            if self.closed_eyes_progress >= CLOSED_EYES_SECONDS:
                self.result = "won"
                self.message = "Dawn breaks. The fog and every demon disappear."

    def _advance(self, now: float) -> None:
        if self.encounter_index + 1 >= len(ENCOUNTERS):
            self.result = "won"
            self.message = "Dawn breaks. You made it through the night."
            return

        self.encounter_index += 1
        self.encounter_started_at = now
        self.stare_started_at = now
        self.blink_progress = 0
        self.closed_eyes_progress = 0.0
        self._set_encounter_message()

    def _set_encounter_message(self) -> None:
        if self.encounter.input_type == "blink":
            self.message = (
                f"{self.encounter.name} is here. Blink "
                f"{self.encounter.blink_goal} time"
                f"{'s' if self.encounter.blink_goal != 1 else ''} to banish it."
            )
        elif self.encounter.input_type == "stare":
            self.message = "The pale grin is watching. Do not blink for 5 seconds."
        else:
            self.message = "Fog fills the window. Hold SPACE to close your eyes for 5 seconds."


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

    def frame_at(self, now: float) -> pygame.Surface:
        elapsed_ms = int((now - self.started_at) * 1000) % self.total_duration
        return self.frames[bisect_right(self.frame_boundaries, elapsed_ms)]


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

    last_blink_time = 0.0
    async with BleakClient(device) as client:
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
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Night Watch — A PiEEG Blink-Controlled Horror Game")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)
    small_font = pygame.font.SysFont("consolas", 14)
    title_font = pygame.font.SysFont("consolas", 30, bold=True)
    assets_dir = Path(__file__).resolve().parent / "assets"

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
            (390, 390),
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

    connect_button = pygame.Rect(28, 74, 236, 42)
    simulator_button = pygame.Rect(276, 74, 210, 42)
    disconnect_button = pygame.Rect(498, 74, 146, 42)
    hud_panel = pygame.Surface((WIDTH, 136), pygame.SRCALPHA)
    hud_panel.fill((8, 12, 22, 220))
    fog_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)

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
            except Exception as error:
                ui_events.put(("error", str(error)))

        ble_thread = threading.Thread(target=run_connection, daemon=True)
        ble_thread.start()

    def switch_to_simulator() -> None:
        nonlocal input_mode, connection_status
        ble_stop.set()
        input_mode = "SIMULATOR"
        connection_status = "Simulator ready — SPACE = blink"

    def draw_button(rect: pygame.Rect, label: str, selected: bool = False) -> None:
        fill = (64, 88, 112) if selected else (31, 42, 60)
        pygame.draw.rect(screen, fill, rect)
        pygame.draw.rect(screen, (151, 166, 178), rect, 2)
        text = small_font.render(label, True, PALE)
        screen.blit(text, text.get_rect(center=rect.center))

    def draw_hud(now: float) -> None:
        screen.blit(hud_panel, (0, 0))
        screen.blit(title_font.render("NIGHT WATCH", True, PALE), (28, 20))
        screen.blit(
            small_font.render(
                "Blink to fight back. Don't blink at the grin. Watch the angel.",
                True,
                (170, 182, 191),
            ),
            (30, 50),
        )

        draw_button(connect_button, "Connect Octopus 16 (BLE)", input_mode.startswith("BLE"))
        draw_button(simulator_button, "Simulator Mode  [Space]", input_mode == "SIMULATOR")
        draw_button(disconnect_button, "Disconnect")

        status_color = GREEN if input_mode == "SIMULATOR" else AMBER
        screen.blit(small_font.render(connection_status, True, status_color), (664, 85))

        if session.result == "won":
            encounter_label = "NIGHT SURVIVED"
            progress_label = "ALL DEMONS GONE"
            progress = 1.0
            progress_color = GREEN
        elif session.result == "lost":
            encounter_label = "GAME OVER"
            progress_label = f"ANGEL BLINKS {session.angel_blinks}/{ANGEL_BLINK_LIMIT}"
            progress = min(1.0, session.angel_blinks / ANGEL_BLINK_LIMIT)
            progress_color = RED
        else:
            encounter_label = (
                f"ENCOUNTER {session.encounter_index + 1:02d}/{len(ENCOUNTERS)}"
            )
            if session.encounter.input_type == "blink":
                remaining = max(
                    0.0,
                    ENCOUNTER_SECONDS - (now - session.encounter_started_at),
                )
                progress_label = f"TIME TO ACT {remaining:0.1f}s"
                progress = 1.0 - remaining / ENCOUNTER_SECONDS
                progress_color = AMBER if remaining > 2.0 else RED
            elif session.encounter.input_type == "stare":
                stared = min(STARE_SECONDS, now - session.stare_started_at)
                progress_label = f"NO BLINK {stared:0.1f}/{STARE_SECONDS:.0f}s"
                progress = stared / STARE_SECONDS
                progress_color = GREEN
            else:
                progress_label = (
                    f"EYES CLOSED {session.closed_eyes_progress:0.1f}/"
                    f"{CLOSED_EYES_SECONDS:.0f}s"
                )
                progress = session.closed_eyes_progress / CLOSED_EYES_SECONDS
                progress_color = GREEN

        screen.blit(font.render(encounter_label, True, PALE), (1000, 18))
        angel_label = f"ANGEL  {session.angel_blinks:02d}/{ANGEL_BLINK_LIMIT}"
        screen.blit(
            small_font.render(angel_label, True, RED if session.angel_blinks >= 15 else AMBER),
            (1000, 48),
        )
        screen.blit(small_font.render(progress_label, True, progress_color), (1000, 66))
        pygame.draw.rect(screen, (46, 49, 62), pygame.Rect(1000, 76, 240, 14))
        pygame.draw.rect(
            screen,
            progress_color,
            pygame.Rect(1000, 76, int(240 * max(0.0, min(progress, 1.0))), 14),
        )

        message_box = pygame.Rect(110, HEIGHT - 78, WIDTH - 220, 52)
        pygame.draw.rect(screen, (8, 12, 22), message_box)
        pygame.draw.rect(screen, (123, 137, 151), message_box, 2)
        message = session.message
        if session.result is not None:
            message += "  Press R to play again."
        text = font.render(message, True, PALE)
        screen.blit(text, text.get_rect(center=message_box.center))

    def draw_enemy(now: float) -> None:
        if session.result == "won" or session.encounter.sprite is None:
            return
        sprite = sprites[session.encounter.sprite].frame_at(now)
        rect = sprite.get_rect(center=(WIDTH // 2, 400))
        screen.blit(sprite, rect)

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

    def reset_game(now: float) -> None:
        nonlocal session, eyes_closed_held
        session = GameSession(now)
        eyes_closed_held = False

    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        now = time.monotonic()
        session.update(now, dt, eyes_closed_held)

        while True:
            try:
                event_type, detail = ui_events.get_nowait()
            except queue.Empty:
                break
            if event_type == "connected":
                input_mode = "BLE"
                connection_status = f"Connected: {detail}"
            elif event_type == "blink":
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
                elif event.key == pygame.K_SPACE and not getattr(event, "repeat", False):
                    eyes_closed_held = True
                    session.blink(now)
                elif event.key == pygame.K_r:
                    reset_game(now)
                elif event.key == pygame.K_b:
                    start_ble()
                elif event.key == pygame.K_s:
                    switch_to_simulator()
                elif event.key == pygame.K_d:
                    ble_stop.set()
                    input_mode = "SIMULATOR"
                    connection_status = "Disconnected. Simulator ready — SPACE = blink"
            elif event.type == pygame.KEYUP and event.key == pygame.K_SPACE:
                eyes_closed_held = False
            elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
                eyes_closed_held = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if connect_button.collidepoint(event.pos):
                    start_ble()
                elif simulator_button.collidepoint(event.pos):
                    switch_to_simulator()
                elif disconnect_button.collidepoint(event.pos):
                    ble_stop.set()
                    input_mode = "SIMULATOR"
                    connection_status = "Disconnected. Simulator ready — SPACE = blink"

        if session.result == "won":
            screen.fill(WHITE)
        else:
            background, background_offset = backgrounds[session.encounter.background]
            screen.blit(background, background_offset)
            draw_enemy(now)
            draw_stone_angel()
            if session.encounter.input_type == "hold":
                alpha = int(
                    255 * session.closed_eyes_progress / CLOSED_EYES_SECONDS
                )
                fog_overlay.fill((WHITE[0], WHITE[1], WHITE[2], alpha))
                screen.blit(fog_overlay, (0, 0))
        draw_hud(now)
        pygame.display.flip()

    ble_stop.set()
    if ble_thread is not None and ble_thread.is_alive():
        ble_thread.join(timeout=2.0)
    pygame.quit()


if __name__ == "__main__":
    main()
