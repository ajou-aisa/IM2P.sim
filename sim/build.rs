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
    let activation_bits = env::var("IM2P_ACTIVATION_BITS").unwrap_or_else(|_| "8".to_string());
    assert!(
        matches!(activation_bits.as_str(), "4" | "8" | "16"),
        "IM2P_ACTIVATION_BITS must be one of 4, 8, or 16"
    );
    let dim = env::var("IM2P_DIM").unwrap_or_else(|_| "16".to_string());
    assert!(
        matches!(dim.as_str(), "16" | "32" | "64"),
        "IM2P_DIM must be 16, 32, or 64"
    );
    let root = env::var_os("IM2P_REPO_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .map(Path::to_path_buf)
        })
        .expect("repository root must be discoverable");
    let artifact_id = format!("a{activation_bits}-w8-d{dim}");
    let obj_dir = root
        .join("build/verilator")
        .join(artifact_id)
        .join("obj_dir");
    let verilator = verilator_root();

    println!("cargo:rerun-if-env-changed=IM2P_ACTIVATION_BITS");
    println!("cargo:rerun-if-env-changed=IM2P_DIM");
    println!("cargo:rerun-if-env-changed=IM2P_REPO_ROOT");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator.cpp");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator.h");
    println!("cargo:rerun-if-changed=ffi/testing/im2p_verilator_testing.h");
    println!(
        "cargo:rerun-if-changed={}",
        obj_dir
            .join(format!("VmkSynthInt{activation_bits}x{dim}.h"))
            .display()
    );

    let mut build = cc::Build::new();
    build
        .cpp(true)
        .std("c++17")
        .include(&obj_dir)
        .include(verilator.join("include"))
        .define("IM2P_ACTIVATION_BITS", Some(activation_bits.as_str()))
        .define("IM2P_DIM", Some(dim.as_str()))
        .warnings(false)
        .cpp_link_stdlib("c++")
        .file("ffi/im2p_verilator.cpp");
    if env::var_os("CARGO_FEATURE_TEST_HOOKS").is_some() {
        build.define("IM2P_VERILATOR_TEST_HOOKS", None);
    }

    let entries = fs::read_dir(&obj_dir).expect("run make verilator target first");
    for entry in entries {
        let path = entry.expect("read Verilator object directory").path();
        if path.extension().and_then(|value| value.to_str()) == Some("cpp") {
            println!("cargo:rerun-if-changed={}", path.display());
            build.file(path);
        }
    }
    build
        .file(verilator.join("include/verilated.cpp"))
        .file(verilator.join("include/verilated_threads.cpp"))
        .compile("im2p_verilator");
}
