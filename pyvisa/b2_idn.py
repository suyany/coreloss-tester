import pyvisa

def connect_device():
    rm = pyvisa.ResourceManager()
    device_string = input("Enter device string: ")
    device = rm.open_resource(device_string)
    device.timeout = 5000
    try:
        print(f"Connected: {device.query('*IDN?').strip()}")
    except pyvisa.errors.VisaIOError:
        print("[ABORT] No reply within 5 s.")
        print("ABORTED")
    return device