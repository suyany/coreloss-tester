import math

def phase_error_rad(freq_hz, skew_s):
    return (2*math.pi*freq_hz*skew_s)

def skew_for_phase(freq_hz, phase_rad):
    return (phase_rad)/(2*math.pi*freq_hz)

if __name__ == "__main__":
    print(phase_error_rad(1e6, 0.318e-9))
    print(phase_error_rad(1e6, 50e-12))