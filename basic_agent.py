import os
from pathlib import Path
import fitz  # PyMuPDF
from openai import OpenAI
from dotenv import load_dotenv

# ======================
# 1. 读取 .env 文件
# ======================
BASE_DIR = Path(__file__).resolve().parent
DOTENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=DOTENV_PATH)

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# ======================
# 2. PDF 路径
# ======================
PDF_PATH = BASE_DIR / "test.pdf"
if not PDF_PATH.exists():
    raise FileNotFoundError(f"PDF not found: {PDF_PATH}")

# ======================
# 3. PDF 解析
# ======================
def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    return "\n".join([page.get_text() for page in doc])

raw_text = extract_pdf_text(PDF_PATH)

# ======================
# 4. 内容理解层
# ======================
def llm_understand(text: str) -> str:
    resp = client.chat.completions.create(
        model="deepseek-v3.2",
        messages=[
            {
                "role": "system",
                "content": "You are a scientific paper reader. Compress the content while preserving technical meaning."
            },
            {"role": "user", "content": text[:12000]}
        ],
        temperature=0.2
    )
    return resp.choices[0].message.content

# ======================
# 5. 分析层
# ======================
def llm_analyze(text: str, mode="summary") -> str:
    prompts = {
        "summary": "Provide a concise scientific summary of this paper. Use chinese language.",
        "keywords": "Extract 5–8 technical keywords. Use english, don't output any other text.",
        "methods": "Summarize the experimental and analytical methods. Use chinese language.",
    }
    if mode not in prompts:
        raise ValueError(f"Unknown mode: {mode}")

    resp = client.chat.completions.create(
        model="deepseek-v3.2",
        messages=[
            {"role": "system", "content": "You are a senior physicist."},
            {"role": "user", "content": prompts[mode] + "\n\n" + text}
        ],
        temperature=0.2
    )
    return resp.choices[0].message.content

# ======================
# 6. 自动压缩决策层
# ======================
def auto_process(text: str, mode="summary", threshold_chars: int = 30000) -> str:
    """
    自动判断文本是否需要压缩：
    - 文本长度大于 threshold_chars → 先压缩再分析
    - 文本长度小于 threshold_chars → 直接分析
    """
    if len(text) > threshold_chars:
        print(f"[INFO] Text length {len(text)} > {threshold_chars}, compressing first...")
        processed_text = llm_understand(text)
    else:
        print(f"[INFO] Text length {len(text)} <= {threshold_chars}, skipping compression...")
        processed_text = text
    return llm_analyze(processed_text, mode=mode)

# ======================
# 7. 执行
# ======================
if __name__ == "__main__":
    result = auto_process(raw_text, mode="keywords")
    print("\n===== RESULT =====\n")
    print(result)
