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

    Readability improvements and support for sunrise/sunset added by
    tantalusrur@gmail.com

    Version 1.2.8
"""

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

# CONFIGURABLE SETTINGS ----------------------------------------------------

TWELVE_HOUR = True      # If set, use 12-hour time vs 24-hour
COUNTDOWN = False       # If set, show time to vs time of rise/set events
BIT_DEPTH = 6           # Ideally 6, but can set lower if RAM is tight
REFRESH_DELAY = 60      # Seconds to wait between updates. Should be <= ~5
GLOBAL_BRIGHTNESS = 0.1 # Value ranging between 0.0 - 1.0 of text brightness

MOON_EVENT_COLOR = 0x333366
MOON_PERCENT_COLOR = 0xFFFF00
SUN_EVENT_COLOR = 0xC04000
TIME_COLOR = 0x808080
DATE_COLOR = 0x808080


# SOME UTILITY FUNCTIONS AND CLASSES ---------------------------------------

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

    return time_struct#, time_data[2]


def hh_mm(time_struct):
    """ Given a time.struct_time, return a string as H:MM or HH:MM, either
        12- or 24-hour style depending on global TWELVE_HOUR setting.
        This is ONLY for 'clock time,' NOT for countdown time, which is
        handled separately in the one spot where it's needed.
    """

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


# pylint: disable=too-few-public-methods
class EarthData():
    """ Class holding lunar data for a given day (00:00:00 to 23:59:59).
        App uses two of these -- one for the current day, and one for the
        following day -- then some interpolations and such can be made.
        Elements include:
        age      : Moon phase 'age' at midnight (start of period)
                   expressed from 0.0 (new moon) through 0.5 (full moon)
                   to 1.0 (next new moon).
        midnight : Epoch time in seconds @ midnight (start of period).
        moonrise : Epoch time of moon rise within this 24-hour period.
        moonset  : Epoch time of moon set within this 24-hour period.
        sunrise  : Epoch time of sun rise within this 24-hour period.
        sunset   : Epoch time of sun set within this 24-hour period.
    """
    def __init__(self, datetime, hours_ahead, utc_offset):
        """ Initialize EarthData object elements (see above) from a
            time.struct_time, hours to skip ahead (typically 0 or 24),
            and a UTC offset (as a string) and a query to the MET Norway
            Sunrise API (also provides lunar data), documented at:
            https://api.met.no/weatherapi/sunrise/2.0/documentation
        """
        if hours_ahead:
            # Can't change attribute in datetime struct, need to create
            # a new one which will roll the date ahead as needed. Convert
            # to epoch seconds and back for the offset to work
            datetime = time.localtime(
                time.mktime(
                    time.struct_time(
                        datetime.tm_year,
                        datetime.tm_mon,
                        datetime.tm_mday,
                        datetime.tm_hour + hours_ahead,
                        datetime.tm_min,
                        datetime.tm_sec,
                        -1,
                        -1,
                        -1
                    )
                )
            )
        # strftime() not available here
        url = ('https://api.met.no/weatherapi/sunrise/2.0/.json' +
            '?lat=' + str(LATITUDE) +
            '&lon=' + str(LONGITUDE) +
            '&date=' + str(datetime.tm_year) + '-' +
            '{0:0>2}'.format(datetime.tm_mon) + '-' +
            '{0:0>2}'.format(datetime.tm_mday) +
            '&offset=' + utc_offset)
        print('Fetching moon data via for: ' +
            '{0:0>2}'.format(datetime.tm_mon) + '/' +
            '{0:0>2}'.format(datetime.tm_mday), url)

        # pylint: disable=bare-except
        for _ in range(5): # Retries
            try:
                full_data = json.loads(NETWORK.fetch_data(url))
                location_data = full_data['location']['time'][0]

                # Reconstitute JSON data into the elements we need
                self.age = float(location_data['moonphase']['value']) / 100
                self.midnight = time.mktime(parse_time(location_data['moonphase']['time']))

                if 'sunrise' in location_data:
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

                return # Success!

            except Exception as e:
                print(e)
                # Server error (maybe), try again after 15 seconds.
                # (Might be a memory error, that should be handled different)
                time.sleep(15)


# ONE-TIME INITIALIZATION --------------------------------------------------

MATRIX = Matrix(bit_depth=BIT_DEPTH)
DISPLAY = MATRIX.display
ACCEL = adafruit_lis3dh.LIS3DH_I2C(busio.I2C(board.SCL, board.SDA), address=0x19)
ACCEL.acceleration # Dummy read to blow out any startup residue
time.sleep(0.1)
DISPLAY.rotation = (int(((math.atan2(-ACCEL.acceleration.y, -ACCEL.acceleration.x) + math.pi) /
    (math.pi * 2) + 0.875) * 4) % 4) * 90

LARGE_FONT = bitmap_font.load_font('/fonts/helvB12.bdf')
SMALL_FONT = bitmap_font.load_font('/fonts/helvR10.bdf')
SYMBOL_FONT = bitmap_font.load_font('/fonts/6x10.bdf')
LARGE_FONT.load_glyphs('0123456789:')
SMALL_FONT.load_glyphs('0123456789:/.%')
SYMBOL_FONT.load_glyphs('\u21A5\u21A7')

# Display group is set up once, then we just shuffle items around later.
# Order of creation here determines their stacking order.
GROUP = displayio.Group(max_size=10)

# Element 0 is a stand-in item, later replaced with the moon phase bitmap
# pylint: disable=bare-except
try:
    FILENAME = 'moon/splash-' + str(DISPLAY.rotation) + '.bmp'
    BITMAP = displayio.OnDiskBitmap(open(FILENAME, 'rb'))
    TILE_GRID = displayio.TileGrid(BITMAP, pixel_shader=displayio.ColorConverter())
    GROUP.append(TILE_GRID)

except:
    GROUP.append(adafruit_display_text.label.Label(SMALL_FONT,
        color=color.set_brightness(0xFF0000, GLOBAL_BRIGHTNESS), text='AWOO'))
    GROUP[0].x = (DISPLAY.width - GROUP[0].bounding_box[2] + 1) // 2
    GROUP[0].y = DISPLAY.height // 2 - 1

# Elements 1-4 are an outline around the moon percentage -- text labels
# offset by 1 pixel up/down/left/right. Initial position is off the matrix,
# updated on first refresh. Initial text value must be long enough for
# longest anticipated string later.
for i in range(4):
    GROUP.append(adafruit_display_text.label.Label(SMALL_FONT, color=0, text='99.9%', y=-99))

# Element 5 is the moon percentage (on top of the outline labels)
GROUP.append(adafruit_display_text.label.Label(SMALL_FONT,
    color=color.set_brightness(MOON_PERCENT_COLOR, GLOBAL_BRIGHTNESS), text='99.9%', y=-99))
# Element 6 is the current time
GROUP.append(adafruit_display_text.label.Label(LARGE_FONT,
    color=color.set_brightness(TIME_COLOR, GLOBAL_BRIGHTNESS), text='12:00', y=-99))
# Element 7 is the current date
GROUP.append(adafruit_display_text.label.Label(SMALL_FONT,
    color=color.set_brightness(DATE_COLOR, GLOBAL_BRIGHTNESS), text='12/31', y=-99))
# Element 8 is a symbol indicating next rise or set - Color is overridden by event colors
GROUP.append(adafruit_display_text.label.Label(SYMBOL_FONT, color=0x00FF00, text='x', y=-99))
# Element 9 is the time of (or time to) next rise/set event - Color is overridden by event colors
GROUP.append(adafruit_display_text.label.Label(SMALL_FONT, color=0x00FF00, text='12:00', y=-99))
DISPLAY.show(GROUP)

NETWORK = Network(status_neopixel=board.NEOPIXEL, debug=False)
NETWORK.connect()

# Fetch latitude/longitude from secrets.py. If not present, use
# IP geolocation. This only needs to be done once, at startup!
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

# Load time zone string from secrets.py, else IP geolocation for this too
# (http://worldtimeapi.org/api/timezone for list).
try:
    TIMEZONE = secrets['timezone'] # e.g. 'America/Los_Angeles'

except:
    TIMEZONE = None # IP geolocation

try:
    UTC_OFFSET = secrets['offset']
except:
    UTC_OFFSET = '-08:00'

# Set initial clock time, also fetch initial UTC offset while
# here (NOT stored in secrets.py as it may change with DST).
# pylint: disable=bare-except
try:
    DATETIME = update_time(TIMEZONE)

except Exception as e:
    print(e)
    DATETIME = time.localtime()
    # time.timezone doesn't exist. wtf? smh...
    # DATETIME, UTC_OFFSET = time.localtime(), '-{0:0>2}:00'.format(time.timezone / 3600)

LAST_SYNC = time.mktime(DATETIME)

# Poll server for moon data for current 24-hour period and +24 ahead
PERIOD = []

for DAY in range(2):
    PERIOD.append(EarthData(DATETIME, DAY * 24, UTC_OFFSET))

# PERIOD[0] is the current 24-hour time period we're in. PERIOD[1] is the
# following 24 hours. Data is shifted down and new data fetched as days
# expire. Thought we might need a PERIOD[2] for certain circumstances but
# it appears not, that's changed easily enough if needed.

FLIP_FLOP = False

# MAIN LOOP ----------------------------------------------------------------

while True:
    gc.collect()
    NOW = time.time() # Current epoch time in seconds

    # Sync with time server every ~12 hours
    if NOW - LAST_SYNC > 12 * 60 * 60:
        try:
            DATETIME, UTC_OFFSET = update_time(TIMEZONE)
            LAST_SYNC = time.mktime(DATETIME)
            continue # Time may have changed; refresh NOW value

        except:
            # update_time() can throw an exception if time server doesn't
            # respond. That's OK, keep running with our current time, and
            # push sync time ahead to retry in 30 minutes (don't overwhelm
            # the server with repeated queries).
            LAST_SYNC += 30 * 60 * 60 # 30 minutes -> seconds

    # If PERIOD has expired, move data down and fetch new +24-hour data
    if NOW >= PERIOD[1].midnight:
        PERIOD[0] = PERIOD[1]
        PERIOD[1] = EarthData(time.localtime(), 24, UTC_OFFSET)

    # Determine weighting of tomorrow's phase vs today's, using current time
    RATIO = ((NOW - PERIOD[0].midnight) / (PERIOD[1].midnight - PERIOD[0].midnight))

    # Determine moon phase 'age'
    # 0.0  = new moon
    # 0.25 = first quarter
    # 0.5  = full moon
    # 0.75 = last quarter
    # 1.0  = new moon
    if PERIOD[0].age < PERIOD[1].age:
        AGE = (PERIOD[0].age + (PERIOD[1].age - PERIOD[0].age) * RATIO) % 1.0
    else: # Handle age wraparound (1.0 -> 0.0)
        # If tomorrow's age is less than today's, it indicates a new moon
        # crossover. Add 1 to tomorrow's age when computing age delta.
        AGE = (PERIOD[0].age + (PERIOD[1].age + 1 - PERIOD[0].age) * RATIO) % 1.0

    # AGE can be used for direct lookup to moon bitmap (0 to 99) -- these
    # images are pre-rendered for a linear timescale (solar terminator moves
    # nonlinearly across sphere).
    FRAME = int(AGE * 100) % 100 # Bitmap 0 to 99

    # Then use some trig to get percentage lit
    if AGE <= 0.5: # New -> first quarter -> full
        PERCENT = (1 - math.cos(AGE * 2 * math.pi)) * 50
    else:          # Full -> last quarter -> new
        PERCENT = (1 + math.cos((AGE - 0.5) * 2 * math.pi)) * 50

    # Find next rise/set event, complicated by the fact that some 24-hour
    # periods might not have one or the other (but usually do) due to the
    # Moon rising ~50 mins later each day. This uses a brute force approach,
    # working backwards through the time periods to locate rise/set events
    # that A) exist in that 24-hour period (are not None), B) are still in
    # the future, and C) are closer than the last guess. What's left at the
    # end is the next rise or set (and the inverse of the event type tells
    # us whether Moon's currently risen or not).
    NEXT_MOON_EVENT = PERIOD[1].midnight + 100000 # Force first match

    for DAY in reversed(PERIOD):
        if DAY.moonrise and NEXT_MOON_EVENT >= DAY.moonrise >= NOW:
            NEXT_MOON_EVENT = DAY.moonrise
            MOON_RISEN = False
        if DAY.moonset and NEXT_MOON_EVENT >= DAY.moonset >= NOW:
            NEXT_MOON_EVENT = DAY.moonset
            MOON_RISEN = True

    # Now same for sun events
    NEXT_SUN_EVENT = PERIOD[1].midnight + 100000 # Force first match

    for DAY in reversed(PERIOD):
        if DAY.sunrise and NEXT_SUN_EVENT >= DAY.sunrise >= NOW:
            NEXT_SUN_EVENT = DAY.sunrise
            SUN_RISEN = False
        if DAY.sunset and NEXT_SUN_EVENT >= DAY.sunset >= NOW:
            NEXT_SUN_EVENT = DAY.sunset
            SUN_RISEN = True

    if DISPLAY.rotation in (0, 180): # Horizontal 'landscape' orientation
        CENTER_X = 48      # Text along right
        MOON_Y = 0         # Moon at left
        TIME_Y = 6         # Time at top right
        EVENT_Y = 26       # Rise/set at bottom right
    else:                  # Vertical 'portrait' orientation
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

    # Update moon image (GROUP[0])
    FILENAME = 'moon/moon' + '{0:0>2}'.format(FRAME) + '.bmp'
    BITMAP = displayio.OnDiskBitmap(open(FILENAME, 'rb'))
    TILE_GRID = displayio.TileGrid(BITMAP, pixel_shader=displayio.ColorConverter())
    TILE_GRID.x = 0
    TILE_GRID.y = MOON_Y
    GROUP[0] = TILE_GRID

    # Update percent value (5 labels: GROUP[1-4] for outline, [5] for text)
    if PERCENT >= 99.95:
        STRING = '100%'
    else:
        STRING = '{:.1f}'.format(PERCENT + 0.05) + '%'
    print(STRING, 'full')

    LOCAL_TIME = time.localtime()
    print(str(LOCAL_TIME.tm_year) + '/' +
        '{0:0>2}'.format(LOCAL_TIME.tm_mon) + '/' +
        '{0:0>2}'.format(LOCAL_TIME.tm_mday) + ' ' +
        '{0:0>2}'.format(LOCAL_TIME.tm_hour) + ':' +
        '{0:0>2}'.format(LOCAL_TIME.tm_min) + ':' +
        '{0:0>2}'.format(LOCAL_TIME.tm_sec) + ' offset: ' + str(UTC_OFFSET))

    # Set element 5 first, use its size and position for setting others
    GROUP[5].text = STRING
    GROUP[5].x = 16 - GROUP[5].bounding_box[2] // 2
    GROUP[5].y = MOON_Y + 16

    for _ in range(1, 5):
        GROUP[_].text = GROUP[5].text

    GROUP[1].x, GROUP[1].y = GROUP[5].x, GROUP[5].y - 1 # Up 1 pixel
    GROUP[2].x, GROUP[2].y = GROUP[5].x - 1, GROUP[5].y # Left
    GROUP[3].x, GROUP[3].y = GROUP[5].x + 1, GROUP[5].y # Right
    GROUP[4].x, GROUP[4].y = GROUP[5].x, GROUP[5].y + 1 # Down

    # Update next-event time (GROUP[8] and [9])
    # Do this before time because we need uncorrupted NOW value

    if FLIP_FLOP is True:
      FLIP_FLOP = False
      EVENT_TIME = time.localtime(NEXT_MOON_EVENT) # Convert to struct for later
      if COUNTDOWN: # Show NEXT_MOON_EVENT as countdown to event
          NEXT_MOON_EVENT -= NOW # Time until (vs time of) next rise/set
          MINUTES = NEXT_MOON_EVENT // 60
          STRING = str(MINUTES // 60) + ':' + '{0:0>2}'.format(MINUTES % 60)
      else: # Show NEXT_MOON_EVENT in clock time
          STRING = hh_mm(EVENT_TIME)
      # Show moon event time in blue/grey
      GROUP[8].color = GROUP[9].color = color.set_brightness(MOON_EVENT_COLOR, GLOBAL_BRIGHTNESS)
    else:
      FLIP_FLOP = True
      EVENT_TIME = time.localtime(NEXT_SUN_EVENT) # Convert to struct for later
      if COUNTDOWN: # Show NEXT_SUN_EVENT as countdown to event
          NEXT_SUN_EVENT -= NOW # Time until (vs time of) next rise/set
          MINUTES = NEXT_SUN_EVENT // 60
          STRING = str(MINUTES // 60) + ':' + '{0:0>2}'.format(MINUTES % 60)
      else: # Show NEXT_SUN_EVENT in clock time
          STRING = hh_mm(EVENT_TIME)
      # Show sun event time amber
      GROUP[8].color = GROUP[9].color = color.set_brightness(SUN_EVENT_COLOR, GLOBAL_BRIGHTNESS)

    GROUP[9].text = STRING # TODO Fix spacing issues for values like 11 :38
    XPOS = CENTER_X - (GROUP[9].bounding_box[2] + 6) // 2
    GROUP[8].x = XPOS

    # Next event is MOONSET or SUNSET
    if MOON_RISEN or SUN_RISEN:
        GROUP[8].text = '\u21A7' # Downward arrow from bar
        GROUP[8].y = EVENT_Y - 2
        if FLIP_FLOP:
            print('Sunset:', STRING)
        else:
            print('Moonset:', STRING)

    # Next event is MOONRISE or SUNRISE
    else:
        GROUP[8].text = '\u21A5' # Upward arrow from bar
        GROUP[8].y = EVENT_Y - 1
        if FLIP_FLOP:
            print('Sunrise:', STRING)
        else:
            print('Moonrise:', STRING)

    GROUP[9].x = XPOS + 6
    GROUP[9].y = EVENT_Y

    # Update time
    NOW = time.localtime()
    STRING = hh_mm(NOW)
    GROUP[6].text = STRING
    GROUP[6].x = CENTER_X - GROUP[6].bounding_box[2] // 2
    GROUP[6].y = TIME_Y
    # Update date
    STRING = str(NOW.tm_mon) + '/ ' + str(NOW.tm_mday)
    GROUP[7].text = STRING
    GROUP[7].x = CENTER_X - GROUP[7].bounding_box[2] // 2 - 1
    GROUP[7].y = TIME_Y + 10

    # Force full repaint (splash screen sometimes sticks)
    DISPLAY.refresh()
    time.sleep(REFRESH_DELAY)
