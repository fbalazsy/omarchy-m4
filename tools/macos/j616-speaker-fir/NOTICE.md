# Notice

The reference pipeline, its native-helper approach (in-memory `AudioUnitRender` of `AudioDSP.component`
units), the impulse normalisation, finite-tail selection, NumPy FFT convolution and the IEEE-float WAV
writer are by **KB2UKA**, <https://github.com/Kb2uka/Omarchy-M4-Native> `audio/j713/owner-fir/` at commit
`b92f048f7f41227e600431926395e3dfe2d7f070`, MIT licence (`LICENSE.txt`, Copyright (c) 2026 KB2UKA).
`upstream-reference/` holds that tool's files unchanged. This J616 variant keeps the MIT headers and
credit in every derived file. NumPy is used as a dependency and not redistributed here.

Apple's tuning files, the AudioDSP component and the generated FIR WAVs are not covered by any licence
grant here; they stay in the local kit. No Apple file is copied into `pipeline/`.
