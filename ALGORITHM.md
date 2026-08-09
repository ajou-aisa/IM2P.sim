# SRMD-Based Residual GEMM with Compaction and Row Packing

## 1. Problem

For a residual stripe, we compute

[
O_{\mathrm{RC}} = RQ_W,
\qquad
R\in\mathbb Z_{32}^{M\times K},
\qquad
Q_W\in\mathbb Z_{8}^{K\times J},
]

where (R) is a sparse signed INT32 residual matrix and (Q_W) is a
signed INT8 weight matrix.

Since the systolic array supports signed INT8 MACs, the INT32 residual
matrix cannot be directly used as an input operand. We transform the
original residual GEMM through four steps:

1. **Signed Radix-256 Matrix Decomposition (SRMD)** converts each INT32
   residual value into signed INT8 digits.
2. **Compaction** removes inactive (K) indices and all-zero digit rows.
3. **Row Packing** places the remaining digit rows into one dense INT8
   GEMM operand.
4. **Radix-256 Reconstruction** maps the packed GEMM outputs back to their
   original rows and combines them according to their digit positions.

---

## 2. Signed Radix-256 Matrix Decomposition

### 2.1 Signed radix-256 digit extraction

SRMD represents a signed integer (x) as a sum of signed INT8 digits with
radix (256=2^8):

[
x=\sum_{p=0}^{P-1}256^p d_p,
\qquad
d_p\in[-128,127].
]

Here, (p) is the **digit index**, and (d_p) is the signed INT8 digit
at radix position (p).

Starting with

[
q_0=x,
]

we first extract the low eight bits of (q_p):

[
u_p=q_p\bmod256,
\qquad
u_p\in[0,255].
]

The unsigned byte (u_p) is then interpreted as a signed INT8 value:

[
d_p=
\begin{cases}
u_p, & u_p<128,\
u_p-256, & u_p\ge128.
\end{cases}
]

Equivalently,

[
d_p=\operatorname{sext}_8(q_p[7:0]).
]

After extracting (d_p), the remaining higher-order value is

[
q_{p+1}
=

\frac{q_p-d_p}{256}.
]

Since (q_p-d_p) is exactly divisible by 256, no approximation is
introduced. The process continues until

[
q_P=0.
]

Thus,

[
x=d_0+256d_1+256^2d_2+\cdots+256^{P-1}d_{P-1}.
]

### 2.2 Why signed carry is required

This is not a simple unsigned byte split. A byte value larger than 127
cannot be directly used by a signed INT8 MAC.

For example,

[
128
]

has low byte (128), but (128\notin[-128,127]). SRMD interprets that
byte as (-128) and propagates the difference to the next digit:

[
128=-128+256(1),
]

giving

[
128\rightarrow[-128,;1].
]

Likewise,

[
-129=127+256(-1),
]

and therefore

[
-129\rightarrow[127,;-1].
]

Other examples are

[
256\rightarrow[0,;1],
]

[
32767\rightarrow[-1,;-128,;1],
]

[
65538\rightarrow[2,;0,;1],
]

and

[
16777216\rightarrow[0,;0,;0,;1].
]

The carry to the next radix position therefore ensures that **every
generated digit remains a valid signed INT8 value** while preserving the
original integer exactly.

If an implementation supports at most (P_{\max}) digit planes, exact
representation requires

[
q_{P_{\max}}=0.
]

For example, a four-plane implementation requires (q_4=0).

---

## 3. Matrix Digit-Plane Construction

The scalar decomposition is applied independently to every element
(R(i,k)).

Let

[
d_p(R(i,k))
]

denote the (p)th signed radix-256 digit of (R(i,k)). We construct a
**digit-plane matrix**

[
D_p(i,k)=d_p(R(i,k)).
]

Hence,

[
D_p\in\mathbb Z_8^{M\times K}
]

and

[
R
=

\sum_{p=0}^{P-1}256^pD_p.
]

Importantly, SRMD itself does **not** change the matrix coordinates:
every digit generated from (R(i,k)) initially remains at the same
((i,k)) position in its corresponding (D_p).

For example, consider

[
R=
\begin{bmatrix}
0 & 128 & 256 & 0 & 0\
0 & -129 & 0 & 0 & 65538\
0 & 0 & 0 & 0 & 16777216
\end{bmatrix}.
]

SRMD produces

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

and

[
D_3=
\begin{bmatrix}
0 & 0 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 0\
0 & 0 & 0 & 0 & 1
\end{bmatrix}.
]

These matrices satisfy

[
R=D_0+256D_1+256^2D_2+256^3D_3.
]

---

# 4. Compaction

SRMD converts the element precision but preserves all original matrix
coordinates. Because the residual matrix is sparse, many of those
coordinates need not participate in GEMM.

Compaction removes redundancy along both the (K) dimension and the
digit-row dimension.

## 4.1 K-index compaction

Define the (K) indices active in at least one digit plane:

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

Let

[
H=|\mathcal U|.
]

Only these (K) indices can contribute to the residual GEMM.

The digit planes are therefore compacted along their (K) columns,

[
\widetilde D_p
=

D_p[:,\mathcal U]
\in\mathbb Z_8^{M\times H},
]

while the corresponding (K) rows are selected from the weight matrix,

[
\widetilde Q_W
=

Q_W[\mathcal U,:]
\in\mathbb Z_8^{H\times J}.
]

For the example above,

[
\mathcal U={k_1,k_2,k_4},
]

so (K) is reduced from 5 to 3.

The compacted digit planes are

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

The same ordered (K)-index set is used for both
(\widetilde D_p) and (\widetilde Q_W), preserving GEMM alignment.

---

## 4.2 Digit-row compaction

A compacted digit plane may still contain rows that are entirely zero.

Such a row satisfies

[
\widetilde D_p(i,:)=0
]

and consequently

[
\widetilde D_p(i,:)\widetilde Q_W=0.
]

It can therefore be omitted from the GEMM.

Define the set of active **digit rows**

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

For the example,

[
\mathcal A=
{
(0,0),(0,1),
(1,0),(1,1),
(2,1),
(3,2)
}.
]

Notice that a row is identified by the pair

[
(p,i),
]

not only by (i). The same original residual row may be active at several
different radix positions.

---

# 5. Row Packing

Let

[
\Gamma=
\bigl(
(p_0,i_0),
(p_1,i_1),
\ldots,
(p_{N-1},i_{N-1})
\bigr)
]

be an ordered list of the active digit rows, where

[
N=|\mathcal A|.
]

(\Gamma) serves as the **packed-row map** used later for reconstruction.

The active digit rows are placed consecutively into

[
A_{\mathrm{pack}}\in\mathbb Z_8^{N\times H}
]

such that

[
A_{\mathrm{pack}}[n,:]
=

\widetilde D_{p_n}[i_n,:].
]

For the running example,

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

Without digit-row compaction, four (3\times3) digit planes would require

[
12\times3
]

rows after conventional row stacking. Row packing reduces this example to

[
6\times3.
]

For compact visualization, the figure may display

[
A_{\mathrm{pack}}^T\in\mathbb Z_8^{3\times6}.
]

---

# 6. Residual GEMM

The transformed residual compensation can now be executed as a standard
signed INT8 GEMM:

[
C_{\mathrm{pack}}
=

A_{\mathrm{pack}}\widetilde Q_W,
]

where

[
C_{\mathrm{pack}}
\in
\mathbb Z_{32}^{N\times J}.
]

Each packed output row preserves the mapping stored in (\Gamma):

[
C_{\mathrm{pack}}[n,:]
=

\widetilde D_{p_n}[i_n,:]\widetilde Q_W.
]

The systolic array therefore does not need to understand the original
INT32 representation or the digit structure. It only performs an ordinary

[
\mathrm{INT8}\times\mathrm{INT8}
\rightarrow
\mathrm{INT32}
]

GEMM.

---

# 7. Radix-256 Reconstruction

After GEMM, output rows no longer appear at their original (M)-row
positions because the active digit rows were packed consecutively.

The packed-row map

[
\Gamma[n]=(p_n,i_n)
]

provides the information required to reconstruct the original output.

For each packed output row (n),

1. (i_n) specifies the original output row,
2. (p_n) specifies its radix-256 place value.

Thus, each packed output contributes

[
256^{p_n}C_{\mathrm{pack}}[n,:]
]

to original output row (i_n).

The reconstruction is therefore

[
O_{\mathrm{RC}}[i,:]
=

\sum_{{n,|,i_n=i}}
256^{p_n}
C_{\mathrm{pack}}[n,:].
]

Equivalently,

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

Hence,

[
\boxed{
O_{\mathrm{RC}}=RQ_W
}
]

and the transformation is exact.

---

# 8. DIM=16 Physical Execution

After compaction and row packing, the logical GEMM dimensions are

[
N\times H
\quad\times\quad
H\times J.
]

For a DIM(=16) systolic array, the number of physical GEMM tiles is

[
N_{\mathrm{tiles}}
=

\left\lceil\frac{N}{16}\right\rceil
\left\lceil\frac{H}{16}\right\rceil
\left\lceil\frac{J}{16}\right\rceil.
]

This differs from plane-wise row stacking, where an active digit plane
retains all (M) rows regardless of how many are nonzero.

With row packing, active digit rows from **different digit planes** can
occupy the same physical (M) tile.

Thus, compaction reduces both:

[
K \rightarrow H
]

and

[
PM \rightarrow N.
]
