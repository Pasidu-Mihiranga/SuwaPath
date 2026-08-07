# Pneumonia chest X-ray model

Drop a trained model here as `model.onnx` (any `*.onnx` filename works) and
restart the API. `OnnxPneumoniaAdapter` picks it up automatically and takes
priority over the bundled untrained baseline — no code changes needed.

The loader reads input shape, channel count and layout from the graph, so both
Keras (NHWC) and PyTorch (NCHW) exports work. Output heads may be a single
sigmoid logit, a 2-class softmax, or an already-normalised probability vector.

Class order is assumed to be `[normal, pneumonia]`.

Exporting from PyTorch:

    torch.onnx.export(model, torch.randn(1, 3, 224, 224), "model.onnx",
                      input_names=["input"], output_names=["output"],
                      dynamic_axes={"input": {0: "batch"}})

Exporting from Keras:

    pip install tf2onnx
    python -m tf2onnx.convert --saved-model saved_model_dir --output model.onnx

Verify it was picked up at: Admin → AI Configuration, or GET /api/v1/vision/adapters
