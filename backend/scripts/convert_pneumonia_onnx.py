"""Convert a trained ResNet50 pneumonia checkpoint to the ONNX the API loads.

    # one-off tooling, deliberately NOT in backend/requirements.txt
    python3 -m venv --system-site-packages .venv-convert
    .venv-convert/bin/pip install torchvision onnx
    .venv-convert/bin/python backend/scripts/convert_pneumonia_onnx.py \
        --checkpoint models/biofusion_pth/pneumonia_resnet50_best.pth \
        --variant general

Torch is a ~2 GB dependency used exactly once, to turn weights into a portable
graph. The API serves that graph with onnxruntime, which it already has for
sentence embeddings. Putting torch in the runtime image to avoid one
conversion step would be the wrong trade by an order of magnitude.

What it writes
--------------
`models/pneumonia/<variant>.onnx` plus `<variant>.json` beside it. The adapter
reads the JSON for its operating point — without one it scores at a neutral
0.5 threshold, which for a screening model is the wrong default: you want the
threshold that hits your target sensitivity, and that number belongs to the
trained model rather than to this code.

**The emitted sidecar has no measured threshold.** It cannot: a threshold
comes from a held-out test set, and this script has no test set. It writes
`threshold: null` and a `TODO`, so the gap is visible rather than silently
becoming 0.5. Fill it in after evaluating, or the model ships uncalibrated.

Verified against the checkpoints in this repo: ResNet50, 3-channel input,
2-class output ordered [normal, pneumonia].
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "models" / "pneumonia"

# Which trained model answers which kind of upload. See
# `app/services/vision/registry.py` — this must stay in step with it.
VARIANTS = ("general", "photo", "paediatric")


def build_model(checkpoint: Path):
    import torch
    from torchvision.models import resnet50

    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if not isinstance(state, dict):
        raise SystemExit(f"{checkpoint.name}: not a state dict.")
    # Tolerate both a bare state_dict and one wrapped by a training loop.
    state = state.get("state_dict", state)
    state = {k.replace("module.", "", 1): v for k, v in state.items()}

    classes = state["fc.weight"].shape[0]
    channels = state["conv1.weight"].shape[1]
    if channels != 3:
        raise SystemExit(f"Expected a 3-channel input, found {channels}.")

    model = resnet50(weights=None)
    model.fc = torch.nn.Linear(model.fc.in_features, classes)
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise SystemExit(
            f"Checkpoint does not match ResNet50.\n  missing: {list(missing)[:5]}\n"
            f"  unexpected: {list(unexpected)[:5]}"
        )
    model.eval()
    return model, classes


def export(model, destination: Path, size: int = 224) -> None:
    import torch

    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        torch.randn(1, 3, size, size),
        str(destination),
        input_names=["input"],
        output_names=["output"],
        # Batch left dynamic so test-time augmentation can send several crops
        # in one call rather than looping.
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
        # One file, not two. Torch's newer exporter writes weights to a
        # sibling `.onnx.data`, which loads fine locally and then quietly
        # breaks anything that copies or downloads "the model" as a single
        # artifact — a release asset, a COPY of one filename, an object-store
        # fetch. At ~94 MB there is no reason to split it.
        dynamo=False,
    )


def verify(destination: Path, size: int = 224) -> dict:
    """Load the export and run one image through it.

    A conversion that produces a file but not a usable graph is the failure
    worth catching here, not on the first patient upload.
    """
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(destination), providers=["CPUExecutionProvider"])
    spec = session.get_inputs()[0]
    output = session.run(
        None, {spec.name: np.random.rand(1, 3, size, size).astype(np.float32)}
    )[0]
    return {
        "input_name": spec.name,
        "input_shape": [str(d) for d in spec.shape],
        "output_shape": list(output.shape),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--variant", required=True, choices=VARIANTS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--size", type=int, default=224)
    parser.add_argument(
        "--trained-on", default="",
        help="Free text describing the dataset. Recorded in the sidecar, and "
             "it is the only place that information will exist — the "
             "checkpoints carry no metadata at all.",
    )
    args = parser.parse_args()

    if not args.checkpoint.is_file():
        raise SystemExit(f"No such checkpoint: {args.checkpoint}")

    print(f"Loading {args.checkpoint.name} ...")
    model, classes = build_model(args.checkpoint)
    print(f"  ResNet50, {classes}-class head")

    destination = args.out_dir / f"{args.variant}.onnx"
    print(f"Exporting to {destination} ...")
    export(model, destination, args.size)

    info = verify(destination, args.size)
    print(f"  verified: input {info['input_shape']} -> output {info['output_shape']}")

    sidecar = destination.with_suffix(".json")
    if sidecar.is_file():
        print(f"  {sidecar.name} already exists — left alone, so a measured "
              f"threshold is never overwritten by a re-export.")
    else:
        sidecar.write_text(json.dumps({
            "model_version": f"resnet50-{args.variant}",
            "source_checkpoint": args.checkpoint.name,
            "class_order": ["normal", "pneumonia"],
            "operating_point": {
                # Deliberately null. A threshold is measured on a held-out set,
                # and this script has none; writing 0.5 here would look like a
                # decision when it is an absence.
                "threshold": None,
                "policy": "TODO: choose the threshold meeting your recall target",
                "min_recall_target": 0.95,
                "tta": False,
            },
            "training": {
                "dataset": args.trained_on or "TODO: describe the training data",
                "trained_at": None,
            },
            "test_metrics": {
                "TODO": "sensitivity, specificity, AUC on a held-out set",
            },
            "converted_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n")
        print(f"  wrote {sidecar.name} — threshold is null until you measure it")

    print(f"\nDone. Restart the API and check GET /api/v1/vision/adapters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
