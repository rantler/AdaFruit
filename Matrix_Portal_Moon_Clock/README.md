# Moon Clock

Memory size of this project is approaching the limits of CircuitPython so be aware that additional code
changes can sometimes behave inconsistently and/or result in Memory Errors.

Note, the BMP images must be 8-bit indexed color or they will not render. You can use ImageMagick, or
ImageScience to convert an existing BMP file to 8-bit using a command like this one:

```
convert image.bmp -depth 8 output.bmp; mv output.bmp image.bmp
```

The properties in the `secrets.yml` file you should set are:

* `latitude` - a floating point value representing your location
* `longitude` - a floating point value representing your location
* `offset` - a string value representing the number of hours difference from GMT / UTC for your timezone
