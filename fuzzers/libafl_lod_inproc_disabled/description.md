# libafl_lod_inproc_disabled

Byte-mutation baseline (A/B control) for `libafl_lod_inproc`. Identical
in-process build and run, but the LOD grammar mutation stage and entropy
feedback are disabled (`lod-disable`), leaving MOpt havoc + cmplog/I2S. Used to
isolate the contribution of the LOD grammar stage.
