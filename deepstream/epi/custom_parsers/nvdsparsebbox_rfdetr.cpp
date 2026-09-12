/* Parser DeepStream custom para RF-DETR (Recognition, task-105).
 * Contrato de saída do export ONNX oficial (rfdetr>=1.8):
 *   dets:   [B, Q, 4]  — cxcywh NORMALIZADO [0,1]
 *   labels: [B, Q, C]  — logits crus (sem ativação)
 * DETR não usa NMS → cluster-mode=4 no nvinfer.
 * Score = sigmoid(melhor logit); classe = argmax.
 */
#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>
#include "nvdsinfer_custom_impl.h"

static inline float sigmoidf(float x) { return 1.0f / (1.0f + expf(-x)); }

extern "C" bool NvDsInferParseCustomRFDETR(
    std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
    NvDsInferNetworkInfo const &networkInfo,
    NvDsInferParseDetectionParams const &detectionParams,
    std::vector<NvDsInferParseObjectInfo> &objectList)
{
    const NvDsInferLayerInfo *dets = nullptr, *labels = nullptr;
    for (auto const &l : outputLayersInfo) {
        if (!strcmp(l.layerName, "dets")) dets = &l;
        else if (!strcmp(l.layerName, "labels")) labels = &l;
    }
    // fallback por shape se os nomes divergirem
    if (!dets || !labels) {
        for (auto const &l : outputLayersInfo) {
            if (l.inferDims.numDims >= 2 && l.inferDims.d[l.inferDims.numDims-1] == 4) dets = &l;
            else labels = &l;
        }
    }
    if (!dets || !labels) return false;

    const int Q = dets->inferDims.d[0];                 // queries (dims sem batch)
    const int C = labels->inferDims.d[labels->inferDims.numDims-1];
    const float *db = static_cast<const float *>(dets->buffer);
    const float *lb = static_cast<const float *>(labels->buffer);
    const float W = networkInfo.width, H = networkInfo.height;

    for (int q = 0; q < Q; q++) {
        const float *lg = lb + q * C;
        int best = 0; float bestv = lg[0];
        for (int c = 1; c < C; c++) if (lg[c] > bestv) { bestv = lg[c]; best = c; }
        float score = sigmoidf(bestv);
        float thr = best < (int)detectionParams.perClassThreshold.size()
                        ? detectionParams.perClassThreshold[best] : 0.4f;
        if (score < thr) continue;
        const float *bx = db + q * 4;   // cx cy w h normalizados
        float bw = bx[2] * W, bh = bx[3] * H;
        float x0 = bx[0] * W - bw / 2.f, y0 = bx[1] * H - bh / 2.f;
        x0 = std::max(0.f, std::min(x0, W - 1)); y0 = std::max(0.f, std::min(y0, H - 1));
        bw = std::min(bw, W - x0); bh = std::min(bh, H - y0);
        if (bw <= 1 || bh <= 1) continue;
        NvDsInferParseObjectInfo o{};
        o.classId = (unsigned int)best;
        o.detectionConfidence = score;
        o.left = x0; o.top = y0; o.width = bw; o.height = bh;
        objectList.push_back(o);
    }
    return true;
}
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomRFDETR);
