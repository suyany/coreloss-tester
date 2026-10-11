def timebase_for(freq_hz, cycles):
    if freq_hz == 0:
        raise ValueError("freq_hz must be greater than 0")
    total_time = cycles/freq_hz
    return total_time/10

def scpi_timebase(freq_hz, cycles):
    scale = timebase_for(freq_hz, cycles)
    return f":TIMebase:SCALe {scale:.6E}"

if __name__ == "__main__":
    print(timebase_for(100000, 20))
    print(scpi_timebase(100000, 20))
    print(timebase_for(1e+06, 20))
    print(scpi_timebase(1e+06, 20))

    """
    timebase_for(100000, 20) = 2e-05
    scpi_timebase(100000, 20) = :TIMebase:SCALe 2.000000E-05
    timebase_for(1e+06, 20) = 2.0000000000000003e-06
    scpi_timebase(1e+06, 20) = :TIMebase:SCALe 2.000000E-06
    """