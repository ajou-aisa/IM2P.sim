package SystolicArray;

import Vector::*;
import PE::*;

interface SystolicArray#(numeric type dim);
    // dim은 실행 중 바뀌는 값이 아니라 합성 시 결정되는 배열 한 변의 크기다.
    // 이 배열에서 PE[row][col]의 row는 matmul의 K축, col은 출력 N축이다.

    // weights[row][col]을 PE[row][col]의 weight register에 저장한다.
    // WS 모드이므로 연산을 시작하기 전에 한 번 호출하고, 같은 weight tile을
    // 처리하는 동안에는 다시 호출하지 않는다.
    method Action preloadWeights(Vector#(dim, Vector#(dim, Int#(8))) weights);

    // 한 cycle 동안 배열의 왼쪽 경계와 위쪽 경계에 데이터를 공급한다.
    // xLeft[row]가 Valid(x)이면 row번째 PE 행에 activation x를 넣고,
    // Invalid이면 해당 행에는 bubble을 넣는다.
    // psumTopValid[col]이 True이면 col번째 PE 열에 초기 psum 0을 넣는다.
    //
    // 배열 내부에는 skew buffer가 없으므로 호출자가 다음 시각에 맞춰 입력한다.
    //   x[i][k] 입력 cycle       = i + k
    //   출력 C[i][n]의 psum cycle = i + n
    method Action step(
        Vector#(dim, Maybe#(Int#(8))) xLeft,
        Vector#(dim, Bool) psumTopValid
    );

    // result[col]은 각 열의 마지막 PE가 출력하는 32-bit partial sum이다.
    // outValid[col]이 True인 cycle에만 대응하는 result[col]을 사용한다.
    method Vector#(dim, Bool) outValid;
    method Vector#(dim, Int#(32)) result;
endinterface

module mkSystolicArray(SystolicArray#(dim))
    // 마지막 PE 행을 참조하므로 dim은 최소 1이어야 한다.
    provisos (Add#(1, dimMinusOne, dim));

    // dim x dim PE 배열이다. 각 PE의 weight는 고정되고,
    // activation은 오른쪽으로, partial sum은 아래쪽으로 한 cycle씩 이동한다.
    Vector#(dim, Vector#(dim, PE)) pes <- replicateM(replicateM(mkPE));

    method Action preloadWeights(Vector#(dim, Vector#(dim, Int#(8))) weights);
        // 정적인 for-loop이므로 모든 PE에 대한 preload 회로가 합성 시 펼쳐진다.
        // 이 method 한 번으로 dim x dim weight를 같은 cycle에 저장한다.
        for (Integer row = 0; row < valueOf(dim); row = row + 1) begin
            for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
                pes[row][col].preloadWeight(weights[row][col]);
            end
        end
    endmethod

    method Action step(
        Vector#(dim, Maybe#(Int#(8))) xLeft,
        Vector#(dim, Bool) psumTopValid
    );
        // 각 PE는 현재 cycle에 보이는 왼쪽/위쪽 PE의 등록된 출력을 읽고,
        // 계산 결과를 자신의 출력 register에 저장한다. 따라서 데이터는
        // step 호출마다 PE 한 칸씩 이동한다.
        for (Integer row = 0; row < valueOf(dim); row = row + 1) begin
            for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
                Bool xValid = False;
                Int#(8) x = 0;
                Bool psumValid = False;
                Int#(32) psum = 0;

                // 첫 열은 외부 xLeft를 읽고, 나머지 열은 왼쪽 PE 출력을 읽는다.
                // Maybe의 Invalid는 실제 0 데이터가 아니라 데이터가 없는 bubble이다.
                if (col == 0) begin
                    xValid = isValid(xLeft[row]);
                    x = fromMaybe(0, xLeft[row]);
                end
                else begin
                    xValid = pes[row][col - 1].outValid;
                    x = pes[row][col - 1].xOut;
                end

                // 첫 행은 외부 valid와 초기값 0으로 누적을 시작한다.
                // 나머지 행은 위쪽 PE가 계산한 partial sum을 이어받는다.
                if (row == 0) begin
                    psumValid = psumTopValid[col];
                end
                else begin
                    psumValid = pes[row - 1][col].outValid;
                    psum = pes[row - 1][col].psumOut;
                end

                // activation과 partial sum이 같은 cycle에 도착해야 해당 PE의
                // x * weight + psum 결과가 유효하다. 올바른 skew 입력에서는
                // 두 valid가 항상 같은 output token을 가리킨다.
                pes[row][col].step(xValid && psumValid, x, psum);
            end
        end
    endmethod

    method Vector#(dim, Bool) outValid;
        Vector#(dim, Bool) valids = newVector;

        // K축 누적을 모두 마친 마지막 PE 행의 valid를 열별로 노출한다.
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            valids[col] = pes[valueOf(dim) - 1][col].outValid;
        end

        return valids;
    endmethod

    method Vector#(dim, Int#(32)) result;
        Vector#(dim, Int#(32)) values = newVector;

        // result[col]은 한 출력 행의 C[i][col]에 해당한다.
        // 서로 다른 col은 skew 때문에 서로 다른 cycle에 유효해질 수 있다.
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            values[col] = pes[valueOf(dim) - 1][col].psumOut;
        end

        return values;
    endmethod

endmodule

endpackage
