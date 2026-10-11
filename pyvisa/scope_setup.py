import pyvisa

rm = pyvisa.ResourceManager()
print(rm.list_resources())

with rm.open_resource("PASTE_YOUR_STRING_HERE") as scope:
    scope 