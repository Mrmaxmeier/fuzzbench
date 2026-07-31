---
layout: default
title: Benchmarks
nav_order: 5
permalink: /reference/benchmarks/
parent: Reference
---

# Benchmarks

This page lists each benchmark currently in this repository. Names match the
directories under `benchmarks/`.

| Benchmark | Fuzz target | Type |
|:----------|:------------|:-----|
| bloaty_fuzz_target | fuzz_target | code |
| bloaty_fuzz_target_52948c | fuzz_target | bug |
| curl_curl_fuzzer_http | curl_fuzzer_http | code |
| freetype2_ftfuzzer | ftfuzzer | code |
| harfbuzz_hb-shape-fuzzer | hb-shape-fuzzer | code |
| harfbuzz_hb-shape-fuzzer_17863b | hb-shape-fuzzer | bug |
| jsoncpp_jsoncpp_fuzzer | jsoncpp_fuzzer | code |
| lcms_cms_transform_fuzzer | cms_transform_fuzzer | code |
| libjpeg-turbo_libjpeg_turbo_fuzzer | libjpeg_turbo_fuzzer | code |
| libpcap_fuzz_both | fuzz_both | code |
| libpng_libpng_read_fuzzer | libpng_read_fuzzer | code |
| libxml2_xml | xml | code |
| libxml2_xml_e85b9b | xml | bug |
| libxslt_xpath | xpath | code |
| mbedtls_fuzz_dtlsclient | fuzz_dtlsclient | code |
| mbedtls_fuzz_dtlsclient_7c6b0e | fuzz_dtlsclient | bug |
| mruby_mruby_fuzzer_8c8bbd | mruby_fuzzer | bug |
| openh264_decoder_fuzzer | decoder_fuzzer | code |
| openssl_x509 | x509 | code |
| openthread_ot-ip6-send-fuzzer | ot-ip6-send-fuzzer | code |
| php_php-fuzz-parser_0dbedb | php-fuzz-parser | bug |
| proj4_proj_crs_to_crs_fuzzer | proj_crs_to_crs_fuzzer | code |
| re2_fuzzer | fuzzer | code |
| sqlite3_ossfuzz | ossfuzz | code |
| stb_stbi_read_fuzzer | stbi_read_fuzzer | code |
| systemd_fuzz-link-parser | fuzz-link-parser | code |
| vorbis_decode_fuzzer | decode_fuzzer | code |
| woff2_convert_woff2ttf_fuzzer | convert_woff2ttf_fuzzer | code |
| zlib_zlib_uncompress_fuzzer | zlib_uncompress_fuzzer | code |

`code` benchmarks measure edge coverage; `bug` benchmarks measure bug coverage
from known-buggy commits.
