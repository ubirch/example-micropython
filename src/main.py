print("*** UBIRCH SIM Testkit ***")
print("++ importing:")
print("\tmachine")
import machine
print("\tOS")
import os
print("\ttime")
import time
print("\tconfig")
from config import load_config
print("\tconnection")
from connection import init_connection
print("\terror handling")
from error_handling import *
print("\tmodem")
from modem import get_imsi, _send_at_cmd
print("\tnetwork")
from network import LTE
print("\tubirch_helpers")
from lib.ubirch.ubirch_helpers import *
print("\tubinascii")
from ubinascii import b2a_base64, a2b_base64, hexlify, unhexlify
print("\tpyboard")
# Pycom specifics
from pyboard import init_pyboard, print_data
print("\tUbirchClient")
# ubirch client
from ubirch import UbirchClient

def wake_up():
    set_led(LED_GREEN)
    return time.time()

def reset_modem():
    print("++ not coming from sleep, resetting modem")
    print("\twaiting for reset to finish")
    lte.reset()
    lte.init()
    print("\tsetting function level")
    _send_at_cmd(lte,"AT+CFUN?",debug_print=False)
    _send_at_cmd(lte,"AT+CFUN=1",debug_print=False)
    time.sleep(5)
    _send_at_cmd(lte,"AT+CFUN?",debug_print=False)

#begin of main code

#remember wake-up time
start_time = wake_up()

#set watchdog: if execution hangs/takes longer than reset_after_ms an automatic reset is triggered
print("++ enabling watchdog")
reset_after_ms =  5 * 60 * 1000
wdt = machine.WDT(timeout=reset_after_ms)  # enable it
wdt.feed()# we only feed it once since this code hopefully finishes with deepsleep (=no WDT) before reset_after_ms

#check reset cause
COMING_FROM_DEEPSLEEP = (machine.reset_cause() == machine.DEEPSLEEP_RESET)

# mount SD card if there is one
print("++ trying to mount SD")
try:
    sd = machine.SD()
    os.mount(sd, '/sd')
    SD_CARD_MOUNTED = True
    print("\tOK")
except OSError:
    SD_CARD_MOUNTED = False
    print("\tfailed")

#intialization section
lte = LTE()

#if we are not coming from deepsleep, modem might be in a strange state (errors/poweron) -> reset
if not COMING_FROM_DEEPSLEEP: reset_modem()

print("++ getting IMSI")
imsi = get_imsi(lte)

if not COMING_FROM_DEEPSLEEP:
    #if not in normal loop operation: save imsi to file
    imsi_file = "imsi.txt"
    if SD_CARD_MOUNTED and imsi_file not in os.listdir('/sd'):
        print("\twriting IMSI to SD")
        with open('/sd/' + imsi_file, 'w') as f:
            f.write(imsi)

# load configuration
print("++ trying to load config")
try:
    cfg = load_config(sd_card_mounted=SD_CARD_MOUNTED)
    print("\tOK")
except Exception as e:
    print("\tError")
    set_led(LED_YELLOW)
    print_to_console(e)
    while True:
        machine.idle()

if cfg['debug']: print("\t"+repr(cfg))

# set measurement interval
interval = cfg['interval']

# set up error handling
error_handler = ErrorHandler(file_logging_enabled=cfg['logfile'], sd_card=SD_CARD_MOUNTED)

#check if the RTC has a time set, if not synchronize it
rtc = machine.RTC()
board_time = rtc.now()
print("++ checking board time\n\ttime is: ",board_time)
board_time_year = board_time[0]
connection= None
if board_time_year < 2020: #time can't be correct -> connect to sync time
    print("\ttime invalid, syncing...")
    # connect to network to set time (done implicitly), disconnect afterwards to speed up SIM communication
    try:
        connection = init_connection(lte, cfg)
    except Exception as e:
        error_handler.log(e, LED_PURPLE, reset=True)
    print("\tdisconnecting")
    connection.disconnect()

# initialise ubirch client
print("++ intializing ubirch client")
try:
    ubirch_client = UbirchClient(cfg, lte, imsi)
except Exception as e:
    error_handler.log(e, LED_RED, reset=True)

# initialise the sensors
print("++ intializing sensors")
sensors = init_pyboard(cfg['board'])

# get data from sensors
print("++ getting measurements:")
data = sensors.get_data()
#print_data(data)

#' pack data and create UPP ##

# pack data message containing measurements, device UUID and timestamp to ensure unique hash
print("++ packing data")
message = pack_data_json(ubirch_client.uuid, data)
#print("\tdata message [json]: {}\n".format(message.decode()))

# seal the data message (data message will be hashed and inserted into UPP as payload by SIM card)
print("++ creating UPP")
upp = ubirch_client.sim.message_chained(ubirch_client.key_name, message, hash_before_sign=True)
#print("\tUPP [msgpack]: {} (base64: {})\n".format(hexlify(upp).decode(),
#                                                    b2a_base64(upp).decode().rstrip('\n')))
# retrieve data message hash from generated UPP for verification
#message_hash = get_upp_payload(upp)
#print("\tdata message hash: {}".format(b2a_base64(message_hash).decode()))                                                    

print("++ checking/establishing connection")
#if there was no previous connection, create it
if connection == None:
    try:
        connection = init_connection(lte, cfg)
    except Exception as e:
        error_handler.log(e, LED_PURPLE, reset=True)
# make sure device is still connected or reconnect
if not connection.is_connected() and not connection.connect():
    error_handler.log("!! unable to reconnect to network", LED_PURPLE, reset=True)

# send data to ubirch data service and certificate to ubirch auth service
try:
    # send data message to data service
    print("++ sending data message ...")
    ubirch_client.api.send_data(ubirch_client.uuid, message)

    # send UPP to the ubirch authentication service to be anchored to the blockchain
    print("++ sending UPP ...")
    ubirch_client.api.send_upp(ubirch_client.uuid, upp)
except Exception as e:
    error_handler.log(e, LED_ORANGE)

# prepare hardware for sleep (needed for low current draw and
# freeing of ressources for after the reset, as the modem stays on)
print("++ preparing hardware for sleep")
print("\tclose connection")
connection.disconnect()
print("\tdeinit ubirch client")
ubirch_client.sim.deinit()
# not detaching causes smaller/no re-attach time on next reset but but 
# somewhat higher sleep current needs to be balanced based on your specific interval
print("\tdeinit LTE")
lte.deinit(detach=False)

# go to deepsleep
sleep_time = interval - int(time.time() - start_time)
if sleep_time < 0:
    sleep_time = 0
print(">> going to deepsleep for {} seconds".format(sleep_time))
set_led(0)  # LED off
machine.deepsleep(1000*sleep_time)#sleep, execution will resume from main.py entry point




