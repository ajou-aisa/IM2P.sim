# Portable Dense PIPELINE fixtures

`m16n16k32`, `m321n48k64`, `m321n48k96`은 기존 host tiler/quantizer로 만든 작은 integration 입력이다. 실제 모델 capture가 아니다. 긴 두 입력의 자동 geometry는 stripe160/160/1, event slot0/1/0이며 theta는 -6/-3/0이다. 긴 입력은 output row stride51, 작은 FULL 입력은 stride19이며 column stride는 모두1이다. Native Q8_H1/EXSIA/A8W8DIM16/RMD OFF를 유지한다.

Nano 이동 준비에서 추가한 `m16n16k32`의7개 파일도 아래 frozen 원본과 byte-exact다. `input-f32.bin`은 기존 live producer와 FULL cycle guard의 작은 입력 검사에 필요하다. 이 파일은2048 bytes이며 SHA256은 `ab610cb081f36d099dcffbbc1ca23d99b7406ee38e159740bac9fcaaa492129f`다. 기존 IFR1 fixture의 별도 provenance와 긴 두 fixture의 입력·정답은 변경하지 않았다.

원본은 `build/experiments/dense-pipeline-20260909T141302Z/final-fixtures`다. 각 directory의 필요한7개 파일을 원본 byte 그대로 복사했다. 하위 `manifest.json`과 `sha256.json`도 원본과 동일하다. 하위 hash manifest는 원래5개 파일만 기록하며, 이 directory의 [sha256.json](sha256.json)은 `input-f32.bin`과 README를 포함한 전체22개 member를 검사한다(자기 자신 제외). Generated RTL, 로그, R1 중복 출력은 복사하지 않았다. 원래 experiment의 plan/provenance/측정값을 변경하지 않는다.

| Fixture | I/J/K | Tile factors | Raw signed32 | Logical f_out | Padding |
|---|---|---|---:|---:|---:|
| m16n16k32 | 16/16/32 | 1/1/2 | 256 | 256 | 48 |
| m321n48k64 | 321/48/64 | 10/3/4 | 30,816 | 15,408 | 963 |
| m321n48k96 | 321/48/96 | 10/3/6 | 46,224 | 15,408 | 963 |

원본 생성은 host06 capture 명령으로 수행됐다. 아래는 최종 host08의 동일 capture 경로 재현 명령이다. Host pin은 `7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, Gemmini include pin은 `cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이다. Host08 source manifest SHA256은 `0bf50916cd891c766f4ef372c68d9c07494a419606bdd44c289e98954bf81454`, `persistent_replay` SHA256은 `f057b99fc188673cbfad29bd9957616143ad89458e5d4c5de0d583595cfd6e03`이다. 빌드는 [상위 README](../README.md)와 [source provenance](../source_provenance.json)를 따른다.

Repository root에서 실행한다. Capture는 기존 `ggml_init` 초기화와 native quantizer를 호출하고 R0/reference와 R1 replay도 수행한다. UART를 열지 않는다. 이미 존재하는 fixture directory에 capture하지 않는다.

```sh
DENSE_REPLAY=build/experiments/dense-pipeline-20260909T141302Z/stream-08/host-build/persistent_replay
DENSE_CAPTURE="$(mktemp -d /tmp/im2p-dense-fixtures.XXXXXX)"
export DENSE_CAPTURE
"$DENSE_REPLAY" capture "$DENSE_CAPTURE/m321n48k64" 321 48 64 7
"$DENSE_REPLAY" capture "$DENSE_CAPTURE/m321n48k96" 321 48 96 8
```

Capture CLI가 만든 파일을 기존 seal 형식으로 기록하고 portable7개 member의 원본 hash와 비교한다. 아래 비교 실패를 허용하거나 expected 값을 갱신하지 않는다.

```sh
python3 - <<'PYVERIFY'
import hashlib, json, os
from pathlib import Path
base = Path(os.environ['DENSE_CAPTURE'])
expected = json.loads(Path('fpga/dense_pipeline/fixtures/sha256.json').read_text())
fields = ['fixture.bin', 'staging.bin', 'expected-raw.bin', 'expected-fout.bin', 'manifest.json']
for name in ['m321n48k64', 'm321n48k96']:
    local = {member: hashlib.sha256((base/name/member).read_bytes()).hexdigest() for member in fields}
    with (base/name/'sha256.json').open('x') as stream:
        stream.write(json.dumps(local, indent=2) + '\n')
    for member in fields + ['sha256.json', 'input-f32.bin']:
        assert hashlib.sha256((base/name/member).read_bytes()).hexdigest() == expected[f'{name}/{member}']
print('CAPTURE_PORTABLE_MEMBERS_EXACT_PASS members=14')
PYVERIFY
```

Persistent replay의 mode는 다음처럼 구분한다. 각 명령은 같은 프로세스에서 fixture당 warm-up1회와 측정5회를 실행하며 warm-up도 수치 검사한다.

```sh
"$DENSE_REPLAY" simulator - 5 fpga/dense_pipeline/fixtures/m321n48k64 fpga/dense_pipeline/fixtures/m321n48k96
"$DENSE_REPLAY" simulator-pipeline - 5 fpga/dense_pipeline/fixtures/m321n48k64 fpga/dense_pipeline/fixtures/m321n48k96
"$DENSE_REPLAY" simulator-live - 5 fpga/dense_pipeline/fixtures/m321n48k64 fpga/dense_pipeline/fixtures/m321n48k96
"$DENSE_REPLAY" simulator-live-pipeline - 5 fpga/dense_pipeline/fixtures/m321n48k64 fpga/dense_pipeline/fixtures/m321n48k96
```

`simulator`/`simulator-pipeline`은 저장된 A codes와 metadata를 사용하는 deterministic replay다. `simulator-live`는 FP32 입력을 기존 producer로 다시 quantize한 뒤 FULL 실행한다. **`simulator-live-pipeline`만 실제 ExSIA post-fold sink에서 준비된 stripe를 즉시 제출하는 live producer 경로다.** 전체 사전 quantization 후 event 재생을 live라고 부르지 않는다. Raw signed32, float32 f_out bits, padding을 기존 reference와 비교한다.

물리 UART backend는 `uart`, `uart-pipeline`, `uart-live`, `uart-live-pipeline`이다. 생성자에서 CAP 전송이 발생하므로 별도 보드 승인을 받기 전 실행하지 않는다. 기존 승인 대기 measurement plan은 experiment의 원본 fixture 경로를 계속 사용한다. Portable 복사본을 만든 사실은 그 계획의 경로나 승인 범위를 바꾸지 않는다.
