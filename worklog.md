# Worklog

Append-only. Committed as you go, including dead ends.

---

Week 1 (2026-09-24) · hours: ~4 of 8–12
1. Done: Open Lab visit: scope MSO-X 3024T (8-bit ADC, manual per-channel skew adjustment), probe N2843A, function gen 33522B, DMM 34461A (no LCR). Called the Electronics Services Shop and confirmed free use.
2. Measured: none yet, bench tests not run.
3. Broke / learned: the scope's skew step is 2.5 ps, not the 200 ps first assumed, so it is negligible at all three test frequencies. The real constraint is the 8-bit ADC (~50 dB SNR ceiling vs. ~60 dB needed), so the Lossless-Cap Test needs heavy averaging and multi-cycle sine-fitting from the start.
4. Next: reading pass, vendor survey · bench:desk hours = 0:4

Week 2 (28 Sep – 4 Oct 2026) · hours: 8 of 8–12
1. Done: ~2 h bench: soldered the edge-source perfboard with a 74F14PC [confirm: Ground-Ring/Edge-Speed board]. Shop trip 28 Sep (4.7 nF film + 56 Ω, wrong for 1 MHz). Picked parts from the shop inventory.
2. Measured: mica marked 160 pF reads 158 pF on the LCR-P1 [S].
3. Broke / learned: I mistook the marking for an EIA code ("16 pF") and replanned around it before measuring (CT-20, withdrawn by CT-21), so measure first. Also: noise ∝ 1/(V_B√N), gain mismatch = ε·ωRC, lead L and probe C_p cancel at R* = √(L/C_p); probe R_p and C_p are in parallel (30 Sep); the divider adds R and X_C as vectors (3 Oct).
4. Next: Edge-Speed and Ground-Ring tests, then the skew tests, then the Lossless-Cap Test; add the MC74AC540N to the perfboard next session · bench:desk hours = 2:[6]