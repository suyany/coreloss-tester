import pyvisa
from b1_find import print_devices
from b2_idn import connect_device
from a3_errors import parse_error

rm = pyvisa.ResourceManager()

def drain_error_queue(device):
    reply = parse_error(device.query(":SYSTem:ERRor?"))
    print(f"error {reply[0]} {reply[1]}")
    while 0 != reply[0]:
        reply = parse_error(device.query(":SYSTem:ERRor?"))
        print(f"error {reply[0]} {reply[1]}")
    print("Queue empty")

def test_cmd(device, cmd):
    device.write(cmd)
    reply = parse_error(device.query(":SYSTem:ERRor?"))
    print(f"Sent: '{cmd}' -> error {reply[0]} {reply[1]}")
    drain_error_queue(device)

print_devices()

with connect_device() as device:
    device.write("*CLS")
    test_cmd(device, ":FOO:BAR 1")
    test_cmd(device, ":CHANnel1:SCALe banana")
    test_cmd(device, ":CHANnel1:SCALe 1E9")