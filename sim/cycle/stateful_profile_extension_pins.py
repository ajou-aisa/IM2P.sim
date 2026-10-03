"""Independent captures in certification order; no cross-precision inheritance."""
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ProfilePin:
    work_count: int
    proof_sha256: str
    milestone_sha256: str
    coverage_sha256: str
    memory_sha256: str
    events_sha256: str


INHERITED_SHA256: Final = "94487af1df6f05b802ed95b4d5281f4623686a153484a8d23af2a886b0baef27"
PROFILE_PINS: Final = {
    "a8w8-d64-hp1": ProfilePin(
        14, "767bd83d8466ed150cf13ef69226fc4805c8d15c8cc15424b1870c487b90fda2",
        "eb67d17f6790b2eb50648532bf9aaf845a9e09ce6d70e87b2e1be84aa7ff2bf7",
        "ce25e3d1de4d59f8a1a81b3270c25dccbf0c3c38779f50159ca2ef6d9411f59e",
        "c7072e6f9c1687558cd6d55108b2ec55d9b496b24a79a82ff6983253e465988c",
        "88437c66f34e74600a2c71919662f05056b018453f25fbcd9c3b437ba7675bbe"),
    "a4w4-d16-hp1": ProfilePin(
        12, "0159c285035c562782ae890162df3657c14cf8f16cded3041dea049455449c45",
        "9647c91254fca47552409b81bfba91374b0e72c8431071ffebe30577fd2889d6",
        "82dc6290fd90110658030b6094e4871895f65844f83d51f0156d0944bf2f145f",
        "280d7fb39b5b43ad0036b026fe3a2d2b8363668441fa25b21ecd9ccd028bc9c4",
        "25c968aa177fa02ab1bba4acf99cffa5e2ef0a808bddf97265b3322764690b87"),
    "a4w4-d32-hp1": ProfilePin(
        14, "7ca87e4ee71a48d6ed841837bc823da9187db2dbbda8e3b20c02433dee774de1",
        "543151c8b7ee48d2a0f2bb27e1a13fa385bc37bf1c9dd67d73d2ae5ed68c7839",
        "04ab72f3021453ba79eed90e31d0b60fdbdb93a2efa85e85d97793b78406ab98",
        "bafacd54efc88575238f4d633112bdda1609394efaaba1fc0d8b12b61dd52cbb",
        "f2dc4997055a10e990f7347c2665be26b0fb5e11716075952e3260a23a7e9b45"),
    "a4w4-d64-hp1": ProfilePin(
        14, "3ac5fda3b152f43e77010785812db9b4c44ac4c8d845c88df174834b176564bb",
        "9db2e148c76ee33672128ce496f233a6e141ec0bcd43b2470b7569f6ca2d97f9",
        "8b4a477332202bf0fbb240813474dcde94864898cdfeb24b216497372439bde3",
        "f5e615a2fa33fdc2ab2d692898ea382e20a0ee58a0ec15206ceff6d928084b85",
        "b7b09aef2afea7ef3f44d862c59103631adb650fa474d752c6a363e75b453c27"),
}
