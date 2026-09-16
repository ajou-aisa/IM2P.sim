use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

fn verilator_root() -> PathBuf {
    let executable =
        env::var("IM2P_VERILATOR_EXECUTABLE").unwrap_or_else(|_| "verilator".to_string());

    let output = Command::new(&executable)
        .arg("-V")
        .output()
        .expect("verilator must be installed");

    assert!(
        output.status.success(),
        "verilator -V failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let text = String::from_utf8(output.stdout).expect("verilator -V output must be UTF-8");

    const RUNTIME_FILES: [&str; 4] = [
        "verilated.cpp",
        "verilated_threads.cpp",
        "verilated.h",
        "verilated_threads.h",
    ];

    text.lines()
        .filter_map(|line| {
            let (key, value) = line.split_once('=')?;
            (key.trim() == "VERILATOR_ROOT").then(|| PathBuf::from(value.trim()))
        })
        .find(|root| {
            RUNTIME_FILES
                .iter()
                .all(|name| root.join("include").join(name).is_file())
        })
        .unwrap_or_else(|| {
            panic!(
                "no usable VERILATOR_ROOT found in `{} -V` output:\n{}",
                executable, text
            )
        })
}
fn main() {
    let implementation =
        env::var("IM2P_SIM_IMPLEMENTATION").unwrap_or_else(|_| "LEGACY_BSV".to_string());
    assert!(
        matches!(implementation.as_str(), "LEGACY_BSV" | "GEMMINI_HP1"),
        "IM2P_SIM_IMPLEMENTATION must be LEGACY_BSV or GEMMINI_HP1"
    );
    let activation_bits = env::var("IM2P_ACTIVATION_BITS").unwrap_or_else(|_| "8".to_string());
    assert!(
        matches!(activation_bits.as_str(), "4" | "8" | "16"),
        "IM2P_ACTIVATION_BITS must be one of 4, 8, or 16"
    );
    let weight_bits = env::var("IM2P_WEIGHT_BITS").unwrap_or_else(|_| "8".to_string());
    assert!(
        matches!(weight_bits.as_str(), "4" | "8" | "16"),
        "IM2P_WEIGHT_BITS must be one of 4, 8, or 16"
    );
    assert!(
        weight_bits == activation_bits,
        "IM2P activation and weight widths must match"
    );
    let dim = env::var("IM2P_DIM").unwrap_or_else(|_| "16".to_string());
    assert!(
        matches!(dim.as_str(), "16" | "32" | "64"),
        "IM2P_DIM must be 16, 32, or 64"
    );
    if implementation == "GEMMINI_HP1" {
        assert!(
            matches!(activation_bits.as_str(), "4" | "8"),
            "GEMMINI_HP1 requires A/W 4 or 8"
        );
    }
    let root = env::var_os("IM2P_REPO_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .map(Path::to_path_buf)
        })
        .expect("repository root must be discoverable");
    let artifact_id = format!("a{activation_bits}-w{weight_bits}-d{dim}");
    let generator = root.join("scripts/im2p_config.py");
    let python = env::var("PYTHON").unwrap_or_else(|_| "python3".to_string());
    let generated = Command::new(python)
        .arg(&generator)
        .args(["--rust", &activation_bits, &weight_bits, &dim])
        .output()
        .expect("profile generator must run");
    assert!(
        generated.status.success(),
        "profile generation failed: {}",
        String::from_utf8_lossy(&generated.stderr)
    );
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo sets OUT_DIR"));
    fs::write(out_dir.join("im2p_profile.rs"), generated.stdout)
        .expect("generated Rust profile must be writable");
    println!("cargo:rerun-if-env-changed=PYTHON");
    println!("cargo:rerun-if-changed={}", generator.display());
    println!(
        "cargo:rerun-if-changed={}",
        root.join("config/im2p_profiles.json").display()
    );
    println!("cargo:rerun-if-changed=ffi/im2p_config.h");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator_log.h");
    println!("cargo:rerun-if-changed=ffi/im2p_verilator_log.cpp");
    println!("cargo:rerun-if-changed=ffi/im2p_verilated_runtime.cpp");
    let build_dir = env::var_os("IM2P_BUILD_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join("build"));
    let verilator = verilator_root();

    println!("cargo:rerun-if-env-changed=IM2P_ACTIVATION_BITS");
    println!("cargo:rerun-if-env-changed=IM2P_WEIGHT_BITS");
    println!("cargo:rerun-if-env-changed=IM2P_DIM");
    println!("cargo:rerun-if-env-changed=IM2P_SIM_IMPLEMENTATION");
    println!("cargo:rerun-if-env-changed=IM2P_REPO_ROOT");
    println!("cargo:rerun-if-env-changed=IM2P_BUILD_DIR");
    println!("cargo:rerun-if-env-changed=IM2P_VERILATOR_EXECUTABLE");
    println!("cargo:rerun-if-env-changed=IM2P_GEMMINI_HP1_OBJ_DIR");
    println!("cargo:rerun-if-env-changed=IM2P_GEMMINI_HP1_TOP");
    println!("cargo:rerun-if-env-changed=IM2P_GEMMINI_HP1_PREFIX");
    let (obj_dir, top, bridge_source, bridge_header, archive_name) =
        if implementation == "GEMMINI_HP1" {
            let expected_top = format!("IM2PGemminiWSHP1A{activation_bits}W{weight_bits}D{dim}");
            let top = env::var("IM2P_GEMMINI_HP1_TOP")
                .expect("GEMMINI_HP1 requires IM2P_GEMMINI_HP1_TOP");
            assert_eq!(top, expected_top, "GEMMINI_HP1 top/profile mismatch");
            let prefix = env::var("IM2P_GEMMINI_HP1_PREFIX")
                .expect("GEMMINI_HP1 requires IM2P_GEMMINI_HP1_PREFIX");
            assert_eq!(
                prefix, "VIM2PGemminiWSHP1Sim",
                "GEMMINI_HP1 Verilator prefix mismatch"
            );
            println!("cargo:rustc-cfg=im2p_gemmini_integrated");
            (
                PathBuf::from(
                    env::var_os("IM2P_GEMMINI_HP1_OBJ_DIR")
                        .expect("GEMMINI_HP1 requires IM2P_GEMMINI_HP1_OBJ_DIR"),
                ),
                prefix,
                "ffi/im2p_gemmini_integrated.cpp",
                "ffi/im2p_verilator.h",
                "im2p_gemmini_integrated",
            )
        } else {
            (
                build_dir
                    .join("verilator")
                    .join(&artifact_id)
                    .join("obj_dir"),
                format!("VmkSynthA{activation_bits}W{weight_bits}D{dim}"),
                "ffi/im2p_verilator.cpp",
                "ffi/im2p_verilator.h",
                "im2p_verilator",
            )
        };
    println!(
        "cargo:rerun-if-changed={}",
        obj_dir.join(format!("{top}.h")).display()
    );
    println!("cargo:rerun-if-changed={bridge_source}");
    println!("cargo:rerun-if-changed={bridge_header}");
    if implementation == "GEMMINI_HP1" {
        println!("cargo:rerun-if-changed=ffi/im2p_integrated_signal.hpp");
    }

    let mut build = cc::Build::new();
    build
        .cpp(true)
        .std("c++17")
        .include(&obj_dir)
        .include(root.join("sim/ffi"))
        .include(root.join("fpga/gemmini_hp1/host"))
        .include(root.join("frontend/include"))
        .include(root.join("sim/include"))
        .include(verilator.join("include"))
        .define("IM2P_ACTIVATION_BITS", Some(activation_bits.as_str()))
        .define("IM2P_WEIGHT_BITS", Some(weight_bits.as_str()))
        .define("IM2P_DIM", Some(dim.as_str()))
        .warnings(false)
        .file(bridge_source)
        .file("ffi/im2p_verilator_log.cpp");
    if implementation == "GEMMINI_HP1" {
        build.define("IM2P_GEMMINI_INTEGRATED", None);
    }
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
        .file("ffi/im2p_verilated_runtime.cpp")
        .file(verilator.join("include/verilated_threads.cpp"))
        .compile(archive_name);
}
