import json
import numpy as np
from openai import OpenAI
from config import LLM_API_KEY, LLM_BASE_URL, SYSTEM_PROMPT, MODEL_ID, EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL_ID
from utils import log_info, log_error, format_summary
from s3_storage import list_s3_files, load_from_s3

# 创建独立的 LLM 和 Embedding 客户端
llm_client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
embedding_client = OpenAI(api_key=EMBEDDING_API_KEY, base_url=EMBEDDING_BASE_URL)

def get_embedding(text):
    """调用火山引擎 Embedding API"""
    try:
        response = embedding_client.embeddings.create(
            model=EMBEDDING_MODEL_ID,
            input=[text],
            encoding_format="float"
        )
        embedding = response.data[0].embedding
        log_info(f"成功生成 Embedding: {text[:20]}...")
        return embedding
    except Exception as e:
        log_error(f"Embedding 生成失败: {str(e)}")
        return None

def cosine_similarity(vec1, vec2):
    """计算余弦相似度"""
    return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

def call_llm(messages):
    """调用火山引擎 LLM"""
    try:
        prompt = f"消息列表：\n{messages}"
        completion = llm_client.chat.completions.create(
            model=MODEL_ID,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        )
        result = completion.choices[0].message.content
        log_info("LLM 分析完成")
        return json.loads(result)  # 假设返回 JSON
    except Exception as e:
        log_error(f"LLM 调用失败: {str(e)}")
        return []

def analyze_messages(messages, last_position):
    """分析消息并生成总结"""
    message_text = "\n".join([f"{m['source']} {m['content']} {m['attachment_link']} {m['original_link']}" for m in messages])
    summaries = call_llm(message_text)
    
    # RAG 去重
    embeddings = list_s3_files("embeddings")
    formatted_summaries = []
    for summary in summaries:
        content = summary["content"]
        embedding = get_embedding(content)
        is_duplicate = False
        for _, embedding_key in embeddings:
            past_data = load_from_s3("embeddings", embedding_key.split("/")[-1])
            if past_data and cosine_similarity(embedding, past_data["embedding"]) > 0.9:
                is_duplicate = True
                break
        if not is_duplicate:
            formatted_summaries.append(format_summary(summary["category"], content, summary.get("importance")))
    return formatted_summaries