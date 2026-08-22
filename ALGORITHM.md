# 압축 및 row packing 기반 SRMD residual GEMM

> **구현 상태:** 이 문서는 SRMD residual GEMM 변환의 algorithm reference다. IM2P.sim RTL, Rust simulator, C ABI, C++ frontend에는 아직 SRMD decomposition, compaction, row packing, radix-256 reconstruction을 구현하지 않았다.
>
> C++ frontend numerical execution은 `q8_0_unpacked_to_h1`, `q8_h0`, `q8_h1`, `q8_hp1`, `q8_channel`, `q8_channel_dense_sidecar`를 지원한다. `q8_h2`는 **Deprecated**이고 `q8_hp2`는 **Unsupported**다.
>
> Channel route는 RTL `VectorBypass`를 사용하며 channel scale은 host output에서 한 번만 적용한다. 구현 범위는 [root README](README.md)에서 확인한다.

## 1. 문제

Residual stripe에 대해 다음을 계산한다.

[
O_{\mathrm{RC}} = RQ_W,
\qquad
R\in\mathbb Z_{32}^{M\times K},
\qquad
Q_W\in\mathbb Z_{8}^{K\times J},
]

(R)은 sparse signed INT32 residual matrix이고 (Q_W)는 signed INT8 weight matrix다.

SystolicArray는 signed INT8 MAC만 지원하므로 INT32 residual matrix를 input operand로 직접 사용할 수 없다. residual GEMM은 다음 네 단계를 거쳐 변환한다.

1. **Signed Radix-256 Matrix Decomposition(SRMD)**으로 각 INT32 residual 값을 signed INT8 digit으로 변환한다.
2. **Compaction**으로 inactive (K) index와 all-zero digit row를 제거한다.
3. **Row Packing**으로 남은 digit row를 하나의 dense INT8 GEMM operand에 배치한다.
4. **Radix-256 Reconstruction**으로 packed GEMM output을 원래 row에 다시 매핑하고 digit position에 따라 결합한다.

---

## 2. Signed Radix-256 Matrix Decomposition

### 2.1 Signed radix-256 digit 추출

SRMD는 signed integer (x)를 radix (256=2^8) 기반 signed INT8 digit의 합으로 표현한다.

[
x=\sum_{p=0}^{P-1}256^p d_p,
\qquad
d_p\in[-128,127].
]

(p)는 **digit index**, (d_p)는 radix position (p)의 signed INT8 digit이다.

초깃값은 다음과 같다.

[
q_0=x,
]

(q_p)에서 low 8 bit를 추출한다.

[
u_p=q_p\bmod256,
\qquad
u_p\in[0,255].
]

unsigned byte (u_p)를 signed INT8 값으로 해석한다.

[
d_p=
\begin{cases}
u_p, & u_p<128,\
u_p-256, & u_p\ge128.
\end{cases}
]

이를 sign extension으로 나타내면 다음과 같다.

[
d_p=\operatorname{sext}_8(q_p[7:0]).
]

(d_p)를 추출하고 남은 higher-order 값은 다음과 같이 계산한다.

[
q_{p+1}
=

\frac{q_p-d_p}{256}.
]

(q_p-d_p)는 256으로 나누어떨어지므로 approximation이 발생하지 않는다. 다음 조건을 만족할 때까지 반복한다.

[
q_P=0.
]

그 결과 다음 등식이 성립한다.

[
x=d_0+256d_1+256^2d_2+\cdots+256^{P-1}d_{P-1}.
]

### 2.2 Signed carry가 필요한 이유

SRMD는 단순한 unsigned byte split이 아니다. 127보다 큰 byte 값은 signed INT8 MAC에 직접 넣을 수 없다.

예를 들어 다음 값은 low byte가 (128)이지만 (128\notin[-128,127])이다.

[
128
]

SRMD는 이 byte를 (-128)로 해석하고 차이를 next digit으로 넘긴다.

[
128=-128+256(1),
]

따라서 다음 digit sequence를 얻는다.

[
128\rightarrow[-128,;1].
]

(-129)도 같은 방식으로 분해한다.

[
-129=127+256(-1),
]

[
-129\rightarrow[127,;-1].
]

추가 예시는 다음과 같다.

[
256\rightarrow[0,;1],
]

[
32767\rightarrow[-1,;-128,;1],
]

[
65538\rightarrow[2,;0,;1],
]

[
16777216\rightarrow[0,;0,;0,;1].
]

Next radix position으로 넘기는 carry는 원래 integer를 정확히 보존하면서 모든 digit을 유효한 signed INT8 범위에 둔다.

최대 (P_{\max}) digit plane만 지원하는 구현이 exact representation을 보장하려면 다음 조건을 만족해야 한다.

[
q_{P_{\max}}=0.
]

예를 들어 four-plane 구현에서는 (q_4=0)이어야 한다.

---

## 3. Matrix digit-plane 구성

모든 (R(i,k)) element에 scalar decomposition을 독립적으로 적용한다.

[
d_p(R(i,k))
]

이 값은 (R(i,k))의 (p)번째 signed radix-256 digit이다. 이를 이용해 다음 **digit-plane matrix**를 구성한다.

[
D_p(i,k)=d_p(R(i,k)).
]

따라서,

[
D_p\in\mathbb Z_8^{M\times K}
]

[
R
=

\sum_{p=0}^{P-1}256^pD_p.
]

SRMD 자체는 matrix coordinate를 바꾸지 않는다. (R(i,k))에서 생성한 각 digit은 (D_p)의 같은 ((i,k)) position에 놓인다.

다음 matrix를 예로 든다.

[
R=
\begin{bmatrix}
0 & 128 & 256 & 0 & 0\
0 & -129 & 0 & 0 & 65538\
0 & 0 & 0 & 0 & 16777216
\end{bmatrix}.
]

SRMD를 적용하면 다음 digit plane을 얻는다.

[
D_0=
\begin{bmatrix}
0 & -128 & 0 & 0 & 0\
0 & 127 & 0 & 0 & 2\
0 & 0 & 0 & 0 & 0
\end{bmatrix},
]

[
D_1=
\begin{bmatrix}
0 & 1 & 1 & 0 & 0\
0 & -1 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 0
\end{bmatrix},
]

[
D_2=
\begin{bmatrix}
0 & 0 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 1\
0 & 0 & 0 & 0 & 0
\end{bmatrix},
]

[
D_3=
\begin{bmatrix}
0 & 0 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 1
\end{bmatrix}.
]

각 digit plane은 다음 등식을 만족한다.

[
R=D_0+256D_1+256^2D_2+256^3D_3.
]

---

## 4. Compaction

SRMD는 element precision만 변환하고 matrix coordinate는 모두 유지한다. Residual matrix가 sparse이므로 많은 coordinate를 GEMM에서 처리할 필요가 없다.

Compaction은 (K) dimension과 digit-row dimension의 중복을 제거한다.

### 4.1 K-index compaction

하나 이상의 digit plane에서 active인 (K) index를 다음과 같이 정의한다.

[
\mathcal U
=

\left{
k
;\middle|;
\exists,p,i:
D_p(i,k)\neq0
\right}.
]

[
H=|\mathcal U|.
]

이 (K) index만 residual GEMM에 기여한다. Digit plane은 (K) column 방향으로 compact한다.

[
\widetilde D_p
=

D_p[:,\mathcal U]
\in\mathbb Z_8^{M\times H},
]

weight matrix에서도 이에 대응하는 (K) row를 선택한다.

[
\widetilde Q_W
=

Q_W[\mathcal U,:]
\in\mathbb Z_8^{H\times J}.
]

앞의 예에서는 다음 index가 선택된다.

[
\mathcal U={k_1,k_2,k_4},
]

이에 따라 (K)는 5에서 3으로 줄어든다.

[
\widetilde D_0=
\begin{bmatrix}
-128&0&0\
127&0&2\
0&0&0
\end{bmatrix},
]

[
\widetilde D_1=
\begin{bmatrix}
1&1&0\
-1&0&0\
0&0&0
\end{bmatrix},
]

[
\widetilde D_2=
\begin{bmatrix}
0&0&0\
0&0&1\
0&0&0
\end{bmatrix},
]

[
\widetilde D_3=
\begin{bmatrix}
0&0&0\
0&0&0\
0&0&1
\end{bmatrix}.
]

(\widetilde D_p)와 (\widetilde Q_W)에 동일한 ordered (K)-index set을 적용해 GEMM alignment를 보존한다.

### 4.2 Digit-row compaction

Compacted digit plane에도 모든 element가 0인 row가 남을 수 있다.

[
\widetilde D_p(i,:)=0
]

이 row는 다음과 같이 0만 출력한다.

[
\widetilde D_p(i,:)\widetilde Q_W=0.
]

따라서 GEMM에서 이 row를 생략할 수 있다. Active **digit row** set은 다음과 같이 정의한다.

[
\mathcal A
=

\left{
(p,i)
;\middle|;
\exists h:
\widetilde D_p(i,h)\neq0
\right}.
]

앞의 예에서 active digit row set은 다음과 같다.

[
\mathcal A=
{
(0,0),(0,1),
(1,0),(1,1),
(2,1),
(3,2)
}.
]

Row는 (i) 하나가 아니라 다음 pair로 식별한다.

[
(p,i),
]

하나의 residual row가 여러 radix position에서 active일 수 있기 때문이다.

---

## 5. Row packing

Active digit row의 ordered list를 다음과 같이 정의한다.

[
\Gamma=
\bigl(
(p_0,i_0),
(p_1,i_1),
\ldots,
(p_{N-1},i_{N-1})
\bigr)
]

[
N=|\mathcal A|.
]

(\Gamma)는 reconstruction에 쓰는 **packed-row map**이다. Active digit row는 다음 matrix에 연속으로 배치한다.

[
A_{\mathrm{pack}}\in\mathbb Z_8^{N\times H}
]

[
A_{\mathrm{pack}}[n,:]
=

\widetilde D_{p_n}[i_n,:].
]

앞의 예에 적용한 결과는 다음과 같다.

[
A_{\mathrm{pack}}
=

\begin{bmatrix}
-128&0&0\
127&0&2\
1&1&0\
-1&0&0\
0&0&1\
0&0&1
\end{bmatrix}
\in\mathbb Z_8^{6\times3}.
]

Digit-row compaction을 적용하지 않으면 four (3\times3) digit plane을 conventional row stacking한 결과에 다음 row 수가 필요하다.

[
12\times3
]

Row packing을 적용하면 다음 크기로 줄어든다.

[
6\times3.
]

필요하면 다음 transpose로 나타낼 수 있다.

[
A_{\mathrm{pack}}^T\in\mathbb Z_8^{3\times6}.
]

---

## 6. Residual GEMM

변환된 residual compensation은 standard signed INT8 GEMM으로 실행할 수 있다.

[
C_{\mathrm{pack}}
=

A_{\mathrm{pack}}\widetilde Q_W,
]

[
C_{\mathrm{pack}}
\in
\mathbb Z_{32}^{N\times J}.
]

각 packed output row는 (\Gamma)의 mapping을 그대로 유지한다.

[
C_{\mathrm{pack}}[n,:]
=

\widetilde D_{p_n}[i_n,:]\widetilde Q_W.
]

SystolicArray는 원래 INT32 representation이나 digit structure와 무관하게 다음 GEMM만 수행한다.

[
\mathrm{INT8}\times\mathrm{INT8}
\rightarrow
\mathrm{INT32}
]

---

## 7. Radix-256 reconstruction

GEMM에서는 active digit row를 연속 배치하므로 output row의 position이 원래 (M)-row와 다르다.

[
\Gamma[n]=(p_n,i_n)
]

packed-row map은 원래 output을 reconstruction하는 데 필요한 정보를 담는다.

1. (i_n)은 원래 output row를 가리킨다.
2. (p_n)은 radix-256 place value를 지정한다.

각 packed output은 원래 output row (i_n)에 다음 값을 더한다.

[
256^{p_n}C_{\mathrm{pack}}[n,:]
]

reconstruction 식은 다음과 같다.

[
O_{\mathrm{RC}}[i,:]
=

\sum_{{n,|,i_n=i}}
256^{p_n}
C_{\mathrm{pack}}[n,:].
]

이를 전개하면 다음과 같다.

[
\begin{aligned}
O_{\mathrm{RC}}[i,:]
&=
\sum_p
256^p
\widetilde D_p[i,:]\widetilde Q_W\
&=
\sum_p
256^p
D_p[i,:]Q_W\
&=
\left(
\sum_p256^pD_p[i,:]
\right)Q_W\
&=
R[i,:]Q_W.
\end{aligned}
]

결과적으로 다음 등식이 성립한다.

[
\boxed{
O_{\mathrm{RC}}=RQ_W
}
]

이 등식은 decomposition과 reconstruction을 mathematical integer arithmetic으로 계산할 때 exact하다. Fixed-width INT32 GEMM output이나 reconstruction을 사용하는 구현에는 overflow가 발생하지 않는다는 bound나 명시적인 wrapping semantics가 추가로 필요하다.

---

## 8. DIM16/DIM32/DIM64 physical execution

Compaction과 row packing을 거친 logical GEMM dimension은 다음과 같다.

[
N\times H
\quad\times\quad
H\times J.
]

Array dimension이 (D)일 때 physical GEMM tile 수는 다음과 같다.

[
N_{\mathrm{tiles}}
=

\left\lceil\frac{N}{D}\right\rceil
\left\lceil\frac{H}{D}\right\rceil
\left\lceil\frac{J}{D}\right\rceil.
]

IM2P.sim의 현재 generated INT8 target은 (D=16), (D=32), 또는 (D=64)다. Row packing은 active digit plane의 all-zero가 아닌 row 수와 관계없이 모든 (M) row를 유지하는 plane-wise row stacking과 다르다.

Row packing에서는 서로 다른 digit plane의 active digit row를 같은 physical (M) tile에 배치할 수 있다. compaction은 다음 두 dimension을 줄인다.

[
K \rightarrow H
]

[
PM \rightarrow N.
]
