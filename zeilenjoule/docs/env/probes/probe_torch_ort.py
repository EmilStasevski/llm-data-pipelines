"""Phase-0 probe: torch matmul on sm_120 + onnxruntime-gpu providers."""
import json, time, sys
out = {}
import torch
out["torch"] = torch.__version__
out["torch_cuda"] = torch.version.cuda
out["cuda_available"] = torch.cuda.is_available()
if torch.cuda.is_available():
    out["device"] = torch.cuda.get_device_name(0)
    out["capability"] = torch.cuda.get_device_capability(0)
    out["arch_list"] = torch.cuda.get_arch_list()
    a = torch.randn(8192, 8192, device="cuda", dtype=torch.float16)
    b = torch.randn(8192, 8192, device="cuda", dtype=torch.float16)
    torch.cuda.synchronize(); (a @ b); torch.cuda.synchronize()
    t = time.perf_counter(); n = 20
    for _ in range(n): c = a @ b
    torch.cuda.synchronize(); dt = time.perf_counter() - t
    out["fp16_matmul_tflops"] = round(2 * 8192**3 * n / dt / 1e12, 1)
    ref = (a[:64].float() @ b[:, :64].float())
    out["fp16_vs_fp32_maxabs"] = float((c[:64, :64].float() - ref).abs().max())
    out["sdpa_flash"] = torch.backends.cuda.flash_sdp_enabled()
    q = torch.randn(4, 16, 512, 64, device="cuda", dtype=torch.float16)
    torch.nn.functional.scaled_dot_product_attention(q, q, q); out["sdpa_ok"] = True
try:
    import onnxruntime as ort, numpy as np, onnx
    from onnx import helper, TensorProto
    out["onnxruntime"] = ort.__version__
    out["ort_available_providers"] = ort.get_available_providers()
    X = helper.make_tensor_value_info("X", TensorProto.FLOAT, [256, 256])
    Y = helper.make_tensor_value_info("Y", TensorProto.FLOAT, [256, 256])
    g = helper.make_graph([helper.make_node("MatMul", ["X", "X"], ["Y"])], "g", [X], [Y])
    m = helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)]); m.ir_version = 9
    try: ort.preload_dlls()
    except Exception as e: out["ort_preload_dlls"] = repr(e)
    s = ort.InferenceSession(m.SerializeToString(), providers=["CUDAExecutionProvider"])
    out["ort_session_providers"] = s.get_providers()
    x = np.random.rand(256, 256).astype(np.float32)
    y = s.run(None, {"X": x})[0]
    out["ort_cuda_maxabs"] = float(np.abs(y - x @ x).max())
except Exception as e:
    out["ort_error"] = repr(e)
print(json.dumps(out, indent=1))
