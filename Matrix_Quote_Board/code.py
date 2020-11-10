# Quote board matrix display
# uses AdafruitIO to serve up a quote text feed and color feed
# random quotes are displayed, updates periodically to look for new quotes
# avoids repeating the same quote twice in a row

import time
import random
import board
import terminalio
from adafruit_matrixportal.matrixportal import MatrixPortal


# Display setup
matrixportal = MatrixPortal(status_neopixel=board.NEOPIXEL, debug=True)

# Setup the font and text position to be rendered
matrixportal.add_text(
    text_font="/fonts/IBMPlexMono-Medium-24_jep.bdf",
    text_position=(0, (matrixportal.graphics.display.height // 2) - 1),
    scrolling=True,
)


SCROLL_DELAY = 0.03
UPDATE_DELAY = 1

quotes = [
    "This is a test of the emergency broadcast system...",
    "OH HAI!",
    "wtf",
    "lol"
]
colors = [0xFFA500, 0x008000, 0x0000FF, 0x4B0082, 0xEE82EE]

quote_index = random.randrange(0, len(quotes))
color_index = random.randrange(0, len(colors))
last_color_index = 0
last_quote_index = 0
last_update = time.monotonic()
matrixportal.set_text(" ", 0)


while True:
    # Set the quote text
    matrixportal.set_text(quotes[quote_index], 0)

    # Set the text color
    matrixportal.set_text_color(colors[color_index])

    # Scroll it
    matrixportal.scroll_text(SCROLL_DELAY)

    the_time = time.monotonic()
    if the_time > last_update + UPDATE_DELAY:
        # Time's up!
        last_update = time.monotonic()

        # Pick a new quote
        while quote_index == last_quote_index:
            quote_index = random.randrange(0, len(quotes))
        last_quote_index = quote_index

        # Pick a new color
        while color_index == last_color_index:
            color_index = random.randrange(0, len(colors))
        last_color_index = color_index
