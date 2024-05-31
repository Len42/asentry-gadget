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
import os
import ssl
import time
import traceback
import gc

import board
import displayio
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
import audiobusio

# Pin assignments for Pi Pico W
pin_switch = board.GP14 # pushbutton switch, low = pressed
pin_i2s_bclk = board.GP26 # I2S bit_clock
pin_i2s_wsel = board.GP27 # I2S word_select
pin_i2s_data = board.GP28 # I2S data

# Define the interval between data updates
check_interval = 1 * 60 * 60 # 1 hour in seconds

# Define idle display time
display_time = 10 # TODO: longer

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


def wait_button_scroll_text(button: keypad.Keys, max_time: int = 0, screen_time: int = 0):
    """ Wait while scrolling the text display, until the button is pressed
        or max_time seconds has passed (if specified).
    """
    button.events.clear()
    now = ticks_ms()
    scroll_time = ticks_add(now, 5000 if wrapped_text.on_last_line() else 1000)
    timeout = ticks_add(now, max_time * 1000)
    screen_timeout = ticks_add(now, screen_time * 1000)
    while True:
        if (event := button.events.get()) and event.pressed:
            break
        now = ticks_ms()
        if max_time and ticks_less(timeout, now):
            break
        if screen_time and ticks_less(screen_timeout, now):
            # Clear the screen to avoid burn-in
            wrapped_text.show('')
            screen_time = 0
        if wrapped_text.max_offset() > 0 and ticks_less(scroll_time, ticks_ms()):
            wrapped_text.scroll_next_line()
            wrapped_text.refresh()
            scroll_time = ticks_add(scroll_time,
                                5000 if wrapped_text.on_last_line() else 1000)

def fetch_latest_data() -> list:
    """ Fetch the most "interesting" objects from NASA JPL's Sentry service. """
    wrapped_text.show('Fetching data')
    # Only fetch a few of the most threatening objects.
    ps_min = -3 # minimum threat level
    sRequest = f'https://ssd-api.jpl.nasa.gov/sentry.api?ps-min={ps_min}'
    # DEBUG sRequest = f'https://lenp.net/x'
    with requests.get(sRequest) as response:
        if response.status_code != 200:
            raise Exception(f'Bad HTTP response: {response.status_code} {response.reason.decode()}')
        else:
            results = response.json()
    if (results['signature']['source'] != 'NASA/JPL Sentry Data API'
            or results['signature']['version'] != '2.0'):
        raise Exception('Unexpected data format')
    return results['data']

def fetch_dummy_data() -> list:
    """ Return a dummy data set - a real query with one object removed. """
    return [
        {"ps_max":"-3.01","des":"1979 XB","id":"bJ79X00B","last_obs":"1979-12-15","v_inf":"23.7606234552547","diameter":"0.66","ts_max":"0","range":"2056-2113","ps_cum":"-2.71","ip":"8.515158e-07","h":"18.54","last_obs_jd":"2444222.5","fullname":"(1979 XB)","n_imp":4},
        {"n_imp":300,"fullname":"(2000 SG344)","last_obs_jd":"2451820.5","ip":"0.002743395186","h":"24.79",
         "ps_cum":"-2.78","range":"2069-2122","ts_max":"0","diameter":"0.037","v_inf":"1.35802744453748","last_obs":"2000-10-03","des":"2000 SG344","id":"bK00SY4G","ps_max":"-3.13"},
        {"ps_max":"-2.86","last_obs":"2008-05-09","id":"bK08J03L","des":"2008 JL3","diameter":"0.029","v_inf":"8.41901237821941","ps_cum":"-2.86","range":"2027-2122","ts_max":"0","ip":"0.0001658147615","h":"25.31","fullname":"(2008 JL3)","last_obs_jd":"2454595.5","n_imp":44},
        {"ps_max":"-2.79","id":"bK07F03T","last_obs":"2007-03-21","des":"2007 FT3","v_inf":"17.065343203718","diameter":"0.341","ps_cum":"-2.63","ts_max":"0","range":"2024-2119","ip":"8.635192e-07","h":"19.97","fullname":"(2007 FT3)","last_obs_jd":"2454180.5","n_imp":89},
        {"ps_max":"-1.59","last_obs":"2020-10-3.80160","id":"a0101955","des":"101955","v_inf":"5.9916984432395","diameter":"0.49","ps_cum":"-1.41","ts_max":None,"range":"2178-2290","ip":"0.000571699999999996","h":"20.63","fullname":"101955 Bennu (1999 RQ36)","last_obs_jd":"2459126.3016","n_imp":157},
        {"ip":"2.859e-05","h":"17.94","n_imp":1,"last_obs_jd":"2459551.5","fullname":"29075 (1950 DA)","des":"29075","last_obs":"2021-12-03","id":"a0029075","ps_max":"-2.05","range":"2880-2880","ts_max":None,"ps_cum":"-2.05","diameter":"1.3","v_inf":"14.10"}
    ]

def check_for_updates(saved_objects: list, latest_objects: list) -> list:
    """ Compare the saved data to the latest data and alert the user to any
        new objects or objects with increased threat levels.
        Return a list of new or increased threat objects.
    """
    changed_objects = []
    for object in latest_objects:
        found = [ old for old in saved_objects if old['id'] == object['id'] ]
        if len(found) == 0:
            # new object
            object['is_new'] = True
            changed_objects.append(object)
        else:
            # previously-seen object
            old_obj = found[0]
            if (float(object['ps_cum']) > float(old_obj['ps_cum'])
                    or (object['ts_max'] != None and old_obj['ts_max'] == None)
                    or (object['ts_max'] != None and old_obj['ts_max'] != None
                        and float(object['ts_max']) > float(old_obj['ts_max']))):
                # object threat level has increased
                object['is_new'] = False
                changed_objects.append(object)
    return changed_objects

def display_updates(objects: list):
    """ Display a list of objects that are new or increased threats. """
    wrapped_text.set_text('')
    nl = ''
    for object in objects:
        wrapped_text.add_text(nl)
        nl = '\n'
        wrapped_text.add_text(f"{'NEW' if object['is_new'] else 'INCREASED'} THREAT!\n")
        wrapped_text.add_text(f"{object['fullname']}\n")
        wrapped_text.add_text(f"Year: {object['range']}\n")
        wrapped_text.add_text(f"Threat level: {object['ts_max']}")
    wrapped_text.refresh()

def display_uptime(start_time: int) -> str:
    """ Display the current uptime """
    sec = time.time() - start_time
    s = 'Uptime: '
    min = sec // 60
    sec -= min * 60
    hr = min // 60
    min -= hr * 60
    day = hr // 24
    hr -= day * 24
    yr = day // 365
    day -= yr * 365
    wk = day // 7
    day -= wk * 7
    if yr > 0:
        s += f'{yr} yrs '
    if wk > 0:
        s += f'{wk} wks '
    if day > 0:
        s += f'{day} days '
    if hr > 0:
        s += f'{hr} hrs '
    if min > 0:
        s += f'{min} mins '
    s += f'{sec} secs'
    wrapped_text.add_show(s)


# MAIN

# Initialize an input pin for the button using keypad.Keys
# (Do this here so it can be used in the except block)
button = keypad.Keys((pin_switch,), value_when_pressed=False)

try:
    start_time = time.time()

    print('asentry started')

    # Initialize the wrapped-text display
    display.root_group = wrapped_text = WrappedTextDisplay()
    wrapped_text.show('asentry')

    # Load the alert sound
    wave_file = None
    alert_wav = None
    try:
        wave_file = open('alert.wav', 'rb')
        alert_wav = audiocore.WaveFile(wave_file)
    except:
        pass # Not an error if the file is missing

    # Initialize I2S audio output
    audio = audiobusio.I2SOut(pin_i2s_bclk, pin_i2s_wsel, pin_i2s_data)

    # Initialize the internet connection
    if radio.ipv4_address is None:
        wrapped_text.show(f"Connecting to {os.getenv('WIFI_SSID')}")
        radio.connect(os.getenv('WIFI_SSID'), os.getenv('WIFI_PASSWORD'))
    requests = adafruit_requests.Session(socketpool.SocketPool(radio),
                                         ssl.create_default_context())

    # Fetch and display initial data
    # TODO: choose how to initialize
    saved_objects = []
    #saved_objects = fetch_latest_data()
    #saved_objects = fetch_dummy_data() # DEBUG

    # Periodically fetch the latest data and display results
    while True:
        latest_objects = fetch_latest_data()
        updates = check_for_updates(saved_objects, latest_objects)
        saved_objects = latest_objects
        if updates:
            # Display new/increased threat(s) and play an obnoxious alert sound
            display_updates(updates)
            # Play alert sound
            if alert_wav:
                audio.play(alert_wav, loop=False)
            # Wait and keep waiting until the button is pressed
            wait_button_scroll_text(button)
        else:
            wrapped_text.show('No new threats')
            wrapped_text.add_text('\n\n')
            display_uptime(start_time)
            # Wait for a while or until the button is pressed
            # Clear the screen after a few secs to avoid OLED burn-in
            wait_button_scroll_text(button, check_interval, display_time)

except Exception as e:
    # Error! Display the error message, wait for button press, and reset.
    #print(f'Error: {e}')
    traceback.print_exception(e)
    display.root_group = displayio.CIRCUITPYTHON_TERMINAL
    display.auto_refresh = True
    button.events.clear()
    while True:
        if (event := button.events.get()) and event.pressed:
            break
    # DEBUG: reload() will allow button to reset after error
    #supervisor.reload()