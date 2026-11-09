# Third-party notices

WhisperSub bundles or downloads the following components. Each is governed by its own license.

| Component | License | Notes |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Whisper inference wrapper (bundled) |
| [CTranslate2](https://github.com/OpenNMT/CTranslate2) | MIT | Inference engine (bundled) |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT | Voice activity detection model shipped with faster-whisper |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | Runs the VAD model (bundled) |
| [PyAV](https://github.com/PyAV-Org/PyAV) + FFmpeg libraries | BSD-3 / LGPL-2.1+ | Audio decoding. FFmpeg shared libraries are the LGPL builds distributed in the PyAV wheels; source: https://ffmpeg.org |
| [PySide6 / Qt 6](https://www.qt.io/) | LGPL-3.0 | GUI toolkit (dynamically linked). Qt source: https://code.qt.io |
| [NumPy](https://numpy.org/) | BSD-3 | |
| [huggingface_hub](https://github.com/huggingface/huggingface_hub), tokenizers | Apache-2.0 | Model download / tokenizer |
| NVIDIA cuBLAS, cuDNN (pip `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) | NVIDIA Software License Agreement / cuDNN SLA | Redistributable runtime libraries required for GPU inference. See https://docs.nvidia.com/cuda/eula/ and https://docs.nvidia.com/deeplearning/cudnn/sla/ |
| OpenAI Whisper weights (CTranslate2 conversions, e.g. `Systran/faster-whisper-large-v3`) | MIT | **Not bundled** — downloaded from Hugging Face on first use into the user's cache |

The application's own source code is released under the MIT License (see LICENSE).
