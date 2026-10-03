from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rmd_scu_certificate as certificate


class RmdScuCertificateTest(unittest.TestCase):
    def test_build_uses_clean_profile_inputs_when_pinned_trace_is_absent(self) -> None:
        # Given a fresh official profile and a source root without optrace.cpp.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            llama = base / 'pinned-llama'
            profile = 'a8w8-d16-hp1'
            host = base / 'official' / profile
            cargo = base / 'cargo' / profile / 'debug'
            cycle = base / 'cycle'
            for path in (host / 'host-params/gemmini_params.h',
                         host / 'im2p_gemmini_hardware.h',
                         host / 'host-build/libgemmini_hp1_ggml_numeric.a',
                         host / 'host-build/gemmini-utils/libggml-gemmini-utils.a',
                         cargo / 'libim2p_sim.a', cycle / 'libim2p_cycle_model.a'):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fresh')
            source_names = (
                'ggml/src/ggml-gemmini/residual/rmd/rmd-builder.cpp',
                'ggml/src/ggml-gemmini/residual/rmd/rmd-compose.cpp',
                'ggml/src/ggml-gemmini/residual/rmd/rmd-executor.cpp',
                'ggml/src/ggml-gemmini/residual/rmd/rmd-im2p-executor.cpp',
                'ggml/src/ggml-gemmini/residual/rmd/rmd-reference.cpp',
                'ggml/src/ggml-gemmini/quants/common/weight_reader.cpp',
                'ggml/src/ggml-gemmini/quants/common/dequant.cpp',
                'ggml/src/ggml-gemmini/quants/act/dispatch.cpp',
                'ggml/src/ggml-gemmini/quants/act/exsia/exsia.cpp',
                'ggml/src/ggml-gemmini/ggml-gemmini-telemetry.cpp',
            )
            for name in source_names:
                path = llama / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fresh')
            flags = f'-std=c++20 -DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=1 -I{llama / "ggml/src"} -I{host / "host-params"}'
            command = ['verilator', '--cc', '-CFLAGS', flags]
            result = {'stage': 'host-test', 'status': 'PASS', 'profiles': [{
                'profile': profile, 'status': 'PASS',
                'llama_source': {'root': str(llama), 'head': 'pinned-head'},
                'resolved_profile': str(host / 'resolved-profile.json'),
                'command_results': [{'arguments': command, 'returncode': 0}],
            }]}
            (host / 'resolved-profile.json').write_text(json.dumps({
                'profile': profile, 'llama_source': result['profiles'][0]['llama_source'],
            }))
            (host / 'host-build/CMakeCache.txt').write_text('IM2P_PRODUCTION_TRACE_ENABLED:BOOL=OFF\n')
            (base / 'official/result.json').write_text(json.dumps(result))
            commands: list[list[str]] = []
            (base / 'out').mkdir()

            def compiler(_root: Path, argv: list[str], _log: Path) -> int:
                commands.append(argv)
                Path(argv[argv.index('-o') + 1]).write_bytes(b'probe')
                return 0

            # When the certificate builds against the explicit pinned root.
            with patch.object(certificate, 'clean_head', return_value='pinned-head'), \
                    patch.object(certificate, 'run_logged', side_effect=compiler):
                certificate.build(None, cargo.parent.parent, cycle, None, base / 'out',
                                  profile, llama_root=llama, build_root=base / 'official')

            # Then no active source, stale include, or absent optrace is compiled.
            argv = commands[0]
            self.assertNotIn('optrace.cpp', ' '.join(argv))
            self.assertIn('-DIM2P_PRODUCTION_TRACE_ENABLED=0', argv)
            self.assertIn(str((llama / source_names[0]).resolve()), argv)
            self.assertIn(str((host / 'host-build/gemmini-utils/libggml-gemmini-utils.a').resolve()), argv)
            self.assertNotIn('-DIM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=1', argv)


if __name__ == '__main__':
    unittest.main()
