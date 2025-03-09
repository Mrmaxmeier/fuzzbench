set -e
set +x

mkdir /templates
cp /formatfuzzer/templates/png.bt /templates/libpng_libpng_read_fuzzer.bt
cp /formatfuzzer/templates/jpg.bt /templates/libjpeg-turbo_libjpeg_turbo_fuzzer.bt
cp /formatfuzzer/templates/ogg-orig.bt /templates/vorbis_decode_fuzzer.bt

# TODO
# cp /formatfuzzer/templates/pcap.bt /templates/libpcap_fuzz_both.bt

for f in /templates/*.bt; do
    target=$(basename $f .bt)
    echo $target;
    python3 ./ffcompile /templates/$target.bt /templates/$target.cpp;
    # clang++ -c -I . -std=c++17 -g -O3 -Wall -Wno-parentheses-equality /templates/$target.cpp -o /templates/$target.o;
    clang++ -I . -std=c++17 -g -O3 -Wall -Wno-parentheses-equality -shared -fPIC /templates/$target.cpp fuzzer.cpp -o /templates/$target.so -lz
done
