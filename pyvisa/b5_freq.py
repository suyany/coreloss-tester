import pyvisa
from b1_find import print_devices
from b2_idn import connect_device

rm = pyvisa.ResourceManager()

print_devices()
gen_hz = float(input("Gen_HZ: "))
samples = []

with connect_device() as device:
    for i in range(10):
        data = float(device.query(":MEASure:FREQuency? CHANnel1").strip())
        if (data > 1E37):
            print(f"Reading {i + 1}/10: NO SIGNAL")
        else:
            samples.append(data)
            print(f"Reading {i + 1}/10: {data}Hz")
    if len(samples) > 0:
        mean = sum(samples)/len(samples)
        print(f"Mean = {mean}Hz, Min = {min(samples)}Hz, Max = {max(samples)}Hz (n = {len(samples)})")
        print(f"Difference from generator: {((100*(mean - gen_hz))/gen_hz):+.3f}%")
    else:
        print("NO VALID DATA")