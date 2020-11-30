""" MOON PHASE CLOCK for Adafruit Matrix Portal: displays current time, lunar
    phase and time of next moonrise/sunrise or moonset/sunset. Requires WiFi
    internet access.

    Uses IP geolocation if timezone and/or lat/lon not provided in secrets.py

    Written by Phil 'PaintYourDragon' Burgess for Adafruit Industries.
    MIT license, all text above must be included in any redistribution.

    BDF fonts from the X.Org project. Startup 'splash' images should not be
    included in derivative projects, thanks. Tall splash images licensed from
    123RF.com, wide splash images used with permission of artist Lew Lashmit
    (viergacht@gmail.com). Rawr!

    Changes by tantalusrur@gmail.com:
    ---------------------------------
    Support for portrait/landscape for event time format
    Add support for moon/sun rise/set for today/tomorrow
    Add support or sleep mode during certain hours
    Add global-ish brightness control
    Code simplification, formatting and readability improvements
"""

print("VERSION 1.5.8")

# pylint: disable=import-error
import gc
import time
import math
import json
import board
import busio
import displayio
from rtc import RTC
from adafruit_matrixportal.network import Network
from adafruit_matrixportal.matrix import Matrix
from adafruit_bitmap_font import bitmap_font
import adafruit_display_text.label
import adafruit_lis3dh
import color

try:
    from secrets import secrets

except ImportError:
    print('WiFi secrets are kept in /secrets.py. Please add them there!')
    raise

# CONFIGURABLE SETTINGS #######################################################

TWELVE_HOUR = True      # If set, use 12-hour time vs 24-hour
HOURS_BETWEEN_SYNC = 1  # Number of hours between syncs with time server
SECONDS_PER_HOUR = 3600 # Number of seconds in one hour = 60 * 60
COUNTDOWN = False       # If set, show time to vs time of rise/set events
BIT_DEPTH = 6           # Ideally 6, but can set lower if RAM is tight
REFRESH_DELAY = 10      # Seconds to wait between screen updates. Should be 5 >= n <= 60
GLOBAL_BRIGHTNESS = 0.5 # Text brightness value ranging between 0.0 - 1.0

MOON_EVENT_COLOR = 0x333388
MOON_PERCENT_COLOR = 0xFFFF00
SUN_EVENT_COLOR = 0xC04000
TIME_COLOR = 0x808080
DATE_COLOR = 0x808080

# The meteorological data for TODAY and TOMORROW is kept in the PERIOD array.
PERIOD = [None, None]
TODAY = 0
TOMORROW = 1

TODAY_RISE = "\u2191" # ↑
TODAY_SET = "\u2193" # ↓
TOMORROW_RISE = "\u219F" # ↟
TOMORROW_SET = "\u21A1" # ↡

# SOME UTILITY FUNCTIONS FOR TIME MANIPULATION ################################

def parse_time(timestring, is_dst=-1):
    """ Given a string of the format YYYY-MM-DDTHH:MM:SS.SS-HH:MM (and
        optionally a DST flag), convert to and return an equivalent
        time.struct_time (strptime() isn't available here). Calling function
        can use time.mktime() on result if epoch seconds is needed instead.
        Time string is assumed local time; UTC offset is ignored. If seconds
        value includes a decimal fraction it's ignored.
    """

    date_time = timestring.split('T')        # Separate into date and time
    year_month_day = date_time[0].split('-') # Separate time into Y/M/D
    hour_minute_second = date_time[1].split('+')[0].split('-')[0].split(':')

    return time.struct_time(
        int(year_month_day[0]),
        int(year_month_day[1]),
        int(year_month_day[2]),
        int(hour_minute_second[0]),
        int(hour_minute_second[1]),
        int(hour_minute_second[2].split('.')[0]),
        -1, -1, is_dst
    )

def update_time(timezone=None):
    """ Update system date/time from WorldTimeAPI public server;
        no account required. Pass in time zone string
        (http://worldtimeapi.org/api/timezone for list)
        or None to use IP geolocation. Returns current local time as a
        time.struct_time and UTC offset as string. This may throw an
        exception on fetch_data() - it is NOT CAUGHT HERE, should be
        handled in the calling code because different behaviors may be
        needed in different situations (e.g. reschedule for later).
    """

    if timezone: # Use timezone api
        time_url = 'http://worldtimeapi.org/api/timezone/' + timezone
    else: # Use IP geolocation
        time_url = 'http://worldtimeapi.org/api/ip'

    time_data = NETWORK.fetch_data(time_url, json_path=[['datetime'], ['dst'], ['utc_offset']])
    time_struct = parse_time(time_data[0], time_data[1])
    RTC().datetime = time_struct

    return time_struct

def hh_mm(time_struct):
    # Given a time.struct_time, return a string as H:MM or HH:MM, in either 12 or 24 hour style.

    if TWELVE_HOUR:
        if time_struct.tm_hour > 12:
            hour_string = str(time_struct.tm_hour - 12) # 13-23 -> 1-11 (pm)
        elif time_struct.tm_hour > 0:
            hour_string = str(time_struct.tm_hour) # 1-12
        else:
            hour_string = '12' # 0 -> 12 (am)
    else:
        hour_string = '{0:0>2}'.format(time_struct.tm_hour)

    return hour_string + ':' + '{0:0>2}'.format(time_struct.tm_min)

def strftime(time_struct):
    return (str(time_struct.tm_year) + '/' +
        '{0:0>2}'.format(time_struct.tm_mon) + '/' +
        '{0:0>2}'.format(time_struct.tm_mday) + ' ' +
        '{0:0>2}'.format(time_struct.tm_hour) + ':' +
        '{0:0>2}'.format(time_struct.tm_min) + ':' +
        '{0:0>2}'.format(time_struct.tm_sec) + ' offset: ' + str(UTC_OFFSET))

def display_event(name, event, icon):
    time_struct = time.localtime(event)
    print(name + ': ' + strftime(time_struct))
    if LANDSCAPE_MODE:
        XPOS = 31
    else:
        XPOS = CENTER_X - (CLOCK_FACE[CLOCK_EVENT].bounding_box[2] + 6) // 2
    CLOCK_FACE[CLOCK_GLYPH].x = XPOS
    if name.startswith("Sun"):
        CLOCK_FACE[CLOCK_GLYPH].color = CLOCK_FACE[CLOCK_EVENT].color = color.set_brightness(SUN_EVENT_COLOR, GLOBAL_BRIGHTNESS)
    else:
        CLOCK_FACE[CLOCK_GLYPH].color = CLOCK_FACE[CLOCK_EVENT].color = color.set_brightness(MOON_EVENT_COLOR, GLOBAL_BRIGHTNESS)
    CLOCK_FACE[CLOCK_GLYPH].text = icon
    CLOCK_FACE[CLOCK_GLYPH].y = EVENT_Y - 2
    CLOCK_FACE[CLOCK_EVENT].x = XPOS + 6
    CLOCK_FACE[CLOCK_EVENT].y = EVENT_Y
    # 24 hour times are too large to fit with the glyph
    if time_struct.tm_hour > 12 and PORTRAIT_MODE:
        hour_string = str(time_struct.tm_hour - 12)
    elif time_struct.tm_hour > 0:
        hour_string = str(time_struct.tm_hour)
    CLOCK_FACE[CLOCK_EVENT].text = hour_string + ':' + '{0:0>2}'.format(time_struct.tm_min)

# METEOROLOGICAL DATA CLASS ###################################################

# pylint: disable=too-few-public-methods
class EarthData():
    """ Class holding lunar data for a given day (00:00:00 to 23:59:59). App uses two of these -- one for the
        current day, and one for the following day -- then some interpolations and such can be made.

        age      : Moon phase 'age' at midnight (start of period) expressed from 0.0 (new moon) through 0.5
                   (full moon) to 1.0 (next new moon).
        midnight : Epoch time in seconds @ midnight (start of period).
        moonrise : Epoch time of moon rise within this 24-hour period.
        moonset  : Epoch time of moon set within this 24-hour period.
        sunrise  : Epoch time of sun rise within this 24-hour period.
        sunset   : Epoch time of sun set within this 24-hour period.
    """
    def __init__(self, datetime, utc_offset):
        """ Initialize EarthData object elements (see above) from a time.struct_time, hours to skip ahead
            (typically 0 or 24), and a UTC offset (as a string) and a query to the MET Norway Sunrise API
            and provides lunar data. Documented at https://api.met.no/weatherapi/sunrise/2.0/documentation

            Example URL:
            https://api.met.no/weatherapi/sunrise/2.0/.json?lat=47.56&lon=-122.39&date=2020-11-28&offset=-08:00
        """
        # strftime() not available here
        url = ('https://api.met.no/weatherapi/sunrise/2.0/.json' +
            '?lat=' + str(LATITUDE) +
            '&lon=' + str(LONGITUDE) +
            '&date=' + str(datetime.tm_year) + '-' +
            '{0:0>2}'.format(datetime.tm_mon) + '-' +
            '{0:0>2}'.format(datetime.tm_mday) +
            '&offset=' + utc_offset)

        for _ in range(5): # Number of retries
            try:
                print('Fetching moon data via for: ' + '{0:0>2}'.format(datetime.tm_mon) + '/' +
                    '{0:0>2}'.format(datetime.tm_mday), url)
                full_data = json.loads(NETWORK.fetch_data(url))
                location_data = full_data['location']['time'][0]

                self.age = float(location_data['moonphase']['value']) / 100
                self.midnight = time.mktime(parse_time(location_data['moonphase']['time']))

                if 'sunrise' in location_data:
                    print(location_data['sunrise']['time'])
                    self.sunrise = time.mktime(parse_time(location_data['sunrise']['time']))
                else:
                    self.sunrise = None
                if 'sunset' in location_data:
                    self.sunset = time.mktime(parse_time(location_data['sunset']['time']))
                else:
                    self.sunset = None
                if 'moonrise' in location_data:
                    self.moonrise = time.mktime(parse_time(location_data['moonrise']['time']))
                else:
                    self.moonrise = None
                if 'moonset' in location_data:
                    self.moonset = time.mktime(parse_time(location_data['moonset']['time']))
                else:
                    self.moonset = None

                return

            except Exception as e:
                print('Fetching moon data via for: ' + str(e))
                time.sleep(15)

# ONE-TIME INITIALIZATION #####################################################

MATRIX = Matrix(bit_depth=BIT_DEPTH)
DISPLAY = MATRIX.display
ACCEL = adafruit_lis3dh.LIS3DH_I2C(busio.I2C(board.SCL, board.SDA), address=0x19)
ACCEL.acceleration # Dummy read to blow out any startup residue
time.sleep(0.1)
DISPLAY.rotation = (int(((math.atan2(-ACCEL.acceleration.y, -ACCEL.acceleration.x) + math.pi) /
    (math.pi * 2) + 0.875) * 4) % 4) * 90
if DISPLAY.rotation in (0, 180):
    LANDSCAPE_MODE = True
    PORTRAIT_MODE = False
else:
    LANDSCAPE_MODE = False
    PORTRAIT_MODE = True

LARGE_FONT = bitmap_font.load_font('/fonts/helvB12.bdf')
SMALL_FONT = bitmap_font.load_font('/fonts/helvR10.bdf')
SYMBOL_FONT = bitmap_font.load_font('/fonts/6x10.bdf')
LARGE_FONT.load_glyphs('0123456789:')
SMALL_FONT.load_glyphs('0123456789:/.%')
SYMBOL_FONT.load_glyphs('\u2191\u2193\u219F\u21A1')

# Display group is set up once, then we just shuffle items around later.
# Order of creation here determines their stacking order.
CLOCK_FACE = displayio.Group(max_size=10)
SLEEPING = displayio.Group(max_size=1)

# Element 0 is a stand-in image, later replaced with the moon phase bitmap
try:
    FILENAME = 'splash-' + str(DISPLAY.rotation) + '.bmp'
    TILE_GRID = displayio.TileGrid(displayio.OnDiskBitmap(
        open(FILENAME, 'rb')), pixel_shader=displayio.ColorConverter())
    CLOCK_FACE.append(TILE_GRID)

    TILE_GRID = displayio.TileGrid(displayio.OnDiskBitmap(
        open('sleeping.bmp', 'rb')), pixel_shader=displayio.ColorConverter())
    SLEEPING.append(TILE_GRID)

except Exception as e:
    print("Error loading image(s): " + str(e))
    CLOCK_FACE.append(adafruit_display_text.label.Label(SMALL_FONT,
        color=color.set_brightness(0xFF0000, GLOBAL_BRIGHTNESS), text='AWOO'))
    CLOCK_FACE[0].x = (DISPLAY.width - CLOCK_FACE[0].bounding_box[2] + 1) // 2
    CLOCK_FACE[0].y = DISPLAY.height // 2 - 1

# Elements 1-4 are an outline around the moon percentage -- text labels
# offset by 1 pixel up/down/left/right. Initial position is off the matrix,
# updated on first refresh. Initial text value must be long enough for
# longest anticipated string later.
for i in range(4):
    CLOCK_FACE.append(adafruit_display_text.label.Label(SMALL_FONT, color=0, text='99.9%', y=-99))

# Element 5 is the moon percentage (on top of the outline labels)
CLOCK_FACE.append(adafruit_display_text.label.Label(SMALL_FONT,
    color=color.set_brightness(MOON_PERCENT_COLOR, GLOBAL_BRIGHTNESS), text='99.9%', y=-99))
# Element 6 is the current time
CLOCK_FACE.append(adafruit_display_text.label.Label(LARGE_FONT,
    color=color.set_brightness(TIME_COLOR, GLOBAL_BRIGHTNESS), text='12:00', y=-99))
# Element 7 is the current date
CLOCK_FACE.append(adafruit_display_text.label.Label(SMALL_FONT,
    color=color.set_brightness(DATE_COLOR, GLOBAL_BRIGHTNESS), text='12/31', y=-99))
# Element 8 is a symbol indicating next rise or set - Color is overridden by event colors
CLOCK_GLYPH = 8
CLOCK_FACE.append(adafruit_display_text.label.Label(SYMBOL_FONT, color=0x00FF00, text='x', y=-99))
# Element 9 is the time of (or time to) next rise/set event - Color is overridden by event colors
CLOCK_EVENT = 9
CLOCK_FACE.append(adafruit_display_text.label.Label(SMALL_FONT, color=0x00FF00, text='12:00', y=-99))

DISPLAY.show(CLOCK_FACE)
DISPLAY.refresh()

NETWORK = Network(status_neopixel=board.NEOPIXEL, debug=False)
NETWORK.connect()

# Fetch latitude/longitude from secrets.py. If not present, use IP geolocation.
try:
    LATITUDE = secrets['latitude']
    LONGITUDE = secrets['longitude']
    print('Using stored geolocation: ', LATITUDE, LONGITUDE)

except KeyError:
    LATITUDE, LONGITUDE = (NETWORK.fetch_data(
        'http://www.geoplugin.net/json.gp',
        json_path=[['geoplugin_latitude'],
        ['geoplugin_longitude']]
    ))
    print('Using IP geolocation: ', LATITUDE, LONGITUDE)

# Load timezone from secrets.py, or use IP geolocation. See http://worldtimeapi.org/api/timezone
try:
    TIMEZONE = secrets['timezone'] # e.g. 'America/Los_Angeles'

except:
    TIMEZONE = None # Use IP geolocation

try:
    UTC_OFFSET = secrets['offset']
except:
    UTC_OFFSET = '-08:00' # If all else fails, default to PST

try:
    print("Setting initial clock time")
    DATETIME = update_time(TIMEZONE)

except Exception as e:
    print("Error setting initial clock time: " + str(e))
    DATETIME = time.localtime()

LAST_SYNC = time.mktime(DATETIME)

# Poll server for moon data for current TODAY and TOMORROW.
PERIOD[TODAY] = EarthData(DATETIME, UTC_OFFSET)
PERIOD[TOMORROW] = EarthData(time.localtime(time.mktime(DATETIME) + 24*3600), UTC_OFFSET)

# This is a count down for the 8 events: sunrise/sunset/moonrise/moonset x today/tomorrow
DIURNAL_EVENT = 8

# MAIN LOOP ###################################################################

while True:
    gc.collect()
    NOW = time.time() # Current epoch time in seconds

    # Periodically sync with time server since on-board clock is inaccurate
    if NOW - LAST_SYNC > HOURS_BETWEEN_SYNC * SECONDS_PER_HOUR:
        print("Syncing with time server")
        try:
            DATETIME, UTC_OFFSET = update_time(TIMEZONE)
            LAST_SYNC = time.mktime(DATETIME)
            continue # Time may have changed; refresh NOW value

        except Exception as e:
            print("Error syncing with time server: " + str(e))
            # update_time() can throw an exception if time server doesn't
            # respond. That's OK, keep running with our current time, and
            # push sync time ahead to retry in 30 minutes.
            LAST_SYNC += 30 * SECONDS_PER_HOUR # 30 minutes -> seconds

    # If PERIOD has expired, move data down and fetch new +24-hour data
    if NOW >= PERIOD[TOMORROW].midnight:
        PERIOD[TODAY] = PERIOD[TOMORROW]
        PERIOD[TOMORROW] = EarthData(time.localtime(), 24, UTC_OFFSET)

    # Determine weighting of tomorrow's phase vs today's, using current time
    RATIO = ((NOW - PERIOD[TODAY].midnight) / (PERIOD[TOMORROW].midnight - PERIOD[TODAY].midnight))

    # Determine moon phase 'age'
    # 0.0  = new moon
    # 0.25 = first quarter
    # 0.5  = full moon
    # 0.75 = last quarter
    # 1.0  = new moon
    if PERIOD[TODAY].age < PERIOD[TOMORROW].age:
        AGE = (PERIOD[TODAY].age + (PERIOD[TOMORROW].age - PERIOD[TODAY].age) * RATIO) % 1.0
    else: # Handle age wraparound (1.0 -> 0.0)
        # If tomorrow's age is less than today's, it indicates a new moon
        # crossover. Add 1 to tomorrow's age when computing age delta.
        AGE = (PERIOD[TODAY].age + (PERIOD[TOMORROW].age + 1 - PERIOD[TODAY].age) * RATIO) % 1.0

    # AGE can be used for direct lookup to moon bitmap (0 to 99) -- these
    # images are pre-rendered for a linear timescale (solar terminator moves
    # nonlinearly across sphere).
    FRAME = int(AGE * 100) % 100 # Bitmap 0 to 99

    # Then use some trig to get percentage lit
    if AGE <= 0.5: # New -> first quarter -> full
        PERCENT = (1 - math.cos(AGE * 2 * math.pi)) * 50
    else:          # Full -> last quarter -> new
        PERCENT = (1 + math.cos((AGE - 0.5) * 2 * math.pi)) * 50

    NEXT_MOON_EVENT = PERIOD[1].midnight + 100000 # Force first match
    for DAY in reversed(PERIOD):
        if DAY.moonrise and NEXT_MOON_EVENT >= DAY.moonrise >= NOW:
            NEXT_MOON_EVENT = DAY.moonrise
            MOON_RISEN = False
        if DAY.moonset and NEXT_MOON_EVENT >= DAY.moonset >= NOW:
            NEXT_MOON_EVENT = DAY.moonset
            MOON_RISEN = True

    NEXT_SUN_EVENT = PERIOD[1].midnight + 100000 # Force first match
    for DAY in reversed(PERIOD):
        if DAY.sunrise and NEXT_SUN_EVENT >= DAY.sunrise >= NOW:
            NEXT_SUN_EVENT = DAY.sunrise
            SUN_RISEN = False
        if DAY.sunset and NEXT_SUN_EVENT >= DAY.sunset >= NOW:
            NEXT_SUN_EVENT = DAY.sunset
            SUN_RISEN = True

    if LANDSCAPE_MODE: # Horizontal 'landscape' orientation
        CENTER_X = 48      # Text along right
        MOON_Y = 0         # Moon at left
        TIME_Y = 6         # Time at top right
        EVENT_Y = 26       # Rise/set at bottom right
        EVENTS_24 = True   # In landscape mode, there's enough room for 24 event hour times
    else:                  # Vertical 'portrait' orientation
        EVENTS_24 = True   # In portrain mode, there's only room for 12 event hour times
        CENTER_X = 16      # Text down center
        if MOON_RISEN or SUN_RISEN:
            MOON_Y = 0     # Moon at top
            EVENT_Y = 38   # Rise/set in middle
            TIME_Y = 49    # Time/date at bottom
        else:
            TIME_Y = 6     # Time/date at top
            EVENT_Y = 26   # Rise/set in middle
            MOON_Y = 32    # Moon at bottom

    print()

    # Update moon image (CLOCK_FACE[0])
    try:
        FILENAME = 'moon/moon' + '{0:0>2}'.format(FRAME) + '.bmp'
        BITMAP = displayio.OnDiskBitmap(open(FILENAME, 'rb'))
        TILE_GRID = displayio.TileGrid(BITMAP, pixel_shader=displayio.ColorConverter())
        TILE_GRID.x = 0
        TILE_GRID.y = MOON_Y
        CLOCK_FACE[0] = TILE_GRID
    except Exception as e:
        print(e)

    # Update percent value (5 labels: CLOCK_FACE[1-4] for outline, [5] for text)
    if PERCENT >= 99.95:
        STRING = '100%'
    else:
        STRING = '{:.1f}'.format(PERCENT + 0.05) + '%'
    print(STRING, 'full')

    LOCAL_TIME = time.localtime()
    print(strftime(LOCAL_TIME))

    # Set element 5 first, use its size and position for setting others
    CLOCK_FACE[5].text = STRING
    CLOCK_FACE[5].x = 16 - CLOCK_FACE[5].bounding_box[2] // 2
    CLOCK_FACE[5].y = MOON_Y + 16

    for _ in range(1, 5):
        CLOCK_FACE[_].text = CLOCK_FACE[5].text

    CLOCK_FACE[1].x, CLOCK_FACE[1].y = CLOCK_FACE[5].x, CLOCK_FACE[5].y - 1 # Up 1 pixel
    CLOCK_FACE[2].x, CLOCK_FACE[2].y = CLOCK_FACE[5].x - 1, CLOCK_FACE[5].y # Left
    CLOCK_FACE[3].x, CLOCK_FACE[3].y = CLOCK_FACE[5].x + 1, CLOCK_FACE[5].y # Right
    CLOCK_FACE[4].x, CLOCK_FACE[4].y = CLOCK_FACE[5].x, CLOCK_FACE[5].y + 1 # Down

    # Update next-event time (CLOCK_FACE[CLOCK_GLYPH] and [CLOCK_EVENT])
    # Do this before time because we need uncorrupted NOW value

    if DIURNAL_EVENT == 8:
        display_event("Sunrise today", PERIOD[TODAY].sunrise, TODAY_RISE)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 7:
        display_event("Sunset today", PERIOD[TODAY].sunset, TODAY_SET)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 6:
        display_event("Moonrise today", PERIOD[TODAY].moonrise, TODAY_RISE)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 5:
        display_event("Moonset today", PERIOD[TODAY].moonset, TODAY_SET)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 4:
        display_event("Sunrise tomorrow", PERIOD[TOMORROW].sunrise, TOMORROW_RISE)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 3:
        display_event("Sunset tomorrow", PERIOD[TOMORROW].sunset, TOMORROW_SET)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 2:
        display_event("Moonrise tomorrow", PERIOD[TOMORROW].moonrise, TOMORROW_RISE)
        DIURNAL_EVENT -= 1
    elif DIURNAL_EVENT == 1:
        display_event("Moonset tomorrow", PERIOD[TOMORROW].moonset, TOMORROW_SET)
        DIURNAL_EVENT = 8

    # Update time
    NOW = time.localtime()
    STRING = hh_mm(NOW)
    CLOCK_FACE[6].text = STRING
    CLOCK_FACE[6].x = CENTER_X - CLOCK_FACE[6].bounding_box[2] // 2
    CLOCK_FACE[6].y = TIME_Y
    # Update date
    STRING = str(NOW.tm_mon) + '/ ' + str(NOW.tm_mday)
    CLOCK_FACE[7].text = STRING
    CLOCK_FACE[7].x = CENTER_X - CLOCK_FACE[7].bounding_box[2] // 2 - 1
    CLOCK_FACE[7].y = TIME_Y + 10

    # Show the clock between 7AM and 11PM, otherwise go to sleep
    if 7 < NOW.tm_hour < 23:
        DISPLAY.show(CLOCK_FACE)
    else:
        DISPLAY.show(SLEEPING)

    DISPLAY.refresh()
    time.sleep(REFRESH_DELAY)
