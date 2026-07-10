# setup-vulkan.py
# Repo-checkout shim: installs the whisper.cpp Vulkan GPU runtime into
# runtime/whisper-cpp-vulkan/. The real logic (pins, hash verification,
# provenance notes) lives in src/parley/vulkan_setup.py — installed users get
# the same thing via `parley --setup-vulkan`.
#
#   python setup-vulkan.py                 # installs large-v3-turbo (recommended)
#   set WHISPER_GGML_MODEL=large-v3 && python setup-vulkan.py

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from parley.vulkan_setup import main

if __name__ == "__main__":
    sys.exit(main())
