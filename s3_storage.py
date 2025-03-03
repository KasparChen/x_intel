import boto3
import json
from config import AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET
from utils import log_info, log_error

s3_client = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY_ID, aws_secret_access_key=AWS_SECRET_ACCESS_KEY)

def save_to_s3(data, folder, filename):
    try:
        key = f"{folder}/{filename}"
        s3_client.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(data, ensure_ascii=False))
        log_info(f"成功保存到 S3: {key}")
    except Exception as e:
        log_error(f"S3 保存失败: {key}, 错误: {str(e)}")

def load_from_s3(folder, filename):
    try:
        key = f"{folder}/{filename}"
        response = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except Exception as e:
        log_error(f"S3 加载失败: {key}, 错误: {str(e)}")
        return None

def list_s3_files(folder, start_time=None):
    """列出指定文件夹中符合时间条件的文件"""
    try:
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=folder)
        files = []
        for obj in response.get("Contents", []):
            filename = obj["Key"].split("/")[-1]
            timestamp = filename.replace(".json", "").replace("_", " ")
            if start_time and timestamp <= start_time:
                continue
            files.append((timestamp, obj["Key"]))
        files.sort()  # 按时间排序
        return files
    except Exception as e:
        log_error(f"S3 列出文件失败: {folder}, 错误: {str(e)}")
        return []

def append_to_mempool(message):
    timestamp = message.get("timestamp", utils.get_timestamp())
    filename = f"{timestamp.replace(' ', '_')}.json"
    save_to_s3(message, "intel_mempool", filename)

def save_published_message(message):
    timestamp = utils.get_timestamp()
    filename = f"{timestamp.replace(' ', '_')}.json"
    save_to_s3(message, "intel_publish", filename)
    # 同时保存 Embedding
    from llm_agent import get_embedding  # 动态导入避免循环依赖
    embedding = get_embedding(message["content"])
    save_to_s3({"timestamp": timestamp, "content": message["content"], "embedding": embedding}, "embeddings", filename)