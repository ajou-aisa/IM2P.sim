"""Reviewed immutable proof identities; hashes are not a general Tag6 proof."""
from typing import Final

PROOFS: Final = {
    'comparison': ('tag6/comparison/actual-prefix240-v1/comparison.json',
        '74eee90259e1d561cf95d0ec9eed77235e21cba66d20d0fc30dbe26037c55d3b'),
    'capture': ('tag6/comparison/actual-prefix240-v1/capture.json',
        'a015760c05d7f9bef243f293e0b394661b394d2a400f809a255d8a022e6f6adc'),
    'mutations': ('state-domain/mutations-v1/report.json',
        'a8c59dc0264c01881ec2328dcc7714122e4bfe327d89675902411be4fff95e73'),
    'stimulus': ('tag6/prefix240.json',
        '39a89c99d2596759908aab0d97e180d612de05b6fc0fa046ff5302f0dcab59b8'),
    'numeric_stimulus': ('tag6/prefix240.txt',
        '750c169cdfc899c61e1839add5ecff06d1e27af2ae374163e6ad7aa5c5b3bfe1'),
    'source_bridge': ('state-domain/native-source-bridge.json',
        'ee373e402f1add71f95fe197b4b6fb4cad0e4d3e61580dff5646a15e196f817e'),
    'tag5_bundle_receipt': ('baseline/tag5-source-bundle.json',
        '88191a5b80372e69af17be904324c889756b79d4d2834696c019f5a1eab65cf6'),
    'tag5_binding': ('baseline/tag5-binding.json',
        'd3ca1220522c48cc58460d594c83bbfb445d133e4a1b86296a0b621f4854b607'),
    'inherited': ('regression/inherited-current/report.json',
        'd56d54fd512e9b66c9db857d53199e4b603d15478843563068b5ebcf5dee16b8'),
    'base240': ('regression/inherited-current/base240.json',
        '22543523ed8b60a9f8d8a3df47e8305113017e26d091f988073df9d7cf203c38'),
    'run42': ('regression/inherited-current/run-aware42.json',
        '64ed0059d93a25f59fc0b9aef446b9363637629035be3ea91347d707408e807f'),
    'drained1056': ('regression/inherited-current/drained1056.json',
        '9a9a3d5537ef2b8a2a35cd74840d18f399b374e1392808ad9e688bda3048dfdf'),
    'exact30': ('regression/inherited-current/exact30.json',
        '355a978172217cf5b9861e6248973ffdffd2a0a66d23a5700fbbd5895f9fb3ee'),
    'stateful80': ('regression/inherited-current/guarded-stateful80.json',
        '7987b754c664b58c9296077810a0c7c0a8509caf8c0ad418be327285d7904066'),
    'parity': ('parity-v2/run/receipts.json',
        '00dbf174b87331a399fafb3c8b14608b9d2adb795b73100c465c8f769e3013db'),
    'parity_execution': ('parity-v2/execution.json',
        '8ffb731dcf7bfa820a4c03acae17dfc19971b597e89ef60bba29aec04114d7e7'),
    'endpoints': ('tag6/comparison/actual-prefix240-v1/endpoints.json',
        '785d9068eff7baea20805071a6e5a6139382f7a3d401a8f00722f6b2bc451582'),
    'boundaries': ('tag6/comparison/actual-prefix240-v1/boundaries.json',
        'f63a245076e7b9d2256ffb34eaf4a9887d573f29656077fd7e7454faa089dde2'),
}

ARTIFACTS: Final = {
    'library': '16ae9f633af34ec4d2151e7fa68c2af340891d21f21cd066d6cfefac1c338935',
    'shared_library': 'f648b4bc22368ef0bf13b24dfd85028cc76dc8c03ebf13c2295322337c55b0d5',
    'probe': '7f8f48fa1698deb347b6d9514aad3f54cec8eea955e9604ec027ea3f63a297ac',
    'tag5_bundle': 'bab09c79be8a84af6bfed3aa5e5d9a251585e7337b4794173c48b49d3200924e',
    'tag5_certificate': '03701984443a1c04ae13a6e9477b51c701ec9652e2858d958cd7607a6a27ffff',
    'rtl_object': '46c5cd254bdf5c0883ca0eed862ea2775b3f75b467bf8ae39ec227a2d1f5ebb7',
    'rtl_build': 'af45132cac418c2b63cfd08dfabebb55c9ce66e99987aa1f9b995671ca6b96f5',
    'trace': '3c1206633ac653a036f94bb7a25a447ea0320cfcd930a312713753bfde79a843',
    'lifecycle': 'a448326e4b925fc89a2c39d87bf6504246e49c38e3d5c2fc09620c56513065ee',
}

NATIVE_SOURCES: Final = {
    'sim/cycle/CMakeLists.txt':
        '41dcbf7954ad9b163a0ec6394c67a84ab95c552dab3e0c0d86c0567cce6f0661',
    'sim/cycle/sequence_c_api.cpp':
        'a3712d44d786f7c139d6c0619dbc1bae830e1a6da62e549f67436cf0ae649086',
    'sim/cycle/c_api.cpp':
        'd3a663271502b7f362548fdc392fd8e1ac78eedff1b57e63c072ca6ac464a7d7',
    'sim/cycle/control_engine.cpp':
        '90c58e4252bd08fdfe6a38146429199fdfa9fa0f4a80a98d5f29e53c6ff6a968',
    'sim/cycle/control_engine.hpp':
        'b892a2f4d6a20a47280f5b550335266ae1ca94cc5a1e025f968bc83634490f65',
    'sim/cycle/execute_engine.cpp':
        'b66a4bef9ec6497b03abdea9622e3f62d58282f42ff944a2de22f34bd946ac8f',
    'sim/cycle/scheduled_work.cpp':
        '419f43831bc70145b9b08fbe93aa2b324e9f3d1ef525264e939682aa273f2da5',
    'sim/cycle/scheduled_work.hpp':
        'ee2c884bcf9ba410acea94c35ad2616f82c2cbe541cbad483cb44668a9c35e80',
    'sim/cycle/timing_profile.hpp':
        '10ab352a7e93072cab45bba1a4a0a3cd1976273702ae5e7b4a9a92c2b8b0888a',
    'sim/cycle/cycle_model.hpp':
        'eebcacc483397308558325fbdf6e84cf8f1b2b48831b4992357adf638b3db226',
    'sim/common/gemmini_schedule.cpp':
        '8caf828be22a0576001a890f641026ba4719d4e83d9eab17c95764204d86558c',
    'sim/include/im2p_cycle_sequence.h':
        '353e9e7342a7d3c64e8e4add33559f598b653beb61c696f9d774b1366e7fd192',
    'sim/include/im2p_cycle_model.h':
        '7fd7f2abde59d2a973320239d22449ce8789b1671d23f8e1cd109d3fd573f9ae',
    'sim/include/im2p_compact_runs.h':
        '4904c5639227ba160bb4c350830e5d00476187934a3298e84951e85d8bf7cb49',
    'config/gemmini_hp1_profiles.json':
        '6bdd02b14b29fbab044bcebe6695a0f1a3836f9092a681c5fc25ef6b6564e433',
    'config/gemmini_host_memory_contracts/a8w8-d16-hp1.json':
        'd6650a34b66c2819c8dd905e0615f6c58b77c2382a81eba9bf6700d000d7cb27',
}
