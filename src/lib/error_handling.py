import machine
import os
import pycom
import sys
import time

LED_GREEN = 0x000200
LED_YELLOW = 0x020200
LED_ORANGE = 0x040200
LED_RED = 0x020000
LED_PURPLE = 0x020002


def set_led(led_color):
    pycom.heartbeat(False)  # disable blue heartbeat blink
    pycom.rgbled(led_color)


def print_to_console(error: str or Exception):
    if isinstance(error, Exception):
        sys.print_exception(error)
    else:
        print(error)


class ErrorHandler:

    def __init__(self, file_logging_enabled: bool = False, sd_card: bool = False):
        self.logfile = None
        if file_logging_enabled:
            self.logfile = FileLogger(sd_card)

    def log(self, error: str or Exception, led_color: int, reset: bool = False):
        set_led(led_color)
        print_to_console(error)
        if self.logfile is not None:
            self.logfile.log(error)
        machine.idle()
        time.sleep(3)
        if reset:
            print(">> Resetting device...")
            time.sleep(1)
            machine.reset()


class FileLogger:

    def __init__(self, max_file_size_bytes: int = 20000, sd_card_mounted: bool = False):
        # set up error logging to log file
        self.MAX_FILE_SIZE = max_file_size_bytes  # in bytes
        self.rtc = machine.RTC()
        self.logfile = ('/sd/' if sd_card_mounted else '') + 'log.txt'
        with open(self.logfile, 'a') as f:
            self.file_position = f.tell()
        print("++ file logging enabled")
        print("\tfile: \"{}\"".format(self.logfile))
        print("\tcurrent size:     {: 6.2f} KB".format(self.file_position / 1000.0))
        print("\tmaximal size:     {: 6.2f} KB".format(self.MAX_FILE_SIZE / 1000.0))
        print("\tfree flash memory:{: 6d} KB".format(os.getfree('/flash')))
        if sd_card_mounted:
            print("\tfree SD memory:   {: 6d} MB".format(int(os.getfree('/sd') / 1000)))
        print("")

    def log(self, error: str or Exception):
        with open(self.logfile, 'a') as f:
            # start overwriting oldest logs once file reached its max size
            # known issue:
            #  once file reached its max size, file position will always be set to beginning after device reset
            if self.file_position > self.MAX_FILE_SIZE:
                self.file_position = 0
            # set file to recent position
            f.seek(self.file_position, 0)

            # log error message and traceback if error is an exception
            t = self.rtc.now()
            f.write('({:04d}.{:02d}.{:02d} {:02d}:{:02d}:{:02d}) '.format(t[0], t[1], t[2], t[3], t[4], t[5]))
            if isinstance(error, Exception):
                sys.print_exception(error, f)
            else:
                f.write(error + "\n")

            # remember current file position
            self.file_position = f.tell()
