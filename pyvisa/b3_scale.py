import pyvisa

rm = pyvisa.ResourceManager()

def adjust_scale(device, requested_V_per_div):
    og_scale = device.query(":CHANnel1:SCALe?").strip()
    device.write(f":CHANnel1:SCALe {requested_V_per_div}")
    print(f"Requested_V_per_div: {requested_V_per_div}")
    new_scale = device.query(":CHANnel1:SCALe?").strip()
    print(f"Actual_V_per_div: {new_scale}")
    if (float(new_scale) != float(og_scale)):
        print("Match: NO")
        device.write(f":CHANnel1:SCALe {og_scale}")
        print(f"Restored CH1 scale to {og_scale} V/div. DONE")
    else:
        print(f"Match: YES")