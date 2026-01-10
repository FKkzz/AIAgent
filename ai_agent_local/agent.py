import os
import json
import re
from pathlib import Path
from flask import Flask, request, jsonify
import fitz  # PyMuPDF
from openai import OpenAI
from dotenv import load_dotenv

# ======================
# 1. 环境配置
# ======================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

app = Flask(__name__)

# ======================
# 2. PDF 文本提取 (增强版)
# ======================
def extract_pdf_text(pdf_path: str) -> str:
    try:
        doc = fitz.open(pdf_path)
        text_list = []
        for page in doc:
            t = page.get_text()
            if t: text_list.append(t) # 确保只添加非空字符串
        doc.close()
        return "\n".join(text_list)
    except Exception as e:
        print(f"[Error] PDF Extraction failed: {e}")
        return ""

# ======================
# 3. 分析层 (增强鲁棒性)
# ======================
def llm_analyze(text: str, existing_tags: list, mode: str = "both") -> dict:
    # --- 修复点：过滤 existing_tags 中的 None 或非字符串对象 ---
    safe_tags = [str(t) for t in existing_tags if t is not None]
    tags_context = ", ".join(safe_tags) if safe_tags else "None"
    
    # 根据模式生成不同的提示
    if mode == "summary":
        prompt = f"""Analyze this paper.
        1. Detailed Summarize in Chinese.
        Rules: Use existing tags if possible: [{tags_context}].
        Output JSON format: {{"summary": "...", "keywords": []}}
        """
    elif mode == "keywords":
        prompt = f"""Analyze this paper.
        1. 5-8 English keywords.
        Rules: Use existing tags if possible: [{tags_context}]. No 'A+B' compound tags.
        Output JSON format: {{"summary": "", "keywords": ["...", "..."]}}
        """
    else:  # both
        prompt = f"""Analyze this paper.
        1. Detailed Summarize in Chinese.
        2. 5-8 English keywords.
        Rules: Use existing tags if possible: [{tags_context}]. No 'A+B' compound tags.
        Output JSON format: {{"summary": "...", "keywords": ["...", "..."]}}
        """

    try:
        resp = client.chat.completions.create(
            model="deepseek-v3",
            messages=[
                {"role": "system", "content": "You are a scientist. Respond only in valid JSON."},
                {"role": "user", "content": f"{prompt}\n\nContent:\n{text[:15000]}"}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        content = resp.choices[0].message.content
        # 强力提取 JSON
        match = re.search(r'(\{.*\})', content, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return json.loads(content)
    except Exception as e:
        print(f"[Error] LLM failed: {e}")
        return {"summary": "AI分析失败", "keywords": []}

# ======================
# 4. 路由
# ======================
@app.route("/process", methods=["POST"])
def process():
    try:
        data = request.get_json()
        pdf_path = data.get("pdf_path")
        # 接收并初步清洗标签
        existing_tags = data.get("existing_tags", [])
        # 接收处理模式，默认为"both"
        mode = data.get("mode", "both")
        
        print(f"[Status] Processing PDF: {pdf_path} with mode: {mode}")
        
        text = extract_pdf_text(pdf_path)
        if not text:
            return jsonify({"error": "Empty text"}), 400

        result = llm_analyze(text, existing_tags, mode)
        return jsonify(result)
    except Exception as e:
        # 这里会捕获刚才那个 Critical Error
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # Check if SSL context is available for HTTPS
    import ssl
    ssl_context = None
    cert_file = os.getenv("SSL_CERT_FILE", "")
    key_file = os.getenv("SSL_KEY_FILE", "")
    if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_context = (cert_file, key_file)
        print("Starting server with HTTPS support...")
        app.run(host="127.0.0.1", port=3333, ssl_context=ssl_context)
    else:
        # For development, we'll run on HTTP by default, but recommend using HTTPS in production
        print("Warning: Running on HTTP. For production, set up SSL certificates using SSL_CERT_FILE and SSL_KEY_FILE environment variables.")
        print("To enable HTTPS: create a .env file with SSL_CERT_FILE and SSL_KEY_FILE paths, then run the server.")
        app.run(host="127.0.0.1", port=3333)
