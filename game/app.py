import asyncio
import math
import queue
import threading
import time
from pathlib import Path
from typing import Optional

import pygame

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
SURVIVE_SECONDS = 75.0
THREAT_GROWTH_PER_SECOND = 0.012
BLINK_COOLDOWN_GAME_SECONDS = 1.2

INK = (14, 20, 35)
PALE = (235, 231, 221)
AMBER = (255, 190, 104)
RED = (228, 107, 111)
GREEN = (158, 220, 174)


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
    pygame.display.set_caption("Nightlight — A PiEEG Blink-Controlled Horror Game")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", 18)
    small_font = pygame.font.SysFont("consolas", 14)
    title_font = pygame.font.SysFont("consolas", 30, bold=True)

    asset_path = Path(__file__).resolve().parent / "assets" / "child-bedroom-pixel.png"
    background_image = pygame.image.load(str(asset_path)).convert()
    image_width, image_height = background_image.get_size()
    scale = max(WIDTH / image_width, HEIGHT / image_height)
    scaled_size = (round(image_width * scale), round(image_height * scale))
    background_image = pygame.transform.scale(background_image, scaled_size)
    background_offset = ((WIDTH - scaled_size[0]) // 2, (HEIGHT - scaled_size[1]) // 2)

    ui_events: queue.Queue = queue.Queue()
    ble_stop = threading.Event()
    ble_thread: Optional[threading.Thread] = None
    running = True
    input_mode = "SIMULATOR"
    connection_status = "Simulator ready — SPACE = blink"
    threat = 0.12
    elapsed = 0.0
    last_action_time = -10.0
    flash_until = 0.0
    message = "You are safe in bed. Keep the closet light away."
    message_until = 5.0
    game_result: Optional[str] = None

    connect_button = pygame.Rect(28, 74, 236, 42)
    simulator_button = pygame.Rect(276, 74, 210, 42)
    disconnect_button = pygame.Rect(498, 74, 146, 42)
    shadow = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    hud_panel = pygame.Surface((WIDTH, 136), pygame.SRCALPHA)
    hud_panel.fill((8, 12, 22, 220))
    flash_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
    flash_overlay.fill((255, 211, 135, 52))

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

    def trigger_blink(now: float, peak_uv: Optional[float] = None) -> None:
        nonlocal threat, last_action_time, flash_until, message, message_until
        if game_result is not None or now - last_action_time < BLINK_COOLDOWN_GAME_SECONDS:
            return
        last_action_time = now
        threat = max(0.0, threat - 0.24)
        flash_until = now + 0.8
        if peak_uv is None:
            message = "A blink! The nightlight pushes the shadow back."
        else:
            message = f"Blink detected ({peak_uv:.0f} µV). The nightlight flares!"
        message_until = now + 2.8

    def draw_button(rect: pygame.Rect, label: str, selected: bool = False) -> None:
        fill = (64, 88, 112) if selected else (31, 42, 60)
        pygame.draw.rect(screen, fill, rect)
        pygame.draw.rect(screen, (151, 166, 178), rect, 2)
        text = small_font.render(label, True, PALE)
        screen.blit(text, text.get_rect(center=rect.center))

    def draw_character(now: float) -> None:
        bob = int(math.sin(now * 2.0) * 2)
        pillow = pygame.Rect(546, 564 + bob, 190, 64)
        pygame.draw.rect(screen, (113, 127, 145), pillow)
        pygame.draw.rect(screen, (190, 192, 190), pygame.Rect(558, 573 + bob, 166, 44))

        pygame.draw.rect(screen, (31, 28, 39), pygame.Rect(602, 548 + bob, 76, 80))
        pygame.draw.rect(screen, (215, 164, 133), pygame.Rect(612, 562 + bob, 56, 54))
        pygame.draw.rect(screen, (31, 28, 39), pygame.Rect(602, 548 + bob, 76, 20))
        pygame.draw.rect(screen, (31, 28, 39), pygame.Rect(596, 556 + bob, 16, 34))
        pygame.draw.rect(screen, (31, 28, 39), pygame.Rect(668, 556 + bob, 16, 34))
        pygame.draw.rect(screen, (80, 57, 55), pygame.Rect(623, 588 + bob, 10, 4))
        pygame.draw.rect(screen, (80, 57, 55), pygame.Rect(649, 588 + bob, 10, 4))
        pygame.draw.rect(screen, (190, 126, 112), pygame.Rect(636, 600 + bob, 9, 5))

        blanket = [(478, 642 + bob), (544, 616 + bob), (736, 616 + bob),
                   (802, 642 + bob), (842, 720), (438, 720)]
        pygame.draw.polygon(screen, (64, 79, 111), blanket)
        pygame.draw.polygon(screen, (104, 119, 149), blanket, 4)
        for x, y in ((526, 668), (728, 671), (618, 696)):
            pygame.draw.rect(screen, (148, 153, 170), pygame.Rect(x, y, 12, 8))
            pygame.draw.rect(screen, (148, 153, 170), pygame.Rect(x + 2, y - 2, 8, 12))

    def draw_shadow() -> None:
        alpha = min(210, int(40 + threat * 160))
        shadow.fill((0, 0, 0, 0))
        color = (5, 6, 14, alpha)
        pygame.draw.rect(shadow, color, pygame.Rect(925, 254, 94, 210))
        pygame.draw.rect(shadow, color, pygame.Rect(939, 220, 66, 58))
        pygame.draw.rect(shadow, color, pygame.Rect(907, 286, 22, 126))
        pygame.draw.rect(shadow, color, pygame.Rect(1015, 280, 20, 132))
        screen.blit(shadow, (0, 0))

    def draw_hud(now: float) -> None:
        screen.blit(hud_panel, (0, 0))
        screen.blit(title_font.render("NIGHTLIGHT", True, PALE), (28, 20))
        screen.blit(small_font.render("A tiny pixel-horror game controlled by blinks", True, (170, 182, 191)), (30, 50))

        draw_button(connect_button, "Connect Octopus 16 (BLE)", input_mode.startswith("BLE"))
        draw_button(simulator_button, "Simulator Mode  [Space]", input_mode == "SIMULATOR")
        draw_button(disconnect_button, "Disconnect")

        status_color = GREEN if input_mode == "SIMULATOR" else AMBER
        screen.blit(small_font.render(connection_status, True, status_color), (664, 85))
        remaining = max(0, int(SURVIVE_SECONDS - elapsed))
        screen.blit(font.render(f"UNTIL DAWN  {remaining:02d}s", True, PALE), (1000, 22))
        screen.blit(small_font.render(f"THREAT  {int(threat * 100)}%", True, RED if threat > 0.65 else AMBER), (1000, 52))
        pygame.draw.rect(screen, (46, 49, 62), pygame.Rect(1000, 76, 240, 14))
        pygame.draw.rect(screen, RED, pygame.Rect(1000, 76, int(240 * threat), 14))

        if now < message_until or game_result is not None:
            label = message
            if game_result == "lost":
                label = "The shadow reached the bed. Press R to try again."
            elif game_result == "won":
                label = "Morning! You made it through the night. Press R to play again."
            box = pygame.Rect(250, 470, 780, 48)
            pygame.draw.rect(screen, (8, 12, 22), box)
            pygame.draw.rect(screen, (123, 137, 151), box, 2)
            text = font.render(label, True, PALE)
            screen.blit(text, text.get_rect(center=box.center))

        if now < flash_until:
            screen.blit(flash_overlay, (0, 0))

    def reset_game() -> None:
        nonlocal threat, elapsed, last_action_time, flash_until, message, message_until, game_result
        threat = 0.12
        elapsed = 0.0
        last_action_time = -10.0
        flash_until = 0.0
        message = "You are safe in bed. Keep the closet light away."
        message_until = 5.0
        game_result = None

    while running:
        dt = min(clock.tick(60) / 1000.0, 0.05)
        now = time.monotonic()

        while True:
            try:
                event_type, detail = ui_events.get_nowait()
            except queue.Empty:
                break
            if event_type == "connected":
                input_mode = "BLE"
                connection_status = f"Connected: {detail}"
            elif event_type == "blink":
                trigger_blink(now, detail)
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
                elif event.key == pygame.K_SPACE:
                    trigger_blink(now)
                elif event.key == pygame.K_r:
                    reset_game()
                elif event.key == pygame.K_b:
                    start_ble()
                elif event.key == pygame.K_s:
                    switch_to_simulator()
                elif event.key == pygame.K_d:
                    ble_stop.set()
                    input_mode = "SIMULATOR"
                    connection_status = "Disconnected. Simulator ready — SPACE = blink"
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if connect_button.collidepoint(event.pos):
                    start_ble()
                elif simulator_button.collidepoint(event.pos):
                    switch_to_simulator()
                elif disconnect_button.collidepoint(event.pos):
                    ble_stop.set()
                    input_mode = "SIMULATOR"
                    connection_status = "Disconnected. Simulator ready — SPACE = blink"

        if game_result is None:
            elapsed += dt
            threat = min(1.0, threat + dt * THREAT_GROWTH_PER_SECOND)
            if threat >= 1.0:
                game_result = "lost"
            elif elapsed >= SURVIVE_SECONDS:
                game_result = "won"

        screen.fill(INK)
        screen.blit(background_image, background_offset)
        draw_shadow()
        draw_character(now)
        draw_hud(now)
        pygame.display.flip()

    ble_stop.set()
    if ble_thread is not None and ble_thread.is_alive():
        ble_thread.join(timeout=2.0)
    pygame.quit()


if __name__ == "__main__":
    main()
