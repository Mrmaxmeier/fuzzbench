use std::{
    borrow::Cow,
    cell::Cell,
    ffi::{CStr, CString},
};

use libloading::{Library, Symbol};

/*
(venv) ⋊> ~/_/_/FormatFuzzer on master ⨯  rg -F "extern \"C\""                                                                                                                                               14:06:50
fuzzer.cpp
198:extern "C" size_t ff_generate(unsigned char* data, size_t size, unsigned char** new_data);
199:extern "C" int ff_parse(unsigned char* data, size_t size, unsigned char** new_data, size_t* new_size);
1226:extern "C" int process_file(const char *file_name, const char *rand_name) {
1300:extern "C" void generate_random_file(unsigned char** file, unsigned* file_size) {
1313:extern "C" int one_smart_mutation(int target_file_index, unsigned char** file, unsigned* file_size) {
*/

#[derive(Debug, Clone)]
pub struct FileHandle {
    id: usize,
    pub validity: f32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct DecisionSeed<'a>(pub Cow<'a, [u8]>);
#[derive(Debug, Clone, PartialEq)]
pub struct InputData<'a>(pub Cow<'a, [u8]>);

#[expect(non_camel_case_types)]
type cstr = *const i8;
#[expect(non_camel_case_types)]
type cbytes = *const u8;
pub struct FormatFuzzer<'a> {
    ff_generate: Symbol<'a, unsafe extern "C" fn(cbytes, usize, *mut cbytes) -> usize>,
    ff_parse: Symbol<'a, unsafe extern "C" fn(cbytes, usize, *mut cbytes, *mut usize) -> i32>,
    process_file: Symbol<'a, unsafe extern "C" fn(cstr, cstr) -> i32>,
    generate_random_file: Symbol<'a, unsafe extern "C" fn(*mut cbytes, *mut u32) -> ()>,
    one_smart_mutation: Symbol<'a, unsafe extern "C" fn(i32, *mut cbytes, *mut u32) -> i32>,
    mutation_info: Symbol<'a, cstr>,
    // lib: &'a Library,
    file_ctr: Cell<usize>,
    tmpdir: tempfile::TempDir,
}

impl<'a> FormatFuzzer<'a> {
    pub fn from(lib: &'a Library) -> Result<Self, libloading::Error> {
        unsafe {
            Ok(Self {
                ff_generate: lib.get(b"ff_generate\0")?,
                ff_parse: lib.get(b"ff_parse\0")?,
                process_file: lib.get(b"process_file\0")?,
                generate_random_file: lib.get(b"generate_random_file\0")?,
                one_smart_mutation: lib.get(b"one_smart_mutation\0")?,
                mutation_info: lib.get(b"mutation_info\0")?,
                file_ctr: Cell::new(0),
                tmpdir: tempfile::tempdir().unwrap(),
                // lib,
            })
        }
    }

    pub fn get_mutation_info(&self) -> String {
        unsafe { CStr::from_ptr(*self.mutation_info) }
            .to_string_lossy()
            .trim_end()
            .to_string()
    }

    pub fn generate(&self, data: DecisionSeed) -> InputData<'static> {
        tracy_full::zone!("FormatFuzzer::generate");
        unsafe {
            let mut new_data = std::ptr::null();
            let new_size =
                (self.ff_generate)(data.0.as_ptr(), data.0.len(), &mut new_data as *mut _);
            if new_size == 0 {
                return InputData(b"".into());
            }
            // SAFETY: The resulting slice will probably be invalidated by the next call?
            let slice = std::slice::from_raw_parts(new_data, new_size);
            InputData(slice.to_owned().into())
        }
    }

    pub fn parse(&self, data: InputData) -> Result<DecisionSeed<'static>, DecisionSeed<'static>> {
        tracy_full::zone!("FormatFuzzer::parse");
        unsafe {
            let mut new_data = std::ptr::null();
            let mut new_size = 0;
            let res = (self.ff_parse)(
                data.0.as_ptr(),
                data.0.len(),
                &mut new_data as *mut _,
                &mut new_size as *mut _,
            );
            // SAFETY: The resulting slice will probably be invalidated by the next call?
            let slice = std::slice::from_raw_parts(new_data, new_size);
            let seed = DecisionSeed(slice.to_owned().into());
            if res != 0 {
                Ok(seed)
            } else {
                Err(seed)
            }
        }
    }

    // Note: This is effectively self.generate(DecisionSeed::random())
    pub fn generate_random_file(&self) -> InputData<'static> {
        tracy_full::zone!("FormatFuzzer::generate_random_file");
        unsafe {
            let mut new_data = std::ptr::null();
            let mut new_size: u32 = 0;
            (self.generate_random_file)(&mut new_data as *mut _, &mut new_size as *mut _);
            // SAFETY: The resulting slice will probably be invalidated by the next call?
            let slice = std::slice::from_raw_parts(new_data, new_size as usize);
            InputData(slice.to_owned().into())
        }
    }

    pub fn one_smart_mutation(&self, file_handle: &FileHandle) -> InputData<'static> {
        tracy_full::zone!("FormatFuzzer::one_smart_mutation");
        unsafe {
            let mut new_data = std::ptr::null();
            let mut new_size: u32 = 0;
            let _res = (self.one_smart_mutation)(
                file_handle.id as i32,
                &mut new_data as *mut _,
                &mut new_size as *mut _,
            );
            if new_size == 0 {
                return InputData(b"".into());
            }
            // SAFETY: The resulting slice will probably be invalidated by the next call?
            let slice = std::slice::from_raw_parts(new_data, new_size as usize);
            InputData(slice.to_owned().into())
        }
    }

    pub fn process_file(&self, data: &[u8]) -> FileHandle {
        tracy_full::zone!("FormatFuzzer::process_file");
        // let id = self.file_ctr.update(|x| x + 1);
        let id = self.file_ctr.get();
        self.file_ctr.set(id + 1);

        let mut path = self.tmpdir.path().to_path_buf();
        path.push(format!("{}.bin", id));
        std::fs::write(&path, data).unwrap();
        let file_name = path.as_os_str().to_str().unwrap();
        let mut path = self.tmpdir.path().to_path_buf();
        path.push(format!("{}-decision.seed", id));
        let rand_name = path.as_os_str().to_str().unwrap();

        let fname = CString::new(file_name).unwrap();
        let rname = CString::new(rand_name).unwrap();

        let res = unsafe { (self.process_file)(fname.as_ptr(), rname.as_ptr()) };
        let validity = res as f32 / 100.0;
        FileHandle { id, validity }
    }
}

impl<'a> std::fmt::Debug for FormatFuzzer<'a> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FormatFuzzer")
            .field("tmpdir", &self.tmpdir)
            .field("file_ctr", &self.file_ctr)
            .finish_non_exhaustive()
    }
}
