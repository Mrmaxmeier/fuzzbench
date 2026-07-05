# FuzzBench benchmark image size reduction

## Problem

Several benchmark builder images are much larger than necessary because OSS-Fuzz
`build.sh` scripts compile **all** fuzz targets and (for OpenSSL) the full test
suite, even though FuzzBench only runs the single target named in
`benchmark.yaml` (`fuzz_target`). The extra binaries and build trees remain in
the Docker image layer produced by `fuzzer_build`.

`sudo docker` is required to inspect the real image store on this machine.

## Plan

1. **Measure** current `gcr.io/fuzzbench/builders/libafl/<benchmark>` image size
   and key in-container directories (`/out`, `/src`, problem paths).
2. **Adjust `benchmarks/*/build.sh`** to honor `$FUZZ_TARGET` (set by
   `fuzzers.utils.initialize_env()`):
   - Build only the requested fuzz target (not `make all` / all OSS-Fuzz targets).
   - Copy only that target's binary, dictionary, and seed corpus to `$OUT`.
   - Remove large unneeded trees left in `$SRC` (e.g. OpenSSL `test/`).
3. **Rebuild** the libafl builder image for each changed benchmark.
4. **Re-measure** and record savings.

## Baseline measurements (before changes)

_Measurements taken 2026-07-05 using existing images on disk._

| Benchmark | Image size | `/out` | `/src` | Notes |
|-----------|----------:|-------:|-------:|-------|
| `openssl_x509` | 12.5 GB | 215 MB | 4.0 GB | `test/` 3.2 GB, `fuzz/` 486 MB (11 binaries) |
| `curl_curl_fuzzer_http` | 10.5 GB | 441 MB | ~2.3 GB | 19 curl fuzz binaries in `/out` |
| `mbedtls_fuzz_dtlsclient` | 11.2 GB | 94 MB | ~3.0 GB | 9 fuzz binaries in `/out`; openssl+boringssl clones |
| `libjpeg-turbo_libjpeg_turbo_fuzzer` | 9.3 GB | 299 MB | 841 MB | Many fuzz binaries in `/out` |

Most of the reported image size is shared toolchain weight (LibAFL, LLVM, Rust).
The changes above target **per-benchmark bloat** in `/src` and `/out` that is
unique to each builder layer.

## After measurements (slimmed + rebuilt)

_Measurements after `./scripts/rebuild-libafl-benchmark.sh` (local layers, no cache)._

| Benchmark | Image (before → after) | `/out` (before → after) | Notes |
|-----------|------------------------|-------------------------|-------|
| `openssl_x509` | 12.5 GB → **6.55 GB** | 215 MB → **20 MB** | `make build_libs` then `make fuzz/x509` |
| `curl_curl_fuzzer_http` | 10.5 GB → **7.72 GB** | 441 MB → **23 MB** | only `curl_fuzzer_http` |
| `mbedtls_fuzz_dtlsclient` | 11.2 GB → **8.83 GB** | 94 MB → **11 MB** | cmake single target |
| `libjpeg-turbo_libjpeg_turbo_fuzzer` | 9.3 GB → **6.23 GB** | 299 MB → **13 MB** | cmake single target |
| `php_php-fuzz-parser_0dbedb` | — | 527 MB → **55 MB** | single fuzzer; bash corpus extract |
| `mbedtls_fuzz_dtlsclient_7c6b0e` | — | empty → **14 MB** | same cmake fix |
| `zlib_zlib_uncompress_fuzzer` | — | 8.4 MB → **8.2 MB** | `make libz.a` vs `make all` |
| `libpcap_fuzz_both` | — | 9.3 MB → **9.9 MB** | dropped extra seed/options copies |
| `libxml2_xml` | — | empty → **14 MB** | only `xml.dict`/`.options` |
| `libxml2_xml_e85b9b` | — | 19 MB → **20 MB** | dropped extra dicts |
| `openthread_ot-ip6-send-fuzzer` | — | empty → **15 MB** | single `ninja` target |

Combined savings for the four largest offenders: **~44.5 GB → ~29.3 GB** (~15 GB less).

## Full benchmark audit (29 targets)

_Last updated 2026-07-05. `/out` contents from `gcr.io/fuzzbench/builders/libafl/<benchmark>`._

| Benchmark | `fuzz_target` | Unwanted? | `/out` status |
|-----------|---------------|-----------|---------------|
| `bloaty_fuzz_target` | `fuzz_target` | No | OK — single binary (42 MB) |
| `bloaty_fuzz_target_52948c` | `fuzz_target` | No | OK — single binary (63 MB) |
| `curl_curl_fuzzer_http` | `curl_fuzzer_http` | **Fixed** | OK — 23 MB |
| `freetype2_ftfuzzer` | `ftfuzzer` | Build-time only | OK — 11 MB (`make all` builds tools) |
| `harfbuzz_hb-shape-fuzzer` | `hb-shape-fuzzer` | No | OK — 60 MB |
| `harfbuzz_hb-shape-fuzzer_17863b` | `hb-shape-fuzzer` | No | OK — 68 MB |
| `jsoncpp_jsoncpp_fuzzer` | `jsoncpp_fuzzer` | No | OK — 9.2 MB |
| `lcms_cms_transform_fuzzer` | `cms_transform_fuzzer` | No | OK — 8.1 MB |
| `libjpeg-turbo_libjpeg_turbo_fuzzer` | `libjpeg_turbo_fuzzer` | **Fixed** | OK — 13 MB |
| `libpcap_fuzz_both` | `fuzz_both` | **Fixed** | OK — 9.9 MB |
| `libpng_libpng_read_fuzzer` | `libpng_read_fuzzer` | Minor | OK — 7.6 MB (extra `png.dict` from Dockerfile) |
| `libxml2_xml` | `xml` | **Fixed** | OK — 14 MB |
| `libxml2_xml_e85b9b` | `xml` | **Fixed** | OK — 20 MB |
| `libxslt_xpath` | `xpath` | **Fixed** | OK — 13 MB (only `xpath`) |
| `mbedtls_fuzz_dtlsclient` | `fuzz_dtlsclient` | **Fixed** | OK — 11 MB |
| `mbedtls_fuzz_dtlsclient_7c6b0e` | `fuzz_dtlsclient` | **Fixed** | OK — 14 MB |
| `mruby_mruby_fuzzer_8c8bbd` | `mruby_fuzzer` | No | No libafl image on registry |
| `openh264_decoder_fuzzer` | `decoder_fuzzer` | No | OK — 9.3 MB (was stale empty image) |
| `openssl_x509` | `x509` | **Fixed** | OK — 20 MB |
| `openthread_ot-ip6-send-fuzzer` | `ot-ip6-send-fuzzer` | **Fixed** | OK — 15 MB |
| `php_php-fuzz-parser_0dbedb` | `php-fuzz-parser` | **Fixed** | OK — 55 MB (only `php-fuzz-parser`) |
| `proj4_proj_crs_to_crs_fuzzer` | `proj_crs_to_crs_fuzzer` | No (grid data) | OK — 70 MB (143 runtime data files) |
| `re2_fuzzer` | `fuzzer` | No | OK — 12 MB (was stale dict-only image) |
| `sqlite3_ossfuzz` | `ossfuzz` | No | OK — 17 MB |
| `stb_stbi_read_fuzzer` | `stbi_read_fuzzer` | **Fixed** | OK — 13 MB (only `stbi_read_fuzzer`) |
| `systemd_fuzz-link-parser` | `fuzz-link-parser` | **Fixed** | OK — 20 MB (single `fuzz-link-parser`) |
| `vorbis_decode_fuzzer` | `decode_fuzzer` | No | OK — 7.5 MB |
| `woff2_convert_woff2ttf_fuzzer` | `convert_woff2ttf_fuzzer` | No | OK — 13 MB (was stale seeds-only image) |
| `zlib_zlib_uncompress_fuzzer` | `zlib_uncompress_fuzzer` | Build-time only | OK — 8.2 MB |

### Summary

- **11 benchmarks** had unwanted output artifacts; fixes are written for all of them.
- **15 benchmarks** verified OK after slimming rebuild (including php).
- **proj4** grid files in `/out` are intentional runtime data, not extra fuzz targets.

## Broken libafl builds — investigation

Manual `build.sh` runs inside the existing libafl images (with libafl CC/CXX and
`/usr/lib/libFuzzingEngine.a` from `stub_rt.a`) show:

| Benchmark | Root cause | Fix |
|-----------|------------|-----|
| `re2_fuzzer` | Image from Jan 2023; `fuzzer_build` never completed | Rebuild — `build.sh` works as-is |
| `openh264_decoder_fuzzer` | Same — stale empty `/out` | Rebuild — `build.sh` works as-is |
| `woff2_convert_woff2ttf_fuzzer` | Seeds copied in Dockerfile; binary never linked | Rebuild — `build.sh` works as-is |
| `systemd_fuzz-link-parser` | (1) Image had old `oss-fuzz.sh` as `build.sh`; (2) meson needs `-lFuzzingEngine` (provided by `utils.build_benchmark()`); (3) sed patches broke corpus loop | Rewrote `build.sh` standalone; **rebuilt OK** |

re2, openh264, and woff2 needed only image rebuild — `build.sh` was already correct.

## Build script changes

| Benchmark | Change |
|-----------|--------|
| `openssl_x509` | `make build_libs` then `make fuzz/$FUZZ_TARGET`; prune `test/` |
| `curl_curl_fuzzer_http` | Patch `fuzz_targets` + `compile_fuzzer.sh` for single target |
| `mbedtls_fuzz_dtlsclient` (+ `_7c6b0e`) | `cmake --build --target $FUZZ_TARGET` |
| `libjpeg-turbo_libjpeg_turbo_fuzzer` | cmake single target; explicit `cp` to `$OUT` |
| `stb_stbi_read_fuzzer` | Inline compile of `stbi_read_fuzzer` only |
| `libxslt_xpath` | Link/copy loop over `$FUZZ_TARGET` only |
| `systemd_fuzz-link-parser` | Standalone build; `ninja $FUZZ_TARGET`; Dockerfile `COPY build.sh` |
| `openthread_ot-ip6-send-fuzzer` | Standalone build; pinned commit; `ninja $TARGET` |
| `php_php-fuzz-parser_0dbedb` | Copy only `php-fuzz-parser`; perl corpus from `.phpt` (avoids UBSan in PHP CLI) |
| `zlib_zlib_uncompress_fuzzer` | `make libz.a` instead of `make all` |
| `libpcap_fuzz_both` | Drop `fuzz_filter`/`fuzz_pcap` seed and options copies |
| `libxml2_xml` / `libxml2_xml_e85b9b` | Copy only `${FUZZ_TARGET}.dict` and `.options` |

## Build dependency notes

| Benchmark | Approach | Why |
|-----------|----------|-----|
| **openssl** | `make build_libs` then `make fuzz/$TARGET` | Combined `make -j` races the link ahead of libcrypto/libssl |
| **curl** | ossfuzz dep scripts, then `make $TARGET` | Only fuzz link step is narrowed |
| **mbedtls** / **libjpeg** | `cmake --build --target $TARGET` | CMake orders library deps |
| **systemd** | meson + `ninja $TARGET` | `FUZZING_ENGINE=1` selects oss-fuzz meson path |
| **php** | `make` single fuzzer; perl corpus extract | `sapi/cli/php` trips UBSan during ini parse under libafl sanitizers |

## Rebuild commands

Plain `make .libafl-<benchmark>-builder` can pull a stale remote intermediate or
reuse a cached `fuzzer_build` layer. After changing `benchmarks/*/build.sh`, use
`./scripts/rebuild-libafl-benchmark.sh` (project-builder uses `--no-cache` to avoid
podman `layer not known` errors):

```bash
cd fuzzbench
# One benchmark:
./scripts/rebuild-libafl-benchmark.sh openssl_x509

# Several benchmarks in parallel:
./scripts/rebuild-libafl-benchmarks.sh stb_stbi_read_fuzzer libxslt_xpath \
  php_php-fuzz-parser_0dbedb systemd_fuzz-link-parser

# Broken images (no build.sh changes needed):
./scripts/rebuild-libafl-benchmarks.sh re2_fuzzer openh264_decoder_fuzzer \
  woff2_convert_woff2ttf_fuzzer

# All slimmed targets:
./scripts/rebuild-libafl-benchmarks.sh all
```

Logs for parallel runs: `$TMPDIR/fuzzbench-rebuild-logs/<benchmark>.log`
