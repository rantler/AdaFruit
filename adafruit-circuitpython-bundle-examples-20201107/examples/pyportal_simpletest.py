# NOTE: Make sure you've created your secrets.py file before running this example
# https://learn.adafruit.com/adafruit-pyportal/internet-connect#whats-a-secrets-file-17-2
import board
from adafruit_pyportal import PyPortal

# Set a data source URL
TEXT_URL = "http://wifitest.adafruit.com/testwifi/index.html"

# Create the PyPortal object
pyportal = PyPortal(url=TEXT_URL, status_neopixel=board.NEOPIXEL)

# Set display to show REPL
board.DISPLAY.show(None)

# Go get that data
print("Fetching text from", TEXT_URL)
data = pyportal.fetch()

# Print out what we got
print("-" * 40)
print(data)
print("-" * 40)