import pyvisa

def print_devices():
    rm = pyvisa.ResourceManager()
    resources = rm.list_resources()
    list_size = len(resources)
    if list_size == 0:
        print("Found 0 instruments. Check: scope on, USB cable in the rear USB DEVICE port.")
    else:
        print(f"Found {list_size} instrument(s):")
        for i in resources:
            print(i)