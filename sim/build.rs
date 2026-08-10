use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn verilator_root() -> PathBuf {
    let output = Command::new("verilator")
        .arg("-V")
        .output()
        .expect("verilator must be installed");
    let text = String::from_utf8(output.stdout).expect("verilator -V output must be UTF-8");
    text.lines()
        .find_map(|line| {
            let (key, value) = line.split_once('=')?;
            (key.trim() == "VERILATOR_ROOT").then(|| value.trim())
        })
        .map(PathBuf::from)
        .expect("VERILATOR_ROOT missing from verilator -V")
}

fn main() {
    let dim = env::var("IM2P_DIM").unwrap_or_else(|_| "16".to_string());
    assert!(dim == "16" || dim == "32", "IM2P_DIM must be 16 or 32");
    let root = env::var_os("IM2P_REPO_ROOT")
        .map(PathBuf::from)
        .or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).parent().map(Path::to_path_buf))
        .expect("repository root must be discoverable");
    let obj_dir = root.join("build/verilator").join(format!("int8x{dim}/obj_dir"));
    let verilator = verilator_root();

    println!("cargo:rerun-if-env-changed=IM2P_DIM");
    println!("cargo:rerun-if-env-changed=IM2P_REPO_ROOT");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator.cpp");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator.h");

    let mut build = cc::Build::new();
    build
        .cpp(true)
        .std("c++17")
        .include(&obj_dir)
        .include(verilator.join("include"))
        .define("IM2P_DIM", Some(dim.as_str()))
        .warnings(false)
        .cpp_link_stdlib("c++")
        .file("ffi/im2p_verilator.cpp");

    let entries = fs::read_dir(&obj_dir).expect("run make verilator target first");
    for entry in entries {
        let path = entry.expect("read Verilator object directory").path();
        if path.extension().and_then(|value| value.to_str()) == Some("cpp") {
            build.file(path);
        }
    }
    build
        .file(verilator.join("include/verilated.cpp"))
        .file(verilator.join("include/verilated_threads.cpp"))
        .compile("im2p_verilator");
}
