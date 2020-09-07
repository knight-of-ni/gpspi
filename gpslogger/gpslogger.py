#! /usr/bin/python
#
# GPS Data and Photo Logger for the Raspberry Pi
#
# 1) Logs GPS data to csv every 100 feet
# 2) Takes a photo from the pi camera every 100 feet
# 3) Embeds Exif data into each photo
#
# This is a heavaily modified version of the following script by Mark Williams:
# https://ozzmaker.com/how-to-save-gps-data-to-a-file-using-python/
#
# GPS string conversion formats obtained from:
# https://wiki.openstreetmap.org/wiki/User:Kannix/RPI-Cam
#
# DS18B20 Temperature functions from Adafruit:
# https://learn.adafruit.com/adafruits-raspberry-pi-lesson-11-ds18b20-temperature-sensing/software
#
# The following packages may need to be installed to run this script:
#
# apt-get install \
# gpsd gpsd-clients python-gps python-geopy python-tz python-dateutil \
# python-rpi.gpio
#

from gps import *
import time, inspect, os, picamera, math, argparse, glob
import sys
from datetime import datetime
from pytz import timezone
from geopy import distance
import RPi.GPIO as GPIO

GPIO.setmode(GPIO.BCM)

# Allows us to log data from GPIO button
trig_received = False

# Our callback function simply sets a flag when a button has been pressed
# The main worker loop will handle the rest
def button_pressed(channel):
  global trig_received
  trig_received = True

# DS18B20 temperature probe function
def read_temp_raw():
    f = open(glob.glob('/sys/bus/w1/devices/' + '28*')[0] + '/w1_slave', 'r')
    lines = f.readlines()
    f.close()
    return lines

# DS18B20 temperature probe function
def read_temp():
    lines = read_temp_raw()
    while lines[0].strip()[-3:] != 'YES':
        time.sleep(0.2)
        lines = read_temp_raw()
    equals_pos = lines[1].find('t=')
    if equals_pos != -1:
        temp_fing = lines[1][equals_pos+2:]
        temp_c = float(temp_fing) / 1000.0
        temp_f = temp_c * 9.0 / 5.0 + 32.0
        return temp_c, temp_f

# convert decimal degrees to degrees, minutes, seconds
# by design, the negative sign is ignored
def dec2dms(ddeg):
   ddeg = math.fabs(ddeg)

   degrees = int(ddeg)
   minutes = int(60 * (ddeg - degrees))
   seconds = int(6000 * (60 * (ddeg - degrees) - minutes))

   return (degrees,minutes,seconds)

# Python's isnumeric doesn't work on floats, so define our own test for a number
# It must handle "NaN" too
def is_number(s):
  try:
    x = float(s)
    return x == x # this will return false for NaN and Inf
  except ValueError:
    return False

# convert string to float, handle "nan" correctly
def strtofloat(s):
  if not is_number(s): s = 0.0
  return float(s)

# retrive latitude, longitude, and satellite fix status from the gps report
def latlonfix(report):
  lat = strtofloat(getattr(report,'lat',0.0))
  lon = strtofloat(getattr(report,'lon',0.0))
  satfix = int(strtofloat(getattr(report,'mode',0)))

  return (lat, lon, satfix)

# This function does most of the work
def logGPSdata(fullpath,subdir,csvfilename,ndx,prev_loc,f,dtraveled,debug):
  lat = 0.0
  lon = 0.0
  satfix = 0
  sats = 0

  # We must create a new gpsd object each time we call logGPSdata, to flush the buffer and get latest data
  gpsd = gps(mode=WATCH_ENABLE|WATCH_NEWSTYLE)

  # Keep looping until we get valid latitude, longitude, and satellite values from gpsd
  noSats = noCoords = True
  while noSats or noCoords:
    report = gpsd.next()
    if report['class'] == 'SKY' and noSats: 
      sats = len(report['satellites']) # update num of satellites in view
      noSats = False
    elif report['class'] == 'TPV' and noCoords:
      (lat,lon,satfix) = latlonfix(report)
      if lon and lat: # Yeah, this won't work if standing exactly on the prime meridian or the equator
        saveReport = report
        noCoords = False
      elif not satfix: time.sleep(0.5) # We don't have a satellite fix so slow our roll
 
  # Define additional variables
  utc = datetime.strptime(str(getattr(saveReport,'time','')), '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone('UTC'))
  central = utc.astimezone(timezone('US/Central'))
  date_str = central.strftime("%b %d %Y")
  time_str = central.strftime("%I:%M:%S%p %Z")
  lat_ref = 'S' if lat < 0 else 'N'
  lon_ref = 'W' if lon < 0 else 'E' 
  speed_mps =  strtofloat(getattr(saveReport,'speed','0.0'))
  speed_mph =  round(speed_mps*2.23694,1)
  alt_meters = strtofloat(getattr(saveReport,'alt','0.0'))
  alt_feet = round(alt_meters*3.28084,1)
  cur_loc = (lat, lon)
  temp = read_temp() # temp is an array containing both fahrenheit and celcius values
  temp_f = round(temp[1],1)

  # Only log a data point if we've traveled more than X feet
  if distance.distance(prev_loc, cur_loc).feet > dtraveled:
    ndx += 1
    picname = subdir + '-' + str(ndx) + '.jpg'

    # print some output to the screen if debug is on
    if debug:
      print  date_str,"\t",
      print  time_str,"\t",
      print  lat,"\t",
      print  lon,"\t",
      print  speed_mph,"\t",
      print  alt_feet,"\t",
      print  temp_f,"\t",
      print  sats,"\t",
      print  picname,"\t"

    with open(fullpath + '/' + csvfilename,'a') as f:
      f.write('%s,%s,%s,%s,%s,%s,%s,%s,%s' % (date_str, time_str, lat, lon, speed_mph, alt_feet, temp_f, sats, picname))
      
    # Fire up the Pi Camera then take a picture!
    with picamera.PiCamera() as camera:
      camera.resolution = (3280, 2464)
      camera.rotation = 270
      camera.start_preview()
      # Camera warm-up time
      time.sleep(2)

      # Apply GPS Exif tags
      camera.exif_tags['GPS.GPSLatitude'] = '%d/1,%d/1,%d/100' % dec2dms(lat)
      camera.exif_tags['GPS.GPSLatitudeRef'] = lat_ref
      camera.exif_tags['GPS.GPSLongitude'] = '%d/1,%d/1,%d/100' % dec2dms(lon) 
      camera.exif_tags['GPS.GPSLongitudeRef'] = lon_ref
      camera.exif_tags['GPS.GPSAltitude'] = '%d/100' % int(100 * alt_meters)
      camera.exif_tags['GPS.GPSAltitudeRef'] = '0'
      camera.exif_tags['GPS.GPSSpeed'] = '%d/1000' % int(1000 * speed_mps)
      camera.exif_tags['GPS.GPSSpeedRef'] = 'M'
      camera.exif_tags['GPS.GPSSatellites'] = str(sats)
      camera.exif_tags['GPS.GPSTimeStamp'] = '%s/1,%s/1,%s/1' % (utc.strftime('%H'),utc.strftime('%M'),utc.strftime('%S'))
      camera.exif_tags['GPS.GPSDateStamp'] = utc.strftime('%Y:%m:%d')

      camera.capture(fullpath + '/' + picname )

  return (cur_loc,ndx)

def main():
  parser = argparse.ArgumentParser()

  parser.add_argument('-q', dest='quiet', action='store_true', help="Don't write to stdout")
  parser.add_argument('-p', dest='path', type=str, default='/usr/local/gpsdata', help="Absolute path to the desination folder")
  parser.add_argument('-t', dest='poll', type=int, default=10, help="Time in seconds to wait before calling the main worker loop")
  parser.add_argument('-d', dest='dist', type=int, default=100, help="Distance in feet to travel before writing a new data point")

  args = parser.parse_args()

  debug = False if args.quiet else True
  path = '/usr/local/gpsdata' if not args.path else args.path
  polling_time = 10 if not args.poll else args.poll
  dtraveled = 100 if not args.dist else args.dist

  # TBD Configure pin 23 - input, pull up
  GPIO.setup(23, GPIO.IN, pull_up_down=GPIO.PUD_UP)

  # Define our callback button to react when a button has been pressed
  GPIO.add_event_detect(23, GPIO.RISING, callback=button_pressed, bouncetime=300)

  # init variables
  start = time.time()
  ndx = 0
  prev_loc = (0,0)
  global trig_received

  # set paths and filenames
  subdir = time.strftime("%y%m%d.%H%M%S")
  fullpath = path + '/' + subdir

  csvfilename = 'gpsdata.' + subdir + '.csv'

  # Create our csv file in a subfolder
  os.mkdir(fullpath)
  with open(fullpath + '/' + csvfilename,'w') as f:
    # write the header to the csv  file 
    f.write("Date,Localtime,latitude,longitude,speed,alt,temp,sats,photo\n")

  # if debug is on, write the header to stdout
  if debug:
    # '\t' = TAB to try and output the data in columns.
    print 'Date\t\tLocaltime\tlatitude\tlongitude\tspeed\talt\ttemp\tsats\tphoto'

  # The main worker loop runs periodically in a non-blocking fashion
  while True:
    try:
      while True:
        if time.time() - start > polling_time or trig_received:
          # Use the same name as the subfolder name as the name of each photo + index
          (prev_loc,ndx) = logGPSdata(fullpath,subdir,csvfilename,ndx,prev_loc,f,dtraveled,debug)
          start = time.time()
          trig_received = False

        time.sleep(0.1)

    # We want to keep trying indefinitely unless we are told to stop
    except Exception as ex:
      template = "An exception of type {0} occurred while polling the GPS. Arguments:\n{1!r}"
      message = template.format(type(ex).__name__, ex.args)
      print message

      time.sleep(1)
      print "Trying GPS again..."
      trig_received = True
      continue

    except:
      GPIO.cleanup()
      print "GPSLogger done.\nExiting."
      sys.exit()

if __name__ == "__main__":
    main()

