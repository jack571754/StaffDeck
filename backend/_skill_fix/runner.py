import json
payload=json.loads(input())
print(json.dumps({"success": True, "city": "北京", "weather": "晴", "query": payload["query"]}, ensure_ascii=False))
