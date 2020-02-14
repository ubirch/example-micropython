import logging
import machine
import os
import sys
import time
import ubinascii
from uuid import UUID

# Pycom specifics
import pycom
from pyboard import Pysense, Pytrack

# Ubirch client
from ubirch import UbirchClient
from config import get_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

rtc = machine.RTC()

logfile_name = 'log.txt'
MAX_FILE_SIZE = 20000  # in bytes


def log_to_file(error: str or Exception):
    global file_position
    with open(logfile_name, 'a') as f:
        # start overwriting oldest logs once file reached its max size
        # issue: once file reached its max size, file position will always be set to beginning after device reset
        if file_position > MAX_FILE_SIZE:
            file_position = 0
        # set file to recent position
        f.seek(file_position, 0)

        # log error message and traceback if error is an exception
        t = rtc.now()
        f.write('({:04d}.{:02d}.{:02d} {:02d}:{:02d}:{:02d}) '.format(t[0], t[1], t[2], t[3], t[4], t[5]))
        if isinstance(error, Exception):
            sys.print_exception(error, f)
        else:
            f.write(error + "\n")

        # remember current file position
        file_position = f.tell()


def report_and_reset(message: str, logfile: bool):
    pycom.heartbeat(False)
    pycom.rgbled(0x440044)  # LED purple
    print(message)
    if logfile: log_to_file(message)
    time.sleep(3)
    machine.reset()


def pretty_print_data(data: dict):
    print("{")
    for key in sorted(data):
        print("  \"{}\": {},".format(key, data[key]))
    print("}\n")


class Main:
    """
    |  UBIRCH example for pycom modules.
    |
    |  The devices creates a unique UUID and sends data to the ubirch data and auth services.
    |  At the initial start these steps are required:
    |
    |  - start the pycom module with this code
    |  - take note of the UUID printed on the serial console
    |  - register your device at the Ubirch Web UI
    |
    """

    def __init__(self) -> None:

        # generate UUID
        self.uuid = UUID(b'UBIR' + 2 * machine.unique_id())
        print("\n** UUID   : " + str(self.uuid))
        print("** MAC    : " + ubinascii.hexlify(machine.unique_id(), ':').decode() + "\n")

        # load configuration from file (raises exception if file can't be found)
        self.cfg = get_config()

        if self.cfg['debug']:
            logging.basicConfig(level=logging.DEBUG)

        if self.cfg['logfile']:
            # set up error logging to log file
            with open(logfile_name, 'a') as f:
                file_position = f.tell()
            print("log file size ({}): {:.1f}kb".format(logfile_name, file_position / 1000.0))
            print("free flash memory: {:d}kb\n".format(os.getfree('/flash')))

        # connect to network
        if self.cfg["connection"] == "wifi":
            import wifi
            from network import WLAN
            self.wlan = WLAN(mode=WLAN.STA)
            if not wifi.connect(self.wlan, self.cfg['networks']):
                report_and_reset("ERROR: unable to connect to network. Resetting device...", self.cfg['logfile'])
            if not wifi.set_time():
                report_and_reset("ERROR: unable to set time. Resetting device...", self.cfg['logfile'])
        elif self.cfg["connection"] == "nbiot":
            import nb_iot
            from network import LTE
            self.lte = LTE()
            if not nb_iot.attach(self.lte, self.cfg["apn"]):
                report_and_reset("ERROR: unable to attach to network. Resetting device...", self.cfg['logfile'])
            if not nb_iot.connect(self.lte):
                report_and_reset("ERROR: unable to connect to network. Resetting device...", self.cfg['logfile'])
            if not nb_iot.set_time():
                report_and_reset("ERROR: unable to set time. Resetting device...", self.cfg['logfile'])

        # initialize the sensor based on the type of the Pycom expansion board
        if self.cfg["type"] == "pysense":
            self.sensor = Pysense()
        elif self.cfg["type"] == "pytrack":
            self.sensor = Pytrack()
        else:
            raise Exception("Expansion board type not supported. This version supports types 'pysense' and 'pytrack'")

        # ubirch client for setting up ubirch protocol, authentication and data service
        self.ubirch_client = UbirchClient(self.uuid, self.cfg)

    def prepare_data(self) -> dict:
        """
        Prepare the data from the sensor module and return it in the format we need.
        :return: a dictionary (json) with the data
        """

        data = {
            "V": self.sensor.voltage()
        }

        if isinstance(self.sensor, Pysense) or isinstance(self.sensor, Pytrack):
            accel = self.sensor.accelerometer.acceleration()
            roll = self.sensor.accelerometer.roll()
            pitch = self.sensor.accelerometer.pitch()

            data.update({
                "AccX": accel[0],
                "AccY": accel[1],
                "AccZ": accel[2],
                "AccRoll": roll,
                "AccPitch": pitch
            })

        if isinstance(self.sensor, Pysense):
            data.update({
                "T": self.sensor.barometer.temperature(),
                "P": self.sensor.barometer.pressure(),
                # "Alt": self.sensor.altimeter.altitude(),
                "H": self.sensor.humidity.humidity(),
                "L_blue": self.sensor.light()[0],
                "L_red": self.sensor.light()[1]
            })

        if isinstance(self.sensor, Pytrack):
            data.update({
                "GPS_long": self.sensor.location.coordinates()[0],
                "GPS_lat": self.sensor.location.coordinates()[1]
            })

        return data

    def loop(self):
        # disable blue heartbeat blink
        pycom.heartbeat(False)
        print("Starting loop. Measure interval: {} seconds".format(self.cfg["interval"]))
        while True:
            start_time = time.time()
            pycom.rgbled(0x002200)  # LED green

            # get data
            print("** getting measurements:")
            data = self.prepare_data()
            pretty_print_data(data)

            # make sure device is still connected
            if self.cfg["connection"] == "wifi" and not self.wlan.isconnected():
                import wifi
                pycom.rgbled(0x440044)  # LED purple
                print("!! lost wifi connection, trying to reconnect ...")
                if self.cfg['logfile']: log_to_file("!! lost wifi connection, trying to reconnect ...")
                if not wifi.connect(self.wlan, self.cfg['networks']):
                    report_and_reset("ERROR: unable to connect to network. Resetting device...", self.cfg['logfile'])
                else:
                    pycom.rgbled(0x002200)  # LED green
            elif self.cfg["connection"] == "nbiot" and not self.lte.isconnected():
                import nb_iot
                pycom.rgbled(0x440044)  # LED purple
                print("!! lost NB-IoT connection, trying to reconnect ...")
                if self.cfg['logfile']: log_to_file("!! lost NB-IoT connection, trying to reconnect ...")
                if not nb_iot.connect(self.lte):
                    report_and_reset("ERROR: unable to connect to network. Resetting device...", self.cfg['logfile'])
                else:
                    pycom.rgbled(0x002200)  # LED green

            # send data to ubirch data service and certificate to ubirch auth service
            try:
                self.ubirch_client.send(data)
            except Exception as e:
                pycom.rgbled(0x440000)  # LED red
                sys.print_exception(e)
                if self.cfg['logfile']: log_to_file(e)
                time.sleep(3)

            print("** done.\n")
            passed_time = time.time() - start_time
            if self.cfg['interval'] > passed_time:
                pycom.rgbled(0)  # LED off
                time.sleep(self.cfg['interval'] - passed_time)


main = Main()
main.loop()
