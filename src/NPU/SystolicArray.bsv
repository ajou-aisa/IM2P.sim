package SystolicArray;

import Vector::*;
import NumericFormat::*;
import PE::*;

// dim은 배열 한 변의 크기, format은 INT/FLOAT, precision은 element 비트 수다.
// 입출력과 내부 psum은 모두 NumericElement#(format, precision)이므로
// 별도의 accumulator 폭을 지정하거나 서로 다른 폭을 연결할 수 없다.
interface SystolicArray#(
    numeric type dim,
    type format,
    numeric type precision
);
    // PE[row][col]의 row는 matmul K축, col은 출력 N축이다.
    // WS mode이므로 같은 tile을 처리하는 동안 weight는 PE에 고정된다.
    method Action preloadWeights(
        Vector#(
            dim,
            Vector#(dim, NumericElement#(format, precision))
        ) weights
    );

    // 한 cycle 동안 왼쪽 경계에 activation token, 위쪽 경계에 초기 psum
    // valid를 넣는다. 내부 skew buffer가 없으므로 호출자가 맞춰 입력한다.
    //   x[i][k] 입력 cycle  = i + k
    //   초기 psum 입력 cycle = i + n
    method Action step(
        Vector#(
            dim,
            Maybe#(NumericElement#(format, precision))
        ) xLeft,
        Vector#(dim, Bool) psumTopValid
    );

    // result[col]은 outValid[col]인 cycle에만 유효하다.
    method Vector#(dim, Bool) outValid;
    method Vector#(dim, NumericElement#(format, precision)) result;
endinterface

module mkSystolicArray(SystolicArray#(dim, format, precision))
provisos (
    Add#(1, dimMinusOne, dim),
    NumericFormat#(format, precision)
);
    // Add proviso는 dim이 1 이상임을 증명하므로 마지막 PE row가 항상 존재한다.
    // 정적 Vector와 loop는 합성 시 고정된 dim x dim mesh로 펼쳐진다.
    Vector#(
        dim,
        Vector#(dim, PE#(format, precision))
    ) pes <- replicateM(replicateM(mkPE));

    method Action preloadWeights(
        Vector#(
            dim,
            Vector#(dim, NumericElement#(format, precision))
        ) weights
    );
        for (Integer row = 0; row < valueOf(dim); row = row + 1) begin
            for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
                pes[row][col].preloadWeight(weights[row][col]);
            end
        end
    endmethod

    method Action step(
        Vector#(
            dim,
            Maybe#(NumericElement#(format, precision))
        ) xLeft,
        Vector#(dim, Bool) psumTopValid
    );
        // 매 호출마다 activation은 오른쪽, psum은 아래쪽으로 PE 한 칸 이동한다.
        // Invalid payload는 bubble이므로 산술 함수에 전달되지 않는다.
        for (Integer row = 0; row < valueOf(dim); row = row + 1) begin
            for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
                Bool xValid = False;
                NumericElement#(format, precision) x = numericZero(?);
                Bool psumValid = False;
                NumericElement#(format, precision) psum = numericZero(?);

                if (col == 0) begin
                    xValid = isValid(xLeft[row]);
                    x = fromMaybe(numericZero(?), xLeft[row]);
                end
                else begin
                    xValid = pes[row][col - 1].outValid;
                    x = pes[row][col - 1].xOut;
                end

                if (row == 0) begin
                    // 각 출력 token의 누적은 format에 맞는 0에서 시작한다.
                    psumValid = psumTopValid[col];
                end
                else begin
                    psumValid = pes[row - 1][col].outValid;
                    psum = pes[row - 1][col].psumOut;
                end

                // activation과 psum token이 함께 도착할 때만 결과를 낸다.
                pes[row][col].step(xValid && psumValid, x, psum);
            end
        end
    endmethod

    method Vector#(dim, Bool) outValid;
        Vector#(dim, Bool) valids = newVector;

        // 마지막 row 출력은 K축 전체를 소비한 결과다.
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            valids[col] = pes[valueOf(dim) - 1][col].outValid;
        end

        return valids;
    endmethod

    method Vector#(dim, NumericElement#(format, precision)) result;
        Vector#(dim, NumericElement#(format, precision)) values = newVector;

        // column별 출력 cycle은 skew 상태이므로 outValid와 함께 읽어야 한다.
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            values[col] = pes[valueOf(dim) - 1][col].psumOut;
        end

        return values;
    endmethod

endmodule

endpackage
