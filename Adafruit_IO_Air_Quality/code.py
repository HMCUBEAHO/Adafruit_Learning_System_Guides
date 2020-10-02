import time
import board
import busio
from digitalio import DigitalInOut, Direction, Pull
import adafruit_dotstar as dotstar
from adafruit_esp32spi import adafruit_esp32spi
from adafruit_esp32spi import adafruit_esp32spi_wifimanager
import adafruit_esp32spi.adafruit_esp32spi_socket as socket
import adafruit_minimqtt.adafruit_minimqtt as MQTT
from adafruit_io.adafruit_io import IO_MQTT
from adafruit_io.adafruit_io_errors import AdafruitIO_MQTTError
from simpleio import map_range

import adafruit_pm25
import adafruit_bme280


### WiFi ###

# Get wifi details and more from a secrets.py file
try:
    from secrets import secrets
except ImportError:
    print("WiFi secrets are kept in secrets.py, please add them there!")
    raise

# If you have an externally connected ESP32:
esp32_cs = DigitalInOut(board.D13)
esp32_reset = DigitalInOut(board.D12)
esp32_ready = DigitalInOut(board.D11)

spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
status_light = dotstar.DotStar(board.APA102_SCK, board.APA102_MOSI, 1, brightness=0.2)
wifi = adafruit_esp32spi_wifimanager.ESPSPI_WiFiManager(esp, secrets, status_light)


# Connect to a PM2.5 sensor over UART
uart = busio.UART(board.TX, board.RX, baudrate=9600)
pm25 = adafruit_pm25.PM25_UART(uart)

# Connect to a BME280 sensor over I2C
i2c = busio.I2C(board.SCL, board.SDA)
bme280 = adafruit_bme280.Adafruit_BME280_I2C(i2c)

### MiniMQTT Callback Functions ###
def connected(client):
    # Connected function will be called when the client is connected to Adafruit IO.
    # This is a good place to subscribe to feed changes.  The client parameter
    # passed to this function is the Adafruit IO MQTT client so you can make
    # calls against it easily.
    print("Connected to Adafruit IO!")

# pylint: disable=unused-argument
def disconnected(client):
    # Disconnected function will be called when the client disconnects.
    print("Disconnected from Adafruit IO!")

def message(client, topic, message):
    pass

def on_new_time(client, topic, message):
    """Obtains new time from Adafruit IO time service
    and parses the current_hour/current_minute
    """
    global current_hour, current_minute
    current_time = message.split("-")[2].split("T")[1]
    current_time = current_time.split(".")[0]
    current_hour = int(current_time.split(":")[0])
    current_minute = int(current_time.split(":")[1])



### Sensor Functions ###
def calculate_aqi(pm_sensor_reading):
    """Returns a calculated air quality index (AQI)
    and category as a tuple.
    NOTE: The AQI returned by this function should ideally be measured
    using the 24-hour concentration average. Calculating a AQI without
    averaging will result in higher AQI values than expected.
    :param float pm_sensor_reading: Particulate matter sensor value.

    """
    # Check sensor reading using EPA breakpoint (Clow-Chigh)
    if 0.0 <= pm_sensor_reading <= 12.0:
        # AQI calculation using EPA breakpoints (Ilow-IHigh)
        aqi = map_range(int(pm_sensor_reading), 0, 12, 0, 50)
        aqi_category = "Good"
    elif 12.1 <= pm_sensor_reading <= 35.4:
        aqi = map_range(int(pm_sensor_reading), 12, 35, 51, 100)
        aqi_category = "Moderate"
    elif 35.5 <= pm_sensor_reading <= 55.4:
        aqi = map_range(int(pm_sensor_reading), 36, 55, 101, 150)
        aqi_category = "Unhealthy for Sensitive Groups"
    elif 55.5 <= pm_sensor_reading <= 150.4:
        aqi = map_range(int(pm_sensor_reading), 56, 150, 151, 200)
        aqi_category = "Unhealthy"
    elif 150.5 <= pm_sensor_reading <= 250.4:
        aqi = map_range(int(pm_sensor_reading), 151, 250, 201, 300)
        aqi_category = "Very Unhealthy"
    elif 250.5 <= pm_sensor_reading <= 350.4:
        aqi = map_range(int(pm_sensor_reading), 251, 350, 301, 400)
        aqi_category = "Hazardous"
    elif 350.5 <= pm_sensor_reading <= 500.4:
        aqi = map_range(int(pm_sensor_reading), 351, 500, 401, 500)
        aqi_category = "Hazardous"
    else:
        print("Invalid PM2.5 concentration")
        aqi = -1
        aqi_category = None
    return aqi, aqi_category

def sample_aq_sensor():
    """Samples PM2.5 sensor
    over a 2.3 second sample rate.

    """
    aq_reading = 0
    aq_samples = []

    # initial timestamp
    time_start = time.monotonic()
    # sample pm2.5 sensor over 2.3 sec sample rate
    while (time.monotonic() - time_start <= 2.3):
        try:
            aqdata = pm25.read()
            aq_samples.append(aqdata["particles 25um"])
        except RuntimeError:
            print("Unable to read from sensor, retrying...")
            continue
        # pm sensor output rate of 1s
        time.sleep(1)
    # average sample reading / # samples
    for sample in range(len(aq_samples)):
        aq_reading += aq_samples[sample]
    aq_reading = aq_reading / len(aq_samples)
    aq_samples.clear()
    return aq_reading

def read_bme280(is_celsius=False):
    """Returns temperature and humidity
    from BME280 environmental sensor, as a tuple.

    :param bool is_celsius: Returns temperature in degrees celsius
                            if True, otherwise fahrenheit.
    """
    humidity = bme280.humidity
    temperature = bme280.temperature
    if not is_celsius:
        temperature = temperature * 1.8 + 32
    return temperature, humidity


### CODE ###

# Connect to WiFi
print("Connecting to WiFi...")
wifi.connect()
print("Connected!")

# Initialize MQTT interface with the esp interface
MQTT.set_socket(socket, esp)

# Initialize a new MQTT Client object
mqtt_client = MQTT.MQTT(
    broker="io.adafruit.com", username=secrets["aio_user"], password=secrets["aio_key"],
)

# Initialize an Adafruit IO MQTT Client
io = IO_MQTT(mqtt_client)

# Connect the callback methods defined above to Adafruit IO
io.on_connect = connected
io.on_disconnect = disconnected
io.on_message = message

# Connect to Adafruit IO
print("Connecting to Adafruit IO...")
io.connect()

# Subscribe to the air quality sensor group
group_air_quality = "air-quality-sensor"
io.subscribe(group_key=group_air_quality)

# Feeds within the air quality sensor group
# Temperature
feed_temp = group_air_quality + ".temperature"
# Humidity
feed_humid = group_air_quality + ".humidity"
# Air quality index (AQI)
feed_aqi = group_air_quality + ".aqi"
# Air quality index category
feed_aqi_category = group_air_quality + ".category"

# Set up location metadata
# TODO: Use secrets.py
location_metadata = "40.726190, -74.005334, -6"

# Call on_new_time to update the time from Adafruit IO
io._client.add_topic_callback("time/ISO-8601", on_new_time)
# Subscribe to the Adafruit IO  UTC time service
io.subscribe_to_time("ISO-8601")

initial_time = time.monotonic()

current_hour = 0
current_minute = 0
prv_hour = 0
prv_minute = 0
time_elapsed = 0

average_aqi = 0

while True:
  try:
    # Keep device connected to io.adafruit.com
    # and process any incoming data.
    io.loop()
  except (ValueError, RuntimeError, AdafruitIO_MQTTError) as e:
      print("Failed to get data, retrying\n", e)
      wifi.reset()
      wifi.connect()
      io.reconnect()
      continue

  # Check current time against io.adafruit time service
  if (current_hour - prv_hour >= 1):
      print("new hour!")
      # Reset prv_hour
      prv_hour = current_hour
      io.publish(feed_temp, current_minute)
      time_elapsed = True
  elif (current_minute - prv_minute >= 1):
      print("new 1m increment!")
      print(current_minute)
      # Reset prv_minute
      prv_minute = int(current_minute)
      io.publish(feed_temp, current_minute)
      time_elapsed = True
  elif current_minute % 10 == 0:
      print("new 10m increment!")
      # Reset prv_minute
      prv_minute = int(current_minute)
      io.publish(feed_temp, current_minute)
      time_elapsed = True
  else:
      # no difference in current time
      continue

  if time_elapsed:
    aqi_reading = sample_aq_sensor()
    aqi, aqi_category = calculate_aqi(aqi_reading)
    # Average AQI readings over amount of readings
    if current_minute > 0:
      average_aqi += aqi
      average_aqi /= current_minute

    print("AQI: %d"%aqi)
    print("Category: %s"%aqi_category)

    # temp and humidity
    temperature, humidity = read_bme280()
    print("Temperature: %0.1f F" % temperature)
    print("Humidity: %0.1f %%" % humidity)

    # Publish to IO
    print("Publishing to Adafruit IO...")
    # TODO: These need to be within a try/except block
    io.publish(feed_aqi, int(aqi), location_metadata)
    io.publish(feed_aqi_category, aqi_category)
    io.publish(feed_humid, humidity)
    io.publish(feed_temp, temperature)
    print("Published!")
    # Reset time_elapsed
    time_elapsed = False

  time.sleep(60)
