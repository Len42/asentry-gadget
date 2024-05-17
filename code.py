""" Monitor the JPL Sentry database for asteroid impact threats.

    This is CircuitPython firmware to run on a Raspberry Pi Pico W.
    It downloads a list of potential Earth-threatening asteroids
    from NASA JPL's Sentry (https://cneos.jpl.nasa.gov/sentry/) service
    and displays an alert message if there are any new or increased threats.

    Copyright 2024 Len Popp
    Copyright 2023 Jeff Epler for Adafruit Industries (display code)
    See LICENSE file
"""
# type: ignore
import json
import os
import ssl
import traceback

import board
import displayio
import digitalio
import keypad
import socketpool
import supervisor
from wifi import radio

import adafruit_requests
import adafruit_displayio_ssd1306
from adafruit_display_text import wrap_text_to_pixels
from adafruit_display_text.bitmap_label import Label
from adafruit_ticks import ticks_add, ticks_less, ticks_ms
from terminalio import FONT
import audiocore


# SSD1306 display setup
nice_font = FONT
line_spacing = 12 # in pixels
display_width = 128
display_height = 64
#  i2c display setup
displayio.release_displays()
oled_reset = board.GP9
# STEMMA I2C on picowbell
i2c = board.STEMMA_I2C()
display_bus = displayio.I2CDisplay(i2c, device_address=0x3D, reset=oled_reset)
display = adafruit_displayio_ssd1306.SSD1306(display_bus, width=display_width, height=display_height)


# WrappedTextDisplay class by jepler
class WrappedTextDisplay(displayio.Group):
    def __init__(self):
        super().__init__()
        self.offset = 0
        self.max_lines = display.height // line_spacing
        for i in range(self.max_lines):
            self.make_label("", i * line_spacing)
        self.lines = [""]
        self.text = ""

    def make_label(self, text, y):
        result = Label(
            font=nice_font,
            color=0xFFFFFF,
            background_color=0,
            line_spacing=line_spacing,
            anchor_point=(0, 0),
            anchored_position=(0, y),
            text=text)
        self.append(result)

    def add_text(self, new_text):
        print(end=new_text)
        if self.lines:
            text = self.lines[-1] + new_text
        else:
            text = new_text
        self.lines[-1:] = wrap_text_to_pixels(text, display.width, nice_font)
        self.scroll_to_end()

    def set_text(self, text):
        print("\n\n", end=text)
        self.text = text
        self.lines = wrap_text_to_pixels(text, display.width, nice_font)
        self.offset = 0

    def show(self, text):
        self.set_text(text)
        self.refresh()

    def add_show(self, new_text):
        self.add_text(new_text)
        self.refresh()

    def scroll_to_end(self):
        self.offset = self.max_offset()

    def scroll_next_line(self):
        max_offset = self.max_offset()
        self.offset = (self.offset + 1) % (max_offset + 1)

    def max_offset(self):
        return max(0, len(self.lines) - self.max_lines)

    def on_last_line(self):
        return self.offset == self.max_offset()

    def refresh(self):
        lines = self.lines
        # update labels from wrapped text, accounting for scroll offset
        for i in range(len(self)):
            offset_i = i + self.offset
            if offset_i >= len(lines):
                text = ""
            else:
                text = lines[offset_i]
            if text != self[i].text:
                self[i].text = text
        # Actually update the display all at once
        display.refresh()


# def wait_button_scroll_text():
#     led.switch_to_output(True)
#     keys.events.clear()
#     deadline = ticks_add(ticks_ms(),
#             5000 if wrapped_text.on_last_line() else 1000)
#     while True:
#         #DEBUG
#         #if (event := keys.events.get()) and event.pressed:
#         #    break
#         if wrapped_text.max_offset() > 0 and ticks_less(deadline, ticks_ms()):
#             wrapped_text.scroll_next_line()
#             wrapped_text.refresh()
#             deadline = ticks_add(deadline,
#                     5000 if wrapped_text.on_last_line() else 1000)
#     led.value = False

# TODO: must be interruptible, or run "until"
def wait_scroll_text():
    deadline = ticks_add(ticks_ms(),
            5000 if wrapped_text.on_last_line() else 1000)
    while True:
        if wrapped_text.max_offset() > 0 and ticks_less(deadline, ticks_ms()):
            wrapped_text.scroll_next_line()
            wrapped_text.refresh()
            deadline = ticks_add(deadline,
                    5000 if wrapped_text.on_last_line() else 1000)

def fetch_latest_data() -> list:
    """ Fetch the latest set of "interesting" objects from NASA JPL. """
    wrapped_text.show('Fetching data')
    # Only fetch a few of the most threatening objects.
    ps_min = -3 # minimum threat level
    sRequest = f"https://ssd-api.jpl.nasa.gov/sentry.api?ps-min={ps_min}"
    ##sRequest = f"https://lenp.net/x"
    with requests.get(sRequest) as response:
        if response.status_code != 200:
            raise Exception(f"Bad HTTP response: {response.status_code} {response.reason.decode()}")
        else:
            results = response.json()
    if (results['signature']['source'] != 'NASA/JPL Sentry Data API'
            or results['signature']['version'] != '2.0'):
        raise Exception('Unexpected data format')
    return results['data']

# TODO: check_for_updates()

def display_updates(objects: list):
    wrapped_text.set_text('')
    nl = ''
    for object in objects:
        # TODO
        wrapped_text.add_text(f'{nl}{object['fullname']}')
        nl = '\n'
    wrapped_text.refresh()

# MAIN
try:
    print("asentry started")

    # Initialize the wrapped-text display
    display.root_group = wrapped_text = WrappedTextDisplay()
    wrapped_text.show('asentry')

    # Load the alert sound
    wav = None
    try:
        with open("alert.wav", "rb") as wave_file:
            wav = audiocore.WaveFile(wave_file)
    except:
        pass # Not an error if the file is missing

    # Initialize the internet connection
    if radio.ipv4_address is None:
        wrapped_text.show(f"Connecting to {os.getenv('WIFI_SSID')}")
        radio.connect(os.getenv('WIFI_SSID'), os.getenv('WIFI_PASSWORD'))
    requests = adafruit_requests.Session(socketpool.SocketPool(radio), ssl.create_default_context())

    # TODO: fetch initial data

    # TODO: loop

    latest_objects = fetch_latest_data()

    # TODO: check_for_updates()
    updates = latest_objects

    if not updates:
        wrapped_text.show('No new threats')
    else:
        display_updates(updates)
        # TODO: play alert sound

    # DEBUG
    wait_scroll_text()
    while True:
        pass

except Exception as e:
    #print(f"Error: {e}")
    traceback.print_exception(e)
    display.root_group = displayio.CIRCUITPYTHON_TERMINAL
    display.auto_refresh = True
    # while True:
    #     if (event1 := keys.events.get()) and event1.pressed:
    #         break
    # supervisor.reload()
    while True:
        pass
