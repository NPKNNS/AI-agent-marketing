# Start local Qwen3.5-9B-GGUF OpenAI-compatible server on port 1235 (-np 1 -c 6144 -ngl 18 fits cleanly in 4GB VRAM)
$env:PATH = "C:\Users\nonlf\.lmstudio\extensions\backends\vendor\win-llama-cuda12-vendor-v2;C:\Users\nonlf\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.73.0;" + $env:PATH
& "C:\Users\nonlf\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.73.0\llama-server.exe" `
    -m "d:\Cloud\miror\NPK\PCfile\TESA\Train\AI_agent\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q4_K_M.gguf" `
    --port 1235 -np 1 -c 6144 -ngl 18 --alias "qwen3.5-9b"
