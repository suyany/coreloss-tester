import numpy as np

def estimate_shift(t, a, b, freq_hz):
    sample_spacing = t[1] - t[0]
    fft_a = np.fft.fft(a)
    fft_index_a = np.abs(np.fft.fftfreq(len(a), sample_spacing) - freq_hz).argmin()
    fft_b = np.fft.fft(b)
    fft_index_b = np.abs(np.fft.fftfreq(len(b), sample_spacing) - freq_hz).argmin()
    a_phase = np.angle(fft_a[fft_index_a])
    b_phase = np.angle(fft_b[fft_index_b])
    phase_diff = (((a_phase - b_phase) + np.pi) % (2*np.pi)) - np.pi
    return phase_diff/(2*np.pi*freq_hz)