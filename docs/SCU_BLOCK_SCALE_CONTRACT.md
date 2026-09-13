# SCU block-scaled integer 수치 계약

이 문서는 수정의 목표 계약이다. 구현 완료나 RTL 검증 결과를 대신하지 않는다.
기준은 IM2P `3aeb5feee6872f88ec1f6a5dc0d77fb1bb8babf8`, host
`7a74ac29c9c8fa8fbc5d47cf073b8b9266e3e7a9`, include
`cd2caed8d93e49d3f8873bd6c4e340fcad54b9e0`이다.

## 기존 동작과 목표

기존 native H1/HP1은 frontend가 VectorExternal을 선택하고 scale callback에서
identity 1을 공급한다. RTL은 K32 block마다 accumulator를 초기화하고 block raw를
반환한다. Host는 각 raw에 block FP factor와 activation scale을 곱해 합산한다.
이는 기존 IFR2/External 검증의 의미이며 새로운 SCU 성공 근거가 아니다.

| Route | Stored metadata | Decoded execution metadata | RTL op | Output domain | Host FP 연산 | Residual |
|---|---|---|---|---|---|---|
| H1 production | UINT8 c, UINT16 R, FP32 S | unsigned beta=c+R, 0..65790 | UnsignedMultiply | 전체 K final signed integer | double(integer) * double(S) * double(activation scale), 최종 float 변환 | 결정 전 ON 거부 |
| HP1 production | INT16 m, FP32 channel S | exponent 0..32767 또는 zero | LeftShift | 전체 K final signed integer | H1과 같은 shared scale 순서 | 결정 전 ON 거부 |
| Legacy H0/External | 기존 block FP metadata | 기존 provider identity/cache | External | block raw planes | 기존 block FP reducer | 기존 legacy 범위 별도 |
| Legacy raw integer | 기존 signed-byte scale 의미 | 명시적인 legacy metadata | Bypass/Multiply/Shift | legacy final raw | 기존 계약 | 별도 |

H1 quantizer는 변경하지 않는다. Native quantizer의 일반 scale 간격은
`(max-min)/255`이며, offset을 UINT16으로 표현하기 위한 기존 하한
`min/65535`와 constant/zero-channel 처리를 그대로 사용한다.
동일 channel에서 반복 저장한 S/R은 전체 K에 걸쳐 동일해야 한다.
Nonfinite S와 불일치 S/R은 실행 전에 거부한다. 유효한 zero channel은 허용한다.
HP1 channel S도 전체 K에서 동일해야 한다. `m=INT16_MIN`만 zero이며,
다른 음수 m은 invalid다. 저장형의 유효한 nonnegative 범위 전체를 수용한다.
기존 residual integer reader의 m<=62 제한은 INT64 factor materialization 한계이며
새 RTL left-shift의 형식 제한으로 재사용하지 않는다.

Activation scale은 해당 row/stripe의 전체 K에 공유된다. 서로 다른 stripe의
theta가 같아야 한다는 조건은 없다. FULL은 시작 전에 검증한다. PIPELINE의
정적 W/channel metadata는 시작 전에, theta는 post-fold event 수락 시 검증한다.
미공개 stripe의 theta를 먼저 읽거나 전체 quantization 완료를 기다리지 않는다.

## Typed ABI와 encoding

현재 ABI4/IFR2와 호환되지 않는 변경이다. 새 canonical ABI는 version5로 구분한다.
Scale element carrier는 native `uint32_t`/Rust `u32`/BSV `UInt#(32)`다.
Element stride는 4 bytes이며 physical factor17과 별개다. Wire는 little endian이다.

- Op0 Bypass, op1 legacy signed Multiply, op2 legacy signed Shift, op3 External을 보존한다.
- Op4 UnsignedMultiply: 0..65790만 유효하다. 남은 carrier 값은 reserved다.
- Op5 LeftShift: 0..32767은 exponent, `0x80000000`은 zero다. 나머지는 reserved다.
- Legacy op1/2 carrier는 signed INT8 값을 INT32로 sign-extend한 bit pattern만 허용한다.
- HP1 zero와 exponent0은 서로 다르며 zero lane도 valid/write/ACK를 유지한다.

Descriptor와 output callback은 명시적 output domain을 전달한다.
Domain0은 legacy final, domain1은 legacy block raw, domain2는 SCU final integer다.
SCU final callback의 block 필드는 K block을 뜻하지 않는다. 반드시 domain2로 구분한다.
Domain/op 불일치, ABI4, stale numerical revision은 실행 전에 거부한다.
Scale 주소는 base+4*(block*row_stride+column_offset+column)이다.
모든 곱셈/덧셈 overflow, alignment, bounds와 padding을 검사한다.

실행한 x86_64 C/C++ layout과 Rust `repr(C)` 계약은 다음과 같다. 같은 struct 크기라도
callback signature와 pointer element 의미가 바뀌었으므로 ABI4와 호환되지 않는다.

| 구조/필드 | native bytes / offset |
|---|---:|
| `im2p_provider_t` | 40 |
| `im2p_matmul_desc_t` | 224; activations24, provider184 |
| `im2p_stripe_work_desc_t` | 216; weights24, provider176 |
| `im2p_activation_stripe_t` | 72 |
| `im2p_stripe_completion_extended_t` | 56; publication32, completion40, delta48 |
| `output_domain` | 두 descriptor 모두 vector_op 바로 다음 byte |
| Scale element | uint32 4B, 4B alignment; 16-lane Verilator vector512bits |

Canonical descriptor의 A/W `*_row_stride_bytes`는 byte 단위이고,
`scale_row_stride`와 `scale_column_offset`은 uint32 element 단위다.
Provider callback의 block/column도 element index다. Rust가 scale stride에4를 곱하여
RTL byte stride를 만들고, memory bridge가 byte 주소를 4B element로 검사·변환한다.
Verilator는 lane0부터32bit씩 pack/unpack한다. FPGA provider의
block/J stride는 별도 명시된256/4 bytes다. Host pointer를 wire device address로 쓰지 않는다.
새 ARM64 native layout/runtime은 이 작업에서 실행하지 않았다.

Provider callback lane은 signed64다. A4/A8의 signed32를 sign-extend하고 A16 signed64를
보존한다. 기존 flat int32 output storage는 compatibility 경계다. A16 SCU final의
정확한 INT64 출력은 provider 경로로 검증하며 int32 storage로 잘라 성공시키지 않는다.

## Fragment별 수치 순서

PE-local partial은 scale을 적용하지 않은 exact dot이다. A8의 partial 폭은
D16/32/64에서 각각20/21/22 bits다. 기존 widenPartial sign extension을 보존한다.
Architectural accumulator는 A4/A8 signed32, A16 signed64다.

각 fragment는 `min(DIM, remaining_K, remaining_in_block)`이다. B_K32/DIM16의
두 fragment는 같은 factor를 사용한다. Block raw를 먼저 합친 뒤 한 번 scale하는
새 단계를 만들지 않는다. 사용자가 지정한 saturation 순서는 다음과 같다.

```text
p = exact unscaled PE dot of this fragment
q = Sat_acc(SCU_exact(p, metadata))
acc = q                              # first contribution of output work
acc = Sat_acc(widen(acc) + widen(q))  # every subsequent contribution
```

H1 full product의 임시 폭은 accWidth+17이다. Signed32*unsigned17은 signed49로
정확히 표현한다. Factor는 zero-extend, partial은 sign-extend한다.
Add는 accWidth+1에서 계산하고 범위를 검사한다. Truncate 후 clamp하지 않는다.
HP1 shift는 0 partial과 zero sentinel을 먼저 처리한다. 큰 유효 exponent는
부호와 exact overflow에 따라 clamp한다. Width 이상의 언어 shift나 음수 C++
left shift를 golden에 사용하지 않는다.

SCU/architectural accumulation에만 saturation을 적용한다. PE exact 합, 주소,
counter, FP 및 명시적인 legacy wrap 연산은 바꾸지 않는다.
같은 work의 K32 경계는 metadata만 전환하고 accumulator는 유지한다.
새 output work/invocation의 첫 contribution은 zero여도 replace한다.
최종 valid scalar는 M*N이다. Vector writes, ACK, wire padding은 별도 count다.

S1은 typed metadata/연산 위치/final routing의 overflow-free 검증 snapshot이다.
임시 wrap이 있으면 production으로 분류하지 않는다. S2는 위 clamp 순서를 적용하고
별도 numerical revision `signed-scu-sat-v2`와 independent golden으로 검증한다.
S1/S2의 source diff와 결과는 서로 다른 artifact로 보존한다.

## Golden과 output 공개

G0는 수정 전 External 결과다. G1은 Python arbitrary integer 등 독립 scalar 구현으로
실제 fragment 순서, SCU clamp, accumulator clamp를 재현한다. G2는 G1에
shared FP 연산을 위 표 순서로 적용하고 float32로 변환한다.
G1 raw exact, G2 float32 bit-exact, padding sentinel exact를 기준으로 한다.
Old External과 FP 재배열 또는 saturation 때문에 생기는 차이는 별도로 분류한다.
Expected를 DUT 결과로 덮어쓰거나 실패 뒤 tolerance를 넓히지 않는다.
Caller f_out 공개는 전체 invocation의 validation/fence/authorization 성공 후에만 한다.

## Residual 결정 필요

기존 H1/HP1 residual은 checked INT64 block factor와 radix 복원 후 별도 FP delta를
dense float에 더한다. 새 dense와 같은 saturation domain을 정의하지 않는다.
Radix별 SCU 결과의 복원 폭, residual 사전 clamp 여부, dense/residual merge clamp
순서는 추가 결정이 필요하다. 해당 결정 없이 기존 CPU block scaling을 유지한 채
production ON 성공을 보고하지 않는다. ON은 명시적으로 거부하고 dense 수정을 계속한다.

## FPGA와 역사적 배포

새 FPGA contract는 IFR3 capability `0x0294`로 분리한다. Version 번호만으로 성공을
판정하지 않고 scale encoding, final-output domain, numerical revision을 CAP/manifest에
함께 고정한다. 우선 A8/W8/D16 H1 FULL/live PIPELINE을 대상으로 한다.
HP1/RMD가 구현되지 않은 FPGA 경로는 시작 전에 거부한다.
UART 1Mbaud/8N1, core25MHz, 기존 geometry와 publication 의미는 유지한다.
기존 bitstream `8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca`는
이 새 SCU 계약을 지원하지 않는다. 기존 cycle361/39907/58695는 새 기대값이 아니다.
