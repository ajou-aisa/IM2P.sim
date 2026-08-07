# Balanced Radix-256 Matrix Decomposition for Decomposed SpMM

## 1. Problem

For one 16-row stripe, compute

\[
Y = XW,
\qquad
X\in\mathbb Z^{R\times K},\quad
W\in\mathbb Z^{K\times J},\quad R=16,
\]

where `X` is sparse signed INT32 and `W` is dense signed INT8. Generated
INT8 activation and weight digits are sign-extended at the NPU boundary to
the selected `INT, 32` element format. PE partial sums and accumulator cells
use that same 32-bit element precision. This simulation intentionally excludes
weight blocks and scales.

## 2. Scalar balanced radix-256 decomposition

Set \(\beta=256\). For an activation value \(x\), define

\[
q_0=x,
\]

\[
d_p=\operatorname{sext}_8(q_p\bmod 256),
\]

where the low eight bits are interpreted as a signed INT8 value, and

\[
q_{p+1}=\frac{q_p-d_p}{256}.
\]

For four lanes, exact representation requires \(q_4=0\), and then

\[
x=d_0+\beta d_1+\beta^2d_2+\beta^3d_3,
\qquad d_p\in[-128,127].
\]

Examples:

\[
128=-128+256,
\]

\[
32767=-1-128\cdot256+65536,
\]

\[
1048575=-1+16\cdot65536.
\]

## 3. Matrix decomposition

Apply the scalar decomposition elementwise to the stripe matrix:

\[
X=\sum_{p=0}^{3}\beta^pD_p,
\qquad D_p\in\mathbb Z_8^{R\times K}.
\]

For each original K column, record the lane mask

\[
m_{k,p}=
\bigvee_{i=0}^{R-1}[D_p(i,k)\ne0].
\]

The maximum decomposed depth of column \(k\) is

\[
\ell_k=
\begin{cases}
0,& \forall p:\ m_{k,p}=0,\\
1+\max\{p\mid m_{k,p}=1\},&\text{otherwise}.
\end{cases}
\]

The union of active original K columns is

\[
U=\{k\mid \exists p:\ m_{k,p}=1\}
  =\{u_0,u_1,\ldots,u_{H-1}\}.
\]

Let the selection matrix be

\[
S=[e_{u_0}\ e_{u_1}\ \cdots\ e_{u_{H-1}}]
\in\{0,1\}^{K\times H}.
\]

Then compact every digit plane and the weight matrix with the same selection:

\[
\widetilde D_p=D_pS\in\mathbb Z_8^{R\times H},
\]

\[
\widetilde W=S^TW\in\mathbb Z_8^{H\times J}.
\]

Let the globally active lane IDs be

\[
P=(p_0,p_1,\ldots,p_{L-1}).
\]

Stack the compact digit matrices in the row direction:

\[
A_{\mathrm{stack}}=
\begin{bmatrix}
\widetilde D_{p_0}\\
\widetilde D_{p_1}\\
\vdots\\
\widetilde D_{p_{L-1}}
\end{bmatrix}
\in\mathbb Z_8^{LR\times H}.
\]

One logical dense GEMM computes

\[
C_{\mathrm{stack}}
=A_{\mathrm{stack}}\widetilde W
\in\mathbb Z_{32}^{LR\times J}.
\]

Its row block \(t\) is

\[
C^{(t)}
=C_{\mathrm{stack}}[tR:(t+1)R,:]
=\widetilde D_{p_t}\widetilde W.
\]

Because all columns outside \(U\) are zero in every active digit plane,

\[
D_{p_t}=D_{p_t}SS^T,
\]

and therefore

\[
C^{(t)}
=D_{p_t}SS^TW
=D_{p_t}W.
\]

The CPU composes the output:

\[
Y=\sum_{t=0}^{L-1}\beta^{p_t}C^{(t)}.
\]

Thus

\[
Y
=\sum_{p=0}^{3}\beta^pD_pW
=\left(\sum_{p=0}^{3}\beta^pD_p\right)W
=XW.
\]

## 4. DIM=16 physical tiling

The logical matrix dimensions are

\[
M_{\mathrm{stack}}=LR,\qquad H=|U|,\qquad J.
\]

A DIM=16 array executes

\[
N_{\mathrm{tiles}}
=\left\lceil\frac{M_{\mathrm{stack}}}{16}\right\rceil
 \left\lceil\frac{H}{16}\right\rceil
 \left\lceil\frac{J}{16}\right\rceil
\]

physical tiles. Since \(R=16\), every active digit plane is exactly one M tile.
The operation is one logical GEMM per stripe, even though the fixed array runs
multiple physical tiles.

## 5. Simulation contract

- C++ owns INT32 decomposition, lane masks, union-K selection, compact matrix
  construction, direct golden calculation, and final radix-256 composition.
- BSV receives signed INT8 `16 x 16` generated activation and weight tiles,
  then sign-extends each value to the configured `INT, 32` compute element.
- BSV performs weight-stationary INT32 dense GEMM and INT32 K-tile accumulation
  so PE elements and accumulator cells have identical precision.
- BSV emits the stacked INT32 output tiles.
- C++ composes those outputs and compares them with direct `X x W`.
